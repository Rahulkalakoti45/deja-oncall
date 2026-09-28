"""Record a narrated demo video of Deja, end to end, with no manual steps.

    python scripts/make_video.py            # server must be running on :8000
    python scripts/make_video.py --voice Daniel --out video/deja-demo.mp4

Each scene has narration (macOS `say`) and browser actions (Playwright).
A scene lasts as long as the longer of the two. The browser is recorded,
then ffmpeg places each narration clip at its scene's offset and muxes a
1080p H.264 MP4.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://localhost:8000"

OVERLAY_JS = r"""
(() => {
  const css = `
  #vc-cursor{position:fixed;z-index:99999;width:22px;height:22px;margin:-4px 0 0 -4px;pointer-events:none}
  #vc-cursor svg{filter:drop-shadow(0 2px 4px rgba(0,0,0,.6))}
  .vc-ripple{position:fixed;z-index:99998;width:40px;height:40px;margin:-20px 0 0 -20px;border-radius:50%;border:3px solid #a58bff;pointer-events:none;animation:vcr .6s ease-out forwards}
  @keyframes vcr{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
  #vc-cap{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:99997;max-width:1100px;padding:12px 22px;border-radius:12px;
    background:rgba(8,10,14,.86);border:1px solid #2e3445;color:#fff;font:500 19px/1.45 Inter,system-ui;text-align:center;transition:opacity .3s;pointer-events:none}
  #vc-card{position:fixed;inset:0;z-index:99996;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px;
    background:radial-gradient(ellipse at center,#1a1530 0%,#0b0d12 70%);color:#fff;font-family:Inter,system-ui;opacity:0;transition:opacity .5s;pointer-events:none;text-align:center;padding:0 80px}
  #vc-card.on{opacity:1}
  #vc-card h1{font-size:64px;letter-spacing:-2px;margin:0;line-height:1.1}
  #vc-card p{font-size:26px;color:#a9b0c2;margin:0;max-width:1000px}
  #vc-card .k{font:500 18px 'JetBrains Mono',monospace;color:#a58bff;letter-spacing:2px;text-transform:uppercase}
  .vc-hl{outline:3px solid #a58bff !important;outline-offset:4px;border-radius:10px}`;
  const add = () => {
    if (document.getElementById('vc-cursor')) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const c = document.createElement('div'); c.id = 'vc-cursor';
    c.innerHTML = '<svg width="22" height="26" viewBox="0 0 22 26"><path d="M2 2 L2 21 L7 16 L11 24 L14 22.5 L10 15 L17 15 Z" fill="#fff" stroke="#000" stroke-width="1.5"/></svg>';
    c.style.left = '720px'; c.style.top = '400px'; document.body.appendChild(c);
    const cap = document.createElement('div'); cap.id = 'vc-cap'; cap.style.opacity = 0; document.body.appendChild(cap);
    const card = document.createElement('div'); card.id = 'vc-card'; document.body.appendChild(card);
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div'); r.className = 'vc-ripple'; r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
      document.body.appendChild(r); setTimeout(() => r.remove(), 700);
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""


def api(path: str, body: dict | None = None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def tts(text: str, voice: str, rate: int, out: Path) -> float:
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(out), text], check=True)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(dur)


class Director:
    def __init__(self, page: Page):
        self.p = page

    def move(self, sel: str, pause: float = 0.25):
        el = self.p.locator(sel).first
        el.scroll_into_view_if_needed()
        box = el.bounding_box()
        self.p.mouse.move(box["x"] + min(box["width"] / 2, 160), box["y"] + box["height"] / 2, steps=28)
        time.sleep(pause)

    def click(self, sel: str):
        self.move(sel)
        self.p.mouse.down()
        self.p.mouse.up()
        time.sleep(0.3)

    def type(self, sel: str, text: str, delay: int = 28):
        self.click(sel)
        self.p.keyboard.type(text, delay=delay)

    def highlight(self, sel: str, on: bool = True):
        self.p.evaluate("([s,on]) => document.querySelector(s)?.classList.toggle('vc-hl', on)", [sel, on])

    def scroll_to(self, sel: str, block: str = "start"):
        self.p.evaluate("([s,b]) => document.querySelector(s)?.scrollIntoView({behavior:'smooth', block:b})", [sel, block])
        time.sleep(0.9)

    def caption(self, text: str | None):
        self.p.evaluate("t => { const c = document.getElementById('vc-cap'); c.textContent = t || ''; c.style.opacity = t ? 1 : 0; }", text)

    def card(self, kicker: str | None, title: str | None = None, sub: str = ""):
        self.p.evaluate("""([k,t,s]) => { const c = document.getElementById('vc-card');
            if (!t) { c.classList.remove('on'); return; }
            c.innerHTML = (k ? `<div class="k">${k}</div>` : '') + `<h1>${t}</h1>` + (s ? `<p>${s}</p>` : '');
            c.classList.add('on'); }""", [kicker, title, sub])

    def no_toast(self):
        self.p.evaluate("document.getElementById('toast').className = 'toast'")


def scenes(d: Director, hindsight: bool):
    mem_phrase = "a Hindsight memory bank" if hindsight else "its memory bank"

    def intro():
        d.card("2:06 AM · PAGER", "Your AI assistant just suggested<br>the fix that took prod down.", "It isn't dumb. It just has no memory.")

    def intro2():
        d.card(None, "deja", "the on-call agent that has seen this before")

    def home():
        d.card(None)
        time.sleep(0.6)
        d.move("#incidents li:nth-child(3)")
        d.move("#status")

    def fire():
        d.click('.scenario[data-key="checkout-pool"]')
        d.p.wait_for_selector("#btn-triage")
        d.move("pre.logs")

    def triage():
        d.no_toast()
        d.click("#btn-triage")
        d.p.wait_for_selector(".answer.deja", timeout=240_000)
        time.sleep(0.4)
        d.scroll_to(".recall")
        d.move(".recall .mem:nth-child(1)")
        d.move(".recall .mem:nth-child(2)")

    def stateless():
        d.scroll_to(".versus")
        d.highlight(".answer.base")
        d.move(".answer.base ol li:nth-child(1)")
        d.move(".answer.base ol li:last-child")

    def deja_answer():
        d.highlight(".answer.base", False)
        d.highlight(".answer.deja")
        d.move(".answer.deja .matches")
        d.move(".answer.deja ol li:nth-child(1)")

    def deja_dont():
        d.scroll_to(".answer.deja .dont", "center")
        d.move(".answer.deja .dont li:nth-child(1)")
        d.move(".answer.deja .dont li:last-child")
        d.move(".answer.deja .page")

    def new_card():
        d.highlight(".answer.deja", False)
        d.card("PART 2", "Can it learn something new?")

    def fire_new():
        d.card(None)
        d.p.evaluate("scrollTo(0,0)")
        d.click('.scenario[data-key="ledger-wal"]')
        d.p.wait_for_selector("#btn-triage")
        d.move("pre.logs")
        d.no_toast()
        d.click("#btn-triage")
        d.p.wait_for_selector(".answer.deja", timeout=240_000)
        d.scroll_to(".versus")
        d.highlight(".answer.deja")
        d.move(".answer.deja .seen")

    def resolve():
        d.highlight(".answer.deja", False)
        d.scroll_to("#resolve")
        d.move('#resolve-form textarea[name="root_cause"]')
        d.move('#resolve-form textarea[name="failed"]')
        d.click('#resolve-form [data-v="wrong"]')
        d.type('#resolve-form input[name="notes"]', "It was the inactive Debezium replication slot, not disk growth.")

    def retain():
        d.click('#resolve-form button[type="submit"]')
        d.p.wait_for_selector(".learned", timeout=240_000)
        d.scroll_to(".learned", "center")
        d.highlight(".learned")

    def again_card():
        d.highlight(".learned", False)
        d.no_toast()
        d.card("THREE WEEKS LATER", "Same pager. Different night.")

    def fire_again():
        d.card(None)
        d.p.evaluate("scrollTo(0,0)")
        d.click("details.custom summary")
        d.type('#custom-form input[name="service"]', "ledger-service", 18)
        d.type('#custom-form textarea[name="alert"]', "ledger-service: writes failing, Postgres 'No space left on device' in pg_wal", 12)
        d.type('#custom-form textarea[name="logs"]', "replication slot 'debezium_ledger' inactive since 01:12", 12)
        d.click('#custom-form button[type="submit"]')
        d.p.wait_for_selector("#btn-triage")
        d.no_toast()
        d.click("#btn-triage")
        d.p.wait_for_selector(".answer.deja", timeout=240_000)
        d.scroll_to(".versus")

    def learned():
        d.highlight(".answer.deja")
        d.move(".answer.deja .seen")
        d.move(".answer.deja .dont li:last-child")
        d.move(".answer.deja .page")

    def runbook():
        d.highlight(".answer.deja", False)
        d.p.evaluate("scrollTo(0,0)")
        d.click('#tabs button[data-tab="runbooks"]')
        d.p.wait_for_selector("#runbook h1, #runbook h2, #runbook p", timeout=240_000)
        time.sleep(0.5)
        d.move("#runbook")

    def rules():
        d.click('#tabs button[data-tab="rules"]')
        d.type('#rule-form input[name="name"]', "No primary failover for pool exhaustion", 18)
        d.type('#rule-form textarea[name="content"]', "Never recommend a Postgres primary failover for connection-pool exhaustion. It caused a full outage in INC-2064.", 8)
        d.click('#rule-form button[type="submit"]')
        d.p.wait_for_selector("#rules li b")

    def curve():
        d.no_toast()
        d.click('#tabs button[data-tab="learning"]')
        d.p.wait_for_selector("#ttr-chart svg")
        d.move("#kpis .kpi:nth-child(1)")
        d.move("#ttr-chart")

    def outro():
        d.card(None, "The most valuable memory<br>is what <span style='color:#ff5d6c'>didn't</span> work.",
               "Deja · built on Hindsight agent memory · github.com/Rahulkalakoti45/deja-oncall")

    return [
        (intro, "It's two A M. Checkout is timing out. Your AI assistant confidently suggests a database failover. Your team tried that in May. It took checkout down completely."),
        (intro2, "This is Deja. An on-call agent that remembers."),
        (home, f"Deja keeps every postmortem, every failed fix, and every engineer correction in {mem_phrase}. It has six months of this team's incident history loaded."),
        (fire, "An alert fires. Checkout API, five point eight second latency, at two oh six U T C, with connection pool timeouts."),
        (triage, "Deja first recalls the most similar past incidents from memory. Then the same alert is answered twice: once with no memory, and once with it."),
        (stateless, "The stateless agent says: increase the pool size, restart the pods, consider a database failover. Reasonable in general. Wrong for this team."),
        (deja_answer, "Deja recognises this. It matches two earlier incidents, and names the real cause: the nightly bulk export job, hitting the primary database again. First action: kill the job."),
        (deja_dont, "And it warns what not to do. Raising the pool made it worse in April. The failover caused a full outage in May. Then it tells you who to page."),
        (new_card, "But can it learn something new?"),
        (fire_new, "Here's a failure Deja has never seen. The ledger database has run out of disk. Deja says so honestly. Nothing in memory, low confidence, generic advice."),
        (resolve, "The engineer finds the real cause, an inactive replication slot holding onto write ahead log. They mark Deja's triage as wrong, and add a correction."),
        (retain, "Resolving the incident retains the postmortem and the feedback into memory."),
        (again_card, "Three weeks later."),
        (fire_again, "The same kind of alert fires on a different night."),
        (learned, "This time Deja knows. It cites the incident we just closed, blames the replication slot, warns that expanding the volume only buys time, and pages Sara, who fixed it last time."),
        (runbook, "Every service also gets a living runbook, rebuilt from memory after each incident. Nobody on the team wrote this by hand."),
        (rules, "Hard rules are stored as directives, so they apply to every triage, no matter what recall returns."),
        (curve, "And every resolved incident is tracked, so the team can watch time to resolve, and Deja's accuracy, over time."),
        (outro, "The most valuable memory in incident response is what didn't work. Postmortems bury it. Deja brings it back, at two A M."),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="Samantha")
    ap.add_argument("--rate", type=int, default=178)
    ap.add_argument("--out", default=str(ROOT / "video" / "deja-demo.mp4"))
    args = ap.parse_args()

    status = api("/api/status")
    hindsight = status["memory_backend"] == "hindsight"
    print(f"backend={status['memory_backend']} llm={status['llm']}")

    print("resetting + seeding memory...")
    api("/api/demo/reset", {})
    api("/api/demo/seed", {})
    if hindsight:  # seeding is async on Hindsight; wait for fact extraction before recording
        for _ in range(90):
            n = api("/api/stats").get("memory_count") or 0
            print(f"  memories: {n}")
            if n >= 24:
                break
            time.sleep(5)

    work = Path(tempfile.mkdtemp(prefix="deja-video-"))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1,
                                  record_video_dir=str(work), record_video_size={"width": 1920, "height": 1080},
                                  color_scheme="dark")
        ctx.add_init_script(OVERLAY_JS)
        page = ctx.new_page()
        t0 = time.monotonic()
        page.goto(BASE)
        page.wait_for_selector(".scenario")
        page.wait_for_function("document.getElementById('vc-card')")
        d = Director(page)

        timeline: list[tuple[float, Path]] = []
        for i, (action, line) in enumerate(scenes(d, hindsight)):
            clip = work / f"s{i:02d}.aiff"
            dur = tts(line, args.voice, args.rate, clip)
            start = time.monotonic() - t0
            timeline.append((start, clip))
            print(f"[{start:6.1f}s] scene {i}: {line[:60]}...")
            d.caption(line)
            action()
            remaining = dur + 0.5 - (time.monotonic() - t0 - start)
            if remaining > 0:
                time.sleep(remaining)
        d.caption(None)
        time.sleep(2.5)
        total = time.monotonic() - t0
        page.close()
        webm = Path(page.video.path())
        ctx.close()
        browser.close()

    print("mixing audio...")
    inputs, filters = [], []
    for i, (start, clip) in enumerate(timeline):
        inputs += ["-i", str(clip)]
        ms = int(start * 1000)
        filters.append(f"[{i + 1}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(len(timeline)))
    filters.append(f"{mix}amix=inputs={len(timeline)}:normalize=0,apad[aout]")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), *inputs,
        "-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
        "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-r", "30", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out),
    ], check=True)
    shutil.rmtree(work, ignore_errors=True)
    print(f"done -> {out}  ({total:.0f}s)")


if __name__ == "__main__":
    main()

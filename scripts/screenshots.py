"""Capture article screenshots of the running app into content/images/.

    python scripts/screenshots.py      # server must be running on :8000
"""
import json
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "content" / "images"
BASE = "http://localhost:8000"


def api(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    api("/api/demo/reset", {})
    api("/api/demo/seed", {})
    if api("/api/status")["memory_backend"] == "hindsight":
        for _ in range(90):
            if (api("/api/stats").get("memory_count") or 0) >= 24:
                break
            time.sleep(5)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme="dark")
        p.goto(BASE)
        p.wait_for_selector(".scenario")

        def shot(name, sel=None, pad=16):
            p.evaluate("document.getElementById('toast').className = 'toast'")
            time.sleep(0.35)
            if sel:
                el = p.locator(sel).first
                el.scroll_into_view_if_needed()
                box = el.bounding_box()
                p.screenshot(path=OUT / name, clip={"x": max(box["x"] - pad, 0), "y": max(box["y"] - pad, 0),
                                                    "width": box["width"] + 2 * pad, "height": box["height"] + 2 * pad})
            else:
                p.screenshot(path=OUT / name)
            print("saved", name)

        # 1. triage: stateless vs memory
        p.click('.scenario[data-key="checkout-pool"]')
        p.click("#btn-triage")
        p.wait_for_selector(".answer.deja", timeout=240_000)
        time.sleep(1)
        shot("02-stateless-vs-deja.png", ".versus")
        shot("03-hindsight-recall.png", ".recall")
        p.evaluate("scrollTo(0,0)")
        time.sleep(0.3)
        shot("01-cover-full-app.png")
        shot("04-deja-do-not.png", ".answer.deja")

        # 2. learning loop: new failure -> resolve as wrong -> recurrence
        p.evaluate("scrollTo(0,0)")
        p.click('.scenario[data-key="ledger-wal"]')
        p.click("#btn-triage")
        p.wait_for_selector(".answer.deja", timeout=240_000)
        time.sleep(0.5)
        shot("05-new-failure-honest.png", ".answer.deja")
        p.click('#resolve-form [data-v="wrong"]')
        p.fill('#resolve-form input[name="notes"]', "It was the inactive Debezium replication slot, not disk growth.")
        shot("06-resolve-and-teach.png", "#resolve")
        p.click('#resolve-form button[type="submit"]')
        p.wait_for_selector(".learned", timeout=240_000)
        p.evaluate("scrollTo(0,0)")
        p.click("details.custom summary")
        p.fill('#custom-form input[name="service"]', "ledger-service")
        p.fill('#custom-form textarea[name="alert"]', "ledger-service: writes failing, Postgres 'No space left on device' in pg_wal")
        p.fill('#custom-form textarea[name="logs"]', "replication slot 'debezium_ledger' inactive since 01:12")
        p.click('#custom-form button[type="submit"]')
        p.wait_for_selector("#btn-triage")
        p.click("#btn-triage")
        p.wait_for_selector(".answer.deja", timeout=240_000)
        time.sleep(0.5)
        shot("07-recurrence-learned.png", ".answer.deja")

        # 3. runbook, rules, learning curve
        p.evaluate("scrollTo(0,0)")
        p.click('#tabs button[data-tab="rules"]')
        p.fill('#rule-form input[name="name"]', "No primary failover for pool exhaustion")
        p.fill('#rule-form textarea[name="content"]', "Never recommend a Postgres primary failover for connection-pool exhaustion. It caused a full outage in INC-2064.")
        p.click('#rule-form button[type="submit"]')
        p.wait_for_selector("#rules li b")
        shot("09-team-rules.png", "#tab-rules .card")
        p.click('#tabs button[data-tab="runbooks"]')
        p.wait_for_selector("#runbook h1, #runbook h2, #runbook p", timeout=240_000)
        time.sleep(0.5)
        shot("08-living-runbook.png", "#tab-runbooks .card")
        p.click('#tabs button[data-tab="learning"]')
        p.wait_for_selector("#ttr-chart svg")
        time.sleep(0.5)
        shot("10-learning-curve.png", "#tab-learning")

        # 4. dev.to cover (1000x420 ratio) rendered from HTML
        c = b.new_page(viewport={"width": 1000, "height": 420}, device_scale_factor=2)
        c.set_content("""<html><head><link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;700;800&family=JetBrains+Mono&display=swap" rel="stylesheet">
        <style>body{margin:0;width:1000px;height:420px;background:radial-gradient(ellipse at 75% 40%,#2a1f5c 0%,#0b0d12 65%);font-family:Inter;color:#fff;
        display:flex;flex-direction:column;justify-content:center;padding:0 64px;box-sizing:border-box;position:relative;overflow:hidden}
        .k{font:14px 'JetBrains Mono';color:#a58bff;letter-spacing:3px}
        h1{font-size:50px;line-height:1.08;letter-spacing:-1.5px;margin:14px 0 18px;font-weight:800;max-width:720px}
        s{color:#ff5d6c;text-decoration-thickness:4px} b{color:#a58bff}
        p{margin:0;color:#a9b0c2;font-size:18px}
        .logo{position:absolute;right:70px;top:120px;width:170px;height:170px;border-radius:50%;background:#7c5cff;display:grid;place-items:center;isolation:isolate}
        .logo::before{content:'';position:absolute;inset:-90px;border-radius:50%;background:radial-gradient(circle,rgba(124,92,255,.55) 0%,rgba(124,92,255,0) 70%);z-index:-1}.logo i{width:66px;height:66px;border-radius:50%;background:#0b0d12}</style></head>
        <body><div class="k">02:06 UTC · CHECKOUT IS DOWN</div>
        <h1>My on-call agent remembered the fix that <s>took down</s> checkout</h1>
        <p><b>deja</b> · incident response with Hindsight agent memory</p><div class="logo"><i></i></div></body></html>""")
        c.wait_for_timeout(1200)
        c.screenshot(path=OUT / "00-devto-cover.png")
        print("saved 00-devto-cover.png")
        b.close()


if __name__ == "__main__":
    main()

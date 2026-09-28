# LinkedIn post (under 800 characters)

Your AI on-call assistant will confidently recommend the exact fix that took prod down last month.

It's not dumb. It just has no memory.

I built Deja, an incident agent on Hindsight agent memory. Same LLM, same alert, two answers:

Without memory: "Increase the pool size. Consider a DB failover."
With memory: "Seen twice before. It's the 02:00 bulk-export cron. Do NOT fail over (full outage in May). Page Priya."

What made the difference:
- Retain what FAILED, not just what worked
- Engineers grade every triage, and the grade is stored
- Hard rules as directives, not memories
- Runbooks rebuilt from memory after each incident

Repo: [YOUR REPO URL]

#AIAgents #AgentMemory #Hindsight #SRE #LLM

> First comment: Here's a link to Hindsight if you want to check it out: https://github.com/vectorize-io/hindsight
> Also post the article URL as a comment. Tag Code.in.

---

# Video script (~3 min, screen recording + voiceover)

**Titles (pick one):**
1. My AI on-call agent remembered the fix that broke prod
2. Same LLM, same alert: memory vs no memory
3. I gave an incident bot a memory. Here's what changed
4. This agent says "don't do that, it failed in May"
5. Building an SRE agent that learns from every postmortem

**0:00–0:30 · Intro** (face cam + app home screen)
"Hi, I'm [NAME]. This is Deja, an on-call agent that remembers every incident your team has had, using Hindsight agent memory. Here's why that matters."

**0:30–1:00 · The problem** (click *Load 6 months of history*, then *Checkout latency at 02:06 UTC*, then *Triage*; point at the LEFT card)
"Checkout is timing out at 2am. The stateless agent, which is the same LLM with no memory, says increase the pool and consider a failover. This team tried both. The pool change made it worse, and the failover took checkout down."

**1:00–2:30 · The demo** (point at the Hindsight recall strip, then the RIGHT card)
- "This strip is Hindsight recall: the actual memories retrieved for this alert."
- "Deja matches INC-2031 and INC-2064, names the bulk-export cron, and puts the failover under DO NOT, with the incident as evidence. It also says to page Priya."
- Fire *Brand-new failure: ledger WAL disk* → Triage. "Nothing in memory. Deja says so and keeps its confidence low."
- Resolve: mark **Wrong**, type a correction, click *Resolve & retain*. "That postmortem and my correction just went into Hindsight."
- Custom alert: `ledger-service`, "No space left on device in pg_wal", logs "replication slot inactive" → Triage. "Now it knows: the Debezium slot, don't bother expanding the PVC, page Sara."
- Quick tour: *Living runbooks* ("a Hindsight mental model, nobody wrote this"), *Team rules* (add the failover rule), *Learning curve*.

**2:30–3:00 · Takeaway** (face cam)
"What surprised me: the most valuable memory is what *didn't* work. Postmortems bury it, and Deja brings it up at 2am."

**Thumbnail prompt (Nano Banana, attach a team photo):**
"Generate a viral thumbnail for this YouTube video. Make it attention grabbing and something people scrolling would want to click on. Aspect ratio 16:9. Split screen: left a grey robot saying 'Fail over the DB!', right a glowing purple robot saying 'NO. That broke prod in May.' Big text: 'MY AI REMEMBERED'. Include the person from the attached photo looking shocked. Here is the video script: [paste script]"

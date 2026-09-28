# My On-Call Agent Remembered the Fix That Took Down Checkout

At 02:06 UTC checkout-api started timing out. I pasted the alert into a capable LLM and asked what to do. It gave me a sensible, textbook answer: raise the connection pool size, rolling-restart the pods, and if that fails, consider a database failover.

Every step of that answer had already been tried on this team. Raising the pool in April made things worse, because Postgres hit `max_connections`. The failover in May turned a SEV2 into a four-minute full checkout outage. The model wasn't wrong in general. It simply didn't know our history, and in incident response, knowing the history is most of the job.

So I built **Deja**, an incident-response agent that remembers. It retains every postmortem, every fix that failed, and every time an engineer told it "you were wrong." When the pager fires it recalls what happened last time before it says anything. This post covers how it works, where [Hindsight agent memory](https://github.com/vectorize-io/hindsight) fits in, and what I got wrong along the way.

## What Deja does

The loop is small on purpose:

1. **An alert fires** (service, alert text, a few log lines).
2. **Deja recalls** similar incidents from a Hindsight memory bank and loads the team's hard rules.
3. **It answers twice.** The same LLM triages the alert once with memory and once without. The UI shows them side by side.
4. **An engineer resolves the incident** and grades Deja's triage: spot on, partly right, or wrong.
5. **Deja retains** the postmortem and the grade. The next similar alert benefits.

The side-by-side view is the whole argument for memory. On the 02:06 checkout alert the stateless agent says "increase the pool." Deja says:

> *Seen before: INC-2031 (Apr 8) and INC-2064 (May 14). The nightly `bulk-export` cron is hitting the Postgres primary. Kill the job. **Do NOT** raise the pool size (made it worse in INC-2031). **Do NOT** fail over the primary (full outage in INC-2064). Page Priya Raman.*

Both answers come from the same model on the same alert. The only difference is memory.

## Why I didn't just use RAG over postmortems

The obvious approach is to embed the postmortem docs and do vector search. That breaks down quickly in on-call work:

- **Alerts and postmortems use different words.** The alert says `HikariPool-1 - Connection is not available`. The postmortem says "bulk-export starved checkout." Pure semantic search misses the exact error string, and pure keyword search misses the paraphrase.
- **Time matters.** "Starts at 02:06" is a strong signal when the culprit is a 02:00 cron.
- **Entities matter across services.** A CoreDNS problem that hit payments last month is relevant to search-indexer today.
- **Some knowledge is a rule, not a fact.** "Never fail over the primary for pool exhaustion" should always apply, not only when retrieval happens to rank it highly.

[Hindsight](https://hindsight.vectorize.io/) covers each of these with a separate primitive, so I didn't have to build that layer myself. Recall runs semantic, BM25, graph and temporal retrieval in parallel and reranks the fused results. Hard rules are **directives**. Summaries that should stay current are **mental models**.

## The memory bank is configured for SRE work

The bank is created once at startup. `create_bank` is an upsert, so it runs on every boot:

```python
self.client.create_bank(
    bank_id=self.bank,
    name="Deja on-call memory",
    mission=BANK_MISSION,
    retain_mission=(
        "Extract operational facts: service names, symptoms and error signatures, root causes, "
        "remediation steps that worked, remediation steps that failed or made things worse, "
        "people involved, time-to-resolve, recurring schedules (cron jobs, deploys)..."
    ),
    reflect_mission=REFLECT_MISSION,
    disposition_skepticism=4,
    disposition_literalism=4,
    disposition_empathy=2,
)
```

The retain mission mattered more than I expected. Without it, fact extraction kept the narrative and dropped the one sentence that matters most at 2am: *what did NOT work*. With it, "failover caused a full outage" becomes a first-class fact that recall can surface.

## Every resolution becomes memory

When an engineer closes an incident, Deja retains a postmortem with its real timestamp, tags, and structured metadata. If the engineer graded the triage, Deja also retains a separate feedback memory:

```python
items = [{
    "content": postmortem_text(record),
    "context": "incident postmortem",
    "timestamp": inc["started_at"],
    "document_id": inc["id"],
    "metadata": postmortem_metadata(record),   # root_cause, fix, failed, resolved_by, ttr...
    "tags": [f"service:{svc}", f"severity:{sev}", "type:postmortem"],
}]
if verdict:
    items.append({
        "content": f"Engineer feedback on Deja's triage of {inc_id}: verdict={verdict}. "
                   f"Deja predicted: {predicted}. Actual root cause: {actual}. Correction: {notes}",
        "context": "engineer feedback",
        ...
    })
self.memory.retain_many(items)
self.memory.refresh_runbook(svc)
```

The feedback memory is what makes this learning rather than just search. When Deja confidently blames the database and the real cause was an inactive Debezium replication slot, that correction is retained. The next time a similar alert fires, recall surfaces both the incident and the fact that Deja got it wrong last time.

## Triage: recall, then reason, with a baseline for honesty

```python
memories = self.memory.recall(alert_query(inc))          # Hindsight recall
rules    = self.memory.directives()                        # hard team rules
deja     = llm.json(DEJA_SYSTEM, rules + memories + incident)
baseline = llm.json(BASELINE_SYSTEM, incident)             # same model, no memory
```

The system prompt has a few strict requirements: team-specific claims must cite an incident ID, anything a memory marks as failed **must** appear in `do_not`, and if nothing relevant was recalled the agent must say so and cap its confidence at 35%. That last rule is what makes the first occurrence of a new failure mode honest. Deja says "I've never seen this," gives general advice, and makes no attempt to pattern-match its way into a confident wrong answer.

## Runbooks nobody has to write

Each service gets a **mental model** in Hindsight. Deja creates it with a source query ("known failure modes, the fix that worked, actions that did NOT work, who to page") and a trigger to refresh after consolidation:

```python
self.client.create_mental_model(
    bank_id=self.bank, id=f"runbook-{service}", name=f"Runbook: {service}",
    source_query=f"Write the operational runbook for {service}: known failure modes ...",
    tags=[f"service:{service}"],
    trigger={"refresh_after_consolidation": True},
)
```

After every resolved incident the runbook is rebuilt from memory. Nobody schedules a "runbook cleanup sprint" anymore, because the runbook is a side effect of doing on-call.

## Things that were painful

- **Open-weight models and JSON.** Running `gpt-oss-120b` on Groq is fast, but I still got fenced JSON, `<think>` blocks, and the occasional `json_validate_failed`. The LLM wrapper now tries JSON mode, then plain mode, then a fallback model (`qwen3-32b`), extracts the outermost object, and returns `None` rather than raising. A normaliser coerces whatever comes back into one shape the UI trusts.
- **Retain is not instant.** Fact extraction and consolidation take time. For seeding history I use async retain and let the UI show the memory count climbing. For a live resolution I retain synchronously, because the demo is "resolve it, fire it again, watch Deja know."
- **Rules don't belong in retrieval.** Stored as an ordinary memory, "never fail over the primary" only shows up when recall happens to rank it highly. As a Hindsight directive it applies on every triage, no matter what recall returns.

## Lessons

1. **Store what failed, explicitly.** A postmortem's most valuable line is the one listing what didn't work. Tell the memory layer to extract it.
2. **Show the counterfactual.** A side-by-side against the same model without memory is the most convincing evidence I've found.
3. **Make the agent admit ignorance.** Cap confidence when recall comes back empty. A wrong answer delivered confidently at 2am costs more than no answer.
4. **Close the loop with feedback.** Grading the agent takes one click and turns every incident into training data, with no fine-tuning involved.
5. **Separate facts from rules.** Facts go through recall. Non-negotiables are directives.

The code is on GitHub: **[REPLACE WITH YOUR REPO URL]**. If you run on-call for anything, try pointing your last ten postmortems at it and see what it remembers. Read more about [what agent memory is](https://vectorize.io/what-is-agent-memory) and the [Hindsight docs](https://hindsight.vectorize.io/).

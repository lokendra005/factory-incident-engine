# The five opening questions

The questions almost every conversation starts with. Spoken-style answers —
say them in your own words, don't recite. Chain them for a pitch, or give one
at a time.

## "What does it do?"
> "You give it a machine and a time window, and it reconstructs the incident —
> a timeline of what happened, the probable root cause, the evidence for it, a
> confidence level, and recommended actions. But the diagnosis is the visible
> tip. Underneath, it ingests messy plant telemetry, checks whether the data is
> even trustworthy enough to act on, evaluates whether its own answers are right,
> and can prove a change is safe before it ships."

## "Why is it useful?"
> "The first hours after a machine trips are chaos — telemetry, error logs,
> maintenance tickets, operator notes scattered across systems, and someone
> burning time piecing together what happened. This does that reconstruction in
> seconds, grounded in the actual records. And it's built so you can *trust* it in
> a factory, where a confident wrong answer means fixing the wrong thing. It's the
> difference between a clever demo and something you'd actually deploy."

## "What problem does it solve?"
> "Two problems. The obvious one: slow, manual incident investigation on the
> floor. The deeper one — the one I built for — is that deploying an AI agent on
> messy real-world data is hard to do *safely*. The data is duplicated,
> out-of-order, sometimes missing; the model will sometimes be wrong; and you
> need to ship improvements without breaking what worked. Most 'AI agent' projects
> skip all of that. This solves the trust-and-safety engineering around the agent,
> not just the agent."

## "On what principle does it work?"
> "Two principles. First, *nothing is trusted that shouldn't be* — bad records get
> dead-lettered instead of crashing the pipeline or silently corrupting results,
> and if a window is too sparse, a reliability gate makes the agent abstain rather
> than guess. Second, *nothing ships that I can't prove is safe* — the reasoning
> engine is a pure function of its evidence, so every run is a replayable trace.
> When I change the model I replay it against the exact same past inputs and get a
> diff: what got fixed, what regressed, ship or hold. Under the hood it's the
> classic chain — messy data → ingestion → store → retrieval → agent → evaluation
> → replay — with a hard rule that each stage is honest about what it doesn't
> know."

## "How did you get this idea?"
> "I started from the job, not the tech. The hard part of a Forward Deployed
> Engineer role isn't building an agent — anyone can wire up an LLM now — it's
> making one trustworthy on messy real plant data. So I reverse-engineered the
> smallest end-to-end system that forces me to solve exactly those problems, and
> incident reconstruction was the concrete task that touches every link in the
> chain. I even shipped a real bug on purpose so I could show evaluation catching
> it and replay proving the fix — because in production you *will* be wrong, and
> the system has to make that observable and recoverable."

---

## The 60-second chained version ("tell me about your project")
> "It reconstructs manufacturing incidents from messy plant telemetry — timeline,
> root cause, evidence, recommended actions. But the point isn't the diagnosis;
> it's the engineering that makes an agent safe to deploy: ingestion that survives
> corrupt, out-of-order data without crashing; a data-quality gate that makes the
> agent abstain when the data can't be trusted; an evaluation harness that measures
> whether it's right; and deterministic replay so I can prove a change fixes a bug
> without introducing regressions. It ships with a real documented bug and the
> machinery that catches it — evaluation flags it, a fix is made, replay shows 62%
> to 100% accuracy, six fixed, zero regressed, ship. It runs offline in one
> command, and the reasoning backend — rules, a trained classifier, or an LLM like
> Grok — is a one-flag swap, all scored by the same harness. I built it because the
> FDE job is exactly that: take messy real-world data, stand up an agent, and make
> it trustworthy and improvable in production."

## Delivery tips
- The one-liner if they want it short: *"It's the engineering that lets you deploy
  an AI that diagnoses machines and sleep at night."*
- **Volunteer** that the data is simulated (for reproducible ground truth) *before*
  they ask — then point to the real Azure PdM training at 0.90 macro-F1 as proof
  it isn't just toy data.
- If they go technical, pivot to `the-four-questions.md` (show a failure / the
  eval / the metrics / a decision). If they go "why you," use `origin-story.md`.

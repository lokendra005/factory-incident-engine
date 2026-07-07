# "Why did this idea come to you?"

A ready, honest answer for when they ask where the project came from. Don't
recite it — internalize the three beats and say them in your own words.

## The short version (say this)
> "I looked at what a Forward Deployed Engineer at a manufacturing-AI company
> actually does day to day, and realized the hard part isn't building an agent —
> anyone can wire up an LLM now. The hard part is making it *trustworthy* on
> messy real plant data: ingestion that survives corruption, knowing when the
> data is too poor to act on, measuring whether the agent is right, and proving a
> change is safe before it ships. So I built the smallest end-to-end system that
> forces me to solve exactly those problems — incident reconstruction was just
> the concrete task that let me show the whole chain."

## The three beats behind it

**1. I started from the job, not the tech.** The role is about dropping into a
factory's messy data and standing up something that works and is safe. So I
asked: what would I have to prove I can do? Not "can you call an LLM," but "can
you build the ingestion, evaluation, and release-safety scaffolding around one."
The project is reverse-engineered from that.

**2. I picked the task that exercises the *whole* chain.** Incident
reconstruction is ideal because it touches every link: messy multi-source
telemetry → ingestion → normalized store → retrieval → agent reasoning →
evaluation → observability. A chatbot or a single model wouldn't force me to
build the unglamorous parts (DLQ, checkpoints, the data-quality gate, replay) —
and those are exactly what separate "a demo" from "deployable."

**3. I made the failure mode the centerpiece on purpose.** I deliberately shipped
a real bug — an engine that blames every temperature rise on cooling — and the
machinery that catches it: evaluation flags it, a fix is made, and replay proves
it fixed the bug with zero regressions. That mirrors the actual FDE loop: you
*will* be wrong in production, so the system has to make being-wrong observable
and recoverable. I wanted to demonstrate that judgment, not just a green
dashboard.

## If they push: "why manufacturing specifically / why not something you know?"
> "Two reasons. First, it's the domain of the work, so I wanted to speak its
> language — MES, telemetry, failure modes — rather than force a generic demo.
> Second, manufacturing data is a great forcing function for the engineering I
> care about: it's genuinely messy and out-of-order, and a wrong automated action
> has physical cost, which is why the abstain-when-uncertain gate mattered enough
> to build. I leaned on my backend/data background for the pipeline and treated
> the domain modeling as something to get right by research — the simulator
> encodes real failure physics (cooling vs sensor vs overload)."

## If they push: "isn't the data synthetic — so is this real?"
> "The data is simulated so I have ground-truth labels and it's reproducible in
> one command — that's a feature for a portfolio piece. But the ingestion, gate,
> eval, and replay machinery is real and domain-agnostic, and I proved the
> training pipeline on real benchmarks (AI4I 2020 and Microsoft's Azure PdM —
> 0.90 macro-F1). On a real deployment I'd swap the simulator for OPC-UA/MQTT
> connectors and retrain on the client's labeled history; nothing above the
> ingestion layer changes."

## What NOT to say
- Don't claim it was months of production work — it was a focused build.
- Don't oversell the ML (it ties the rule engine on clean synthetic data).
- Don't pretend the agent is novel — the *scaffolding and the safety loop* are
  the point, and that's the honest, stronger claim.

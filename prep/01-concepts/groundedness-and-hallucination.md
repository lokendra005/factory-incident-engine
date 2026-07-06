# Groundedness & Hallucination (why "cited_ids ⊆ bundle ids" is the whole game)

## What it is

A **hallucination** is when a model produces a confident, fluent claim that isn't
supported by the source data — an invented fact, a fabricated citation, a
plausible-sounding number that never existed. It's the defining failure mode of
LLMs: they're trained to produce *likely-sounding* text, and likely-sounding is
not the same as *true*.

**Groundedness** (a.k.a. *faithfulness* or *attribution*) is the opposite
property: every claim in the output can be traced to a specific piece of retrieved
evidence. A **citation** is the concrete link — the id of the record that backs a
claim. A grounded system is one where you can click any statement and land on the
real data behind it.

The critical distinction to hold in your head:

- **Groundedness** = "is this claim backed by real, retrieved evidence?"
- **Correctness** = "is the conclusion actually right?"

These are *independent*. An answer can be grounded but wrong (cites real data,
draws the wrong conclusion) or ungrounded but accidentally right. The dangerous
quadrant is **confidently wrong** — and grounding is how you make wrongness at
least *auditable*.

## Why it matters

In any domain where an AI answer triggers an action — dispatching a repair crew,
approving a claim, editing a record — an ungrounded answer is a liability. An FDE
building over a customer's data must be able to say: "the system physically
cannot cite something that isn't in your data." That's a hard, checkable
guarantee, and it's far more valuable than "the model is usually right." The
interview will test whether you can *design and enforce* grounding, not just
define it.

## How THIS project uses it

Grounding here is not a prompt suggestion — it's a **structural invariant**
enforced in code: **every cited id must resolve to a real record in the
`EvidenceBundle`.** Stated as set algebra: `report.cited_ids() ⊆ bundle_ids`.

### Where citations come from

`IncidentReport.cited_ids()` in `fie/models.py` is the definition of "what this
report claims to rely on":

```python
def cited_ids(self) -> set[str]:
    ids = {e.id for e in self.supporting_evidence}
    for t in self.timeline:
        ids.update(t.evidence_ids)
    return ids
```

So citations come from two places: the `supporting_evidence` list and every
`evidence_ids` list on the timeline entries.

### Enforcement at generation time (the guard)

In the LLM path (`fie/agent/llm.py:LLMEngine.reconstruct`), the model is *told*
to cite only provided ids (the `_SYSTEM` prompt: "Cite only the provided evidence
ids"), but the project does not trust the prompt. After parsing the model's JSON
it filters:

```python
valid = set(candidate_ids)
cited = [i for i in data.get("supporting_evidence_ids", []) if i in valid]
```

Any id the model invented is silently dropped before it ever reaches the report.
The docstring says it plainly: "a cited id that isn't in the bundle is dropped —
the model cannot invent evidence." That's the belt-and-suspenders pattern:
instruct in the prompt, *enforce* in code.

For the rule and ML engines, grounding is structural by construction:
`RuleBasedEngine._ev` (in `engine.py`) builds `Evidence` objects *only* by
looking ids up in `bundle.readings/maintenance/mes` — an id that doesn't exist
simply produces no evidence. There's no path to a fabricated citation.

### Enforcement at evaluation time (the judge)

`fie/eval/evaluators.py:groundedness` independently verifies the invariant:

```python
valid = ({r.id for r in bundle.readings}
         | {m.id for m in bundle.maintenance}
         | {e.id for e in bundle.mes})
cited = report.cited_ids()
if not cited:
    return 1.0 if report.root_cause_category in ("no_incident", "unknown") else 0.0
resolved = sum(1 for c in cited if c in valid)
resolve_frac = resolved / len(cited)
```

Then it blends `resolve_frac` 50/50 with **key-signal coverage** (did the cited
telemetry ids actually cover the signals the label says matter). Two nuances
worth citing:

- **No citations is only OK when there's nothing to cite** — `no_incident` /
  `unknown` return 1.0; any other category with zero citations returns 0.0. You
  don't get to make a positive diagnosis with no evidence.
- **Blending with key-signal coverage** means it's not enough to cite *some* real
  id; you must cite the *right kind* of evidence for the claim.

The pass gate (`harness.py`) requires `groundedness >= 0.75`, so a poorly-grounded
answer fails the case even if the category happens to match.

### Why a wrong-but-grounded answer is the realistic case

This is the most important point in the whole project, and it's the thing to lead
with. The shipped bug — `rule-based/1.1.0` (`engine.py:_classify`, v1.1 branch) —
blames *any* spindle-temperature rise on cooling degradation without checking
whether coolant flow actually dropped:

```python
if temp_rise:
    return "cooling_degradation"   # never checks coolant
```

On a *sensor fault* (temperature reading climbs while coolant and load stay
nominal) this is **wrong**. But it is **fully grounded**: it cites the real
temperature readings that did rise. The eval numbers reflect exactly this:
`rule-based/1.1.0: acc=62%  ground=1.00` (`docs/evaluation.md`). It cites real
evidence and reasons incorrectly from it.

That's what makes it a *realistic* production bug rather than a toy one. Real
model failures rarely look like "made up a fact"; they look like "took real data
and drew a plausible-but-wrong conclusion." Grounding doesn't prevent that — but
it makes it *catchable* (correctness and groundedness diverge in the report) and
*debuggable* (you can see exactly which real readings it leaned on). The fix,
v1.2.0, requires a corroborating coolant-flow drop before a cooling diagnosis —
i.e. it demands *more/better grounding for the specific claim*.

## Deeper mental model

Grounding is a **set-membership contract** wrapped around the generation step:

```
retrieved_ids  ─(retrieval defines the citable universe)─▶  candidate_ids
model/engine   ─(proposes citations)──────────────────────▶  proposed_ids
enforce        ─(cited = proposed ∩ candidate_ids)────────▶  cited_ids ⊆ retrieved_ids
```

Because retrieval defines what *can* be cited (see `rag-and-retrieval.md`) and the
guard intersects the model's proposal with that set, hallucinated citations are
mathematically impossible in the output — regardless of how badly the model
misbehaves. You've converted "please don't hallucinate" (a hope) into "cited ⊆
retrieved" (an invariant).

Two layers of defense, and understanding *why both* is senior signal:

1. **Generation-time guard** — keeps bad citations out of the artifact.
2. **Eval-time check** — verifies the invariant held and quantifies grounding, so
   a regression that reintroduced ungrounded citations would fail CI.

And the deepest point: **grounding is necessary but not sufficient for
trustworthiness.** It guarantees your citations are real; it does *not* guarantee
your reasoning over them is right. That gap is precisely why the harness measures
correctness *separately* and why the corroboration rule (v1.2.0) exists.
Groundedness makes errors *honest and visible*; correctness metrics + good logic
make them *rare*.

## Common interview questions with strong answers

**Q: How do you stop an LLM from hallucinating citations?**
Don't rely on the prompt. Make retrieval define a set of valid ids, tell the
model to cite only from it, and then *filter the model's output to that set* in
code: `cited = [i for i in proposed if i in valid]` (`fie/agent/llm.py`). Invented
ids never survive. Prompt + hard enforcement, not prompt alone.

**Q: What exactly is your grounding guarantee?**
`cited_ids() ⊆ bundle_ids` — every citation resolves to a real record. It's
enforced at generation (the filter) and verified at eval
(`evaluators.groundedness`). It does *not* guarantee correctness; it guarantees
auditability.

**Q: Can an answer be grounded and still wrong? Show me.**
Yes — that's the whole `v1.1.0` story. It cites real rising-temperature readings
(grounded 1.00) but concludes "cooling degradation" on a sensor fault (wrong).
Grounded, well-cited, and incorrect. `acc=62%, ground=1.00`.

**Q: Why keep groundedness separate from correctness?**
Because the most dangerous and most *common* real failure is confidently-wrong
but well-cited. If you folded grounding into correctness you'd lose the ability to
detect and describe it. Keeping them orthogonal lets the harness say "grounded but
inaccurate," which points straight at faulty reasoning rather than faulty data
access.

**Q: What about the timeline — is that grounded too?**
Yes. `cited_ids()` includes `evidence_ids` from every timeline entry, so timeline
claims are held to the same invariant, and `timeline_accuracy` separately checks
the key events were surfaced.

**Q: Is zero citations ever acceptable?**
Only when there's nothing to cite — `no_incident` or `unknown`
(`groundedness` returns 1.0 there). A positive diagnosis with no citations scores
0.0. You can't assert a root cause on nothing.

**Q: How would you extend grounding to free-text claims, not just ids?**
Add span-level attribution (each sentence links to the record(s) it paraphrases)
and, optionally, an NLI/entailment check or an LLM-judge that verifies each
sentence is *entailed* by its cited evidence. The id-level invariant is the floor;
sentence-level faithfulness is the next rung.

## Resources to learn more

- **"Survey of Hallucination in Natural Language Generation"** (Ji et al., 2022)
  — taxonomy of hallucination types.
- **Anthropic docs: "Reduce hallucinations"** — practical prompt + verification
  patterns, including "cite only provided sources."
- **Ragas "faithfulness" metric** — an operational definition of grounding for
  RAG, close in spirit to this project's `groundedness`.
- **"Attributed Question Answering" / RARR (Google Research)** — attribution and
  post-hoc citation verification.
- **TruthfulQA** (Lin et al., 2021) — a benchmark that surfaces confident
  falsehoods; good for intuition about why fluency ≠ truth.

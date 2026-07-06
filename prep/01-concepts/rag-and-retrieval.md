# RAG & Retrieval (and why THIS project uses structured retrieval, not a vector DB)

## What it is

**Retrieval-Augmented Generation (RAG)** means: before the model answers, you
*retrieve* relevant context from an external store and put it in the prompt, so
the answer is **grounded** in real data rather than the model's parametric
memory. The generic pipeline is:

```
query -> retrieve relevant context -> stuff into prompt -> generate -> (cite sources)
```

The most-hyped implementation is **vector RAG**:

- **Embeddings**: a model maps text (or any object) to a dense vector such that
  semantically similar things land near each other.
- **Vector database** (FAISS, pgvector, Pinecone, Weaviate, Milvus, …): stores
  those vectors and answers "give me the *k* nearest neighbours to this query
  vector" (approximate nearest-neighbour / ANN search).
- **Semantic search**: retrieve by *meaning* similarity instead of keyword match.

But RAG is a *pattern*, not a technology. "Retrieval" can be a SQL query, a
keyword filter, a graph traversal, or an API call. The vector DB is one option,
appropriate for *fuzzy, unstructured, semantic* lookups — and overkill (or
actively harmful) for *exact, structured* lookups.

## Why it matters

RAG is the default answer to "how do I make an LLM useful over the customer's
proprietary data without fine-tuning?" As an FDE you'll be asked to build it
constantly. The senior signal in an interview is *not* "I reach for a vector DB";
it's knowing **when retrieval should be structured vs semantic**, and being able
to defend choosing the simpler tool. This project deliberately does the
unfashionable-but-correct thing, and you should be able to explain exactly why.

## How THIS project uses it (structured retrieval over SQL)

This system is 100% RAG in spirit — **every answer is grounded in retrieved
records and cites them** — but it uses **structured retrieval**, not vector
search.

### The retrieval layer

Retrieval happens against a normalized SQLite store (`fie/store.py`,
Postgres-compatible DDL). `fie/agent/reconstruct.py:reconstruct_from_store`
assembles the `EvidenceBundle` with plain, bounded queries:

```python
bundle = EvidenceBundle(
    readings   = store.query_readings(asset, window_start, window_end),
    maintenance= store.query_maintenance(asset, since, window_end),
    mes        = store.query_mes(asset, window_start, window_end),
    past_incidents = store.prior_incidents(asset, window_start),
)
```

That's the "retrieve" step: select the telemetry, maintenance, MES events, and
prior incidents for a specific `(asset, time window)`. The retrieval key is
**structured** (asset + timestamp range), because the question is structured
("what happened to CNC-18 between 14:00 and 15:00?").

### The tool layer as fine-grained retrieval

Inside the engine, the `Toolbox` (`fie/agent/tools.py`) does a second,
tool-shaped retrieval over the bundle:

- `query_telemetry(signal)` — filter readings to one signal, compute stats,
  return the ids that best evidence the signature.
- `search_maintenance(keyword)` — a **keyword** search over maintenance records
  (`kw in (component + note + kind)`), the classic lexical retrieval.
- `find_similar_incidents(category)` — retrieve prior incidents with the same
  `root_cause_category`. This is a "find similar" step — the exact job vector
  search usually claims — but done by **exact categorical match**, which is
  precise and explainable.

### Grounding is enforced, not hoped for

The retrieved ids become the *only* things the answer may cite. In the LLM path
(`fie/agent/llm.py`), `candidate_evidence_ids` is the retrieval result and
`cited = [i for i in supporting_evidence_ids if i in valid]` drops anything
outside it. In the rule/ML paths, supporting evidence is built directly from
retrieved records via `RuleBasedEngine._ev`. So retrieval and grounding are the
same list of ids — retrieval *defines* what can be said. (More on this in
`groundedness-and-hallucination.md`.)

## Why NOT vector RAG here (the honest answer)

Lead with this framing: **vector search is the right tool when the retrieval key
is fuzzy and the data is unstructured text. Neither is true here.**

1. **The data is structured and numeric.** Telemetry is `(machine, ts, signal,
   value)`; maintenance and MES are typed records. The right index for "readings
   for asset X in window W" is a B-tree on `(machine, ts)`, i.e. plain SQL — not
   an embedding. Embedding a float time series to do nearest-neighbour on it
   would be strictly worse than a range scan.

2. **The retrieval key is exact, not semantic.** You want *all* readings in a
   window, deterministically — not the "top-k most similar." Recall must be
   100% and reproducible; ANN gives you approximate, ranked recall, which is the
   wrong guarantee for evidence you're going to cite.

3. **Determinism.** The whole project's value proposition is deterministic
   replay and reproducible eval (see `replay-and-determinism.md`). ANN indexes,
   embedding-model versions, and similarity thresholds are all sources of
   nondeterminism and silent drift. A SQL range query returns the same rows every
   time.

4. **Explainability / auditability.** "I selected these rows by asset and
   timestamp" is trivially defensible to a plant engineer. "The cosine
   similarity was 0.83" is not. In a safety-adjacent setting, the legible
   retrieval wins.

5. **Cost / ops.** No embedding model to run, no vector index to provision, no
   re-embedding on data changes. The project's constraint is "runs in one
   command, offline" (`docs/architecture.md`), and SQLite delivers that.

The general rule to state: **don't add semantic search until the query is
semantic.** Reaching for a vector DB on structured data is a common
over-engineering tell.

## When you WOULD add vector search

Be concrete — this shows judgment, not dogma. You'd add embeddings/vector search
to this exact system when a *fuzzy, textual* retrieval need appears:

- **Free-text incident history / maintenance notes at scale.** Today
  `find_similar_incidents` matches on an exact category and `search_maintenance`
  is keyword-based. If you had 50k historical incident write-ups and wanted "find
  past incidents whose *narrative* resembles this one," embeddings on the
  free-text `summary`/`note` fields would beat keyword match (synonyms,
  paraphrase, "coolant pump" vs "cooling circuit").
- **Operator/technician natural-language search.** "Show me cases that looked
  like a slow bearing failure after a lube change" — semantic query over notes.
- **Runbook / manual retrieval.** Pulling the relevant paragraph from thousands
  of pages of equipment manuals to attach to a recommendation.

The clean design move: keep vector search as *another tool on the `Toolbox`*
(e.g. `semantic_search_incidents(text)`), retrieving *ids* just like the existing
tools, so the grounding contract (`cited ⊆ retrieved ids`) and the tracing still
hold. You'd bolt semantic retrieval onto the same architecture rather than
replace it — and you'd keep the exact structured retrieval for the numeric
telemetry, using each where it's strong (hybrid retrieval).

## Deeper mental model

RAG = **retrieve → ground → generate**, and each stage has a "structured" and a
"semantic" flavour:

| Stage | Structured (this project) | Semantic (vector RAG) |
|---|---|---|
| Index | B-tree on `(machine, ts)` | ANN index over embeddings |
| Query | SQL range / equality | k-NN on query embedding |
| Recall | exact, 100%, reproducible | approximate, ranked |
| Best for | typed/numeric/exact keys | fuzzy/textual/semantic keys |
| Failure mode | none if schema is right | wrong chunk, stale embeddings |

The deepest point: **the model quality ceiling is set by retrieval.** If the
right evidence isn't retrieved, no amount of prompting fixes the answer
("garbage in"). Structured retrieval here has *perfect* recall on the numeric
signals the diagnosis depends on, which is why the rule engine can hit 100%
accuracy on the golden set — the evidence is always there to reason over.

## Common interview questions with strong answers

**Q: Is this a RAG system?**
Yes — it retrieves external records and grounds every answer in them with
citations. It just uses *structured* retrieval (SQL + tool queries) instead of
*vector* retrieval, because the retrieval keys are exact (asset + time window),
not semantic.

**Q: Why no vector DB / embeddings?**
The data is structured and numeric, the query is an exact range, and the project
needs deterministic, reproducible, auditable retrieval. Embeddings would add
nondeterminism, ops cost, and approximate recall — all downsides, no upside, for
this data. See `fie/store.py` and `reconstruct_from_store` in `reconstruct.py`.

**Q: When would you switch to vector search?**
When a fuzzy/textual retrieval need appears — e.g. semantic search over free-text
incident narratives or maintenance notes at scale, or runbook retrieval. I'd add
it as another `Toolbox` tool that returns ids, preserving grounding and tracing,
and keep structured retrieval for the numeric telemetry (hybrid).

**Q: What's the difference between keyword and semantic search, and do you use
either?**
Keyword (lexical) matches surface tokens — `search_maintenance` does this.
Semantic matches meaning via embeddings — not used, because there's little free
text to disambiguate yet. Keyword is enough at current scale; semantic earns its
keep once vocabulary variance in notes becomes a recall problem.

**Q: How do you keep RAG from hallucinating?**
Make retrieval define the citable set and *filter* the answer to it: `cited ⊆
retrieved ids` (`fie/agent/llm.py`). The model literally cannot cite a record
that wasn't retrieved.

**Q: What are the failure modes of vector RAG you're avoiding?**
Chunking artifacts, embedding-model version drift (re-embed everything on
upgrade), approximate recall missing the one row that mattered, similarity
thresholds that need per-domain tuning, and nondeterministic results that break
replay/eval.

## Resources to learn more

- **"Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"** (Lewis
  et al., 2020) — the paper that coined RAG.
- **Pinecone Learning Center / "What is a vector database"** — clear intro to
  embeddings and ANN.
- **`pgvector` docs** — vector search *inside* Postgres, the natural upgrade path
  from this project's SQL store if semantic search is ever needed.
- **FAISS (Facebook AI Similarity Search) docs** — the reference ANN library;
  good for understanding the index/recall trade-offs.
- **Anthropic docs: "Contextual Retrieval"** and OpenAI's RAG guides — practical
  grounding patterns, including when *not* to use embeddings.
- **"Building effective agents" (Anthropic)** — reinforces preferring the
  simplest retrieval that solves the problem.

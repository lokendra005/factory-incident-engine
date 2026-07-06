# LLM APIs (messages, tokens, temperature, JSON output, cost) and how THIS project wires them

## What it is

An **LLM API** is a request/response contract for getting text out of a large
language model. You send a list of **messages** (each with a `role` — `system`,
`user`, `assistant` — and `content`) plus generation parameters, and you get back
completion text (and usage counts). The two families you'll meet constantly:

- **Anthropic Messages API** (Claude): `system` is its own top-level field;
  `messages` is the turn history; output is a list of content blocks.
- **OpenAI Chat Completions API** (and the many "OpenAI-compatible" endpoints,
  including **xAI's Grok**): `system` is just the first message in `messages`;
  output is `choices[0].message.content`.

Core concepts you must be fluent in:

- **Tokens**: models read/write in sub-word tokens, not characters. You pay per
  input token and per output token, and every model has a context-window limit
  measured in tokens. Roughly ~4 chars/token for English.
- **System prompt**: the instructions that frame the whole interaction ("you are
  a reliability engineer…"). It sets rules, persona, and output contract.
- **Temperature**: randomness of sampling. `0` = greedy/most-deterministic;
  higher = more varied. For extraction/classification you want `0`.
- **Structured / JSON output**: forcing the model to emit machine-parseable JSON,
  either via a `response_format` flag, tool/function calling, or a strict prompt
  contract + a tolerant parser.
- **Streaming**: tokens delivered incrementally (server-sent events) so a UI can
  render as they arrive. Optional; unrelated to correctness.
- **Cost & latency**: driven by tokens in + tokens out and model tier. Bigger
  models cost more and are slower.

## Why it matters

As an FDE you'll wire these APIs into someone else's stack under real
constraints: keys you may not have, networks you can't rely on, cost budgets,
and a demo that must not fall over. The interview will test whether you can (a)
explain the mechanics, (b) reason about determinism and cost, and (c) design so
the LLM is an *enhancement*, not a *dependency*. This project is a clean example
of the last point: it runs with **no LLM at all**, and an LLM only ever makes it
better, never load-bearing.

## How THIS project uses it

All LLM wiring lives in `fie/agent/llm.py`. There's a shared base class,
`LLMEngine`, and two transports that subclass it.

### The shared contract (provider-agnostic)

`LLMEngine._summarize(tb)` runs the toolbox to build a compact evidence summary
(per-signal baseline/end/delta/max_jump, maintenance, MES events, and a list of
`candidate_evidence_ids`). `LLMEngine.reconstruct`:

1. builds the `user` payload as JSON (asset, reliability, a `_SCHEMA_HINT`, and
   the evidence summary);
2. sends the fixed `_SYSTEM` prompt + that user JSON to `_complete`;
3. parses JSON out of the response with a tolerant slice:
   `data = json.loads(text[text.index("{"):text.rindex("}")+1])`;
4. **grounds** the result: `cited = [i for i in
   data.get("supporting_evidence_ids", []) if i in valid]` where
   `valid = set(candidate_ids)` — any id the model invented is dropped;
5. builds the same `IncidentReport` model every other engine produces.

The `_SYSTEM` prompt (top of `llm.py`) encodes the domain rules directly: "a
cooling diagnosis REQUIRES a corroborating coolant-flow drop; a temperature rise
with nominal coolant and load is a SENSOR FAULT; … Cite only the provided
evidence ids. Respond with ONLY a JSON object matching the schema." That is the
same corroboration logic the fixed rule engine (v1.2.0) implements — the prompt
*is* the spec.

### Claude backend

`ClaudeEngine._complete` (in `llm.py`):

```python
import anthropic
client = anthropic.Anthropic()          # reads ANTHROPIC_API_KEY from env
msg = client.messages.create(
    model=self.model, max_tokens=1024, system=system,
    messages=[{"role": "user", "content": user_json}])
return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
```

Note the Anthropic shape: `system=` is a separate argument; the response
`msg.content` is a list of typed blocks and you concatenate the `text` ones. The
model id defaults to `config.CLAUDE_MODEL` = `claude-opus-4-8`.

### Grok (xAI) backend — OpenAI-compatible REST, no SDK

`GrokEngine._complete` (in `llm.py`) calls the REST endpoint directly with
`httpx` (chosen so there's no extra SDK dependency):

```python
resp = httpx.post(
    f"{config.GROK_BASE_URL}/chat/completions",
    headers={"Authorization": f"Bearer {self._api_key()}", ...},
    json={
        "model": self.model,
        "temperature": 0,                              # determinism-friendly
        "response_format": {"type": "json_object"},    # force JSON
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_json},
        ],
    },
    timeout=30.0,
)
resp.raise_for_status()
return resp.json()["choices"][0]["message"]["content"]
```

This is the canonical OpenAI Chat Completions shape: `system` as the first
message, `temperature`, `response_format`, and the answer at
`choices[0].message.content`. `GROK_BASE_URL` defaults to
`https://api.x.ai/v1` (`fie/config.py`), and the key is read from `XAI_API_KEY`
or `GROK_API_KEY` (`GrokEngine._api_key`).

### Determinism

Both backends aim at reproducibility. Grok explicitly sets `temperature=0`. The
system prompt pins the exact output schema. (Aside worth stating honestly in an
interview: even at `temperature=0`, hosted LLMs are not *bit*-for-bit
deterministic across time — floating-point nondeterminism, batching, and silent
model updates all leak in. That's precisely why the project does **not** depend
on LLM determinism for its guarantees; it depends on the *rule engine's* purity.
The LLM is graded, grounded, and disposable.)

### Graceful fallback — the LLM is never load-bearing

Two layers protect the demo/tests/CI:

1. **Availability checks**: `claude_available()` / `grok_available()` return
   `False` if `config.ENGINE == "rule"`, if the relevant key is missing, or if
   the client library isn't importable. `get_engine` in `fie/agent/engine.py`
   uses these to resolve `auto` → Grok → Claude → rule.
2. **Runtime fallback**: inside `LLMEngine.reconstruct`, the whole
   `_complete` + JSON-parse is wrapped in `try/except`. On *any* failure (missing
   key, network error, bad JSON) it calls `self._fallback.reconstruct(...)` where
   `self._fallback = RuleBasedEngine("1.2.0")`, and tags the engine name
   `"... (fell back to rule-based/1.2.0)"`. The docstring says it plainly:
   "nothing here is ever load-bearing for the demo, the tests, or CI."

### Why the project runs with NO LLM at all

The **default engine is the deterministic `RuleBasedEngine`** (`config.ENGINE`
defaults to `"auto"`, which resolves to rules when no key is present). The rule
engine reproduces the same corroboration logic the LLM prompt describes, calls
the same toolbox, and emits the same `IncidentReport`. So the entire loop —
ingest → reconstruct → evaluate → replay → SHIP/HOLD — works offline, in ~2
seconds, with no keys. The LLM is an *alternative policy behind the same
contract*, not a required component. That's the single most defensible thing you
can say about the design: **the LLM is optional by construction**.

## Deeper mental model

An LLM API call is a pure-ish function `f(system, messages, params) -> text`,
except the function is stochastic, remote, metered, and occasionally down. Good
engineering treats each of those adjectives:

- **Stochastic** → pin `temperature=0`, and don't rely on exact-string outputs;
  parse tolerantly and validate.
- **Remote / occasionally down** → timeouts (`timeout=30.0`) and a deterministic
  fallback path.
- **Metered** → keep prompts small. Note `_summarize` sends *pre-computed
  statistics*, not raw telemetry rows. Sending baseline/end/delta for 6 signals
  is a few hundred tokens; sending thousands of raw readings would be huge and
  costly. Pre-aggregation is a cost lever *and* a grounding lever (the candidate
  ids are curated).
- **Untrusted output** → the grounding guard (`cited ⊆ candidate_ids`) means the
  model can't manufacture evidence even if it hallucinates.

The provider abstraction is worth internalizing: Claude and OpenAI-compatible
APIs differ mainly in *where the system prompt goes* and *how you dig the text
out of the response*. Everything else (roles, tokens, temperature, JSON
contract) transfers. `LLMEngine` captures exactly that: the base class owns the
prompt, the schema, and the grounding; subclasses own only `_complete`
(transport).

## Common interview questions with strong answers

**Q: Walk me through what happens on one LLM reconstruction.**
Build bundle → toolbox computes signal stats → `_summarize` packs stats + candidate
ids into a JSON user message → send with the fixed system prompt → parse JSON out
of the reply → drop any cited id not in `candidate_ids` → assemble
`IncidentReport`. On any exception, fall back to the rule engine. See
`LLMEngine.reconstruct` in `fie/agent/llm.py`.

**Q: Anthropic vs OpenAI-compatible API — what actually differs?**
System prompt placement (Anthropic: top-level `system=`; OpenAI: first message),
response extraction (Anthropic: list of content blocks; OpenAI:
`choices[0].message.content`), and native JSON support (`response_format` on the
OpenAI side; Claude leans on tool-use/prompting). This repo shows both:
`ClaudeEngine._complete` vs `GrokEngine._complete`.

**Q: How do you get reliable JSON out of a model?**
Layered: (1) instruct it in the system prompt ("respond with ONLY a JSON
object"), (2) use a native flag when available (`response_format: json_object`
for Grok), (3) parse defensively — slice from the first `{` to the last `}`
(`text[text.index("{"):text.rindex("}")+1]`) so leading/trailing prose doesn't
break you, (4) validate against your own schema and *discard* anything invalid
(the grounding filter). Never trust the model to be well-formed.

**Q: Why temperature 0?**
For classification/extraction you want the single most-likely, repeatable
answer, and you want eval numbers to mean something run-to-run. High temperature
is for creative diversity, which this task doesn't want.

**Q: How do you control cost?**
Minimize tokens. Here that's done by sending aggregated statistics instead of raw
rows, capping `max_tokens=1024`, and — crucially — running the *free*
deterministic engine by default so most invocations cost nothing. Pick the model
tier to the task; you don't need a frontier model to pick one of eight
categories.

**Q: What if the API is down or the key is missing?**
Nothing breaks. `*_available()` gate selection and the `try/except` in
`reconstruct` falls back to `RuleBasedEngine("1.2.0")`, tagging the engine name
so the provenance shows a fallback happened. CI never touches the network.

**Q: Why not make the LLM the default?**
Because it would make the demo non-reproducible, require a key, cost money, and
introduce network flakiness into CI — for no accuracy gain over the corroboration
rules on this scenario set. The LLM earns its place only when the task exceeds
what rules can express; the architecture lets you flip to it without changing
anything downstream.

## Resources to learn more

- **Anthropic docs: Messages API** — request/response shape, system prompt,
  content blocks, streaming, token counting.
- **OpenAI docs: Chat Completions & "Structured Outputs" / `response_format`** —
  the shape the Grok backend targets.
- **xAI docs (console.x.ai / docs.x.ai)** — confirms the OpenAI-compatible
  endpoint and current Grok model names.
- **OpenAI tokenizer / `tiktoken`** — build intuition for how text maps to
  tokens and therefore to cost.
- **Anthropic "Prompt engineering" guide** — practical patterns for reliable,
  contract-shaped outputs.

"""Evaluation harness.

Runs an engine over a golden set of labeled incidents and scores four things
that matter for deploying an agent in production:
  * correctness   — did it get the root cause right?
  * groundedness  — is every claim backed by evidence that actually exists?
  * timeline      — did it surface the key events?
  * tool usage    — did it actually look at the signals that mattered?

Plus an "appropriate abstention" check: on the insufficient-data case it must
decline rather than guess.
"""
from .golden import build_golden, load_golden          # noqa: F401
from .harness import evaluate, EvalReport, CaseResult  # noqa: F401

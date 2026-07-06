"""Backward-compatible shim. The LLM engines now live in fie/agent/llm.py
(Claude and Grok share a base there). Kept so existing imports keep working."""
from .llm import ClaudeEngine, GrokEngine, claude_available, grok_available  # noqa: F401

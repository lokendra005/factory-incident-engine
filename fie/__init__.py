"""Factory Incident Engine.

Reconstructs manufacturing incidents from messy plant telemetry:

    simulate -> ingest (validate/checkpoint/dedup/DLQ/drift) -> normalized store
             -> reliability gate -> reconstruction agent -> evaluation -> replay

The whole pipeline is deterministic and runs offline. See README.md.
"""

__version__ = "0.8.0"

# The agent version string is stamped into every captured run so replay can
# compare "what the old version produced" against "what a new version produces".
AGENT_VERSION = "rule-based/1.2.0"

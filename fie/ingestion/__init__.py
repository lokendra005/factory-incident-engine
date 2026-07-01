"""Fault-tolerant ingestion.

Turns a messy raw feed into trustworthy canonical records. Guarantees:
  * exactly-once *effect* — crash-safe checkpoints + idempotent upserts
  * nothing silently dropped — every bad record lands in the DLQ with a reason
  * schema drift is detected and logged, never crashes the run
"""
from .pipeline import ingest_all, ingest_file, recover_dlq, IngestStats  # noqa: F401

from fie.models import TelemetryReading


def _r(id, v, ts="2026-07-01T00:00:00+00:00"):
    return TelemetryReading(id=id, machine="CNC-17", ts=ts, signal="spindle_temp_c", value=v)


def test_idempotency_contract(store):
    assert store.upsert_reading(_r("a", 55.0)) == "inserted"
    assert store.upsert_reading(_r("a", 55.0)) == "duplicate"     # exact re-delivery
    assert store.upsert_reading(_r("a", 99.0)) == "conflict"      # same id, new value
    assert store.counts()["telemetry"] == 1                        # first write kept


def test_checkpoint_roundtrip(store):
    assert store.get_checkpoint("f.jsonl") == (0, 0)
    store.set_checkpoint("f.jsonl", 123, 45)
    assert store.get_checkpoint("f.jsonl") == (123, 45)


def test_dlq_recover_cycle(store):
    store.add_dlq("telemetry", '{"x":1}', "malformed_json")
    assert store.dlq_counts() == {"telemetry": 1}
    row = store.dlq_items()[0]
    store.mark_dlq_recovered(row["id"])
    assert store.dlq_counts() == {}

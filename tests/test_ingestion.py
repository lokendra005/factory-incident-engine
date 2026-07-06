import json

from fie.ingestion import ingest_all, ingest_file, recover_dlq
from fie.ingestion.validate import validate_telemetry


def test_full_ingest_survives_mess(store, raw_dir):
    d, manifest = raw_dir
    out = ingest_all(store, raw_dir=d)
    tel = out["telemetry.jsonl"]
    # every injected defect family is caught, nothing crashes the run
    assert tel["inserted"] > 4000
    assert tel["duplicate"] > 0
    for reason in ("out_of_bounds", "malformed_json", "future_timestamp"):
        assert tel["dlq"].get(reason, 0) > 0
    assert store.drift_items(), "schema drift should be logged"


def test_resume_is_idempotent(store, raw_dir):
    d, _ = raw_dir
    ingest_all(store, raw_dir=d)
    before = store.counts()["telemetry"]
    # re-run: checkpoint skips everything already committed
    out = ingest_all(store, raw_dir=d)
    assert out["telemetry.jsonl"]["read"] == 0
    assert store.counts()["telemetry"] == before


def test_crash_midway_no_double_count(store, raw_dir):
    d, _ = raw_dir
    ingest_all(store, raw_dir=d)
    final = store.counts()["telemetry"]
    # simulate a crash: rewind the checkpoint and re-drive
    store.set_checkpoint("telemetry.jsonl", 0, 1500)
    out = ingest_file(store, d / "telemetry.jsonl")
    assert out.inserted == 0            # all re-seen rows are duplicates
    assert out.duplicate > 0
    assert store.counts()["telemetry"] == final


def test_dlq_recovery_after_remap(store, raw_dir):
    d, _ = raw_dir
    ingest_all(store, raw_dir=d)
    before = store.counts()["telemetry"]
    res = recover_dlq(store)                # maps reading_c -> value
    assert res["recovered"] > 0
    after = store.counts()["telemetry"]
    # recovered rows are re-driven idempotently: count rises, but a recovered
    # row whose id already exists is a dedup, so growth <= recovered.
    assert before < after <= before + res["recovered"]


def test_bad_typed_record_dead_letters_not_crashes(store, tmp_path):
    """A valid-JSON record with wrong field types must DLQ, not abort the run."""
    import json as _json
    f = tmp_path / "maintenance.jsonl"
    good = {"kind": "maintenance", "machine": "CNC-17", "ts": "2026-07-01T00:00:00+00:00",
            "kind_of": "inspection", "component": "pump"}
    bad = {"kind": "maintenance", "machine": 999, "ts": "2026-07-01T00:00:00+00:00",
           "kind_of": "inspection", "component": {"nested": "obj"}}
    f.write_text("\n".join(_json.dumps(r) for r in [good, bad, good]) + "\n")
    st = ingest_file(store, f)                 # must not raise
    assert st.inserted == 1                    # the two 'good' lines share one id
    assert st.dlq_total == 1                   # the bad line is dead-lettered
    assert store.counts()["maintenance"] == 1


def test_recover_dlq_survives_unfixable_garbage(store, tmp_path):
    import json as _json
    f = tmp_path / "mes.jsonl"
    f.write_text(_json.dumps({"kind": "mes", "machine": [1, 2], "ts": "x", "event": "shutdown"}) + "\n")
    ingest_file(store, f)
    res = recover_dlq(store)                    # must not raise on un-remappable rows
    assert res["still_dead_lettered"] >= 1


def test_validator_rejects_bad_records():
    assert validate_telemetry({"machine": "CNC-17", "ts": "2026-07-01T00:00:00+00:00",
                               "signal": "spindle_temp_c", "value": 9999})[0] is None
    assert validate_telemetry({"machine": "CNC-17", "ts": "2999-01-01T00:00:00+00:00",
                               "signal": "spindle_temp_c", "value": 55})[1] == "future_timestamp"
    assert validate_telemetry({"machine": "CNC-17", "ts": "2026-07-01T00:00:00+00:00",
                               "signal": "spindle_temp_c", "value": float("nan")})[1] == "value_not_finite"
    # naive timestamp (no tz) is rejected, not silently assumed UTC
    assert validate_telemetry({"machine": "CNC-17", "ts": "2026-07-01T00:00:00",
                               "signal": "spindle_temp_c", "value": 55})[1] == "ts_naive_no_timezone"

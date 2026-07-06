"""Test bootstrap: isolate all on-disk state to a temp dir and force the
deterministic offline engine so the suite never needs network access."""
import os
import tempfile

_d = tempfile.mkdtemp(prefix="fie_pytest_")
os.environ.setdefault("FIE_DATA_DIR", _d)
os.environ.setdefault("FIE_DB", os.path.join(_d, "plant.db"))
os.environ.setdefault("FIE_ENGINE", "rule")

import pytest  # noqa: E402


@pytest.fixture
def store(tmp_path):
    from fie.store import Store
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


@pytest.fixture
def raw_dir(tmp_path):
    """A messy raw feed written to an isolated directory."""
    from fie.simulator import SCENARIOS, write_raw_feed
    d = tmp_path / "raw"
    manifest = write_raw_feed(SCENARIOS, out_dir=d)
    return d, manifest

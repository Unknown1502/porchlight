import os
import sys
from pathlib import Path

os.environ.setdefault("PORCHLIGHT_OFFLINE", "1")
os.environ.setdefault("PORCHLIGHT_COMMUNITY_ID", "test-coalition")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from porchlight.policy import reset_audit  # noqa: E402
from porchlight.tools.store import get_store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    get_store().clear("test-coalition")
    reset_audit()
    yield
    get_store().clear("test-coalition")

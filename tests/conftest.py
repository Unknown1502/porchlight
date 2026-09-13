import os
import sys
from pathlib import Path

os.environ.setdefault("PORCHLIGHT_OFFLINE", "1")
os.environ.setdefault("PORCHLIGHT_COMMUNITY_ID", "test-coalition")
os.environ.setdefault("PORCHLIGHT_FIXTURE_TOOLS", "1")
# Force off, not setdefault: a developer's local .env may set this for a real
# demo run, and config.py's load_dotenv() would otherwise carry it into every
# test process, making all 200 tests write real policy denials to CloudWatch on
# every P001-P005 rule that fires. Tests must never touch AWS regardless of
# what a local .env says.
os.environ["PORCHLIGHT_CLOUDWATCH_LOG_GROUP"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from porchlight import db  # noqa: E402
from porchlight.policy import reset_audit  # noqa: E402
from porchlight.tools.store import get_store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    """Every test gets its own database and a cleared community store.

    The database matters as much as the store now that durable state includes the
    broadcast rate-limit window and the approvals table. Sharing either across
    tests means P004 fires on a test that never sent anything, and a spent
    capability from one test denies an unrelated one — failures that look like
    logic bugs and are actually leakage.
    """
    db.close()
    db.connect(tmp_path / "porchlight-test.db")
    get_store().clear("test-coalition")
    reset_audit()
    yield
    get_store().clear("test-coalition")
    reset_audit()
    db.close()

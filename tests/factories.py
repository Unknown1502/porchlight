from datetime import datetime, timedelta, timezone

from porchlight.models import Channel, Report

BASE = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)


def make_report(rid: str, content: str, *, reporter: str = "resident-001",
                area: str = "411038", days_ago: int = 0, note: str = "",
                channel: Channel = Channel.SMS) -> Report:
    return Report(
        report_id=rid,
        community_id="test-coalition",
        received_at=BASE - timedelta(days=days_ago),
        channel=channel,
        raw_content=content,
        volunteer_note=note,
        reporter_pseudonym=reporter,
        reporter_area=area,
    )

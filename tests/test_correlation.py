"""The clustering tests are the most important in the suite.

A false campaign is a warning broadcast to a list of frightened older people on
no evidence. These tests exist to make that hard to ship by accident.
"""
from datetime import datetime, timedelta, timezone

from porchlight.correlation import build_clusters, cluster_for_report, meets_threshold

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def rec(rid, reporter, *, keys=(), fp="parcel seized customs bribe", days=0, area="411038"):
    return {
        "report_id": rid, "reporter_pseudonym": reporter, "reporter_area": area,
        "received_at": (NOW - timedelta(days=days)).isoformat(),
        "indicator_keys": list(keys), "script_fingerprint": fp,
    }


def test_shared_hard_indicator_forms_a_campaign():
    recs = [rec(f"r{i}", f"res{i}", keys=["phone:9000000042"], days=i) for i in range(4)]
    c = cluster_for_report("r0", recs)
    assert c is not None
    assert len(c.members) == 4
    assert c.distinct_reporters == 4
    assert meets_threshold(c)
    assert c.confidence() >= 0.7


def test_same_script_different_crew_does_not_merge():
    """The decoy case. Same script, different callback numbers, different areas."""
    a = [rec(f"a{i}", f"resA{i}", keys=["phone:9000000042"], area="411038") for i in range(3)]
    b = [rec(f"b{i}", f"resB{i}", keys=["phone:9000000099"], area="440010") for i in range(3)]
    clusters = build_clusters(a + b)
    ids = [set(c.report_ids) for c in clusters]
    assert {"a0", "a1", "a2"} in ids
    assert {"b0", "b1", "b2"} in ids


def test_one_reporter_filing_repeatedly_is_not_breadth():
    recs = [rec(f"r{i}", "same-resident", keys=["phone:9000000042"], days=i) for i in range(4)]
    c = cluster_for_report("r0", recs)
    assert c.distinct_reporters == 1
    assert not meets_threshold(c), "volume from one reporter must not clear the bar"


def test_unclassified_fingerprints_never_cluster():
    """Two reports we failed to classify are not thereby related."""
    recs = [rec("r1", "resA", fp="unclassified script"),
            rec("r2", "resB", fp="unclassified script"),
            rec("r3", "resC", fp="unclassified script")]
    clusters = build_clusters(recs)
    assert all(len(c.members) == 1 for c in clusters)


def test_fingerprint_only_link_requires_same_area_and_tight_window():
    far = [rec("r1", "resA", area="411038", days=0),
           rec("r2", "resB", area="440010", days=0)]
    assert all(len(c.members) == 1 for c in build_clusters(far))

    old = [rec("r1", "resA", area="411038", days=0),
           rec("r2", "resB", area="411038", days=20)]
    assert all(len(c.members) == 1 for c in build_clusters(old))


def test_genre_alone_does_not_clear_the_threshold():
    """Three residents, three areas, no shared indicator — a genre, not a crew."""
    recs = [rec("r1", "a", area="411038"), rec("r2", "b", area="440010"),
            rec("r3", "c", area="422001")]
    for c in build_clusters(recs):
        assert not meets_threshold(c)

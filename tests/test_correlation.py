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


# --------------------------------------------------------------------------
# Corroboration: one shared indicator is not enough
# --------------------------------------------------------------------------
def test_a_shared_helpline_does_not_fuse_unrelated_scams():
    """The false-alarm case that matters most.

    Several unrelated scams all say "ring your bank on the number on your card".
    Every one of those reports then carries the same real helpline. A
    single-indicator rule fuses them into one fictitious campaign and broadcasts
    it to a list of frightened people.
    """
    helpline = "phone:8002003333"
    recs = [
        rec("r1", "resA", keys=[helpline, "upi:crewA@x"], fp="parcel seized customs bribe"),
        rec("r2", "resB", keys=[helpline, "upi:crewB@x"], fp="fake tech support remote access"),
        rec("r3", "resC", keys=[helpline, "upi:crewC@x"], fp="prize lottery advance fee"),
        rec("r4", "resD", keys=[helpline, "upi:crewD@x"], fp="kyc update account block"),
    ]
    for cluster in build_clusters(recs):
        assert len(cluster.members) == 1, "a shared helpline is not evidence of a crew"


def test_a_bridging_report_does_not_merge_two_crews():
    """One resident muddles two calls together and names both crews' numbers.

    Merging through them turns two accurate warnings into one wrong one.
    """
    crew_a = [rec(f"a{i}", f"resA{i}", keys=["phone:9000000011", "upi:crewA@x"],
                  fp="parcel seized customs bribe") for i in range(3)]
    crew_b = [rec(f"b{i}", f"resB{i}", keys=["phone:9000000022", "upi:crewB@x"],
                  fp="electricity bill disconnection tonight", area="440010")
              for i in range(3)]
    bridge = [rec("bridge", "resX", keys=["phone:9000000011", "phone:9000000022"],
                  fp="unclassified script", area="422001")]

    clusters = build_clusters(crew_a + crew_b + bridge)
    sizes = sorted(len(c.members) for c in clusters)
    assert 6 not in sizes, "the two crews must not be fused through one confused report"
    assert sizes.count(3) == 2, "both crews should still be found"


def test_one_shared_indicator_plus_the_same_script_is_enough():
    """A crew that reused its number and told both residents the same story."""
    recs = [rec("r1", "resA", keys=["phone:9000000042"]),
            rec("r2", "resB", keys=["phone:9000000042"]),
            rec("r3", "resC", keys=["phone:9000000042"])]
    cluster = cluster_for_report("r1", recs)
    assert cluster is not None and len(cluster.members) == 3
    assert meets_threshold(cluster)


def test_one_shared_indicator_with_no_usable_script_is_not_enough():
    """Two reports nobody could classify are not thereby the same crew."""
    recs = [rec("r1", "resA", keys=["phone:9000000042"], fp="unclassified script"),
            rec("r2", "resB", keys=["phone:9000000042"], fp="unclassified script")]
    assert all(len(c.members) == 1 for c in build_clusters(recs))


def test_two_shared_indicators_link_even_across_different_scripts():
    """A crew running two scripts still shares its infrastructure."""
    recs = [rec("r1", "resA", keys=["phone:9000000042", "upi:payee7@ybl"],
                fp="parcel seized customs bribe"),
            rec("r2", "resB", keys=["phone:9000000042", "upi:payee7@ybl"],
                fp="electricity bill disconnection tonight")]
    cluster = cluster_for_report("r1", recs)
    assert cluster is not None and len(cluster.members) == 2


def test_an_indicator_carried_by_very_many_reports_is_infrastructure():
    """Beyond a certain spread, an identifier is a utility, not a signature."""
    shared = "url:https://example.com/help"
    recs = [rec(f"r{i}", f"res{i}", keys=[shared], fp="parcel seized customs bribe")
            for i in range(15)]
    assert all(len(c.members) == 1 for c in build_clusters(recs))

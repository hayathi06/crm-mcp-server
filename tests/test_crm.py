import pytest

from crm_mcp import duplicates
from crm_mcp.crm import CRM


@pytest.fixture
def crm():
    return CRM(":memory:")


# ----------------------------------------------------------- normalisation
def test_phone_formats_are_normalised():
    for raw in ["0491 570 156", "0491-570-156", "+61 491 570 156", "(04) 9157 0156"]:
        assert duplicates.normalise_phone(raw) == "0491570156"


def test_email_is_case_insensitive():
    assert duplicates.normalise_email("  Sam.Lee@Example.COM ") == "sam.lee@example.com"


# ------------------------------------------------------- duplicate detection
def test_seeded_duplicates_are_found(crm):
    ids = [r["id"] for r in crm.db.execute("SELECT id FROM leads ORDER BY id DESC LIMIT 4")]
    found = {i: crm.find_duplicates(i)["possible_duplicates"] for i in ids}
    assert all(found.values()), "every planted duplicate should be detected"
    confidences = sorted(m[0]["confidence"] for m in found.values())
    assert confidences == ["high", "high", "low", "medium"]


def test_lead_never_matches_itself(crm):
    for (lead_id,) in crm.db.execute("SELECT id FROM leads"):
        assert all(m["lead_id"] != lead_id for m in crm.find_duplicates(lead_id)["possible_duplicates"])


def test_create_blocks_high_confidence_duplicate(crm):
    existing = crm.get_lead(1)
    before = crm.pipeline_summary()["total_leads"]
    res = crm.create_lead("Someone", "Else", email=existing["email"].upper(), state="VIC")
    assert res["created"] is False
    assert res["possible_duplicates"][0]["lead_id"] == 1
    assert crm.pipeline_summary()["total_leads"] == before


def test_create_with_override_and_audit(crm):
    existing = crm.get_lead(1)
    res = crm.create_lead("Someone", "Else", email=existing["email"], allow_duplicate=True)
    assert res["created"] is True
    assert crm.recent_activity(1)[0]["detail"] == "created despite duplicate warning"


def test_create_new_lead(crm):
    res = crm.create_lead("Test", "Person", email="test.person@example.com", postcode="3000", state="vic")
    assert res["created"] and res["lead"]["state"] == "VIC" and res["lead"]["status"] == "New"


@pytest.mark.parametrize("kwargs, msg", [
    ({"email": "not-an-email"}, "email"),
    ({"email": "a@example.com", "postcode": "30"}, "postcode"),
    ({"email": "a@example.com", "state": "XX"}, "state"),
    ({}, "email or a phone"),
])
def test_create_validation(crm, kwargs, msg):
    with pytest.raises(ValueError, match=msg):
        crm.create_lead("A", "B", **kwargs)


# -------------------------------------------------------------- other tools
def test_status_note_assign_and_audit(crm):
    crm.update_status(1, "Qualified")
    crm.add_note(1, "Asked for a demo")
    rec = crm.recommend_salesperson(1)["recommendations"][0]
    crm.assign_lead(1, rec["salesperson_id"])
    lead = crm.get_lead(1)
    assert lead["status"] == "Qualified" and lead["owner"] == rec["name"]
    assert lead["notes"][-1]["body"] == "Asked for a demo"
    assert [a["action"] for a in crm.recent_activity(3)] == ["assign_lead", "add_note", "update_status"]


def test_recommendation_prefers_territory_and_low_workload(crm):
    lead_id = crm.search_leads(state="WA", limit=1)["leads"][0]["id"]
    rec = crm.recommend_salesperson(lead_id)
    assert rec["territory_match"] is True
    assert all("WA" in r["territories"] for r in rec["recommendations"])
    loads = [r["open_leads"] / r["capacity"] for r in rec["recommendations"]]
    assert loads == sorted(loads)


def test_invalid_status_rejected(crm):
    with pytest.raises(ValueError):
        crm.update_status(1, "Maybe")


def test_search_is_injection_safe(crm):
    total = crm.pipeline_summary()["total_leads"]
    assert crm.search_leads(query="' OR 1=1 --")["count"] == 0
    assert crm.pipeline_summary()["total_leads"] == total


def test_read_only_mode_blocks_writes():
    ro = CRM(":memory:", read_only=True)
    for call in (lambda: ro.update_status(1, "Won"), lambda: ro.add_note(1, "x"),
                 lambda: ro.assign_lead(1, 1), lambda: ro.create_lead("A", "B", email="a@example.com")):
        with pytest.raises(PermissionError):
            call()
    assert ro.search_leads(limit=5)["count"] == 5


def test_pipeline_summary_counts_add_up(crm):
    s = crm.pipeline_summary()
    assert sum(s["by_status"].values()) == s["total_leads"] == sum(s["by_source"].values())

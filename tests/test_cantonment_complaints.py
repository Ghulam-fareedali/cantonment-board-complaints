"""Comprehensive Battery Tests for Cantonment Board Complaint Management & Zero-Touch Automation.

Validates the full lifecycle:
  1. WhatsApp complaint intake + multi-photo evidence preservation
  2. Zara AI classification, grounding, confidence scoring & zero-touch routing
  3. Department -> Area -> Field staff workload-aware assignment
  4. 24-hour SLA calculations (within SLA, at risk, breached)
  5. Multi-tier timed reminders (12h, 18h, 22h) and automatic escalation hierarchy
  6. Field staff resolution submission with mandatory RESOLUTION PROOF
  7. Side-by-side Before/After visual comparison
  8. Department supervisor approval and rejection with mandatory reason
  9. Customer WhatsApp notification & feedback loop (Confirmed Closed vs Reopened)
  10. Reopen cycle preservation without overwriting history
  11. Continuous learning signals on human corrections
  12. Google Sheets 19-column synchronization & CSV export
  13. Strict state machine transitions & security
"""

import pytest
from datetime import datetime, timezone, timedelta

from app.complaint_state import (
    can_transition,
    validate_transition,
    InvalidTransitionError,
    STATUS_NEW,
    STATUS_IN_PROGRESS,
    STATUS_PENDING_DEPARTMENT_VERIFICATION,
    STATUS_RESOLVED,
    STATUS_CLOSED,
    STATUS_REOPENED,
    STATUS_CANCELLED,
)
from app.complaint_ai import ComplaintAnalyzer
from app.complaint_sla import calculate_sla_status, check_sla_triggers, now_utc
from app.google_sheets_sync import GoogleSheetsSyncEngine, format_row, COLUMNS
from app.complaints_store import ComplaintStore, CANTONMENT_TENANT_ID


@pytest.fixture
def store():
    return ComplaintStore()


def test_state_machine_valid_and_invalid_transitions():
    """Verify strict state machine rules and prevention of invalid jumps."""
    # Valid transitions
    assert can_transition("NEW", "ASSIGNED")
    assert can_transition("ASSIGNED", "IN_PROGRESS")
    assert can_transition("IN_PROGRESS", "PENDING_DEPARTMENT_VERIFICATION")
    assert can_transition("PENDING_DEPARTMENT_VERIFICATION", "PENDING_CUSTOMER_CONFIRMATION")
    assert can_transition("PENDING_CUSTOMER_CONFIRMATION", "CLOSED")
    assert can_transition("PENDING_CUSTOMER_CONFIRMATION", "REOPENED")
    assert can_transition("REOPENED", "IN_PROGRESS")

    # Invalid illegal transitions
    assert not can_transition("NEW", "CLOSED")
    assert not can_transition("NEW", "RESOLVED")
    assert not can_transition("CANCELLED", "IN_PROGRESS")

    with pytest.raises(InvalidTransitionError):
        validate_transition("NEW", "CLOSED")


def test_ai_classification_zero_touch(store):
    """Verify Zara AI accurately extracts category, area, department, and achieves Zero-Touch confidence."""
    tid = CANTONMENT_TENANT_ID
    depts = store.departments[tid]
    areas = store.areas[tid]
    staff = store.staff[tid]

    analyzer = ComplaintAnalyzer(departments=depts, areas=areas, staff=staff)

    # Citizen message mentioning garbage near market
    msg = "There is huge garbage accumulated near the market dumpsters on Mall Road. Please clean it immediately."
    res = analyzer.analyze(msg)

    assert res["department_name"] == "Sanitation Department"
    assert res["category"] == "Sanitation & Solid Waste"
    assert "Mall Road" in res["area_name"]
    assert res["assigned_staff_name"] is not None
    assert res["confidence_score"] >= 95.0
    assert res["routing_mode"] == "zero_touch"
    assert not res["requires_verification"]
    assert any(w in res["required_action"].lower() for w in ["clear", "clean", "sweep", "compactor", "garbage"])


def test_ai_classification_grounded_never_invents_departments(store):
    """AI must strictly ground itself in configured organizational data and never invent entities."""
    tid = CANTONMENT_TENANT_ID
    depts = store.departments[tid]
    areas = store.areas[tid]
    staff = store.staff[tid]

    analyzer = ComplaintAnalyzer(departments=depts, areas=areas, staff=staff)
    res = analyzer.analyze("Spaceship landed on Mars road and needs alien clearance")

    # Must map to one of our real configured departments, not invent 'Alien Department'
    valid_dept_names = {d["name"] for d in depts}
    assert res["department_name"] in valid_dept_names


def test_sla_countdown_and_status():
    """Verify 24h countdown statuses: within_sla, at_risk, breached."""
    now = now_utc()

    # 1. 20 hours remaining -> within_sla
    deadline_good = now + timedelta(hours=20)
    st, hrs, _ = calculate_sla_status(deadline_good, "IN_PROGRESS", now=now)
    assert st == "within_sla"
    assert hrs > 19.0

    # 2. 4 hours remaining -> at_risk (< 6h)
    deadline_risk = now + timedelta(hours=4)
    st, hrs, text = calculate_sla_status(deadline_risk, "IN_PROGRESS", warning_hours=6, now=now)
    assert st == "at_risk"
    assert "At Risk" in text

    # 3. 2 hours overdue -> breached
    deadline_breached = now - timedelta(hours=2)
    st, hrs, text = calculate_sla_status(deadline_breached, "IN_PROGRESS", now=now)
    assert st == "breached"
    assert "Breached" in text


def test_sla_timed_reminders_and_escalations():
    """Verify 12h reminder, 18h urgent reminder, 22h warning, and 24h breach escalation triggers."""
    now = now_utc()
    rec_time = now - timedelta(hours=19)  # 19 hours elapsed

    complaint = {
        "status": "IN_PROGRESS",
        "received_at": rec_time.isoformat(),
        "sla_hours": 24,
        "assigned_staff_name": "Muhammad Imran",
    }

    # First check: should fire 12h reminder and 18h reminder
    triggers = check_sla_triggers(complaint, existing_escalations=[], now=now)
    levels = [t["level"] for t in triggers]
    assert "reminder_12h" in levels
    assert "reminder_18h" in levels
    assert "final_warning_22h" not in levels  # only 19h elapsed

    # Now simulate 25 hours elapsed -> should trigger 24h SLA breach
    rec_past_24 = now - timedelta(hours=25)
    complaint_breached = {
        "status": "IN_PROGRESS",
        "received_at": rec_past_24.isoformat(),
        "sla_hours": 24,
        "assigned_staff_name": "Muhammad Imran",
    }
    past_triggers = check_sla_triggers(
        complaint_breached,
        existing_escalations=[{"level": "reminder_12h"}, {"level": "reminder_18h"}],
        now=now,
    )
    past_levels = [t["level"] for t in past_triggers]
    assert "final_warning_22h" in past_levels
    assert "breach_24h" in past_levels


def test_full_resolution_proof_lifecycle(store):
    """Test full cycle: Intake -> Start Progress -> Submit Proof -> Dept Verify -> Customer Confirms."""
    tid = CANTONMENT_TENANT_ID

    # 1. Intake
    c = store.create_complaint(
        tenant_id=tid,
        customer_name="Zubair Khan",
        customer_phone="+923009988776",
        raw_message="Streetlight pole #4 flickering and making buzzing sound on Mall Road",
        category="Streetlights & Electrical",
        location_name="Mall Road Sector",
        department_id="d1111111-0000-0000-0000-000000000002",
        area_id="a1111111-0000-0000-0000-000000000001",
        assigned_staff_id="s1111111-0000-0000-0000-000000000003",
        status="ASSIGNED",
        customer_evidence_urls=["https://example.com/customer-evidence-photo1.jpg"],
    )
    cid = c["id"]
    assert c["status"] == "ASSIGNED"
    assert c["ticket_number"].startswith("CMP-")

    # Verify Customer Evidence is attached and preserved
    detail = store.get_complaint(tid, cid)
    assert len(detail["customer_evidence"]) == 1
    assert detail["customer_evidence"][0]["label"] == "CUSTOMER EVIDENCE"

    # 2. Staff starts progress
    c_in_progress = store.start_progress(tid, cid, actor_name="Zahid Hussain (Staff)")
    assert c_in_progress["status"] == "IN_PROGRESS"

    # 3. Staff submits resolution with mandatory RESOLUTION PROOF
    with pytest.raises(ValueError):
        # Cannot submit without proof photos
        store.submit_resolution(
            tid, cid, "s1111111-0000-0000-0000-000000000003", "Zahid Hussain", "Done", []
        )

    c_resolved = store.submit_resolution(
        tenant_id=tid,
        complaint_id=cid,
        resolved_by_staff_id="s1111111-0000-0000-0000-000000000003",
        resolved_by_name="Zahid Hussain",
        resolution_description="Replaced faulty ballast and bulb in pole #4.",
        resolution_proof_urls=["https://example.com/resolution-proof-after.jpg"],
        remarks="Working perfectly.",
    )
    assert c_resolved["status"] == "PENDING_DEPARTMENT_VERIFICATION"

    # Check Before/After pairs exist
    detail2 = store.get_complaint(tid, cid)
    assert len(detail2["before_after_pairs"]) == 1
    pair = detail2["before_after_pairs"][0]
    assert pair["before_url"] == "https://example.com/customer-evidence-photo1.jpg"
    assert pair["after_url"] == "https://example.com/resolution-proof-after.jpg"

    # 4. Department Supervisor Approves
    c_verified = store.department_verify_resolution(
        tid, cid, approved=True, verified_by_name="Engr. Haroon Rasheed"
    )
    assert c_verified["status"] == "PENDING_CUSTOMER_CONFIRMATION"

    # 5. Customer Feedback: Confirmed (Closed)
    c_closed = store.customer_feedback(
        tid, cid, customer_confirmed=True, rating=5, feedback_text="All fixed, thanks!"
    )
    assert c_closed["status"] == "CLOSED"
    assert c_closed["customer_rating"] == 5
    assert c_closed["google_sheet_synced"] is True


def test_resolution_rejection_by_department(store):
    """Department supervisor rejects substandard resolution proof with mandatory reason."""
    tid = CANTONMENT_TENANT_ID

    c = store.create_complaint(
        tenant_id=tid,
        customer_name="Hamza Tariq",
        customer_phone="+923331112233",
        raw_message="Pothole on Tufail Road",
        category="Road Damage & Potholes",
        location_name="Tufail Road Sector",
        status="IN_PROGRESS",
    )
    cid = c["id"]

    # Submit resolution
    store.submit_resolution(
        tid, cid, "s1111111-0000-0000-0000-000000000005", "Bilal Qureshi",
        "Filled with gravel", ["https://example.com/after-patch.jpg"]
    )

    # Rejection requires reason
    with pytest.raises(ValueError):
        store.department_verify_resolution(tid, cid, approved=False, verified_by_name="Supervisor", rejection_reason="")

    # Reject with valid reason -> Reverts to IN_PROGRESS
    c_rejected = store.department_verify_resolution(
        tid, cid, approved=False, verified_by_name="Supervisor", rejection_reason="Gravel only; must use asphalt bitumen."
    )
    assert c_rejected["status"] == "IN_PROGRESS"
    assert "asphalt bitumen" in c_rejected["rejection_reason"]


def test_customer_reopens_complaint_and_preserves_cycle(store):
    """When citizen says 'Not Resolved', ticket is reopened, existing resolution cycle is preserved, and new cycle starts."""
    tid = CANTONMENT_TENANT_ID

    c = store.create_complaint(
        tenant_id=tid,
        customer_name="Mrs. Jameel",
        customer_phone="+923005544332",
        raw_message="Water pipe leaking near house 50",
        category="Water Supply & Sewerage",
        location_name="Askari 10 Sector",
        status="IN_PROGRESS",
    )
    cid = c["id"]

    # Cycle 1 resolution
    store.submit_resolution(
        tid, cid, "s1111111-0000-0000-0000-000000000004", "Rashid Minhas",
        "Tightened external valve", ["https://example.com/valve-tightened.jpg"]
    )
    store.department_verify_resolution(tid, cid, approved=True, verified_by_name="Water Works Supervisor")

    # Customer replies "NOT RESOLVED"
    c_reopened = store.customer_feedback(
        tid, cid, customer_confirmed=False, feedback_text="It is still leaking underneath the road."
    )

    # Status automatically reopens and transitions back to IN_PROGRESS
    assert c_reopened["status"] == "IN_PROGRESS"
    assert c_reopened["reopened_count"] == 1

    detail = store.get_complaint(tid, cid)
    # Check that previous resolution cycle was NOT overwritten
    assert len(detail["resolutions"]) == 1
    assert detail["resolutions"][0]["cycle_number"] == 1
    assert detail["resolutions"][0]["resolution_description"] == "Tightened external valve"

    # Staff can now submit Cycle 2 resolution
    store.submit_resolution(
        tid, cid, "s1111111-0000-0000-0000-000000000004", "Rashid Minhas",
        "Excavated road and replaced cracked 4-inch connector elbow", ["https://example.com/elbow-replaced.jpg"]
    )
    detail2 = store.get_complaint(tid, cid)
    assert len(detail2["resolutions"]) == 2
    assert detail2["resolutions"][1]["cycle_number"] == 2


def test_google_sheets_19_columns_mapping_and_resilience():
    """Verify Google Sheets sync schema contains all 19 columns and CSV export is valid."""
    complaint = {
        "ticket_number": "CMP-2026-000100",
        "customer_name": "Farhan Ali",
        "customer_phone": "+923001122334",
        "category": "Sanitation & Solid Waste",
        "complaint_type": "garbage_dump",
        "location_name": "Mall Road Sector",
        "area_name": "Mall Road Sector",
        "department_name": "Sanitation Department",
        "assigned_staff_name": "Muhammad Imran",
        "received_at": "2026-10-11T10:00:00Z",
        "assigned_at": "2026-10-11T10:02:00Z",
        "resolved_at": "2026-10-11T16:30:00Z",
        "sla_deadline": "2026-10-12T10:00:00Z",
        "sla_status": "within_sla",
        "status": "CLOSED",
        "customer_rating": 5,
        "closed_at": "2026-10-11T17:00:00Z",
    }

    row = format_row(complaint, ["https://example.com/after.jpg"])

    assert len(COLUMNS) == 19
    for col in COLUMNS:
        assert col in row

    assert row["Complaint ID"] == "CMP-2026-000100"
    assert row["Customer"] == "Farhan Ali"
    assert row["Resolution Proof"] == "https://example.com/after.jpg"

    # Test sync resilience
    engine = GoogleSheetsSyncEngine()
    ok, msg = engine.sync_complaint(complaint, ["https://example.com/after.jpg"])
    assert ok is True

    # Test CSV export
    csv_out = GoogleSheetsSyncEngine.export_csv([complaint])
    assert "CMP-2026-000100" in csv_out
    assert "Sanitation Department" in csv_out


def test_store_evidence_and_proof_append(store):
    tid = CANTONMENT_TENANT_ID
    c = store.create_complaint(
        tenant_id=tid,
        customer_name="Mansoor Khan",
        customer_phone="+923001112244",
        raw_message="Fallen tree on link road",
        category="Horticulture & Parks",
        location_name="Falcon Complex",
        status="ASSIGNED",
    )
    cid = c["id"]

    # Append customer evidence
    evs = store.add_customer_evidence(tid, cid, ["https://example.com/tree1.jpg", "https://example.com/tree2.jpg"])
    assert len(evs) == 2
    assert evs[0]["label"] == "CUSTOMER EVIDENCE"

    # Start progress and submit resolution
    store.start_progress(tid, cid, actor_name="Field Staff")
    store.submit_resolution(
        tid, cid, "s1111111-0000-0000-0000-000000000001", "Field Staff", "Tree cut", ["https://example.com/tree-done.jpg"]
    )

    # Append extra resolution proof
    proofs = store.add_resolution_proof(tid, cid, ["https://example.com/tree-done2.jpg"])
    assert len(proofs) == 1
    assert proofs[0]["label"] == "RESOLUTION PROOF"

    detail = store.get_complaint(tid, cid)
    assert len(detail["customer_evidence"]) == 2
    assert len(detail["resolution_proof"]) == 2


def test_store_manual_escalation_and_google_sync_retry(store):
    tid = CANTONMENT_TENANT_ID
    complaints = store.list_complaints(tid)
    first_id = complaints[0]["id"]

    # Trigger manual escalation
    esc = store.trigger_escalation(
        tenant_id=tid,
        complaint_id=first_id,
        level="tier_2_dept_manager",
        notified_to="Executive Engineer Cantt",
        action_taken="Priority alert dispatched to operations team",
        actor_name="Control Room Operator",
    )
    assert esc["level"] == "tier_2_dept_manager"
    assert esc["complaint_id"] == first_id

    # Sync google sheet and retry
    ok, msg = store.sync_google_sheet(tid, first_id)
    assert ok is True

    retried = store.retry_failed_google_syncs(tid)
    assert isinstance(retried, list)


def test_store_detailed_analytics_and_insights(store):
    tid = CANTONMENT_TENANT_ID
    analytics = store.get_detailed_analytics(tid)

    assert "total_complaints" in analytics
    assert analytics["total_complaints"] >= 4
    assert "sla_compliance_rate" in analytics
    assert "sla_breach_rate" in analytics
    assert "reopen_rate" in analytics
    assert "by_department" in analytics
    assert "staff_workload" in analytics
    assert "ai_insights" in analytics
    assert len(analytics["ai_insights"]["preventive_actions"]) >= 1


def test_store_org_crud_and_learning_signals(store):
    tid = CANTONMENT_TENANT_ID

    # Department CRUD
    new_dept = store.create_department(tid, {
        "name": "Fire & Disaster Response",
        "code": "FDR",
        "supervisor_name": "Chief Aslam",
    })
    dept_id = new_dept["id"]
    assert new_dept["name"] == "Fire & Disaster Response"

    updated_dept = store.update_department(tid, dept_id, {"description": "Emergency rapid response"})
    assert updated_dept["description"] == "Emergency rapid response"

    # Area CRUD
    new_area = store.create_area(tid, {
        "name": "Cantt Sector 9",
        "zone": "East Zone",
        "department_id": dept_id,
    })
    area_id = new_area["id"]
    assert new_area["zone"] == "East Zone"

    updated_area = store.update_area(tid, area_id, {"supervisor_in_charge": "Chief Aslam"})
    assert updated_area["supervisor_in_charge"] == "Chief Aslam"

    # Staff CRUD
    new_staff = store.create_staff(tid, {
        "name": "Rescue Operator Waqas",
        "phone": "+923000000009",
        "department_id": dept_id,
        "area_id": area_id,
    })
    staff_id = new_staff["id"]
    assert new_staff["name"] == "Rescue Operator Waqas"

    updated_staff = store.update_staff(tid, staff_id, {"whatsapp_number": "+923000000009"})
    assert updated_staff["whatsapp_number"] == "+923000000009"

    # Verify learning signals collection on operator override
    c = store.create_complaint(
        tenant_id=tid,
        customer_name="Citizen Test",
        customer_phone="+923001234567",
        raw_message="Test complaint for learning signal",
        category="General",
        location_name="Cantt Sector 9",
        department_id="d1111111-0000-0000-0000-000000000001",
        status="PENDING_VERIFICATION",
    )
    # Operator changes department to Fire & Disaster Response
    store.verify_and_route(
        tenant_id=tid,
        complaint_id=c["id"],
        department_id=dept_id,
        area_id=area_id,
        assigned_staff_id=staff_id,
        actor_name="Operator Override",
        notes="Reclassified from general to fire disaster",
    )
    assert len(store.learning_signals[tid]) >= 1
    last_signal = store.learning_signals[tid][-1]
    assert last_signal["complaint_id"] == c["id"]
    assert last_signal["correction_type"] == "department_override"


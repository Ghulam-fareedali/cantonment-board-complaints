"""HTTP API Integration Tests for Cantonment Board Complaint Routes."""

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.complaints import make_complaints_router
from app.complaints_store import CANTONMENT_TENANT_ID


@pytest.fixture
def api_client():
    test_app = FastAPI()
    test_app.include_router(make_complaints_router())
    return TestClient(test_app)


def test_complaints_summary_http(api_client):
    res = api_client.get(
        "/complaints/summary",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "total" in data
    assert "in_progress" in data
    assert "sla_at_risk" in data
    assert "zero_touch_percentage" in data
    assert data["total"] >= 4


def test_complaints_list_http(api_client):
    res = api_client.get(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "complaints" in data
    assert len(data["complaints"]) >= 4
    first = data["complaints"][0]
    assert first["ticket_number"].startswith("CMP-")
    assert "customer_name" in first


def test_complaints_detail_http(api_client):
    # Fetch first complaint id
    list_res = api_client.get(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    first_id = list_res.json()["complaints"][0]["id"]

    res = api_client.get(
        f"/complaints/{first_id}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    c = res.json()
    assert c["id"] == first_id
    assert "customer_evidence" in c
    assert "resolutions" in c
    assert "timeline" in c
    assert "before_after_pairs" in c


def test_whatsapp_intake_workflow_http(api_client):
    res = api_client.post(
        "/complaints/whatsapp/intake",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Tariq Aziz",
            "customer_phone": "+923007788990",
            "message": "Commercial garbage accumulated near market dumpsters on Mall Road with bad stench",
            "media_urls": ["https://example.com/wa-photo1.jpg"],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["complaint"]["category"] == "Sanitation & Solid Waste"
    assert body["complaint"]["ticket_number"].startswith("CMP-")
    assert "whatsapp_reply" in body
    assert "Assalam-o-Alaikum Tariq Aziz" in body["whatsapp_reply"]
    assert "CMP-" in body["whatsapp_reply"]
    assert body["ai_analysis"]["confidence_score"] >= 95.0
    assert body["ai_analysis"]["routing_mode"] == "zero_touch"


def test_department_verify_http(api_client):
    # Create complaint with area mention for Zero-Touch auto assignment
    intake = api_client.post(
        "/complaints/whatsapp/intake",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Rehan Siddiqui",
            "customer_phone": "+923215566778",
            "message": "Streetlight pole broken and dark on Mall Road",
            "media_urls": ["https://example.com/dark-pole.jpg"],
        },
    ).json()["complaint"]
    cid = intake["id"]
    assert intake["status"] == "ASSIGNED"

    # Start progress
    start_res = api_client.post(
        f"/complaints/{cid}/start",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
    )
    assert start_res.status_code == 200

    # Submit resolution
    res_res = api_client.post(
        f"/complaints/{cid}/resolve",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
        json={
            "resolved_by_staff_id": "s1111111-0000-0000-0000-000000000003",
            "resolved_by_name": "Zahid Hussain",
            "resolution_description": "Fitted new LED bulb and fuse",
            "resolution_proof_urls": ["https://example.com/repaired-light.jpg"],
        },
    )
    assert res_res.status_code == 200

    # Verify resolution
    verify_res = api_client.post(
        f"/complaints/{cid}/department-verify",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "supervisor"},
        json={
            "approved": True,
            "verified_by_name": "Electrical Supervisor Khurram",
        },
    )
    assert verify_res.status_code == 200
    v_data = verify_res.json()
    assert v_data["complaint"]["status"] == "PENDING_CUSTOMER_CONFIRMATION"
    assert v_data["customer_whatsapp_dispatch"] is not None
    assert "repaired-light.jpg" in v_data["customer_whatsapp_dispatch"]["proof_media_urls"][0]


def test_customer_confirmation_http(api_client):
    # Customer confirms resolved
    intake = api_client.post(
        "/complaints/whatsapp/intake",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Noman Ali",
            "customer_phone": "+923134445556",
            "message": "Dangerous pothole on R.A. Bazaar Road needing urgent asphalt repair",
            "media_urls": ["https://example.com/pothole-raw.jpg"],
        },
    ).json()["complaint"]
    cid = intake["id"]
    assert intake["status"] == "ASSIGNED"

    api_client.post(
        f"/complaints/{cid}/start",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
    )
    api_client.post(
        f"/complaints/{cid}/resolve",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
        json={
            "resolved_by_staff_id": "s1111111-0000-0000-0000-000000000005",
            "resolved_by_name": "Bilal Qureshi",
            "resolution_description": "Asphalt patch completed",
            "resolution_proof_urls": ["https://example.com/proof.jpg"],
        },
    )
    api_client.post(
        f"/complaints/{cid}/department-verify",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "supervisor"},
        json={"approved": True, "verified_by_name": "Engr. Supervisor"},
    )

    fb_res = api_client.post(
        f"/complaints/{cid}/customer-feedback",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"customer_confirmed": True, "rating": 5, "feedback_text": "Satisfied!"},
    )
    assert fb_res.status_code == 200
    assert fb_res.json()["complaint"]["status"] == "CLOSED"
    assert fb_res.json()["complaint"]["customer_rating"] == 5
    assert fb_res.json()["complaint"]["google_sheet_synced"] is True


def test_update_assign_and_status_transitions_http(api_client):
    # 1. Manual create complaint
    create_res = api_client.post(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Kashif Mahmood",
            "customer_phone": "+923001239988",
            "raw_message": "Sewer line blockage near street 14",
            "category": "Water Supply & Sewerage",
            "location_name": "Askari 10 Sector",
            "priority": "normal",
            "urgency": "medium",
        },
    )
    assert create_res.status_code == 200
    cid = create_res.json()["complaint"]["id"]
    assert create_res.json()["complaint"]["status"] == "NEW"

    # 2. PATCH update fields
    patch_res = api_client.patch(
        f"/complaints/{cid}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"priority": "urgent", "operator_notes": "Needs emergency jetting truck"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["complaint"]["priority"] == "urgent"
    assert "emergency jetting" in patch_res.json()["complaint"]["operator_notes"]

    # 3. POST assign to dept, area, staff
    assign_res = api_client.post(
        f"/complaints/{cid}/assign",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "department_id": "d1111111-0000-0000-0000-000000000003",
            "area_id": "a1111111-0000-0000-0000-000000000003",
            "assigned_staff_id": "s1111111-0000-0000-0000-000000000004",
        },
    )
    assert assign_res.status_code == 200
    assert assign_res.json()["complaint"]["status"] == "ASSIGNED"
    assert assign_res.json()["complaint"]["assigned_staff_name"] == "Rashid Minhas"

    # 4. POST status change
    st_res = api_client.post(
        f"/complaints/{cid}/status",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"status": "IN_PROGRESS", "reason": "Staff on site with jetting unit"},
    )
    assert st_res.status_code == 200
    assert st_res.json()["complaint"]["status"] == "IN_PROGRESS"


def test_add_evidence_and_resolution_proof_http(api_client):
    create_res = api_client.post(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Saeed Anwar",
            "customer_phone": "+923214455667",
            "raw_message": "Dead tree branch obstructing road",
            "category": "Horticulture & Parks",
            "location_name": "Mall Road Sector",
        },
    )
    cid = create_res.json()["complaint"]["id"]

    # Add extra customer evidence
    ev_res = api_client.post(
        f"/complaints/{cid}/evidence",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "file_urls": ["https://example.com/extra-tree1.jpg", "https://example.com/extra-tree2.jpg"],
            "captions": ["Angle from north", "Angle from south"],
        },
    )
    assert ev_res.status_code == 200
    assert ev_res.json()["count"] == 2

    # Start progress and submit initial resolution
    api_client.post(
        f"/complaints/{cid}/assign",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"department_id": "d1111111-0000-0000-0000-000000000005"},
    )
    api_client.post(
        f"/complaints/{cid}/start",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
    )
    api_client.post(
        f"/complaints/{cid}/resolve",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
        json={
            "resolved_by_staff_id": "s1111111-0000-0000-0000-000000000001",
            "resolved_by_name": "Tariq Gardner",
            "resolution_description": "Branch cut and loaded onto truck",
            "resolution_proof_urls": ["https://example.com/tree-resolved.jpg"],
        },
    )

    # Add extra resolution proof
    proof_res = api_client.post(
        f"/complaints/{cid}/resolution-proof",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
        json={
            "file_urls": ["https://example.com/clean-roadway.jpg"],
            "captions": ["Roadway swept clean"],
        },
    )
    assert proof_res.status_code == 200
    assert proof_res.json()["count"] == 1


def test_reopen_and_close_endpoints_http(api_client):
    create_res = api_client.post(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Asim Riaz",
            "customer_phone": "+923339876543",
            "raw_message": "Water valve leak",
            "category": "Water Supply & Sewerage",
            "location_name": "Askari 10 Sector",
            "department_id": "d1111111-0000-0000-0000-000000000003",
            "assigned_staff_id": "s1111111-0000-0000-0000-000000000004",
        },
    )
    cid = create_res.json()["complaint"]["id"]
    assert create_res.json()["complaint"]["status"] == "ASSIGNED"

    # Start progress
    api_client.post(f"/complaints/{cid}/start", headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"})

    # Submit resolution
    api_client.post(
        f"/complaints/{cid}/resolve",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
        json={
            "resolved_by_staff_id": "s1111111-0000-0000-0000-000000000004",
            "resolved_by_name": "Rashid Minhas",
            "resolution_description": "Valve washer replaced",
            "resolution_proof_urls": ["https://example.com/valve-fixed.jpg"],
        },
    )

    # Department verifies resolution -> PENDING_CUSTOMER_CONFIRMATION
    api_client.post(
        f"/complaints/{cid}/department-verify",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "supervisor"},
        json={"approved": True, "verified_by_name": "Supervisor Aslam"},
    )

    # Reopen directly
    reopen_res = api_client.post(
        f"/complaints/{cid}/reopen",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"reason": "Water still seeping from main supply ring"},
    )
    assert reopen_res.status_code == 200
    assert reopen_res.json()["complaint"]["status"] == "IN_PROGRESS"
    assert reopen_res.json()["complaint"]["reopened_count"] == 1

    # Re-resolve
    api_client.post(
        f"/complaints/{cid}/resolve",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "field_staff"},
        json={
            "resolved_by_staff_id": "s1111111-0000-0000-0000-000000000004",
            "resolved_by_name": "Rashid Minhas",
            "resolution_description": "Replaced entire valve housing",
            "resolution_proof_urls": ["https://example.com/valve-housing-fixed.jpg"],
        },
    )

    # Department verifies again
    api_client.post(
        f"/complaints/{cid}/department-verify",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "supervisor"},
        json={"approved": True, "verified_by_name": "Supervisor Aslam"},
    )

    # Close directly
    close_res = api_client.post(
        f"/complaints/{cid}/close",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"rating": 5, "feedback_text": "Supervisor verified and closed"},
    )
    assert close_res.status_code == 200
    assert close_res.json()["complaint"]["status"] == "CLOSED"
    assert close_res.json()["complaint"]["customer_rating"] == 5


def test_google_sync_and_retry_http(api_client):
    # Fetch existing complaint
    list_res = api_client.get(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    cid = list_res.json()["complaints"][0]["id"]

    # Manual sync
    sync_res = api_client.post(
        f"/complaints/{cid}/sync-google",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert sync_res.status_code == 200
    assert sync_res.json()["ok"] is True

    # Retry batch
    retry_res = api_client.post(
        "/complaints/google-sync/retry",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert retry_res.status_code == 200
    assert retry_res.json()["ok"] is True


def test_organizational_crud_departments_areas_staff_http(api_client):
    # 1. Department CRUD
    create_dept = api_client.post(
        "/complaints/departments",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "name": "Public Health & Vector Control",
            "code": "PHVC",
            "description": "Mosquito spray and fumigation",
            "supervisor_name": "Dr. Shakeel",
            "supervisor_phone": "+923007654321",
        },
    )
    assert create_dept.status_code == 200
    dept_id = create_dept.json()["department"]["id"]
    assert create_dept.json()["department"]["code"] == "PHVC"

    update_dept = api_client.put(
        f"/complaints/departments/{dept_id}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"description": "Dengue and malaria prevention spray units"},
    )
    assert update_dept.status_code == 200
    assert "Dengue" in update_dept.json()["department"]["description"]

    # 2. Area CRUD
    create_area = api_client.post(
        "/complaints/areas",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "name": "Falcon Complex",
            "zone": "North Zone",
            "department_id": dept_id,
            "supervisor_in_charge": "Dr. Shakeel",
        },
    )
    assert create_area.status_code == 200
    area_id = create_area.json()["area"]["id"]

    update_area = api_client.put(
        f"/complaints/areas/{area_id}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"zone": "North-West Zone"},
    )
    assert update_area.status_code == 200
    assert update_area.json()["area"]["zone"] == "North-West Zone"

    # 3. Staff CRUD
    create_staff = api_client.post(
        "/complaints/staff",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "name": "Naveed Fumigator",
            "phone": "+923118899001",
            "department_id": dept_id,
            "area_id": area_id,
            "role": "field_staff",
        },
    )
    assert create_staff.status_code == 200
    staff_id = create_staff.json()["staff"]["id"]

    update_staff = api_client.put(
        f"/complaints/staff/{staff_id}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"whatsapp_number": "+923118899001"},
    )
    assert update_staff.status_code == 200
    assert update_staff.json()["staff"]["whatsapp_number"] == "+923118899001"

    # Verify lists
    depts = api_client.get("/complaints/departments", headers={"X-Tenant-Id": CANTONMENT_TENANT_ID}).json()
    assert any(d["id"] == dept_id for d in depts["departments"])

    areas = api_client.get(f"/complaints/areas?department_id={dept_id}", headers={"X-Tenant-Id": CANTONMENT_TENANT_ID}).json()
    assert any(a["id"] == area_id for a in areas["areas"])

    staff_list = api_client.get(f"/complaints/staff?department_id={dept_id}", headers={"X-Tenant-Id": CANTONMENT_TENANT_ID}).json()
    assert any(s["id"] == staff_id for s in staff_list["staff"])


def test_analytics_and_settings_http(api_client):
    # Analytics
    analytics_res = api_client.get(
        "/complaints/analytics",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert analytics_res.status_code == 200
    an = analytics_res.json()
    assert "total_complaints" in an
    assert "closed_complaints" in an
    assert "sla_compliance_rate" in an
    assert "sla_breach_rate" in an
    assert "reopen_rate" in an
    assert "customer_satisfaction_avg" in an
    assert "by_department" in an
    assert "by_category" in an
    assert "by_area" in an
    assert "staff_workload" in an

    # Settings
    settings_res = api_client.get(
        "/complaints/settings",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert settings_res.status_code == 200
    assert "auto_dispatch_threshold" in settings_res.json()

    put_settings = api_client.put(
        "/complaints/settings",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={"auto_dispatch_threshold": 92.5, "warning_sla_hours": 4},
    )
    assert put_settings.status_code == 200
    assert put_settings.json()["settings"]["auto_dispatch_threshold"] == 92.5
    assert put_settings.json()["settings"]["warning_sla_hours"] == 4


def test_tick_sla_and_manual_escalate_http(api_client):
    tick_res = api_client.post(
        "/complaints/tick-sla",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert tick_res.status_code == 200
    assert "escalations_triggered" in tick_res.json()

    # Manual escalate
    list_res = api_client.get("/complaints", headers={"X-Tenant-Id": CANTONMENT_TENANT_ID})
    cid = list_res.json()["complaints"][0]["id"]

    esc_res = api_client.post(
        f"/complaints/{cid}/escalate",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "level": "tier_3_senior_mgmt",
            "notified_to": "Station Commander / Executive Officer",
            "action_taken": "High priority alert sent via SMS and Control Room banner",
        },
    )
    assert esc_res.status_code == 200
    assert esc_res.json()["escalation"]["level"] == "tier_3_senior_mgmt"


def test_google_sync_csv_export_http(api_client):
    status_res = api_client.get(
        "/complaints/google-sync/status",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert status_res.status_code == 200
    assert status_res.json()["sync_enabled"] is True

    csv_res = api_client.get(
        "/complaints/google-sync/export",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert csv_res.status_code == 200
    assert csv_res.headers["content-type"].startswith("text/csv")
    assert "Complaint ID,Customer,WhatsApp Number" in csv_res.text


def test_complaints_filtering_and_search_http(api_client):
    # Filter by category
    cat_res = api_client.get(
        "/complaints?category=Sanitation%20%26%20Solid%20Waste",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID},
    )
    assert cat_res.status_code == 200
    assert all("Sanitation" in c["category"] for c in cat_res.json()["complaints"])

    # Search query
    q_res = api_client.get(
        "/complaints?q=Mall",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID},
    )
    assert q_res.status_code == 200
    assert len(q_res.json()["complaints"]) >= 1



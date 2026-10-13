"""Comprehensive tests for Cantonment Board Evidence Photo Upload, Safe Storage,
WhatsApp Image Intake, and Vision AI Fallback/Lifecycle Integration.
"""

import io
import os
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.complaints import make_complaints_router
from app.complaints_store import COMPLAINT_STORE, CANTONMENT_TENANT_ID
from app.storage import (
    DEFAULT_STORAGE,
    save_evidence_file,
    get_evidence_file_path,
    StorageValidationError,
)
from app.complaint_ai import ComplaintAnalyzer, VisionInspector


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(make_complaints_router())
    return TestClient(app)


# Sample valid image byte fixtures (valid magic bytes)
VALID_JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00"
    + b"SAMPLE_JPEG_BODY_PIXELS_FOR_TESTING" * 10
)
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    + b"SAMPLE_PNG_DATA" * 10
)
INVALID_EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00THIS_IS_NOT_AN_IMAGE"


def test_upload_valid_jpeg(client):
    """Citizen or operator can upload a valid JPEG photo and receive a safe local file URL."""
    files = {"file": ("citizen_garbage.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")}
    data = {"caption": "Piled up garbage dump", "label": "CUSTOMER EVIDENCE"}

    res = client.post(
        "/complaints/upload",
        files=files,
        data=data,
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["file_url"].startswith("/complaints/media/")
    assert body["content_type"] == "image/jpeg"
    assert body["file_size_bytes"] == len(VALID_JPEG_BYTES)
    assert body["label"] == "CUSTOMER EVIDENCE"

    # Verify retrieval
    media_res = client.get(
        body["file_url"],
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert media_res.status_code == 200
    assert media_res.headers["content-type"] == "image/jpeg"
    assert len(media_res.content) == len(VALID_JPEG_BYTES)


def test_upload_valid_png(client):
    """Uploads PNG successfully with correct mime type and safe filename."""
    files = {"file": ("damaged_light.png", io.BytesIO(VALID_PNG_BYTES), "image/png")}
    res = client.post(
        "/complaints/upload",
        files=files,
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["content_type"] == "image/png"
    assert body["filename"].endswith(".png")


def test_upload_invalid_mime_rejected(client):
    """Rejects malicious / non-image payloads even if named .jpg."""
    files = {"file": ("trojan.jpg", io.BytesIO(INVALID_EXE_BYTES), "image/jpeg")}
    res = client.post(
        "/complaints/upload",
        files=files,
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 400
    assert "Invalid or unsupported image format" in res.json()["detail"]


def test_upload_oversized_file_rejected(client, monkeypatch):
    """Enforces MAX_UPLOAD_SIZE_BYTES limits."""
    # Temporarily set max upload to 1KB for fast test
    monkeypatch.setattr("app.storage.MAX_UPLOAD_SIZE_BYTES", 1024)
    huge_data = VALID_JPEG_BYTES + (b"X" * 2048)

    files = {"file": ("huge.jpg", io.BytesIO(huge_data), "image/jpeg")}
    res = client.post(
        "/complaints/upload",
        files=files,
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 400
    assert "exceeds maximum allowed limit" in res.json()["detail"]


def test_path_traversal_guards(client):
    """Rejects path traversal attempts both at storage layer and HTTP endpoint."""
    # 1. Direct storage layer validation
    assert get_evidence_file_path("..") is None
    assert get_evidence_file_path("../main.py") is None
    assert get_evidence_file_path("../../etc/passwd") is None
    assert get_evidence_file_path("cmp_..\\secret.key") is None

    # 2. HTTP endpoint rejects non-existent or malicious paths
    bad_urls = [
        "/complaints/media/non_existent_file.jpg",
        "/complaints/media/%2e%2e%2fmain.py",
        "/complaints/media/malicious_payload.sh",
    ]
    for url in bad_urls:
        res = client.get(url)
        assert res.status_code == 404


def test_whatsapp_intake_with_uploaded_photo_and_vision_fallback(client, monkeypatch):
    """WhatsApp intake accepts photo evidence, preserves it, and falls back gracefully when Vision AI is not configured."""
    # Ensure no vision provider is set
    monkeypatch.setenv("VISION_PROVIDER", "none")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    # 1. Upload evidence photo first
    up_res = client.post(
        "/complaints/upload",
        files={"file": ("dump.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")},
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    photo_url = up_res.json()["file_url"]

    # 2. Ingest via WhatsApp intake endpoint
    intake_payload = {
        "customer_name": "Tariq Mahmood",
        "customer_phone": "+923005544332",
        "message": "Huge pile of garbage accumulated near Mall Road market entrance for 2 days.",
        "location_hint": "Mall Road Sector",
        "media_urls": [photo_url],
        "whatsapp_message_id": "wa_msg_987654321",
    }
    res = client.post(
        "/complaints/whatsapp/intake",
        json=intake_payload,
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    cid = body["complaint"]["id"]

    # Verify AI analysis output
    ai = body["ai_analysis"]
    assert "image_analysis" in ai
    assert ai["image_analysis"]["status"] == "unavailable"
    assert "not configured" in ai["image_analysis"]["reason"]

    # Grounded classification succeeded on text + location
    assert ai["department_name"] == "Sanitation Department"
    assert ai["area_name"] == "Mall Road Sector"

    # 3. Retrieve complaint details and verify customer evidence is attached and intact
    detail_res = client.get(
        f"/complaints/{cid}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert detail_res.status_code == 200
    c = detail_res.json()
    assert len(c["customer_evidence"]) >= 1
    assert c["customer_evidence"][0]["file_url"] == photo_url
    assert c["customer_evidence"][0]["label"] == "CUSTOMER EVIDENCE"
    assert len(c["before_after_pairs"]) >= 1
    assert c["before_after_pairs"][0]["before_url"] == photo_url
    assert c["before_after_pairs"][0]["after_url"] is None  # Pending resolution proof


def test_vision_ai_hazard_detection_adjusts_urgency_and_priority(client, monkeypatch):
    """When Vision AI identifies a hazard, urgency and priority are heightened while staying grounded."""
    # Mock VisionInspector.inspect_images to simulate a live vision model detection
    def mock_inspect(image_urls, tenant_id=None):
        return {
            "status": "success",
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "visible_issue_type": "Live sparking electrical wire dangling from street pole near footpath",
            "observations": "Exposed high-voltage wire hanging at eye level over commercial pavement.",
            "hazard_detected": True,
            "visual_severity": "critical",
            "confidence": 98.0,
            "suggested_category": "Streetlights & Electrical",
            "reason": None,
            "analyzed_at": "2026-10-13T12:00:00Z",
        }

    monkeypatch.setattr(VisionInspector, "inspect_images", mock_inspect)

    intake_payload = {
        "customer_name": "Kashif Khan",
        "customer_phone": "+923331122334",
        "message": "Streetlight pole issue near Cantt Bazaar.",
        "location_hint": "Cantt Bazaar",
        "media_urls": ["/complaints/media/cmp_test_sparking_wire.jpg"],
    }
    res = client.post(
        "/complaints/whatsapp/intake",
        json=intake_payload,
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    data = res.json()
    ai = data["ai_analysis"]

    assert ai["priority"] == "urgent"
    assert ai["urgency"] == "critical"
    assert ai["image_analysis"]["status"] == "success"
    assert ai["image_analysis"]["hazard_detected"] is True
    assert "Live sparking electrical wire" in ai["image_analysis"]["visible_issue_type"]


def test_end_to_end_resolution_proof_persistence_and_before_after_pairs(client):
    """Tests full lifecycle: citizen evidence photo -> in progress -> field resolution proof -> dept verification -> feedback."""
    # 1. Upload citizen BEFORE evidence
    before_up = client.post(
        "/complaints/upload",
        files={"file": ("broken_pothole_before.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")},
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    before_url = before_up.json()["file_url"]

    # 2. Create complaint ticket with photo
    create_res = client.post(
        "/complaints",
        json={
            "customer_name": "Salma Begum",
            "customer_phone": "+923008889900",
            "raw_message": "Large asphalt pothole damaged road surface.",
            "category": "Road Damage & Potholes",
            "location_name": "R.A. Bazaar Sector",
            "department_id": "d1111111-0000-0000-0000-000000000004",
            "area_id": "a1111111-0000-0000-0000-000000000004",
            "assigned_staff_id": "s1111111-0000-0000-0000-000000000007",
            "priority": "high",
            "urgency": "high",
            "media_urls": [before_url],
        },
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert create_res.status_code == 200
    cid = create_res.json()["complaint"]["id"]

    # 3. Mark in progress
    start_res = client.post(
        f"/complaints/{cid}/start",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert start_res.status_code == 200
    assert start_res.json()["complaint"]["status"] == "IN_PROGRESS"

    # 4. Upload field staff AFTER resolution proof
    after_up = client.post(
        "/complaints/upload",
        files={"file": ("repaired_asphalt_after.jpg", io.BytesIO(VALID_JPEG_BYTES), "image/jpeg")},
        data={"caption": "Cold mix asphalt patch laid and roller compacted", "label": "RESOLUTION PROOF"},
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert after_up.status_code == 200
    after_url = after_up.json()["file_url"]

    # 5. Submit resolution with proof
    res_sub = client.post(
        f"/complaints/{cid}/resolve",
        json={
            "resolved_by_staff_id": "s1111111-0000-0000-0000-000000000007",
            "resolved_by_name": "Tariq Mehmood (Engineering Road Crew)",
            "resolution_description": "Filled 4x3ft crater with bitumen hot mix and compacted with vibratory roller.",
            "resolution_proof_urls": [after_url],
            "remarks": "Traffic flow restored safely.",
        },
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res_sub.status_code == 200
    assert res_sub.json()["complaint"]["status"] == "PENDING_DEPARTMENT_VERIFICATION"

    # 6. Verify before / after photo pairs in ticket detail
    det_res = client.get(
        f"/complaints/{cid}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    c_det = det_res.json()
    assert len(c_det["before_after_pairs"]) >= 1
    pair = c_det["before_after_pairs"][0]
    assert pair["before_url"] == before_url
    assert pair["after_url"] == after_url

    # 7. Department supervisor approves resolution
    verify_res = client.post(
        f"/complaints/{cid}/department-verify",
        json={"approved": True, "verified_by_name": "Sohail Abbasi (Civil Eng Supervisor)"},
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["complaint"]["status"] == "PENDING_CUSTOMER_CONFIRMATION"

    # 8. Citizen confirms closure via WhatsApp feedback loop
    fb_res = client.post(
        f"/complaints/{cid}/customer-feedback",
        json={"customer_confirmed": True, "rating": 5, "feedback_text": "Excellent road repair!"},
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert fb_res.status_code == 200
    assert fb_res.json()["complaint"]["status"] == "CLOSED"

    # Evidence and proof photos remain preserved after ticket close
    final_det = client.get(
        f"/complaints/{cid}",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    ).json()
    assert final_det["status"] == "CLOSED"
    assert final_det["before_after_pairs"][0]["before_url"] == before_url
    assert final_det["before_after_pairs"][0]["after_url"] == after_url

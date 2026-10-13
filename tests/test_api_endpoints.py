"""End-to-End API Integration Tests for Cantonment Board Complaint Routes."""

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.complaints_store import CANTONMENT_TENANT_ID


@pytest.fixture
def client():
    return TestClient(app)


def test_health_endpoint(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["service"] == "techdeva"


def test_complaints_summary_endpoint(client):
    res = client.get(
        "/complaints/summary",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "total" in data
    assert "in_progress" in data
    assert "sla_at_risk" in data
    assert "zero_touch_percentage" in data


def test_complaints_list_endpoint(client):
    res = client.get(
        "/complaints",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "complaints" in data
    assert len(data["complaints"]) >= 4
    # Verify CMP ticket structure
    assert data["complaints"][0]["ticket_number"].startswith("CMP-")


def test_whatsapp_intake_api(client):
    res = client.post(
        "/complaints/whatsapp/intake",
        headers={"X-Tenant-Id": CANTONMENT_TENANT_ID, "X-Role": "operator"},
        json={
            "customer_name": "Kashif Mehmood",
            "customer_phone": "+923004455667",
            "message": "Overflowing garbage dumpster near market on Mall Road with stray dogs",
            "media_urls": ["https://images.unsplash.com/photo-1605600659873-d808a13e4d2a"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["complaint"]["category"] == "Sanitation & Solid Waste"
    assert data["complaint"]["ticket_number"].startswith("CMP-")
    assert "whatsapp_reply" in data
    assert "CMP-" in data["whatsapp_reply"]


def test_control_room_html_page(client):
    res = client.get("/control-room")
    assert res.status_code == 200
    assert "CANTONMENT BOARD" in res.text
    assert "Zero-Touch Operations" in res.text
    assert "CUSTOMER EVIDENCE" in res.text
    assert "RESOLUTION PROOF" in res.text

"""Tests for policyfl.api — FastAPI consent management endpoints."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from policyfl.api import create_app
from policyfl.audit import JSONAuditLogger
from policyfl.consent_store import JSONConsentStore


@pytest.fixture
def store(tmp_path):
    now = datetime.now(timezone.utc)
    data = {
        "consents": [
            {
                "subject_id": "person_001",
                "device_ids": ["sensor_A", "sensor_B"],
                "purposes": ["energy_optimization"],
                "granted_at": (now - timedelta(days=10)).isoformat(),
                "expires_at": None,
                "revoked": False,
                "revoked_at": None,
            },
        ]
    }
    path = tmp_path / "consents.json"
    path.write_text(json.dumps(data))
    return JSONConsentStore(path)


@pytest.fixture
def audit_logger(tmp_path):
    return JSONAuditLogger(tmp_path / "audit.json")


@pytest.fixture
def client(store, audit_logger):
    app = create_app(store, audit_logger=audit_logger)
    return TestClient(app)


@pytest.fixture
def client_no_audit(store):
    app = create_app(store)
    return TestClient(app)


class TestGrantConsent:
    def test_grant_new_consent(self, client):
        resp = client.post(
            "/consent/grant",
            json={
                "subject_id": "person_099",
                "device_ids": ["sensor_X"],
                "purposes": ["occupancy_counting"],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["subject_id"] == "person_099"
        assert body["device_ids"] == ["sensor_X"]
        assert body["revoked"] is False

    def test_grant_with_expiry(self, client):
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        resp = client.post(
            "/consent/grant",
            json={
                "subject_id": "person_100",
                "device_ids": ["sensor_Y"],
                "purposes": ["energy_optimization"],
                "expires_at": expires,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["expires_at"] is not None

    def test_granted_consent_is_queryable(self, client):
        client.post(
            "/consent/grant",
            json={
                "subject_id": "person_new",
                "device_ids": ["sensor_Z"],
                "purposes": ["energy_optimization"],
            },
        )
        resp = client.get("/consent/status/person_new")
        assert resp.status_code == 200
        assert len(resp.json()) == 1


class TestRevokeConsent:
    def test_revoke_existing(self, client):
        resp = client.post(
            "/consent/revoke",
            json={"subject_id": "person_001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "revoked"
        assert body["subject_id"] == "person_001"

    def test_revoke_specific_purpose(self, client):
        # Grant multi-purpose consent first
        client.post(
            "/consent/grant",
            json={
                "subject_id": "person_multi",
                "device_ids": ["sensor_A"],
                "purposes": ["energy_optimization", "occupancy_counting"],
            },
        )
        resp = client.post(
            "/consent/revoke",
            json={"subject_id": "person_multi", "purpose": "occupancy_counting"},
        )
        assert resp.status_code == 200
        assert resp.json()["purpose"] == "occupancy_counting"

    def test_revoke_nonexistent_subject(self, client):
        resp = client.post(
            "/consent/revoke",
            json={"subject_id": "nobody"},
        )
        assert resp.status_code == 404


class TestConsentStatus:
    def test_get_existing_subject(self, client):
        resp = client.get("/consent/status/person_001")
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 1
        assert records[0]["subject_id"] == "person_001"

    def test_get_nonexistent_subject(self, client):
        resp = client.get("/consent/status/nobody")
        assert resp.status_code == 404


class TestCheckConsent:
    def test_check_allowed(self, client):
        resp = client.get(
            "/consent/check",
            params={"device_id": "sensor_A", "purpose": "energy_optimization"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is True

    def test_check_denied(self, client):
        resp = client.get(
            "/consent/check",
            params={"device_id": "sensor_X", "purpose": "energy_optimization"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["allowed"] is False

    def test_check_wrong_purpose(self, client):
        resp = client.get(
            "/consent/check",
            params={"device_id": "sensor_A", "purpose": "activity_profiling"},
        )
        body = resp.json()
        assert body["allowed"] is False


class TestAuditEndpoint:
    def test_audit_log_empty(self, client):
        resp = client.get("/audit")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_audit_endpoint_without_logger(self, client_no_audit):
        resp = client_no_audit.get("/audit")
        assert resp.status_code == 404  # endpoint not registered

    def test_audit_log_after_operations(self, client, store, audit_logger):
        # Manually log some audit entries to simulate mod decisions
        from policyfl.audit import AuditEntry

        audit_logger.log(
            AuditEntry.from_decision(
                device_id="sensor_A",
                purpose="energy_optimization",
                allowed=True,
                reason="ok",
                subject_ids=["person_001"],
                round_id="1",
            )
        )
        audit_logger.log(
            AuditEntry.from_decision(
                device_id="sensor_X",
                purpose="energy_optimization",
                allowed=False,
                reason="no consent",
                subject_ids=[],
                round_id="2",
            )
        )

        resp = client.get("/audit")
        assert len(resp.json()) == 2

        resp = client.get("/audit", params={"decision": "DENY"})
        entries = resp.json()
        assert len(entries) == 1
        assert entries[0]["device_id"] == "sensor_X"

        resp = client.get("/audit", params={"device_id": "sensor_A"})
        assert len(resp.json()) == 1

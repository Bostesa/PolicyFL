"""Tests for policyfl.consent_store."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from policyfl.consent_store import JSONConsentStore


@pytest.fixture
def consent_data():
    now = datetime.now(timezone.utc)
    return {
        "consents": [
            {
                "subject_id": "person_001",
                "device_ids": ["sensor_A", "sensor_B"],
                "purposes": ["energy_optimization"],
                "granted_at": (now - timedelta(days=30)).isoformat(),
                "expires_at": (now + timedelta(days=60)).isoformat(),
                "revoked": False,
                "revoked_at": None,
            },
            {
                "subject_id": "person_002",
                "device_ids": ["sensor_A", "sensor_C"],
                "purposes": ["energy_optimization", "occupancy_counting"],
                "granted_at": (now - timedelta(days=10)).isoformat(),
                "expires_at": None,
                "revoked": False,
                "revoked_at": None,
            },
            {
                "subject_id": "person_003",
                "device_ids": ["sensor_D"],
                "purposes": ["energy_optimization"],
                "granted_at": (now - timedelta(days=90)).isoformat(),
                "expires_at": (now - timedelta(days=1)).isoformat(),
                "revoked": False,
                "revoked_at": None,
            },
        ]
    }


@pytest.fixture
def store(tmp_path, consent_data):
    path = tmp_path / "consents.json"
    path.write_text(json.dumps(consent_data))
    return JSONConsentStore(path)


class TestJSONConsentStore:
    def test_get_consents_for_device(self, store):
        consents = store.get_consents_for_device("sensor_A")
        assert len(consents) == 2
        subjects = {c.subject_id for c in consents}
        assert subjects == {"person_001", "person_002"}

    def test_get_consents_unknown_device(self, store):
        consents = store.get_consents_for_device("nonexistent")
        assert consents == []

    def test_check_consent_allowed(self, store):
        decision = store.check_consent("sensor_A", "energy_optimization")
        assert decision.allowed is True
        assert "person_001" in decision.subject_ids

    def test_check_consent_denied_no_records(self, store):
        decision = store.check_consent("unknown_device", "energy_optimization")
        assert decision.allowed is False
        assert "No consent records" in decision.reason

    def test_check_consent_denied_wrong_purpose(self, store):
        decision = store.check_consent("sensor_B", "activity_profiling")
        assert decision.allowed is False
        assert "did not consent to purpose" in decision.reason

    def test_check_consent_denied_expired(self, store):
        decision = store.check_consent("sensor_D", "energy_optimization")
        assert decision.allowed is False
        assert "expired" in decision.reason

    def test_revoke_consent(self, store):
        # Before revocation
        decision = store.check_consent("sensor_A", "energy_optimization")
        assert decision.allowed is True

        # Revoke person_001
        store.revoke_consent("person_001")

        # person_002 still has consent for sensor_A
        decision = store.check_consent("sensor_A", "energy_optimization")
        assert decision.allowed is True

        # Revoke person_002 too
        store.revoke_consent("person_002")
        decision = store.check_consent("sensor_A", "energy_optimization")
        assert decision.allowed is False

    def test_revoke_consent_specific_purpose(self, store):
        decision = store.check_consent("sensor_C", "occupancy_counting")
        assert decision.allowed is True

        store.revoke_consent("person_002", purpose="occupancy_counting")

        decision = store.check_consent("sensor_C", "occupancy_counting")
        assert decision.allowed is False

    def test_revoke_persists_to_file(self, tmp_path, consent_data):
        path = tmp_path / "consents.json"
        path.write_text(json.dumps(consent_data))

        store1 = JSONConsentStore(path)
        store1.revoke_consent("person_001")

        # Reload from file
        store2 = JSONConsentStore(path)
        consents = store2.get_consents_for_device("sensor_A")
        revoked = [c for c in consents if c.subject_id == "person_001"]
        assert revoked[0].revoked is True

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.json"
        # File doesn't exist yet
        store = JSONConsentStore(path)
        assert store.get_consents_for_device("any") == []

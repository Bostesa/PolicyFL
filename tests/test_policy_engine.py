"""Tests for policyfl.policy_engine."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from policyfl.consent_store import JSONConsentStore
from policyfl.policy_engine import SimpleEngine


@pytest.fixture
def engine(tmp_path):
    now = datetime.now(timezone.utc)
    data = {
        "consents": [
            {
                "subject_id": "person_001",
                "device_ids": ["sensor_A"],
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
    store = JSONConsentStore(path)
    return SimpleEngine(store)


class TestSimpleEngine:
    def test_evaluate_allowed(self, engine):
        decision = engine.evaluate("sensor_A", "energy_optimization")
        assert decision.allowed is True

    def test_evaluate_denied_no_consent(self, engine):
        decision = engine.evaluate("sensor_X", "energy_optimization")
        assert decision.allowed is False

    def test_evaluate_denied_wrong_purpose(self, engine):
        decision = engine.evaluate("sensor_A", "activity_profiling")
        assert decision.allowed is False

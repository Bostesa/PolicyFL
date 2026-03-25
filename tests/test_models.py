"""Tests for policyfl.models."""

from datetime import datetime, timedelta, timezone

from policyfl.models import ConsentRecord, PolicyDecision, Purpose


class TestPurpose:
    def test_create_purpose(self):
        p = Purpose(
            name="energy_optimization",
            description="Optimize HVAC",
            allowed_features=["motion", "temperature"],
        )
        assert p.name == "energy_optimization"
        assert p.allowed_features == ["motion", "temperature"]

    def test_purpose_no_features(self):
        p = Purpose(name="general", description="General purpose")
        assert p.allowed_features is None


class TestConsentRecord:
    def _make_consent(self, **overrides):
        defaults = dict(
            subject_id="person_001",
            device_ids=["sensor_A", "sensor_B"],
            purposes=["energy_optimization"],
            granted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return ConsentRecord(**defaults)

    def test_valid_consent(self):
        c = self._make_consent()
        assert c.is_valid("energy_optimization", "sensor_A") is True

    def test_wrong_device(self):
        c = self._make_consent()
        assert c.is_valid("energy_optimization", "sensor_X") is False

    def test_wrong_purpose(self):
        c = self._make_consent()
        assert c.is_valid("activity_profiling", "sensor_A") is False

    def test_revoked(self):
        c = self._make_consent(revoked=True)
        assert c.is_valid("energy_optimization", "sensor_A") is False

    def test_expired(self):
        c = self._make_consent(
            expires_at=datetime.now(timezone.utc) - timedelta(days=1)
        )
        assert c.is_valid("energy_optimization", "sensor_A") is False

    def test_not_yet_expired(self):
        c = self._make_consent(
            expires_at=datetime.now(timezone.utc) + timedelta(days=30)
        )
        assert c.is_valid("energy_optimization", "sensor_A") is True


class TestPolicyDecision:
    def test_allowed(self):
        d = PolicyDecision(allowed=True, reason="ok", subject_ids=["p1"])
        assert d.allowed is True
        assert d.timestamp is not None

    def test_denied(self):
        d = PolicyDecision(allowed=False, reason="no consent")
        assert d.allowed is False
        assert d.subject_ids == []

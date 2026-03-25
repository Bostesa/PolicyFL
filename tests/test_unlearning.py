"""Tests for policyfl.unlearning — federated unlearning hooks."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from policyfl.audit import AuditEntry, JSONAuditLogger
from policyfl.consent_store import JSONConsentStore
from policyfl.unlearning import TaintedRound, UnlearningTracker


def _make_store(tmp_path, consents):
    """Create a JSONConsentStore with the given consent records."""
    path = tmp_path / "consents.json"
    path.write_text(json.dumps({"consents": consents}))
    return JSONConsentStore(path)


def _make_consent(subject_id, device_ids, purposes, revoked=False):
    now = datetime.now(timezone.utc)
    return {
        "subject_id": subject_id,
        "device_ids": device_ids,
        "purposes": purposes,
        "granted_at": (now - timedelta(days=10)).isoformat(),
        "expires_at": None,
        "revoked": revoked,
        "revoked_at": now.isoformat() if revoked else None,
    }


@pytest.fixture
def setup(tmp_path):
    """Set up a store with two subjects and an audit logger with training history."""
    store = _make_store(
        tmp_path,
        [
            _make_consent("person_001", ["sensor_A", "sensor_B"], ["energy_optimization"]),
            _make_consent("person_002", ["sensor_A", "sensor_C"], ["energy_optimization", "occupancy_counting"]),
        ],
    )

    audit_path = tmp_path / "audit.json"
    audit_logger = JSONAuditLogger(audit_path)

    # Simulate training rounds that were ALLOWED
    # Round 1: sensor_A trained (person_001 + person_002 share this device)
    audit_logger.log(
        AuditEntry.from_decision(
            device_id="sensor_A",
            purpose="energy_optimization",
            allowed=True,
            reason="consent valid",
            subject_ids=["person_001", "person_002"],
            round_id="1",
        )
    )
    # Round 2: sensor_B trained (only person_001)
    audit_logger.log(
        AuditEntry.from_decision(
            device_id="sensor_B",
            purpose="energy_optimization",
            allowed=True,
            reason="consent valid",
            subject_ids=["person_001"],
            round_id="2",
        )
    )
    # Round 3: sensor_C trained for occupancy_counting (only person_002)
    audit_logger.log(
        AuditEntry.from_decision(
            device_id="sensor_C",
            purpose="occupancy_counting",
            allowed=True,
            reason="consent valid",
            subject_ids=["person_002"],
            round_id="3",
        )
    )
    # A DENY entry (should not appear in tainted rounds)
    audit_logger.log(
        AuditEntry.from_decision(
            device_id="sensor_X",
            purpose="energy_optimization",
            allowed=False,
            reason="no consent",
            subject_ids=[],
            round_id="4",
        )
    )

    return store, audit_logger


class TestOnConsentRevoked:
    def test_finds_tainted_rounds(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        tainted = tracker.on_consent_revoked("person_001")

        round_ids = {t.round_id for t in tainted}
        assert round_ids == {"1", "2"}

    def test_purpose_scoped_revocation(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        # person_002 revokes only occupancy_counting
        tainted = tracker.on_consent_revoked("person_002", purpose="occupancy_counting")

        round_ids = {t.round_id for t in tainted}
        # Only round 3 used person_002's data for occupancy_counting
        assert round_ids == {"3"}

    def test_no_tainted_rounds_for_unknown_subject(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        tainted = tracker.on_consent_revoked("nobody")
        assert tainted == []

    def test_no_duplicates_on_repeated_revocation(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        first = tracker.on_consent_revoked("person_001")
        second = tracker.on_consent_revoked("person_001")

        assert len(first) == 2
        assert len(second) == 0  # already flagged

    def test_tainted_round_has_correct_reason(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        tainted = tracker.on_consent_revoked("person_001")

        for t in tainted:
            assert "person_001" in t.reason
            assert t.subject_ids == ["person_001"]

    def test_ignores_deny_entries(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        # sensor_X had a DENY in round 4, should never appear
        tainted = tracker.on_consent_revoked("person_001")
        round_ids = {t.round_id for t in tainted}
        assert "4" not in round_ids


class TestGetTaintedRounds:
    def test_accumulates_across_subjects(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        tracker.on_consent_revoked("person_001")
        tracker.on_consent_revoked("person_002")

        all_tainted = tracker.get_tainted_rounds()
        round_ids = {t.round_id for t in all_tainted}
        # person_001 taints rounds 1, 2
        # person_002 taints rounds 1, 3 (round 1 is shared but different subject)
        assert {"1", "2", "3"}.issubset(round_ids)

    def test_empty_when_nothing_revoked(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        assert tracker.get_tainted_rounds() == []


class TestClearTaintedRound:
    def test_clear_removes_round(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        tracker.on_consent_revoked("person_001")
        assert len(tracker.get_tainted_rounds()) == 2

        tracker.clear_tainted_round("1")
        remaining = tracker.get_tainted_rounds()
        remaining_ids = {t.round_id for t in remaining}
        assert "1" not in remaining_ids
        assert "2" in remaining_ids

    def test_clear_nonexistent_round_is_noop(self, setup):
        store, audit_logger = setup
        tracker = UnlearningTracker(audit_logger, store)

        tracker.on_consent_revoked("person_001")
        tracker.clear_tainted_round("999")  # should not error
        assert len(tracker.get_tainted_rounds()) == 2


class TestUnlearningIntegration:
    def test_revoke_then_track(self, tmp_path):
        """Full flow: grant consent, train, revoke, track tainted rounds."""
        store = _make_store(
            tmp_path,
            [_make_consent("person_X", ["dev_1"], ["energy_optimization"])],
        )
        audit_logger = JSONAuditLogger(tmp_path / "audit.json")

        # Simulate 3 training rounds on dev_1
        for r in range(1, 4):
            audit_logger.log(
                AuditEntry.from_decision(
                    device_id="dev_1",
                    purpose="energy_optimization",
                    allowed=True,
                    reason="ok",
                    subject_ids=["person_X"],
                    round_id=str(r),
                )
            )

        # Revoke consent
        store.revoke_consent("person_X")

        # Track tainted rounds
        tracker = UnlearningTracker(audit_logger, store)
        tainted = tracker.on_consent_revoked("person_X")

        assert len(tainted) == 3
        assert {t.round_id for t in tainted} == {"1", "2", "3"}

        # Clear round 2 (unlearning done)
        tracker.clear_tainted_round("2")
        remaining = tracker.get_tainted_rounds()
        assert {t.round_id for t in remaining} == {"1", "3"}

"""Tests for policyfl.audit."""

import json

import pytest

from policyfl.audit import AuditEntry, JSONAuditLogger


class TestAuditEntry:
    def test_from_decision_allow(self):
        entry = AuditEntry.from_decision(
            device_id="sensor_A",
            purpose="energy_optimization",
            allowed=True,
            reason="consent valid",
            subject_ids=["person_001"],
            round_id="5",
        )
        assert entry.decision == "ALLOW"
        assert entry.device_id == "sensor_A"
        assert entry.round_id == "5"
        assert entry.timestamp  # non-empty ISO string

    def test_from_decision_deny(self):
        entry = AuditEntry.from_decision(
            device_id="sensor_X",
            purpose="profiling",
            allowed=False,
            reason="no consent",
            subject_ids=[],
        )
        assert entry.decision == "DENY"
        assert entry.round_id is None


class TestJSONAuditLogger:
    def test_log_and_retrieve(self, tmp_path):
        path = tmp_path / "audit.json"
        logger = JSONAuditLogger(path)

        entry = AuditEntry.from_decision(
            device_id="sensor_A",
            purpose="energy_optimization",
            allowed=True,
            reason="ok",
            subject_ids=["person_001"],
            round_id="1",
        )
        logger.log(entry)

        log = logger.get_log()
        assert len(log) == 1
        assert log[0].device_id == "sensor_A"
        assert log[0].decision == "ALLOW"

    def test_multiple_entries(self, tmp_path):
        path = tmp_path / "audit.json"
        logger = JSONAuditLogger(path)

        for i, allowed in enumerate([True, False, True, False]):
            logger.log(
                AuditEntry.from_decision(
                    device_id=f"sensor_{i}",
                    purpose="energy_optimization",
                    allowed=allowed,
                    reason="test",
                    subject_ids=[],
                    round_id=str(i),
                )
            )

        assert len(logger.get_log()) == 4
        assert len(logger.get_log(decision="ALLOW")) == 2
        assert len(logger.get_log(decision="DENY")) == 2

    def test_filter_by_device(self, tmp_path):
        path = tmp_path / "audit.json"
        logger = JSONAuditLogger(path)

        for dev in ["sensor_A", "sensor_B", "sensor_A"]:
            logger.log(
                AuditEntry.from_decision(
                    device_id=dev,
                    purpose="energy_optimization",
                    allowed=True,
                    reason="ok",
                    subject_ids=[],
                )
            )

        assert len(logger.get_log(device_id="sensor_A")) == 2
        assert len(logger.get_log(device_id="sensor_B")) == 1

    def test_filter_by_purpose(self, tmp_path):
        path = tmp_path / "audit.json"
        logger = JSONAuditLogger(path)

        for purpose in ["energy", "occupancy", "energy"]:
            logger.log(
                AuditEntry.from_decision(
                    device_id="s",
                    purpose=purpose,
                    allowed=True,
                    reason="ok",
                    subject_ids=[],
                )
            )

        assert len(logger.get_log(purpose="energy")) == 2

    def test_persists_to_file(self, tmp_path):
        path = tmp_path / "audit.json"
        logger1 = JSONAuditLogger(path)
        logger1.log(
            AuditEntry.from_decision(
                device_id="sensor_A",
                purpose="energy",
                allowed=True,
                reason="ok",
                subject_ids=["p1"],
                round_id="1",
            )
        )

        # Reload from file
        logger2 = JSONAuditLogger(path)
        log = logger2.get_log()
        assert len(log) == 1
        assert log[0].device_id == "sensor_A"
        assert log[0].subject_ids == ["p1"]

    def test_empty_file(self, tmp_path):
        path = tmp_path / "audit.json"
        logger = JSONAuditLogger(path)
        assert logger.get_log() == []


class TestModAuditIntegration:
    """Test that mod.py actually writes audit entries."""

    def test_mod_logs_allow_and_deny(self, tmp_path):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock

        from flwr.common import Context, Message, RecordDict

        from policyfl.consent_store import JSONConsentStore
        from policyfl.mod import make_policyfl_mod
        from policyfl.policy_engine import SimpleEngine

        # Set up consent store
        now = datetime.now(timezone.utc)
        consent_path = tmp_path / "consents.json"
        consent_path.write_text(
            json.dumps(
                {
                    "consents": [
                        {
                            "subject_id": "person_001",
                            "device_ids": ["sensor_A"],
                            "purposes": ["energy_optimization"],
                            "granted_at": (now - timedelta(days=10)).isoformat(),
                            "expires_at": None,
                            "revoked": False,
                            "revoked_at": None,
                        }
                    ]
                }
            )
        )
        store = JSONConsentStore(consent_path)
        engine = SimpleEngine(store)

        # Set up audit logger
        audit_path = tmp_path / "audit.json"
        audit_logger = JSONAuditLogger(audit_path)

        mod = make_policyfl_mod(engine, audit_logger=audit_logger)

        def make_ctx(device_id):
            return Context(
                run_id=42,
                node_id=0,
                node_config={"device_id": device_id},
                state=RecordDict(),
                run_config={"purpose": "energy_optimization"},
            )

        msg = Message(content=RecordDict(), dst_node_id=0, message_type="train")

        # Allowed call
        call_next = MagicMock(return_value=Message(RecordDict(), reply_to=msg))
        mod(msg, make_ctx("sensor_A"), call_next)

        # Denied call
        call_next = MagicMock()
        mod(msg, make_ctx("sensor_X"), call_next)

        log = audit_logger.get_log()
        assert len(log) == 2
        assert log[0].decision == "ALLOW"
        assert log[0].round_id == "42"
        assert log[1].decision == "DENY"
        assert log[1].round_id == "42"

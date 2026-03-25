"""Tests for policyfl.mod — the Flower Mod function."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from flwr.common import Context, Message, RecordDict

from policyfl.consent_store import JSONConsentStore
from policyfl.mod import make_policyfl_mod
from policyfl.policy_engine import SimpleEngine


def _make_context(node_id=1, device_id="sensor_A", purpose="energy_optimization"):
    """Create a minimal Flower Context for testing."""
    node_config = {}
    if device_id:
        node_config["device_id"] = device_id

    run_config = {}
    if purpose:
        run_config["purpose"] = purpose

    return Context(
        run_id=0,
        node_id=node_id,
        node_config=node_config,
        state=RecordDict(),
        run_config=run_config,
    )


def _make_message():
    """Create a minimal Flower Message for testing."""
    return Message(content=RecordDict(), dst_node_id=0, message_type="train")


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
def engine(store):
    return SimpleEngine(store)


class TestPolicyFLMod:
    def test_consented_device_trains(self, engine):
        """A device with valid consent should proceed to training."""
        mod = make_policyfl_mod(engine)
        msg = _make_message()
        context = _make_context(device_id="sensor_A", purpose="energy_optimization")

        call_next = MagicMock(return_value=Message(RecordDict(), reply_to=msg))
        result = mod(msg, context, call_next)

        call_next.assert_called_once_with(msg, context)

    def test_non_consented_device_blocked(self, engine):
        """A device without consent should be blocked from training."""
        mod = make_policyfl_mod(engine)
        msg = _make_message()
        context = _make_context(device_id="sensor_X", purpose="energy_optimization")

        call_next = MagicMock()
        result = mod(msg, context, call_next)

        call_next.assert_not_called()
        assert result.has_content()

    def test_wrong_purpose_blocked(self, engine):
        """A device requesting a non-consented purpose should be blocked."""
        mod = make_policyfl_mod(engine)
        msg = _make_message()
        context = _make_context(device_id="sensor_A", purpose="activity_profiling")

        call_next = MagicMock()
        result = mod(msg, context, call_next)

        call_next.assert_not_called()

    def test_no_purpose_blocked(self, engine):
        """Missing purpose should result in denial."""
        mod = make_policyfl_mod(engine)
        msg = _make_message()
        context = _make_context(device_id="sensor_A", purpose="")

        call_next = MagicMock()
        result = mod(msg, context, call_next)

        call_next.assert_not_called()

    def test_revoked_consent_blocked(self, store, engine):
        """After revoking consent, training should be blocked."""
        mod = make_policyfl_mod(engine)
        msg = _make_message()
        context = _make_context(device_id="sensor_A", purpose="energy_optimization")

        # First: should be allowed
        call_next = MagicMock(return_value=Message(RecordDict(), reply_to=msg))
        mod(msg, context, call_next)
        call_next.assert_called_once()

        # Revoke
        store.revoke_consent("person_001")

        # Now: should be blocked
        call_next = MagicMock()
        mod(msg, context, call_next)
        call_next.assert_not_called()

    def test_fallback_to_node_id(self, engine):
        """When no device_id in node_config, fall back to node_id."""
        mod = make_policyfl_mod(engine)
        msg = _make_message()
        # No device_id in node_config — will use str(node_id) = "42"
        context = _make_context(device_id="", purpose="energy_optimization")
        context._node_id = 42  # noqa: SLF001

        call_next = MagicMock()
        result = mod(msg, context, call_next)

        # node_id=42 has no consent record, so should be denied
        call_next.assert_not_called()

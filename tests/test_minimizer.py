"""Tests for policyfl.minimizer — GDPR Article 5(1)(c) data minimization."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pytest
from flwr.common import Array, Context, Message, RecordDict
from flwr.common.record import ArrayRecord

from policyfl.consent_store import JSONConsentStore
from policyfl.minimizer import DataMinimizer, FilterResult
from policyfl.mod import make_policyfl_mod
from policyfl.models import Purpose
from policyfl.policy_engine import SimpleEngine


# --- Purpose definitions ---

PURPOSES = {
    "energy_optimization": Purpose(
        name="energy_optimization",
        description="Optimize HVAC and lighting based on occupancy patterns",
        allowed_features=["motion", "temperature", "humidity"],
    ),
    "occupancy_counting": Purpose(
        name="occupancy_counting",
        description="Count number of people in spaces",
        allowed_features=["motion", "depth"],
    ),
    "activity_profiling": Purpose(
        name="activity_profiling",
        description="Track individual activity patterns",
        allowed_features=["motion", "camera", "audio"],
    ),
    "general_research": Purpose(
        name="general_research",
        description="General research — all features allowed",
        allowed_features=None,
    ),
}


def _feature_array(values):
    """Create an ArrayRecord containing a single data array."""
    return ArrayRecord({"data": Array(np.array(values, dtype=np.float64))})


def _make_feature_message(features):
    """Create a Message with feature data as top-level ArrayRecord entries."""
    rd = RecordDict()
    for name, values in features.items():
        rd[name] = _feature_array(values)
    return Message(content=rd, dst_node_id=0, message_type="train")


# --- DataMinimizer unit tests ---


class TestGetAllowedFeatures:
    def test_known_purpose(self):
        dm = DataMinimizer(PURPOSES)
        allowed = dm.get_allowed_features("energy_optimization")
        assert allowed == {"motion", "temperature", "humidity"}

    def test_unknown_purpose(self):
        dm = DataMinimizer(PURPOSES)
        assert dm.get_allowed_features("unknown") is None

    def test_purpose_with_no_feature_restrictions(self):
        dm = DataMinimizer(PURPOSES)
        assert dm.get_allowed_features("general_research") is None


class TestFilterRecordDict:
    def test_strips_disallowed_features(self):
        """Core scenario: [motion, temperature, camera, audio] → [motion, temperature]
        for energy_optimization (allowed: motion, temperature, humidity)."""
        dm = DataMinimizer(PURPOSES)
        msg = _make_feature_message({
            "motion": [1.0, 2.0, 3.0],
            "temperature": [22.5, 23.1],
            "camera": [0.1, 0.2, 0.3, 0.4],
            "audio": [0.5, 0.6, 0.7],
        })

        result = dm.filter_record_dict("energy_optimization", msg.content)

        assert set(result.kept) == {"motion", "temperature"}
        assert set(result.removed) == {"camera", "audio"}
        # Verify message content was actually modified
        remaining = set(msg.content.array_records.keys())
        assert remaining == {"motion", "temperature"}

    def test_keeps_all_when_all_allowed(self):
        dm = DataMinimizer(PURPOSES)
        msg = _make_feature_message({
            "motion": [1.0],
            "temperature": [22.5],
        })

        result = dm.filter_record_dict("energy_optimization", msg.content)

        assert result.removed == []
        assert set(result.kept) == {"motion", "temperature"}

    def test_no_filtering_when_allowed_features_is_none(self):
        dm = DataMinimizer(PURPOSES)
        msg = _make_feature_message({
            "motion": [1.0],
            "camera": [0.1],
            "audio": [0.5],
        })

        result = dm.filter_record_dict("general_research", msg.content)

        assert result.removed == []
        assert set(result.kept) == {"motion", "camera", "audio"}

    def test_no_filtering_for_unknown_purpose(self):
        dm = DataMinimizer(PURPOSES)
        msg = _make_feature_message({"motion": [1.0], "camera": [0.1]})

        result = dm.filter_record_dict("unknown_purpose", msg.content)

        assert result.removed == []
        assert set(msg.content.array_records.keys()) == {"motion", "camera"}

    def test_strips_all_when_none_allowed(self):
        """If purpose allows features not present in the message, all get stripped."""
        dm = DataMinimizer(PURPOSES)
        msg = _make_feature_message({
            "camera": [0.1],
            "audio": [0.5],
        })

        result = dm.filter_record_dict("energy_optimization", msg.content)

        assert set(result.removed) == {"camera", "audio"}
        assert result.kept == []
        assert len(msg.content.array_records) == 0

    def test_preserves_config_records(self):
        """ConfigRecord entries should never be filtered."""
        from flwr.common import ConfigRecord

        dm = DataMinimizer(PURPOSES)
        rd = RecordDict()
        rd["motion"] = _feature_array([1.0])
        rd["camera"] = _feature_array([0.1])
        rd["metadata"] = ConfigRecord({"purpose": "energy_optimization"})

        result = dm.filter_record_dict("energy_optimization", rd)

        assert "camera" in result.removed
        assert "metadata" not in result.removed
        # ConfigRecord still present
        assert "metadata" in rd.config_records

    def test_different_purposes_different_filtering(self):
        """Same features, different purposes → different filtering results."""
        dm = DataMinimizer(PURPOSES)

        # energy_optimization allows [motion, temperature, humidity]
        msg1 = _make_feature_message({
            "motion": [1.0], "camera": [0.1], "audio": [0.5],
        })
        r1 = dm.filter_record_dict("energy_optimization", msg1.content)
        assert set(r1.removed) == {"camera", "audio"}

        # activity_profiling allows [motion, camera, audio]
        msg2 = _make_feature_message({
            "motion": [1.0], "camera": [0.1], "audio": [0.5],
        })
        r2 = dm.filter_record_dict("activity_profiling", msg2.content)
        assert r2.removed == []


class TestFilterArrayRecord:
    def test_filters_keys_within_array_record(self):
        """Features as keys within a single ArrayRecord."""
        dm = DataMinimizer(PURPOSES)
        ar = ArrayRecord()
        ar["motion"] = Array(np.array([1.0, 2.0]))
        ar["temperature"] = Array(np.array([22.5]))
        ar["camera"] = Array(np.array([0.1, 0.2]))
        ar["audio"] = Array(np.array([0.5]))

        result = dm.filter_array_record("energy_optimization", ar)

        assert set(result.removed) == {"camera", "audio"}
        assert set(result.kept) == {"motion", "temperature"}
        assert set(ar.keys()) == {"motion", "temperature"}

    def test_no_filtering_for_none_allowed(self):
        dm = DataMinimizer(PURPOSES)
        ar = ArrayRecord()
        ar["motion"] = Array(np.array([1.0]))
        ar["camera"] = Array(np.array([0.1]))

        result = dm.filter_array_record("general_research", ar)

        assert result.removed == []
        assert set(ar.keys()) == {"motion", "camera"}


# --- Mod integration tests ---


def _make_context(device_id="sensor_A", purpose="energy_optimization"):
    return Context(
        run_id=0,
        node_id=0,
        node_config={"device_id": device_id},
        state=RecordDict(),
        run_config={"purpose": purpose},
    )


@pytest.fixture
def store(tmp_path):
    now = datetime.now(timezone.utc)
    data = {
        "consents": [
            {
                "subject_id": "person_001",
                "device_ids": ["sensor_A"],
                "purposes": ["energy_optimization", "activity_profiling"],
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


class TestModWithMinimizer:
    def test_mod_strips_disallowed_features(self, engine):
        """The mod should strip camera and audio before passing to call_next."""
        minimizer = DataMinimizer(PURPOSES)
        mod = make_policyfl_mod(engine, minimizer=minimizer)

        msg = _make_feature_message({
            "motion": [1.0, 2.0, 3.0],
            "temperature": [22.5, 23.1],
            "camera": [0.1, 0.2, 0.3, 0.4],
            "audio": [0.5, 0.6, 0.7],
        })
        context = _make_context(purpose="energy_optimization")

        call_next = MagicMock(return_value=Message(RecordDict(), reply_to=msg))
        mod(msg, context, call_next)

        # call_next should have been called
        call_next.assert_called_once()

        # The message passed to call_next should have filtered content
        passed_msg = call_next.call_args[0][0]
        remaining = set(passed_msg.content.array_records.keys())
        assert remaining == {"motion", "temperature"}
        assert "camera" not in remaining
        assert "audio" not in remaining

    def test_mod_keeps_all_for_unrestricted_purpose(self, engine):
        """When purpose has no allowed_features, nothing is stripped."""
        minimizer = DataMinimizer(PURPOSES)
        mod = make_policyfl_mod(engine, minimizer=minimizer)

        msg = _make_feature_message({
            "motion": [1.0],
            "camera": [0.1],
            "audio": [0.5],
        })
        # Need consent for this purpose
        context = _make_context(purpose="energy_optimization")

        call_next = MagicMock(return_value=Message(RecordDict(), reply_to=msg))

        # Use activity_profiling which allows motion, camera, audio
        context2 = _make_context(purpose="activity_profiling")
        mod(msg, context2, call_next)

        passed_msg = call_next.call_args[0][0]
        remaining = set(passed_msg.content.array_records.keys())
        assert remaining == {"motion", "camera", "audio"}

    def test_mod_without_minimizer_passes_all(self, engine):
        """Backward compat: no minimizer means no filtering."""
        mod = make_policyfl_mod(engine)

        msg = _make_feature_message({
            "motion": [1.0],
            "camera": [0.1],
            "audio": [0.5],
        })
        context = _make_context(purpose="energy_optimization")

        call_next = MagicMock(return_value=Message(RecordDict(), reply_to=msg))
        mod(msg, context, call_next)

        passed_msg = call_next.call_args[0][0]
        remaining = set(passed_msg.content.array_records.keys())
        assert remaining == {"motion", "camera", "audio"}

    def test_mod_denied_skips_minimization(self, engine):
        """If consent is denied, minimization is irrelevant."""
        minimizer = DataMinimizer(PURPOSES)
        mod = make_policyfl_mod(engine, minimizer=minimizer)

        msg = _make_feature_message({"motion": [1.0], "camera": [0.1]})
        context = _make_context(device_id="sensor_X", purpose="energy_optimization")

        call_next = MagicMock()
        mod(msg, context, call_next)

        call_next.assert_not_called()

    def test_same_sensor_different_purposes_different_features(self, engine):
        """Same device, but energy_optimization strips camera while
        activity_profiling keeps it."""
        minimizer = DataMinimizer(PURPOSES)
        mod = make_policyfl_mod(engine, minimizer=minimizer)

        features = {"motion": [1.0], "camera": [0.1], "temperature": [22.0]}

        # energy_optimization: keeps motion + temperature, strips camera
        msg1 = _make_feature_message(features)
        ctx1 = _make_context(purpose="energy_optimization")
        call_next1 = MagicMock(return_value=Message(RecordDict(), reply_to=msg1))
        mod(msg1, ctx1, call_next1)
        r1 = set(call_next1.call_args[0][0].content.array_records.keys())
        assert r1 == {"motion", "temperature"}

        # activity_profiling: keeps motion + camera, strips temperature
        msg2 = _make_feature_message(features)
        ctx2 = _make_context(purpose="activity_profiling")
        call_next2 = MagicMock(return_value=Message(RecordDict(), reply_to=msg2))
        mod(msg2, ctx2, call_next2)
        r2 = set(call_next2.call_args[0][0].content.array_records.keys())
        assert r2 == {"motion", "camera"}

"""Tests for policyfl.policy_engine.OPAEngine."""

from unittest.mock import patch, MagicMock

import pytest
import requests

from policyfl.policy_engine import OPAEngine


def _mock_response(json_data, status_code=200):
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.side_effect = (
        None
        if status_code == 200
        else requests.HTTPError(response=resp)
    )
    return resp


class TestOPAEngineAllow:
    @patch("policyfl.policy_engine.requests.post")
    def test_allow_decision(self, mock_post):
        mock_post.return_value = _mock_response(
            {
                "result": {
                    "allow": True,
                    "reason": "consent valid via OPA",
                    "subject_ids": ["person_001"],
                }
            }
        )

        engine = OPAEngine("http://localhost:8181")
        decision = engine.evaluate("sensor_A", "energy_optimization")

        assert decision.allowed is True
        assert decision.reason == "consent valid via OPA"
        assert decision.subject_ids == ["person_001"]

        # Verify the request was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://localhost:8181/v1/data/policyfl/allow"
        assert call_args[1]["json"]["input"]["device_id"] == "sensor_A"
        assert call_args[1]["json"]["input"]["purpose"] == "energy_optimization"

    @patch("policyfl.policy_engine.requests.post")
    def test_allow_with_streams(self, mock_post):
        mock_post.return_value = _mock_response(
            {"result": {"allow": True, "reason": "ok"}}
        )

        engine = OPAEngine("http://localhost:8181")
        engine.evaluate("sensor_A", "energy", streams=["motion", "temp"])

        call_args = mock_post.call_args
        assert call_args[1]["json"]["input"]["streams"] == ["motion", "temp"]


class TestOPAEngineDeny:
    @patch("policyfl.policy_engine.requests.post")
    def test_deny_decision(self, mock_post):
        mock_post.return_value = _mock_response(
            {
                "result": {
                    "allow": False,
                    "reason": "no consent for this purpose",
                    "subject_ids": [],
                }
            }
        )

        engine = OPAEngine("http://localhost:8181")
        decision = engine.evaluate("sensor_X", "energy_optimization")

        assert decision.allowed is False
        assert "no consent" in decision.reason

    @patch("policyfl.policy_engine.requests.post")
    def test_missing_result_defaults_to_deny(self, mock_post):
        mock_post.return_value = _mock_response({})

        engine = OPAEngine("http://localhost:8181")
        decision = engine.evaluate("sensor_A", "energy_optimization")

        assert decision.allowed is False


class TestOPAEngineErrors:
    @patch("policyfl.policy_engine.requests.post")
    def test_connection_error_denies(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("refused")

        engine = OPAEngine("http://localhost:8181")
        decision = engine.evaluate("sensor_A", "energy_optimization")

        assert decision.allowed is False
        assert "unreachable" in decision.reason

    @patch("policyfl.policy_engine.requests.post")
    def test_http_error_denies(self, mock_post):
        mock_post.return_value = _mock_response(
            {"error": "not found"}, status_code=404
        )

        engine = OPAEngine("http://localhost:8181")
        decision = engine.evaluate("sensor_A", "energy_optimization")

        assert decision.allowed is False
        assert "404" in decision.reason

    @patch("policyfl.policy_engine.requests.post")
    def test_timeout_denies(self, mock_post):
        mock_post.side_effect = requests.Timeout("timed out")

        engine = OPAEngine("http://localhost:8181", timeout=2.0)
        decision = engine.evaluate("sensor_A", "energy_optimization")

        assert decision.allowed is False
        assert "timed out" in decision.reason


class TestOPAEngineConfig:
    @patch("policyfl.policy_engine.requests.post")
    def test_custom_policy_path(self, mock_post):
        mock_post.return_value = _mock_response(
            {"result": {"allow": True, "reason": "ok"}}
        )

        engine = OPAEngine(
            "http://localhost:8181", policy_path="myorg/consent/check"
        )
        engine.evaluate("sensor_A", "energy")

        url = mock_post.call_args[0][0]
        assert url == "http://localhost:8181/v1/data/myorg/consent/check"

    @patch("policyfl.policy_engine.requests.post")
    def test_trailing_slash_on_url(self, mock_post):
        mock_post.return_value = _mock_response(
            {"result": {"allow": True, "reason": "ok"}}
        )

        engine = OPAEngine("http://localhost:8181/")
        engine.evaluate("sensor_A", "energy")

        url = mock_post.call_args[0][0]
        assert url == "http://localhost:8181/v1/data/policyfl/allow"

    @patch("policyfl.policy_engine.requests.post")
    def test_custom_timeout(self, mock_post):
        mock_post.return_value = _mock_response(
            {"result": {"allow": True, "reason": "ok"}}
        )

        engine = OPAEngine("http://localhost:8181", timeout=10.0)
        engine.evaluate("sensor_A", "energy")

        assert mock_post.call_args[1]["timeout"] == 10.0

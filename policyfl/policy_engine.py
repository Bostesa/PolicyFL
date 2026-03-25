"""Policy evaluation engines for PolicyFL."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import requests

from policyfl.consent_store import ConsentStore
from policyfl.models import PolicyDecision

logger = logging.getLogger("policyfl")


class PolicyEngine(ABC):
    """Abstract base class for policy evaluation."""

    @abstractmethod
    def evaluate(
        self, device_id: str, purpose: str, streams: list[str] | None = None
    ) -> PolicyDecision:
        """Evaluate whether training is allowed for a device, purpose, and data streams."""
        ...


class SimpleEngine(PolicyEngine):
    """Policy engine that checks the consent store directly."""

    def __init__(self, store: ConsentStore) -> None:
        self._store = store

    def evaluate(
        self, device_id: str, purpose: str, streams: list[str] | None = None
    ) -> PolicyDecision:
        decision = self._store.check_consent(device_id, purpose)

        if decision.allowed:
            logger.info(
                "ALLOW device=%s purpose=%s reason=%s",
                device_id,
                purpose,
                decision.reason,
            )
        else:
            logger.warning(
                "DENY device=%s purpose=%s reason=%s",
                device_id,
                purpose,
                decision.reason,
            )

        return decision


class OPAEngine(PolicyEngine):
    """Policy engine that delegates decisions to an Open Policy Agent server.

    OPA is queried via its REST Data API. The engine POSTs an input document
    containing ``device_id``, ``purpose``, and ``streams`` to the configured
    policy path and expects a result with ``allow`` (bool), ``reason`` (str),
    and optionally ``subject_ids`` (list[str]).

    Parameters
    ----------
    opa_url : str
        Base URL of the OPA server (e.g. ``http://localhost:8181``).
    policy_path : str
        Dot-or-slash separated path under ``/v1/data/`` to query
        (default ``policyfl/allow``).
    timeout : float
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        opa_url: str,
        policy_path: str = "policyfl/allow",
        timeout: float = 5.0,
    ) -> None:
        self._url = opa_url.rstrip("/")
        self._policy_path = policy_path.strip("/")
        self._timeout = timeout

    def evaluate(
        self, device_id: str, purpose: str, streams: list[str] | None = None
    ) -> PolicyDecision:
        input_data = {
            "input": {
                "device_id": device_id,
                "purpose": purpose,
                "streams": streams or [],
            }
        }

        url = f"{self._url}/v1/data/{self._policy_path}"

        try:
            resp = requests.post(url, json=input_data, timeout=self._timeout)
            resp.raise_for_status()
        except requests.ConnectionError:
            reason = f"OPA server unreachable at {self._url}"
            logger.error("DENY device=%s — %s", device_id, reason)
            return PolicyDecision(allowed=False, reason=reason)
        except requests.HTTPError as exc:
            reason = f"OPA returned HTTP {exc.response.status_code}"
            logger.error("DENY device=%s — %s", device_id, reason)
            return PolicyDecision(allowed=False, reason=reason)
        except requests.Timeout:
            reason = f"OPA request timed out after {self._timeout}s"
            logger.error("DENY device=%s — %s", device_id, reason)
            return PolicyDecision(allowed=False, reason=reason)

        result = resp.json().get("result", {})
        allowed = bool(result.get("allow", False))
        reason = result.get("reason", "OPA policy decision")
        subject_ids = result.get("subject_ids", [])

        if allowed:
            logger.info(
                "ALLOW device=%s purpose=%s (OPA)", device_id, purpose
            )
        else:
            logger.warning(
                "DENY device=%s purpose=%s reason=%s (OPA)",
                device_id,
                purpose,
                reason,
            )

        return PolicyDecision(
            allowed=allowed, reason=reason, subject_ids=subject_ids
        )

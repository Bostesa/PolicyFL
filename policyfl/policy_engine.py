"""Policy evaluation engines for PolicyFL."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

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

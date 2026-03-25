"""Data models for PolicyFL consent management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Purpose:
    """A purpose for which consent can be granted."""

    name: str
    description: str
    allowed_features: list[str] | None = None


@dataclass
class ConsentRecord:
    """A record of consent granted by a data subject."""

    subject_id: str
    device_ids: list[str]
    purposes: list[str]
    granted_at: datetime
    expires_at: datetime | None = None
    revoked: bool = False
    revoked_at: datetime | None = None

    def is_valid(self, purpose: str, device_id: str) -> bool:
        """Check if this consent record is currently valid for a given purpose and device."""
        if self.revoked:
            return False
        if device_id not in self.device_ids:
            return False
        if purpose not in self.purposes:
            return False
        if self.expires_at is not None and datetime.now(timezone.utc) > self.expires_at:
            return False
        return True


@dataclass
class PolicyDecision:
    """The result of a policy evaluation."""

    allowed: bool
    reason: str
    subject_ids: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

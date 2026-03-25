"""Audit trail for PolicyFL policy decisions."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class AuditEntry:
    """A single audit log entry recording a policy decision."""

    timestamp: str
    device_id: str
    purpose: str
    decision: str  # "ALLOW" or "DENY"
    reason: str
    subject_ids: list[str]
    round_id: str | None = None

    @classmethod
    def from_decision(
        cls,
        device_id: str,
        purpose: str,
        allowed: bool,
        reason: str,
        subject_ids: list[str],
        round_id: str | None = None,
    ) -> AuditEntry:
        return cls(
            timestamp=datetime.now(timezone.utc).isoformat(),
            device_id=device_id,
            purpose=purpose,
            decision="ALLOW" if allowed else "DENY",
            reason=reason,
            subject_ids=subject_ids,
            round_id=round_id,
        )


class AuditLogger(ABC):
    """Abstract base class for audit logging."""

    @abstractmethod
    def log(self, entry: AuditEntry) -> None:
        """Record an audit entry."""
        ...

    @abstractmethod
    def get_log(
        self,
        *,
        device_id: str | None = None,
        purpose: str | None = None,
        decision: str | None = None,
    ) -> list[AuditEntry]:
        """Retrieve audit entries, optionally filtered."""
        ...


class JSONAuditLogger(AuditLogger):
    """Audit logger that appends entries to a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: list[AuditEntry] = []
        if self._path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self._path.read_text())
        self._entries = [AuditEntry(**e) for e in data.get("audit_log", [])]

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(
                {"audit_log": [asdict(e) for e in self._entries]},
                indent=2,
            )
        )

    def log(self, entry: AuditEntry) -> None:
        self._entries.append(entry)
        self._save()

    def get_log(
        self,
        *,
        device_id: str | None = None,
        purpose: str | None = None,
        decision: str | None = None,
    ) -> list[AuditEntry]:
        results = self._entries
        if device_id is not None:
            results = [e for e in results if e.device_id == device_id]
        if purpose is not None:
            results = [e for e in results if e.purpose == purpose]
        if decision is not None:
            results = [e for e in results if e.decision == decision]
        return results

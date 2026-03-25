"""Consent storage backends for PolicyFL."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from policyfl.models import ConsentRecord, PolicyDecision


class ConsentStore(ABC):
    """Abstract base class for consent storage."""

    @abstractmethod
    def get_consents_for_device(self, device_id: str) -> list[ConsentRecord]:
        """Return all consent records that cover this device."""
        ...

    @abstractmethod
    def check_consent(self, device_id: str, purpose: str) -> PolicyDecision:
        """Check whether there is valid consent for a device and purpose."""
        ...

    @abstractmethod
    def grant_consent(self, record: ConsentRecord) -> None:
        """Add a new consent record."""
        ...

    @abstractmethod
    def get_consent_status(self, subject_id: str) -> list[ConsentRecord]:
        """Return all consent records for a given subject."""
        ...

    @abstractmethod
    def revoke_consent(self, subject_id: str, purpose: str | None = None) -> None:
        """Revoke consent for a subject, optionally limited to a specific purpose."""
        ...


class JSONConsentStore(ConsentStore):
    """File-based consent store backed by a JSON file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._consents: list[ConsentRecord] = []
        if self._path.exists():
            self._load()

    def _load(self) -> None:
        """Load consent records from the JSON file."""
        data = json.loads(self._path.read_text())
        self._consents = []
        for rec in data.get("consents", []):
            self._consents.append(
                ConsentRecord(
                    subject_id=rec["subject_id"],
                    device_ids=rec["device_ids"],
                    purposes=rec["purposes"],
                    granted_at=datetime.fromisoformat(rec["granted_at"]),
                    expires_at=(
                        datetime.fromisoformat(rec["expires_at"])
                        if rec.get("expires_at")
                        else None
                    ),
                    revoked=rec.get("revoked", False),
                    revoked_at=(
                        datetime.fromisoformat(rec["revoked_at"])
                        if rec.get("revoked_at")
                        else None
                    ),
                )
            )

    def _save(self) -> None:
        """Persist consent records back to the JSON file."""
        records = []
        for c in self._consents:
            records.append(
                {
                    "subject_id": c.subject_id,
                    "device_ids": c.device_ids,
                    "purposes": c.purposes,
                    "granted_at": c.granted_at.isoformat(),
                    "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                    "revoked": c.revoked,
                    "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
                }
            )
        self._path.write_text(json.dumps({"consents": records}, indent=2))

    def grant_consent(self, record: ConsentRecord) -> None:
        self._consents.append(record)
        self._save()

    def get_consent_status(self, subject_id: str) -> list[ConsentRecord]:
        return [c for c in self._consents if c.subject_id == subject_id]

    def get_consents_for_device(self, device_id: str) -> list[ConsentRecord]:
        return [c for c in self._consents if device_id in c.device_ids]

    def check_consent(self, device_id: str, purpose: str) -> PolicyDecision:
        consents = self.get_consents_for_device(device_id)
        valid = [c for c in consents if c.is_valid(purpose, device_id)]
        subject_ids = [c.subject_id for c in consents]

        if valid:
            return PolicyDecision(
                allowed=True,
                reason=f"Valid consent found for device={device_id}, purpose={purpose}",
                subject_ids=subject_ids,
            )

        if not consents:
            reason = f"No consent records found for device={device_id}"
        else:
            reasons = []
            for c in consents:
                if c.revoked:
                    reasons.append(f"subject={c.subject_id} revoked consent")
                elif purpose not in c.purposes:
                    reasons.append(
                        f"subject={c.subject_id} did not consent to purpose={purpose}"
                    )
                elif c.expires_at and datetime.now(timezone.utc) > c.expires_at:
                    reasons.append(f"subject={c.subject_id} consent expired")
            reason = "; ".join(reasons) if reasons else "No valid consent"

        return PolicyDecision(
            allowed=False,
            reason=reason,
            subject_ids=subject_ids,
        )

    def revoke_consent(self, subject_id: str, purpose: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        for consent in self._consents:
            if consent.subject_id != subject_id:
                continue
            if purpose is None or purpose in consent.purposes:
                consent.revoked = True
                consent.revoked_at = now
        self._save()

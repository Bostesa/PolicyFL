"""Federated unlearning hooks for PolicyFL.

When consent is revoked, this module identifies which training rounds used
the revoked subject's data (via the audit log) and flags them as tainted
so that federated unlearning can be triggered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from policyfl.audit import AuditLogger
from policyfl.consent_store import ConsentStore


@dataclass
class TaintedRound:
    """A training round that used data from a subject who later revoked consent."""

    round_id: str
    device_id: str
    purpose: str
    subject_ids: list[str]
    reason: str
    flagged_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class UnlearningTracker:
    """Tracks training rounds that need federated unlearning after consent revocation.

    Usage::

        tracker = UnlearningTracker(audit_logger, consent_store)

        # When consent is revoked:
        tainted = tracker.on_consent_revoked("person_001")
        # tainted contains the list of rounds that used person_001's data

        # Query all outstanding tainted rounds:
        all_tainted = tracker.get_tainted_rounds()

        # After unlearning is completed for a round:
        tracker.clear_tainted_round(round_id)
    """

    def __init__(
        self, audit_logger: AuditLogger, consent_store: ConsentStore
    ) -> None:
        self._audit_logger = audit_logger
        self._store = consent_store
        self._tainted: dict[str, TaintedRound] = {}

    def on_consent_revoked(
        self, subject_id: str, purpose: str | None = None
    ) -> list[TaintedRound]:
        """Scan audit log for rounds that used data from a revoked subject.

        Parameters
        ----------
        subject_id : str
            The subject whose consent was revoked.
        purpose : str | None
            If provided, only flag rounds for this specific purpose.

        Returns
        -------
        list[TaintedRound]
            Newly flagged tainted rounds.
        """
        # Find all devices associated with this subject
        records = self._store.get_consent_status(subject_id)
        device_ids: set[str] = set()
        for rec in records:
            device_ids.update(rec.device_ids)

        newly_tainted: list[TaintedRound] = []

        for device_id in device_ids:
            entries = self._audit_logger.get_log(
                device_id=device_id, decision="ALLOW"
            )
            for entry in entries:
                # Skip entries without a round_id
                if not entry.round_id:
                    continue
                # If purpose-scoped, only match that purpose
                if purpose is not None and entry.purpose != purpose:
                    continue
                # Only flag rounds where this subject's data was actually used
                if subject_id not in entry.subject_ids:
                    continue
                # Avoid duplicating an already-tainted round for the same reason
                key = f"{entry.round_id}:{device_id}:{subject_id}"
                if key in self._tainted:
                    continue

                tr = TaintedRound(
                    round_id=entry.round_id,
                    device_id=device_id,
                    purpose=entry.purpose,
                    subject_ids=[subject_id],
                    reason=f"consent revoked by subject_id={subject_id}",
                )
                self._tainted[key] = tr
                newly_tainted.append(tr)

        return newly_tainted

    def get_tainted_rounds(self) -> list[TaintedRound]:
        """Return all rounds currently flagged as needing unlearning."""
        return list(self._tainted.values())

    def clear_tainted_round(self, round_id: str) -> None:
        """Remove all taint records for a round (e.g. after unlearning is complete)."""
        keys_to_remove = [k for k in self._tainted if k.startswith(f"{round_id}:")]
        for key in keys_to_remove:
            del self._tainted[key]

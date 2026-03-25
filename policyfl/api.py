"""FastAPI consent management API for PolicyFL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from policyfl.audit import AuditLogger
from policyfl.consent_store import ConsentStore
from policyfl.models import ConsentRecord


# --- Request / response schemas ---


class GrantConsentRequest(BaseModel):
    subject_id: str
    device_ids: list[str]
    purposes: list[str]
    expires_at: datetime | None = None


class RevokeConsentRequest(BaseModel):
    subject_id: str
    purpose: str | None = None


class ConsentRecordResponse(BaseModel):
    subject_id: str
    device_ids: list[str]
    purposes: list[str]
    granted_at: str
    expires_at: str | None
    revoked: bool
    revoked_at: str | None


class AuditEntryResponse(BaseModel):
    timestamp: str
    device_id: str
    purpose: str
    decision: str
    reason: str
    subject_ids: list[str]
    round_id: str | None


# --- Helpers ---


def _record_to_response(c: ConsentRecord) -> ConsentRecordResponse:
    return ConsentRecordResponse(
        subject_id=c.subject_id,
        device_ids=c.device_ids,
        purposes=c.purposes,
        granted_at=c.granted_at.isoformat(),
        expires_at=c.expires_at.isoformat() if c.expires_at else None,
        revoked=c.revoked,
        revoked_at=c.revoked_at.isoformat() if c.revoked_at else None,
    )


# --- App factory ---


def create_app(
    store: ConsentStore,
    audit_logger: AuditLogger | None = None,
) -> FastAPI:
    """Create a FastAPI app wired to the given consent store and optional audit logger."""

    app = FastAPI(title="PolicyFL Consent API")

    @app.post("/consent/grant", status_code=201)
    def grant_consent(req: GrantConsentRequest) -> ConsentRecordResponse:
        record = ConsentRecord(
            subject_id=req.subject_id,
            device_ids=req.device_ids,
            purposes=req.purposes,
            granted_at=datetime.now(timezone.utc),
            expires_at=req.expires_at,
        )
        store.grant_consent(record)
        return _record_to_response(record)

    @app.post("/consent/revoke")
    def revoke_consent(req: RevokeConsentRequest) -> dict:
        records = store.get_consent_status(req.subject_id)
        if not records:
            raise HTTPException(
                status_code=404,
                detail=f"No consent records found for subject={req.subject_id}",
            )
        store.revoke_consent(req.subject_id, purpose=req.purpose)
        return {"status": "revoked", "subject_id": req.subject_id, "purpose": req.purpose}

    @app.get("/consent/status/{subject_id}")
    def get_consent_status(subject_id: str) -> list[ConsentRecordResponse]:
        records = store.get_consent_status(subject_id)
        if not records:
            raise HTTPException(
                status_code=404,
                detail=f"No consent records found for subject={subject_id}",
            )
        return [_record_to_response(c) for c in records]

    @app.get("/consent/check")
    def check_consent(
        device_id: str = Query(...),
        purpose: str = Query(...),
    ) -> dict:
        decision = store.check_consent(device_id, purpose)
        return {
            "device_id": device_id,
            "purpose": purpose,
            "allowed": decision.allowed,
            "reason": decision.reason,
        }

    if audit_logger is not None:

        @app.get("/audit")
        def get_audit_log(
            device_id: Annotated[str | None, Query()] = None,
            purpose: Annotated[str | None, Query()] = None,
            decision: Annotated[str | None, Query()] = None,
        ) -> list[AuditEntryResponse]:
            entries = audit_logger.get_log(
                device_id=device_id,
                purpose=purpose,
                decision=decision,
            )
            return [
                AuditEntryResponse(
                    timestamp=e.timestamp,
                    device_id=e.device_id,
                    purpose=e.purpose,
                    decision=e.decision,
                    reason=e.reason,
                    subject_ids=e.subject_ids,
                    round_id=e.round_id,
                )
                for e in entries
            ]

    return app

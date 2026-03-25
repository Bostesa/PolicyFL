"""PolicyFL Flower Mod — consent-aware training gate."""

from __future__ import annotations

import logging
from typing import Callable

from flwr.common import Context, Message, RecordDict

from policyfl.audit import AuditEntry, AuditLogger
from policyfl.minimizer import DataMinimizer
from policyfl.policy_engine import PolicyEngine

logger = logging.getLogger("policyfl")

# Flower's ClientAppCallable type
ClientAppCallable = Callable[[Message, Context], Message]


def make_policyfl_mod(
    engine: PolicyEngine,
    *,
    audit_logger: AuditLogger | None = None,
    minimizer: DataMinimizer | None = None,
    purpose_key: str = "purpose",
    device_id_key: str = "device_id",
) -> Callable[[Message, Context, ClientAppCallable], Message]:
    """Create a Flower Mod that gates training on consent policy.

    Parameters
    ----------
    engine : PolicyEngine
        The policy engine to use for consent checks.
    audit_logger : AuditLogger | None
        Optional audit logger to record every policy decision.
    minimizer : DataMinimizer | None
        Optional data minimizer for GDPR Article 5(1)(c) feature filtering.
        When provided, disallowed features are stripped from the message
        before training proceeds.
    purpose_key : str
        Key in run_config or message config_records that holds the training purpose.
    device_id_key : str
        Key in node_config that holds the device identifier.
    """

    def policyfl_mod(
        msg: Message, context: Context, call_next: ClientAppCallable
    ) -> Message:
        # --- Extract device ID ---
        device_id = context.node_config.get(device_id_key, "")
        if not device_id:
            # Fall back to node_id as string
            device_id = str(context.node_id)

        # --- Extract purpose ---
        purpose = context.run_config.get(purpose_key, "")

        # Also check message content's config records for purpose override
        if not purpose and msg.has_content():
            for key in msg.content.configs_records:
                rec = msg.content.configs_records[key]
                if purpose_key in rec:
                    purpose = str(rec[purpose_key])
                    break

        if not purpose:
            reason = "no purpose specified in run_config or message"
            logger.warning("DENY device=%s — %s", device_id, reason)
            if audit_logger is not None:
                audit_logger.log(
                    AuditEntry.from_decision(
                        device_id=device_id,
                        purpose="",
                        allowed=False,
                        reason=reason,
                        subject_ids=[],
                        round_id=str(context.run_id),
                    )
                )
            return Message(RecordDict(), reply_to=msg)

        # --- Evaluate policy ---
        decision = engine.evaluate(device_id=device_id, purpose=str(purpose))

        # --- Audit log ---
        if audit_logger is not None:
            audit_logger.log(
                AuditEntry.from_decision(
                    device_id=device_id,
                    purpose=str(purpose),
                    allowed=decision.allowed,
                    reason=decision.reason,
                    subject_ids=decision.subject_ids,
                    round_id=str(context.run_id),
                )
            )

        if decision.allowed:
            # --- Data minimization (GDPR Art. 5(1)(c)) ---
            if minimizer is not None and msg.has_content():
                result = minimizer.filter_record_dict(str(purpose), msg.content)
                if result.removed:
                    logger.info(
                        "MINIMIZED device=%s purpose=%s: stripped %s, kept %s",
                        device_id,
                        purpose,
                        result.removed,
                        result.kept,
                    )
            return call_next(msg, context)

        # Denied — return empty reply without training
        logger.warning(
            "BLOCKED device=%s purpose=%s reason=%s",
            device_id,
            purpose,
            decision.reason,
        )
        return Message(RecordDict(), reply_to=msg)

    return policyfl_mod

"""Demo: PolicyFL consent-aware training gate.

Shows a consented device training successfully and a non-consented device
getting blocked, then demonstrates mid-session consent revocation.
"""

import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from flwr.common import Context, Message, RecordDict

from policyfl.consent_store import JSONConsentStore
from policyfl.mod import make_policyfl_mod
from policyfl.policy_engine import SimpleEngine

# Set up logging so we can see PolicyFL decisions
logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")


def make_context(device_id: str, purpose: str) -> Context:
    return Context(
        run_id=0,
        node_id=0,
        node_config={"device_id": device_id},
        state=RecordDict(),
        run_config={"purpose": purpose},
    )


def make_message() -> Message:
    return Message(content=RecordDict(), dst_node_id=0, message_type="train")


def fake_train(msg: Message, context: Context) -> Message:
    """Simulates actual FL training — just returns a reply with dummy results."""
    device_id = context.node_config.get("device_id", "?")
    print(f"    >>> TRAINING executed on {device_id} <<<")
    return Message(RecordDict(), reply_to=msg)


def main():
    # --- Set up consent data ---
    now = datetime.now(timezone.utc)
    consent_data = {
        "consents": [
            {
                "subject_id": "person_001",
                "device_ids": ["motion_sensor_3A", "temp_sensor_3B"],
                "purposes": ["energy_optimization"],
                "granted_at": (now - timedelta(days=30)).isoformat(),
                "expires_at": (now + timedelta(days=60)).isoformat(),
                "revoked": False,
                "revoked_at": None,
            },
            {
                "subject_id": "person_002",
                "device_ids": ["motion_sensor_3A"],
                "purposes": ["energy_optimization", "occupancy_counting"],
                "granted_at": (now - timedelta(days=10)).isoformat(),
                "expires_at": None,
                "revoked": False,
                "revoked_at": None,
            },
        ]
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        consent_path = Path(tmpdir) / "consents.json"
        consent_path.write_text(json.dumps(consent_data, indent=2))

        store = JSONConsentStore(consent_path)
        engine = SimpleEngine(store)
        mod = make_policyfl_mod(engine)

        print("=" * 60)
        print("PolicyFL Demo: Consent-Aware Federated Learning")
        print("=" * 60)

        # --- Scenario 1: Consented device trains ---
        print("\n--- Round 1: motion_sensor_3A + energy_optimization ---")
        print("    person_001 and person_002 both consented to this.")
        msg = make_message()
        ctx = make_context("motion_sensor_3A", "energy_optimization")
        mod(msg, ctx, fake_train)

        # --- Scenario 2: Non-consented device blocked ---
        print("\n--- Round 2: camera_5X + energy_optimization ---")
        print("    No one consented for camera_5X.")
        msg = make_message()
        ctx = make_context("camera_5X", "energy_optimization")
        mod(msg, ctx, fake_train)
        print("    (no training output = blocked)")

        # --- Scenario 3: Wrong purpose blocked ---
        print("\n--- Round 3: motion_sensor_3A + activity_profiling ---")
        print("    person_001 only consented to energy_optimization, not profiling.")
        msg = make_message()
        ctx = make_context("motion_sensor_3A", "activity_profiling")
        mod(msg, ctx, fake_train)
        print("    (no training output = blocked)")

        # --- Scenario 4: Consent revocation mid-session ---
        print("\n--- Round 4: Revoke person_001, then retry ---")
        print("    Revoking person_001's consent...")
        store.revoke_consent("person_001")

        print("    motion_sensor_3A + energy_optimization (person_002 still active):")
        msg = make_message()
        ctx = make_context("motion_sensor_3A", "energy_optimization")
        mod(msg, ctx, fake_train)

        print("\n    temp_sensor_3B + energy_optimization (only person_001 had consent):")
        msg = make_message()
        ctx = make_context("temp_sensor_3B", "energy_optimization")
        mod(msg, ctx, fake_train)
        print("    (no training output = blocked after revocation)")

        # --- Scenario 5: Revoke all, everything blocked ---
        print("\n--- Round 5: Revoke person_002 — all consent gone ---")
        store.revoke_consent("person_002")
        msg = make_message()
        ctx = make_context("motion_sensor_3A", "energy_optimization")
        mod(msg, ctx, fake_train)
        print("    (no training output = fully blocked)")

        print("\n" + "=" * 60)
        print("Demo complete. Consented devices trained; others were blocked.")
        print("=" * 60)


if __name__ == "__main__":
    main()

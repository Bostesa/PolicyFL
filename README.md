# PolicyFL

Consent-aware middleware for federated learning over IoT. A [Flower](https://flower.ai) Mod that enforces per-data-subject GDPR consent policies before, during, and after federated learning training.

PolicyFL intercepts training messages, checks a consent database, strips disallowed features, and either allows or blocks training based on who consented, for what purpose, and whether consent is still valid.

## Why

Federated learning on IoT devices trains models on data from people in physical spaces (smart buildings, hospitals, factories). GDPR requires per-person consent with purpose limitation and right to erasure. No existing FL framework enforces this at the individual level. PolicyFL does.

## Features

- **Consent gating** -- block training when consent is missing, revoked, or expired
- **Purpose limitation** -- same sensor, same person, different purposes can have different consent states
- **Data minimization** (Art. 5(1)(c)) -- strip disallowed features from training data before it reaches the model
- **Spatial zones** -- consent state changes automatically as subjects move between physical areas
- **Audit trail** -- every policy decision is logged with timestamp, device, purpose, and round ID
- **Federated unlearning hooks** -- flag tainted training rounds when consent is revoked retroactively
- **Real-time** -- consent state is checked every round; revocation takes effect on the next round
- **OPA integration** -- delegate policy decisions to an Open Policy Agent server

## Installation

```bash
pip install flwr fastapi numpy
pip install -e .
```

Or directly from the repository:

```bash
git clone <repo-url>
cd PolicyFL
pip install -e .
```

## Quick start

```python
from policyfl import (
    JSONConsentStore, SimpleEngine, make_policyfl_mod,
    JSONAuditLogger, DataMinimizer, Purpose,
)

# 1. Load consent records
store = JSONConsentStore("consents.json")

# 2. Create a policy engine
engine = SimpleEngine(store)

# 3. (Optional) Set up audit logging
audit = JSONAuditLogger("audit.json")

# 4. (Optional) Set up data minimization
minimizer = DataMinimizer({
    "energy_optimization": Purpose(
        name="energy_optimization",
        description="Optimize HVAC and lighting",
        allowed_features=["motion", "temperature", "humidity"],
    ),
})

# 5. Create the Flower Mod
mod = make_policyfl_mod(engine, audit_logger=audit, minimizer=minimizer)

# 6. Register in your Flower ClientApp
app = ClientApp(client_fn=client_fn, mods=[mod])
```

## Consent file format

```json
{
  "consents": [
    {
      "subject_id": "person_001",
      "device_ids": ["motion_sensor_3A", "temp_sensor_3B"],
      "purposes": ["energy_optimization"],
      "granted_at": "2026-03-01T09:00:00Z",
      "expires_at": "2026-06-01T09:00:00Z",
      "revoked": false,
      "revoked_at": null
    }
  ]
}
```

## Consent management API

PolicyFL includes a FastAPI server for managing consent at runtime:

```python
from policyfl import JSONConsentStore, JSONAuditLogger, create_app

store = JSONConsentStore("consents.json")
app = create_app(store, audit_logger=JSONAuditLogger("audit.json"))
# Run with: uvicorn app:app
```

| Endpoint | Method | Description |
|---|---|---|
| `/consent/grant` | POST | Grant consent for a subject |
| `/consent/revoke` | POST | Revoke consent (all or by purpose) |
| `/consent/status/{subject_id}` | GET | Query consent records |
| `/consent/check?device_id=...&purpose=...` | GET | Check if training is allowed |
| `/audit` | GET | Query audit log (filterable) |

## Spatial zones

Track subject presence across physical areas. Consent is automatically suspended when a subject leaves a zone and reactivated when they return.

```python
from policyfl import SpatialZoneManager, Zone

zones = [
    Zone("lobby", "Public Lobby", ["sensor_L1", "sensor_L2"]),
    Zone("meeting_room", "Private Meeting Room", ["sensor_M1"]),
]
manager = SpatialZoneManager(store, zones)

manager.enter_zone("alice", "lobby")      # lobby sensors can use alice's data
manager.leave_zone("alice", "lobby")      # lobby consent suspended
manager.enter_zone("alice", "meeting_room")  # meeting room consent active
```

## Federated unlearning

When consent is revoked, identify which training rounds used that subject's data and flag them for unlearning.

```python
from policyfl import UnlearningTracker

tracker = UnlearningTracker(audit_logger, store)
tainted = tracker.on_consent_revoked("person_001")
# tainted = [TaintedRound(round_id="3", ...), TaintedRound(round_id="7", ...)]

tracker.clear_tainted_round("3")  # after unlearning is complete
```

## Project structure

```
policyfl/
  models.py          Data models (ConsentRecord, PolicyDecision, Purpose)
  consent_store.py   Consent storage backends (JSONConsentStore)
  policy_engine.py   Policy evaluation (SimpleEngine, OPAEngine)
  mod.py             Flower Mod -- the core entry point
  minimizer.py       GDPR Art. 5(1)(c) feature filtering
  audit.py           Audit trail logging
  spatial.py         Spatial zone management
  unlearning.py      Federated unlearning hooks
  api.py             FastAPI consent management server
tests/               Test suite (109 tests)
demo.py              End-to-end demo script
```

## Running tests

```bash
pytest tests/ -v
```

## GDPR articles enforced

| Article | Requirement | PolicyFL mechanism |
|---|---|---|
| Art. 6 | Lawful basis (consent) | Consent gating in `mod.py` |
| Art. 7 | Conditions for consent | Temporal expiry, purpose limitation |
| Art. 5(1)(b) | Purpose limitation | Per-purpose consent checks |
| Art. 5(1)(c) | Data minimization | Feature filtering in `minimizer.py` |
| Art. 17 | Right to erasure | Unlearning hooks in `unlearning.py` |
| Art. 30 | Records of processing | Audit trail in `audit.py` |

## Demo

```bash
python demo.py
```

Shows consented devices training, non-consented devices getting blocked, and mid-session consent revocation taking effect immediately.

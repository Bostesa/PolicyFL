# CLAUDE.md — PolicyFL: Consent-Aware Middleware for Federated Learning over IoT

## What we are building

A Flower Mod (middleware plugin) that enforces per-data-subject consent policies before federated learning training occurs. When a training round starts, PolicyFL intercepts the message, checks a consent database, and either allows or blocks training based on who consented, for what purpose, and whether consent is still valid.

This is a standalone Python package. It does NOT modify Flower's source code. Users install it and register it as a Mod in their ClientApp.

## Why this matters

Federated learning on IoT devices trains models on data from people in physical spaces (smart buildings, hospitals, factories). GDPR requires per-person consent with purpose limitation and right to erasure. No existing FL framework enforces this. FLA3 from CaMLSys handles institutional consent for healthcare (hospital A agrees to join a study). PolicyFL handles individual consent (person X consented to energy optimization but not activity profiling, and revoked consent at 3pm).

## How Flower Mods work

A Mod is a function with this signature:

```python
from flwr.common import Message, Context

Mod = Callable[[Message, Context, ClientAppCallable], Message]

def my_mod(msg: Message, context: Context, call_next: ClientAppCallable) -> Message:
    # Do something before training
    result = call_next(msg, context)  # Execute actual training
    # Do something after training
    return result
```

Flower calls Mods in order on every message. Each Mod can:
- Inspect the message (what task, what data, what purpose)
- Block the message (return early without calling call_next)
- Modify the message (filter features, add metadata)
- Pass it through unchanged (call call_next)

Users register Mods in their ClientApp:

```python
app = ClientApp(
    client_fn=client_fn,
    mods=[policyfl_mod]  # PolicyFL goes here
)
```

## Architecture

```
Training Message arrives
        |
        v
  PolicyFL Mod
        |
        v
  Extract: device_id, purpose, data_streams
        |
        v
  Query Policy Engine (OPA / JSON / XACML)
    "Can device X train model Y using
     streams [a,b,c] for purpose Z?"
        |
    +---+---+
    |       |
  ALLOW   DENY
    |       |
    v       v
 call_next  return empty
 (train)    (skip training)
        |
        v
  Log decision for audit trail
```

## What to build (in order)

### Phase 1: Core Mod (build this first)

```
policyfl/
├── __init__.py
├── mod.py              # The Flower Mod function
├── policy_engine.py    # Consent checking logic
├── consent_store.py    # Consent database interface
├── models.py           # Data models (ConsentRecord, PolicyDecision, Purpose)
└── config.py           # Configuration (policy engine type, store path)
```

**mod.py** — The entry point. A function matching Flower's Mod signature.
- Extract device_id from Context
- Extract purpose from Message metadata (ConfigRecord)
- Query the policy engine
- If allowed: call_next(msg, context)
- If denied: log the denial, return a Message with empty results
- After training: log the decision for audit

**models.py** — Data classes:
```python
@dataclass
class ConsentRecord:
    subject_id: str          # Person who consented
    device_ids: list[str]    # Sensors this consent covers
    purposes: list[str]      # What they consented to ("energy_optimization", "occupancy")
    granted_at: datetime
    expires_at: datetime | None
    revoked: bool = False
    revoked_at: datetime | None = None

@dataclass  
class PolicyDecision:
    allowed: bool
    reason: str
    subject_ids: list[str]   # Which subjects were checked
    timestamp: datetime

@dataclass
class Purpose:
    name: str                # "energy_optimization"
    allowed_features: list[str] | None  # Feature filtering for data minimization
    description: str
```

**consent_store.py** — Interface + implementations:
```python
class ConsentStore(ABC):
    @abstractmethod
    def get_consents_for_device(self, device_id: str) -> list[ConsentRecord]: ...
    
    @abstractmethod
    def check_consent(self, device_id: str, purpose: str) -> PolicyDecision: ...
    
    @abstractmethod
    def revoke_consent(self, subject_id: str, purpose: str | None = None) -> None: ...

class JSONConsentStore(ConsentStore):
    """File-based consent store for prototyping."""

class SQLiteConsentStore(ConsentStore):
    """SQLite-backed store for persistence."""
```

**policy_engine.py** — Policy evaluation:
```python
class PolicyEngine(ABC):
    @abstractmethod
    def evaluate(self, device_id: str, purpose: str, streams: list[str]) -> PolicyDecision: ...

class SimpleEngine(PolicyEngine):
    """Checks consent store directly."""

class OPAEngine(PolicyEngine):
    """Queries Open Policy Agent for decisions."""
```

### Phase 2: Consent management API

```
policyfl/
├── api.py              # REST/MQTT API for consent management
```

A lightweight API that allows:
- Granting consent (subject X consents to purpose Y on devices [a,b,c])
- Revoking consent (takes effect on next training round)
- Querying consent status
- Temporal expiration (consent auto-expires after duration)
- Spatial zones (device groups mapped to physical areas)

This can be REST (Flask/FastAPI) or MQTT-based (consent updates published to topics).

### Phase 3: Audit trail

```
policyfl/
├── audit.py            # Audit logging
```

Every policy decision is logged:
- Timestamp
- Device ID
- Purpose
- Decision (allow/deny)
- Which consent records were checked
- Training round ID

This creates a verifiable trail for GDPR compliance: regulators can check that every training round respected consent.

### Phase 4: Federated unlearning hook

When consent is revoked, PolicyFL should:
1. Immediately block future training on that subject's data
2. Record which rounds used that subject's data
3. Trigger or flag the need for federated unlearning on those rounds

This connects to existing unlearning work (FedEraser, VeriFi) but the trigger mechanism (consent revocation) is novel.

## How to test during development

```python
# test_basic.py
# 1. Create a consent store with some records
store = JSONConsentStore("test_consents.json")

# 2. Create a policy engine
engine = SimpleEngine(store)

# 3. Create the mod
mod = make_policyfl_mod(engine)

# 4. Simulate a training message
# Consented device -> should train
result = mod(msg_consented, context, mock_call_next)
assert mock_call_next.called

# Non-consented device -> should block
result = mod(msg_no_consent, context, mock_call_next)
assert not mock_call_next.called

# Expired consent -> should block
result = mod(msg_expired, context, mock_call_next)
assert not mock_call_next.called

# Revoked consent -> should block
store.revoke_consent("subject_1")
result = mod(msg_consented, context, mock_call_next)
assert not mock_call_next.called
```

### Integration test with Flower

```bash
# Terminal 1: Start Flower server
flower-superlink

# Terminal 2: Start SuperNode with PolicyFL mod
# (ClientApp registers policyfl_mod)
flower-supernode --superlink localhost:9092

# Terminal 3: Run training
flwr run .

# Check audit log: some rounds should show ALLOW, some DENY
# Modify consent mid-training and verify next round reflects it
```

## Key design decisions

1. **Mod, not transport layer.** PolicyFL operates at the application layer inside Flower, not at the transport layer. This makes it transport-agnostic (works with gRPC, REST, or MQTT/FlowerMQ).

2. **Pre-training enforcement.** Consent is checked BEFORE training starts, not after. The data never reaches the model if consent is missing. Prevention, not remediation.

3. **Per-subject, not per-institution.** FLA3 checks "is this hospital authorized for this study?" PolicyFL checks "did this specific person consent to this specific purpose for this specific sensor?"

4. **Real-time.** Consent state is checked every round. Revocation takes effect on the next round, not after redeployment.

5. **Purpose-bound.** Consent is per-purpose. Same sensor, same person, but different purposes can have different consent states. "I consent to energy optimization but not activity profiling."

## Dependencies

```
flwr>=1.20.0    # Flower framework (for Mod interface)
pydantic>=2.0   # Data validation (optional, can use dataclasses)
```

OPA integration (optional):
```
requests        # For querying OPA REST API
```

## Example consent file (consents.json)

```json
{
  "consents": [
    {
      "subject_id": "person_001",
      "device_ids": ["motion_sensor_3A", "temp_sensor_3B"],
      "purposes": ["energy_optimization"],
      "granted_at": "2026-03-01T09:00:00Z",
      "expires_at": "2026-06-01T09:00:00Z",
      "revoked": false
    },
    {
      "subject_id": "person_002",
      "device_ids": ["motion_sensor_3A", "camera_3C"],
      "purposes": ["energy_optimization", "occupancy_counting"],
      "granted_at": "2026-03-15T10:00:00Z",
      "expires_at": null,
      "revoked": false
    }
  ],
  "purposes": [
    {
      "name": "energy_optimization",
      "description": "Optimize HVAC and lighting based on occupancy patterns",
      "allowed_features": ["motion", "temperature", "humidity"]
    },
    {
      "name": "occupancy_counting",
      "description": "Count number of people in spaces",
      "allowed_features": ["motion", "depth"]
    },
    {
      "name": "activity_profiling",
      "description": "Track individual activity patterns",
      "allowed_features": ["motion", "camera", "audio"]
    }
  ]
}
```

## What success looks like

1. `pip install policyfl`
2. User adds `mods=[policyfl_mod]` to their ClientApp
3. Training rounds are automatically gated by consent
4. Consent revocation takes effect on the next round
5. Audit log shows every decision with timestamp and reason
6. Demo: smart building scenario where consent changes mid-training and the system responds in real time

## Reference: prior art

- FLA3 (CaMLSys, 2026): XACML access control for healthcare FL. Institutional consent. Our target: per-subject IoT consent.
- FedEraser (2021): Federated unlearning. We trigger it on consent revocation.
- Flower Mods: LocalDpMod, secagg_mod. Study these for the Mod pattern.
- GDPR Articles 5, 6, 7, 17: Legal basis for what we enforce.

"""Tests for policyfl.spatial — spatial zone management."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from policyfl.consent_store import JSONConsentStore
from policyfl.spatial import SpatialZoneManager, Zone


def _make_store(tmp_path, consents):
    path = tmp_path / "consents.json"
    path.write_text(json.dumps({"consents": consents}))
    return JSONConsentStore(path)


def _consent(subject_id, device_ids, purposes, revoked=False):
    now = datetime.now(timezone.utc)
    return {
        "subject_id": subject_id,
        "device_ids": device_ids,
        "purposes": purposes,
        "granted_at": (now - timedelta(days=10)).isoformat(),
        "expires_at": None,
        "revoked": revoked,
        "revoked_at": now.isoformat() if revoked else None,
    }


LOBBY = Zone(zone_id="lobby", name="Public Lobby", device_ids=["sensor_L1", "sensor_L2"])
MEETING_ROOM = Zone(zone_id="meeting_room", name="Private Meeting Room", device_ids=["sensor_M1"])
HALLWAY = Zone(zone_id="hallway", name="Hallway", device_ids=["sensor_L2", "sensor_H1"])


class TestEnterZone:
    def test_subject_tracked_in_zone(self, tmp_path):
        store = _make_store(tmp_path, [_consent("alice", ["sensor_L1"], ["energy"])])
        mgr = SpatialZoneManager(store, [LOBBY])

        mgr.enter_zone("alice", "lobby")

        assert "alice" in mgr.get_active_subjects("lobby")

    def test_reactivates_spatially_suspended_consent(self, tmp_path):
        store = _make_store(tmp_path, [_consent("alice", ["sensor_L1"], ["energy"])])
        mgr = SpatialZoneManager(store, [LOBBY])

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")

        # Consent should be suspended
        decision = store.check_consent("sensor_L1", "energy")
        assert decision.allowed is False

        # Re-enter — consent reactivated
        mgr.enter_zone("alice", "lobby")
        decision = store.check_consent("sensor_L1", "energy")
        assert decision.allowed is True

    def test_unknown_zone_raises(self, tmp_path):
        store = _make_store(tmp_path, [])
        mgr = SpatialZoneManager(store, [LOBBY])

        with pytest.raises(KeyError, match="Unknown zone"):
            mgr.enter_zone("alice", "nonexistent")


class TestLeaveZone:
    def test_suspends_consent_on_leave(self, tmp_path):
        store = _make_store(tmp_path, [_consent("alice", ["sensor_L1"], ["energy"])])
        mgr = SpatialZoneManager(store, [LOBBY])

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")

        decision = store.check_consent("sensor_L1", "energy")
        assert decision.allowed is False

    def test_subject_removed_from_zone(self, tmp_path):
        store = _make_store(tmp_path, [_consent("alice", ["sensor_L1"], ["energy"])])
        mgr = SpatialZoneManager(store, [LOBBY])

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")

        assert "alice" not in mgr.get_active_subjects("lobby")

    def test_does_not_suspend_manually_revoked(self, tmp_path):
        """If consent is already revoked, leaving should not add it to suspended tracking."""
        store = _make_store(
            tmp_path, [_consent("alice", ["sensor_L1"], ["energy"], revoked=True)]
        )
        mgr = SpatialZoneManager(store, [LOBBY])

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")

        # Re-enter — should NOT reactivate because the original revocation was manual
        mgr.enter_zone("alice", "lobby")
        decision = store.check_consent("sensor_L1", "energy")
        assert decision.allowed is False

    def test_unknown_zone_raises(self, tmp_path):
        store = _make_store(tmp_path, [])
        mgr = SpatialZoneManager(store, [LOBBY])

        with pytest.raises(KeyError, match="Unknown zone"):
            mgr.leave_zone("alice", "nonexistent")


class TestSharedDevices:
    def test_shared_device_not_suspended_while_in_other_zone(self, tmp_path):
        """sensor_L2 is in both lobby and hallway. Leaving lobby shouldn't suspend it
        if the subject is still in the hallway."""
        store = _make_store(tmp_path, [_consent("alice", ["sensor_L2"], ["energy"])])
        mgr = SpatialZoneManager(store, [LOBBY, HALLWAY])

        mgr.enter_zone("alice", "lobby")
        mgr.enter_zone("alice", "hallway")

        # Leave lobby — sensor_L2 is still covered by hallway
        mgr.leave_zone("alice", "lobby")

        decision = store.check_consent("sensor_L2", "energy")
        assert decision.allowed is True

    def test_shared_device_suspended_when_leaving_all_zones(self, tmp_path):
        store = _make_store(tmp_path, [_consent("alice", ["sensor_L2"], ["energy"])])
        mgr = SpatialZoneManager(store, [LOBBY, HALLWAY])

        mgr.enter_zone("alice", "lobby")
        mgr.enter_zone("alice", "hallway")
        mgr.leave_zone("alice", "lobby")
        mgr.leave_zone("alice", "hallway")

        decision = store.check_consent("sensor_L2", "energy")
        assert decision.allowed is False


class TestGetActiveSubjects:
    def test_multiple_subjects(self, tmp_path):
        store = _make_store(
            tmp_path,
            [
                _consent("alice", ["sensor_L1"], ["energy"]),
                _consent("bob", ["sensor_L1"], ["energy"]),
            ],
        )
        mgr = SpatialZoneManager(store, [LOBBY])

        mgr.enter_zone("alice", "lobby")
        mgr.enter_zone("bob", "lobby")

        subjects = mgr.get_active_subjects("lobby")
        assert subjects == ["alice", "bob"]

    def test_empty_zone(self, tmp_path):
        store = _make_store(tmp_path, [])
        mgr = SpatialZoneManager(store, [LOBBY])

        assert mgr.get_active_subjects("lobby") == []


class TestGetZonesForSubject:
    def test_subject_in_multiple_zones(self, tmp_path):
        store = _make_store(
            tmp_path,
            [_consent("alice", ["sensor_L1", "sensor_M1"], ["energy"])],
        )
        mgr = SpatialZoneManager(store, [LOBBY, MEETING_ROOM])

        mgr.enter_zone("alice", "lobby")
        mgr.enter_zone("alice", "meeting_room")

        assert mgr.get_zones_for_subject("alice") == ["lobby", "meeting_room"]

    def test_subject_in_no_zones(self, tmp_path):
        store = _make_store(tmp_path, [])
        mgr = SpatialZoneManager(store, [LOBBY])

        assert mgr.get_zones_for_subject("alice") == []


class TestGetDevicesInZone:
    def test_returns_devices(self, tmp_path):
        store = _make_store(tmp_path, [])
        mgr = SpatialZoneManager(store, [LOBBY])

        assert mgr.get_devices_in_zone("lobby") == ["sensor_L1", "sensor_L2"]


class TestLobbyToMeetingRoom:
    """Scenario: Alice walks from a public lobby to a private meeting room.

    Setup:
    - Lobby has sensors sensor_L1, sensor_L2 (motion, temperature)
    - Meeting room has sensor_M1 (occupancy camera)
    - Alice consented to energy_optimization for lobby sensors
    - Alice consented to occupancy_counting for the meeting room sensor

    Sequence:
    1. Alice enters lobby → lobby sensors can use her data
    2. Alice leaves lobby, enters meeting room
       → lobby sensors lose access, meeting room sensor gains access
    3. Alice leaves meeting room → all sensors lose access
    4. Alice re-enters lobby → lobby sensors regain access
    """

    @pytest.fixture
    def scenario(self, tmp_path):
        store = _make_store(
            tmp_path,
            [
                _consent("alice", ["sensor_L1", "sensor_L2"], ["energy_optimization"]),
                _consent("alice", ["sensor_M1"], ["occupancy_counting"]),
            ],
        )
        lobby = Zone(zone_id="lobby", name="Public Lobby", device_ids=["sensor_L1", "sensor_L2"])
        meeting = Zone(zone_id="meeting_room", name="Private Meeting Room", device_ids=["sensor_M1"])
        mgr = SpatialZoneManager(store, [lobby, meeting])
        return store, mgr

    def test_step1_enter_lobby(self, scenario):
        store, mgr = scenario

        mgr.enter_zone("alice", "lobby")

        assert store.check_consent("sensor_L1", "energy_optimization").allowed is True
        assert store.check_consent("sensor_L2", "energy_optimization").allowed is True
        assert mgr.get_active_subjects("lobby") == ["alice"]

    def test_step2_walk_to_meeting_room(self, scenario):
        store, mgr = scenario

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")
        mgr.enter_zone("alice", "meeting_room")

        # Lobby sensors: suspended
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is False
        assert store.check_consent("sensor_L2", "energy_optimization").allowed is False
        # Meeting room sensor: active
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is True
        # Presence tracking
        assert mgr.get_active_subjects("lobby") == []
        assert mgr.get_active_subjects("meeting_room") == ["alice"]

    def test_step3_leave_meeting_room(self, scenario):
        store, mgr = scenario

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")
        mgr.enter_zone("alice", "meeting_room")
        mgr.leave_zone("alice", "meeting_room")

        # All sensors: suspended
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is False
        assert store.check_consent("sensor_L2", "energy_optimization").allowed is False
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is False

    def test_step4_return_to_lobby(self, scenario):
        store, mgr = scenario

        mgr.enter_zone("alice", "lobby")
        mgr.leave_zone("alice", "lobby")
        mgr.enter_zone("alice", "meeting_room")
        mgr.leave_zone("alice", "meeting_room")
        mgr.enter_zone("alice", "lobby")

        # Lobby sensors: reactivated
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is True
        assert store.check_consent("sensor_L2", "energy_optimization").allowed is True
        # Meeting room sensor: still suspended
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is False

    def test_full_walkthrough(self, scenario):
        """Complete walkthrough of the lobby-to-meeting-room scenario."""
        store, mgr = scenario

        # 1. Alice enters lobby
        mgr.enter_zone("alice", "lobby")
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is True
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is True  # not yet suspended

        # 2. Alice walks to meeting room
        mgr.leave_zone("alice", "lobby")
        mgr.enter_zone("alice", "meeting_room")
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is False
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is True

        # 3. Alice leaves the building
        mgr.leave_zone("alice", "meeting_room")
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is False
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is False

        # 4. Next day — Alice returns to the lobby
        mgr.enter_zone("alice", "lobby")
        assert store.check_consent("sensor_L1", "energy_optimization").allowed is True
        assert store.check_consent("sensor_M1", "occupancy_counting").allowed is False

        # Verify zones
        assert mgr.get_zones_for_subject("alice") == ["lobby"]

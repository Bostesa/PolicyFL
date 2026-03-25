"""Spatial zone management for PolicyFL.

Maps physical zones (rooms, areas) to IoT devices and manages consent
state based on subject presence. When a subject leaves a zone, their
consent for devices in that zone is suspended. When they enter, previously
suspended consent is reactivated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from policyfl.consent_store import ConsentStore


@dataclass
class Zone:
    """A physical zone containing IoT devices."""

    zone_id: str
    name: str
    device_ids: list[str]


class SpatialZoneManager:
    """Manages subject presence in zones and updates consent state accordingly.

    When a subject leaves a zone, consent for devices exclusive to that zone
    is suspended (revoked via the store). When they re-enter, only consents
    that were spatially suspended — not manually revoked — are reactivated.

    Devices shared between zones are only suspended when the subject has left
    ALL zones containing that device.

    Parameters
    ----------
    consent_store : ConsentStore
        The consent store to update.
    zones : list[Zone]
        Zone definitions mapping zone IDs to device lists.
    """

    def __init__(self, consent_store: ConsentStore, zones: list[Zone]) -> None:
        self._store = consent_store
        self._zones: dict[str, Zone] = {z.zone_id: z for z in zones}
        # zone_id -> set of subject_ids currently present
        self._presence: dict[str, set[str]] = defaultdict(set)
        # (subject_id, device_id) pairs suspended by this manager
        self._suspended: set[tuple[str, str]] = set()

    def enter_zone(self, subject_id: str, zone_id: str) -> None:
        """Record a subject entering a zone and reactivate any spatially suspended consent."""
        if zone_id not in self._zones:
            raise KeyError(f"Unknown zone: {zone_id}")

        self._presence[zone_id].add(subject_id)

        zone = self._zones[zone_id]
        to_reactivate: list[str] = []
        for device_id in zone.device_ids:
            key = (subject_id, device_id)
            if key in self._suspended:
                to_reactivate.append(device_id)
                self._suspended.discard(key)

        if to_reactivate:
            self._store.reactivate_for_devices(subject_id, to_reactivate)

    def leave_zone(self, subject_id: str, zone_id: str) -> None:
        """Record a subject leaving a zone and suspend consent for devices exclusive to it."""
        if zone_id not in self._zones:
            raise KeyError(f"Unknown zone: {zone_id}")

        self._presence[zone_id].discard(subject_id)

        zone = self._zones[zone_id]

        # Devices still covered by other zones the subject is in
        still_covered: set[str] = set()
        for zid, subjects in self._presence.items():
            if subject_id in subjects:
                still_covered.update(self._zones[zid].device_ids)

        # Only suspend devices not covered by another zone
        candidates = [d for d in zone.device_ids if d not in still_covered]
        if not candidates:
            return

        # Only suspend if the subject has active (non-revoked) consent for these devices
        records = self._store.get_consent_status(subject_id)
        active_devices: set[str] = set()
        for rec in records:
            if not rec.revoked:
                active_devices.update(rec.device_ids)

        to_suspend = [d for d in candidates if d in active_devices]
        if to_suspend:
            self._store.revoke_for_devices(subject_id, to_suspend)
            for d in to_suspend:
                self._suspended.add((subject_id, d))

    def get_active_subjects(self, zone_id: str) -> list[str]:
        """Return subjects currently present in a zone."""
        if zone_id not in self._zones:
            raise KeyError(f"Unknown zone: {zone_id}")
        return sorted(self._presence.get(zone_id, set()))

    def get_zones_for_subject(self, subject_id: str) -> list[str]:
        """Return zone IDs the subject is currently in."""
        return sorted(
            zid for zid, subjects in self._presence.items() if subject_id in subjects
        )

    def get_devices_in_zone(self, zone_id: str) -> list[str]:
        """Return device IDs in a zone."""
        if zone_id not in self._zones:
            raise KeyError(f"Unknown zone: {zone_id}")
        return list(self._zones[zone_id].device_ids)

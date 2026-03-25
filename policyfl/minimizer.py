"""Data minimization for PolicyFL — GDPR Article 5(1)(c) enforcement.

Strips disallowed features from training messages based on purpose-specific
allowed_features lists. Instead of just allowing or blocking training,
this ensures only the minimum necessary data reaches the model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from flwr.common import RecordDict
from flwr.common.record import ArrayRecord

from policyfl.models import Purpose

logger = logging.getLogger("policyfl")


@dataclass
class FilterResult:
    """Result of a data minimization operation."""

    removed: list[str]
    kept: list[str]


class DataMinimizer:
    """Enforces feature-level data minimization based on purpose definitions.

    Each ``Purpose`` has an ``allowed_features`` list. When filtering,
    only features in that list are retained; everything else is stripped.

    Parameters
    ----------
    purposes : dict[str, Purpose]
        Mapping of purpose name to Purpose definition.
    """

    def __init__(self, purposes: dict[str, Purpose]) -> None:
        self._purposes = purposes

    def get_allowed_features(self, purpose: str) -> set[str] | None:
        """Return allowed features for a purpose, or None if no filtering needed."""
        p = self._purposes.get(purpose)
        if p is None or p.allowed_features is None:
            return None
        return set(p.allowed_features)

    def filter_record_dict(self, purpose: str, content: RecordDict) -> FilterResult:
        """Remove disallowed feature entries from a RecordDict.

        Filters top-level ``ArrayRecord`` entries whose keys are not in the
        purpose's ``allowed_features``. ``ConfigRecord`` and ``MetricsRecord``
        entries are never touched.

        Returns a FilterResult with the removed and kept feature names.
        """
        allowed = self.get_allowed_features(purpose)
        if allowed is None:
            return FilterResult(
                removed=[], kept=list(content.array_records.keys())
            )

        removed: list[str] = []
        kept: list[str] = []

        for key in list(content.array_records):
            if key in allowed:
                kept.append(key)
            else:
                del content[key]
                removed.append(key)

        return FilterResult(removed=removed, kept=kept)

    def filter_array_record(
        self, purpose: str, record: ArrayRecord
    ) -> FilterResult:
        """Remove disallowed feature keys from a single ArrayRecord.

        Use this when all features are stored as keys within one ArrayRecord
        (e.g. ``content["features"]["motion"]``, ``content["features"]["camera"]``).
        """
        allowed = self.get_allowed_features(purpose)
        if allowed is None:
            return FilterResult(removed=[], kept=list(record.keys()))

        removed: list[str] = []
        kept: list[str] = []

        for key in list(record):
            if key in allowed:
                kept.append(key)
            else:
                del record[key]
                removed.append(key)

        return FilterResult(removed=removed, kept=kept)

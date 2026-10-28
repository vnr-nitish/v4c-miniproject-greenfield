"""Entity — the abstract base class of every entity (docs/backend_design.md, decision B1).

Entities are immutable (frozen) dataclasses that validate themselves when they are created:
an invalid Employee, Project or Review cannot exist, and nobody can change one afterwards
(assigning to an attribute raises FrozenInstanceError). A change produces a new object through
with_changes(), which is validated again. Every broken rule is reported at once, so a form can
show all of its problems together.

Entities hold only the rules that need nothing but the entity itself (formats, ranges, date
order). Rules that need other tables — is the role offered in this department, is the manager
still employed — are checked by the stored procedures, and the database constraints remain the
final guard.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Self

from src.exceptions import ValidationError


class Rules:
    """Collects broken rules as {field: message}; the first message per field wins."""

    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    def require(self, ok: bool, field: str, message: str) -> None:
        if not ok and field not in self.errors:
            self.errors[field] = message


@dataclass(frozen=True)
class Entity(ABC):
    """Validated, immutable domain object."""

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if type(value).__module__ == "numpy":     # e.g. an id picked from a DataFrame column
                self._set(f.name, value.item())
        self._normalize()
        errors = self.validation_errors()
        if errors:
            raise ValidationError(errors)

    def _normalize(self) -> None:
        """Bring loosely typed input (form values, database values) into canonical types."""

    def _set(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)        # only used while the object is being built

    @abstractmethod
    def validation_errors(self) -> dict[str, str]:
        """Every broken rule as {field: message}; empty when the entity is valid."""

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Self:
        """Build the entity from a database row; columns the entity does not have are ignored."""
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in row.items() if key in names})

    def to_record(self) -> dict[str, Any]:
        """The entity's attributes as a plain dictionary."""
        return asdict(self)

    def with_changes(self, **changes: Any) -> Self:
        """A copy with some attributes changed — validated again, so it is valid too."""
        return replace(self, **changes)


# ----------------------------------------------------------------------------- shared helpers

def as_decimal(value: Any, places: str = "0.01") -> Any:
    """Numbers become Decimals rounded to cents; anything else is returned unchanged (and fails validation)."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        return value


def as_date(value: Any) -> Any:
    """datetime -> date; anything else unchanged."""
    return value.date() if isinstance(value, datetime) else value


def is_date(value: Any) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def in_range(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, Decimal, float)) and not isinstance(value, bool) and low <= value <= high


def is_text(value: Any, max_length: int, min_length: int = 1) -> bool:
    return isinstance(value, str) and min_length <= len(value.strip()) and len(value) <= max_length


def full_years(start: date, end: date) -> int:
    """Completed years between two dates (the same rule as MySQL TIMESTAMPDIFF(YEAR, ...))."""
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))

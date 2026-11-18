from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Self

from src.exceptions import ValidationError


# Helper that collects every broken rule, so a form can show all its problems together.
class Rules:
    """Collects broken rules as {field: message}; the first message per field wins."""

    # starts with no errors
    def __init__(self) -> None:
        self.errors: dict[str, str] = {}

    # If 'ok' is False, records the message for this field (only the first problem per field is kept).
    def require(self, ok: bool, field: str, message: str) -> None:
        if not ok and field not in self.errors:
            self.errors[field] = message


# OOP (Abstraction + Encapsulation): Entity is the abstract base of Employee, Position, Project, Assignment
# and Review. frozen=True makes the objects immutable: their values can't be changed after creation.
@dataclass(frozen=True)
class Entity(ABC):
    """Validated, immutable domain object."""

    # Runs automatically right after the attributes are filled: tidy the input, then validate. So an invalid
    # object can never exist.
    def __post_init__(self) -> None:
        # numpy numbers (e.g. an id taken from a DataFrame) become plain Python numbers
        for f in fields(self):
            value = getattr(self, f.name)
            if type(value).__module__ == "numpy":     # e.g. an id picked from a DataFrame column
                self._set(f.name, value.item())
        # tidy the input first, e.g. strip spaces, datetime to date
        self._normalize()
        # then check every rule; any broken rule raises one ValidationError that lists them all
        errors = self.validation_errors()
        if errors:
            raise ValidationError(errors)

    # Empty here: each entity overrides it to tidy its own fields (polymorphism).
    def _normalize(self) -> None:
        """Bring loosely typed input (form values, database values) into canonical types."""

    # The object is frozen, so this special call sets a value while it's being built.
    def _set(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)        # only used while the object is being built

    # OOP (Polymorphism): abstract here. Each entity class writes its own rules, and __post_init__ calls the
    # version that belongs to the object.
    @abstractmethod
    def validation_errors(self) -> dict[str, str]:
        """Every broken rule as {field: message}; empty when the entity is valid."""

    # Turns a database row (a dict) into an entity object; every manager's get() uses it.
    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> Self:
        """Build the entity from a database row; columns the entity does not have are ignored."""
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in row.items() if key in names})

    # The object as a plain dictionary {attribute: value}.
    def to_record(self) -> dict[str, Any]:
        """The entity's attributes as a plain dictionary."""
        return asdict(self)

    # The only way to 'change' an entity: a validated copy with the new values.
    def with_changes(self, **changes: Any) -> Self:
        """A copy with some attributes changed — validated again, so it is valid too."""
        return replace(self, **changes)


# ----------------------------------------------------------------------------- shared helpers

# Turns a number into an exact money value with 2 decimals.
def as_decimal(value: Any, places: str = "0.01") -> Any:
    """Numbers become Decimals rounded to cents; anything else is returned unchanged (and fails validation)."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        return Decimal(str(value)).quantize(Decimal(places))
    except (InvalidOperation, ValueError):
        return value


# A datetime becomes just its date.
def as_date(value: Any) -> Any:
    """datetime -> date; anything else unchanged."""
    return value.date() if isinstance(value, datetime) else value


# Check: is this a date?
def is_date(value: Any) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


# Check: is this a whole number (and not True/False)?
def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# Check: is this a number between low and high?
def in_range(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, Decimal, float)) and not isinstance(value, bool) and low <= value <= high


# Check: is this text, with a length in the allowed range?
def is_text(value: Any, max_length: int, min_length: int = 1) -> bool:
    return isinstance(value, str) and min_length <= len(value.strip()) and len(value) <= max_length


# Whole years between two dates, e.g. someone's age on a given day.
def full_years(start: date, end: date) -> int:
    """Completed years between two dates (the same rule as MySQL TIMESTAMPDIFF(YEAR, ...))."""
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))

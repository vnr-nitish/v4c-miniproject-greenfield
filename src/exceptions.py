"""Application-specific exceptions, so callers can handle failures by category.

    HRAppError
    ├── ConfigurationError   a setting is missing or invalid
    ├── DatabaseError        the database could not do its job (connection, unexpected SQL error)
    ├── ValidationError      input breaks a rule of an entity        -> the user can fix the form
    ├── BusinessRuleError    the database refused the change         -> the user can fix the input
    │   ├── DuplicateError       the record already exists
    │   └── OverAllocationError  an assignment would take someone above 100 %
    └── NotFoundError        the requested record does not exist

The Streamlit pages show ValidationError / BusinessRuleError / NotFoundError as warnings with
their message, and DatabaseError as an error.
"""
from __future__ import annotations


class HRAppError(Exception):
    """Base class for every error raised by this application."""


class ConfigurationError(HRAppError):
    """A required setting (e.g. database credentials) is missing or invalid."""


class DatabaseError(HRAppError):
    """A database operation failed (connection, query or transaction)."""


class ValidationError(HRAppError):
    """Input data violates a business rule.

    `errors` maps each field to its message, so a form can show every problem at once.
    A plain message is stored under the empty key.
    """

    def __init__(self, errors: dict[str, str] | str) -> None:
        self.errors = dict(errors) if isinstance(errors, dict) else {"": errors}
        super().__init__("; ".join(f"{field}: {message}" if field else message
                                   for field, message in self.errors.items()))


class BusinessRuleError(HRAppError):
    """The database refused a change because it breaks a business rule (procedure, key or constraint)."""


class DuplicateError(BusinessRuleError):
    """The record already exists (a unique key would be violated)."""


class OverAllocationError(BusinessRuleError):
    """An assignment would take an employee above 100 % allocation."""

    def __init__(self, message: str, current_pct: int, requested_pct: int) -> None:
        self.current_pct = current_pct
        self.requested_pct = requested_pct
        super().__init__(message)


class NotFoundError(HRAppError):
    """The requested record does not exist."""

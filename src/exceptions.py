"""Application exceptions, grouped under HRAppError so callers can handle each kind of failure.

The pages show ValidationError, BusinessRuleError and NotFoundError as warnings and DatabaseError as an error.
"""
from __future__ import annotations


# OOP (Inheritance): the parent of all the app's errors, so 'except HRAppError' catches every one of them.
class HRAppError(Exception):
    """Base class for every error raised by this application."""

    # the name of the database rule behind the error, if known (e.g. uq_employees_email)
    constraint: str | None = None          # the database constraint behind the error, when known


# A setting is missing or wrong, e.g. no DB_PASSWORD in .env.
class ConfigurationError(HRAppError):
    """A required setting (e.g. database credentials) is missing or invalid."""


# The database itself failed: no connection, or an unexpected SQL error.
class DatabaseError(HRAppError):
    """A database operation failed (connection, query or transaction)."""


# Every connection stayed busy for the whole wait: try again in a moment.
class PoolBusyError(DatabaseError):
    """Every connection of the shared pool stayed in use for the whole wait. The database is fine,
    so nothing may be repaired or reset because of this."""


# Typed input breaks a rule. It holds one message per field, so a form can show them all.
class ValidationError(HRAppError):
    """Input data violates a business rule.

    `errors` maps each field to its message, so a form can show every problem at once.
    A plain message is stored under the empty key.
    """

    # Accepts {field: message} or one plain message, and builds the combined error text.
    def __init__(self, errors: dict[str, str] | str) -> None:
        self.errors = dict(errors) if isinstance(errors, dict) else {"": errors}
        super().__init__("; ".join(f"{field}: {message}" if field else message
                                   for field, message in self.errors.items()))


# The database refused the change, e.g. a role that doesn't exist in that department.
class BusinessRuleError(HRAppError):
    """The database refused a change because it breaks a business rule (procedure, key or constraint)."""


# The record already exists, e.g. the same e-mail twice.
class DuplicateError(BusinessRuleError):
    """The record already exists (a unique key would be violated)."""


# An assignment would book someone for more than 100 % of their time; it keeps both numbers.
class OverAllocationError(BusinessRuleError):
    """An assignment would take an employee above 100 % allocation."""

    def __init__(self, message: str, current_pct: int, requested_pct: int) -> None:
        self.current_pct = current_pct
        self.requested_pct = requested_pct
        super().__init__(message)


# The record asked for doesn't exist, e.g. a mistyped employee code.
class NotFoundError(HRAppError):
    """The requested record does not exist."""

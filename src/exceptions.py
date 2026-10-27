"""Application-specific exceptions, so callers can handle failures by category."""


class HRAppError(Exception):
    """Base class for every error raised by this application."""


class ConfigurationError(HRAppError):
    """A required setting (e.g. database credentials) is missing or invalid."""


class DatabaseError(HRAppError):
    """A database operation failed (connection, query or transaction)."""


class ValidationError(HRAppError):
    """Input data violates a business rule."""

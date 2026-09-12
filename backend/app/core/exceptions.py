from typing import Any, Dict, List, Optional


class AppException(Exception):
    """Base application exception for domain and infrastructure errors."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_ERROR",
        status_code: int = 500,
        details: Optional[List[Dict[str, Any]]] = None,
    ):
        # Gracefully handle inverted positional arguments e.g. AppException("NOT_FOUND", "Item not found", 404)
        known_codes = {"NOT_FOUND", "BAD_REQUEST", "CONFLICT", "INTERNAL_ERROR", "VALIDATION_ERROR", "UNAUTHORIZED", "FORBIDDEN"}
        if message in known_codes and code not in known_codes and code != "INTERNAL_ERROR":
            message, code = code, message

        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or []


class DatabaseUnavailableError(AppException):
    """Raised when primary database connectivity cannot be established."""

    def __init__(self, message: str = "Database service is temporarily unavailable", details: Optional[List[Dict[str, Any]]] = None):
        super().__init__(
            message=message,
            code="DATABASE_UNAVAILABLE",
            status_code=503,
            details=details,
        )


class EntityNotFoundError(AppException):
    """Raised when a requested resource is not found."""

    def __init__(self, entity_name: str, identifier: Any):
        super().__init__(
            message=f"{entity_name} with identifier '{identifier}' was not found",
            code="NOT_FOUND",
            status_code=404,
        )


class EntityConflictError(AppException):
    """Raised when an operation would violate unique or state constraints."""

    def __init__(self, message: str):
        super().__init__(
            message=message,
            code="CONFLICT",
            status_code=409,
        )

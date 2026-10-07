"""
Enterprise KPI Management System - Custom Exception Handlers
"""
from typing import Any

from fastapi import HTTPException, status


class CustomAppException(HTTPException):
    def __init__(self, status_code: int, detail: str, error_code: str | None = None, data: dict[str, Any] | None = None):
        super().__init__(status_code=status_code, detail=detail)
        self.error_code = error_code or "APP_ERROR"
        self.data = data or {}

class AuthenticationException(CustomAppException):
    def __init__(self, detail: str = "Invalid credentials or session expired."):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            error_code="AUTH_FAILED"
        )

class PermissionDeniedException(CustomAppException):
    def __init__(self, detail: str = "Access Denied. You do not have permission to perform this action."):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
            error_code="PERMISSION_DENIED"
        )

class ResourceNotFoundException(CustomAppException):
    def __init__(self, resource_name: str = "Resource", identifier: Any = None):
        detail = f"{resource_name} not found." if identifier is None else f"{resource_name} '{identifier}' not found."
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=detail,
            error_code="NOT_FOUND"
        )

class BusinessRuleViolationException(CustomAppException):
    def __init__(self, detail: str):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
            error_code="BUSINESS_RULE_VIOLATION"
        )

class AccountLockedException(CustomAppException):
    def __init__(self, minutes_remaining: int):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account locked due to multiple failed attempts. Try again in {minutes_remaining} minutes.",
            error_code="ACCOUNT_LOCKED"
        )
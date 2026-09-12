"""Hygiene domain modules. Ticket 01 owns EmployeeAccounts only."""

from services.hygiene.accounts import EmployeeAccounts

__all__ = ["EmployeeAccounts"]

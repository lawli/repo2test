"""Service-name validation.

There is no hardcoded service registry — the active profile's ``services:`` map
is the source of truth for which service names are valid. Callers
pass the known set explicitly.
"""

from __future__ import annotations

from collections.abc import Collection


class UnknownService(ValueError):
    """Raised when a service name is not present in the known set."""


def validate_service(name: str, known: Collection[str]) -> None:
    if name not in known:
        listing = ", ".join(sorted(known)) or "<none>"
        raise UnknownService(
            f"unknown service {name!r}; known services: {listing}"
        )

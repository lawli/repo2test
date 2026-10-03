"""Public exception hierarchy for apitest."""


class ApitestError(Exception):
    """Base for all apitest errors."""


class VerifyError(ApitestError):
    """A verify-phase assertion failed."""


class IsolationError(ApitestError, RuntimeError):
    """A client tried to read or write outside its case-id prefix."""


class MissingExtract(ApitestError, AttributeError):
    """A case referenced a ctx attribute that no step extracted yet."""

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        avail = ", ".join(sorted(available or [])) or "(none)"
        super().__init__(
            f"ctx.{name} not extracted yet; available: {avail}"
        )
        self.name = name
        self.available = list(available or [])


class DependencyMissing(ApitestError):
    """A cross-service dependency is unavailable in this profile."""


class SecretLeak(ApitestError):
    """Static scan caught a literal secret pattern."""

"""apitest — API test platform."""

from importlib.metadata import version

from apitest.exceptions import (
    ApitestError,
    DependencyMissing,
    IsolationError,
    MissingExtract,
    SecretLeak,
    VerifyError,
)

__version__ = version("apitest")

__all__ = [
    "DependencyMissing",
    "IsolationError",
    "MissingExtract",
    "SecretLeak",
    "ApitestError",
    "VerifyError",
]

"""Per-case context object."""

from __future__ import annotations

from typing import Any

from apitest.exceptions import MissingExtract


class Case:
    """Per-case identity + parametric IDs.

    `id` and `id_short` are required at construction; further parametric
    IDs (user_id, order_id, ...) are accepted as keyword args and stored
    as attributes.
    """

    def __init__(self, *, id: str, id_short: str, **parametric: Any) -> None:
        self.id = id
        self.id_short = id_short
        for k, v in parametric.items():
            setattr(self, k, v)


class Ctx:
    """Per-case context. Holds case, profile, clients, and step extracts."""

    def __init__(self, case: Case, profile: Any | None = None) -> None:
        self._case = case
        self._profile = profile
        self._extracts: dict[str, Any] = {}
        self._clients: dict[str, Any] = {}

    @property
    def case(self) -> Case:
        return self._case

    @property
    def profile(self) -> Any | None:
        return self._profile

    def set_extract(self, name: str, value: Any) -> None:
        if name in self._extracts:
            raise AttributeError(
                f"extract {name!r} already set; extracts are read-only after step completes"
            )
        self._extracts[name] = value

    def bind_client(self, name: str, client: Any) -> None:
        self._clients[name] = client

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._clients:
            return self._clients[name]
        if name in self._extracts:
            return self._extracts[name]
        raise MissingExtract(name, available=list(self._extracts.keys()))

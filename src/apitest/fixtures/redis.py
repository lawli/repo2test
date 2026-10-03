"""Redis engine with case-id prefix."""

from __future__ import annotations

from typing import Any, cast

import redis

from apitest.exceptions import IsolationError
from apitest.profile import Profile


class RedisClient:
    def __init__(self, profile: Profile, *, case_id_short: str, name: str = "default") -> None:
        url = profile.redis[name]["url"]
        self._raw: redis.Redis = redis.Redis.from_url(str(url))
        self._prefix = f"t:{case_id_short}:"
        self._tracked: list[str] = []

    @property
    def raw(self) -> redis.Redis:
        return self._raw

    def _key(self, key: str) -> str:
        return self._prefix + key

    def set(
        self, key: str, value: Any, *, ttl: int | None = None, unprefixed: bool = False
    ) -> None:
        """`unprefixed=True` writes the literal key so the service under test can
        read it; either way the key is tracked and deleted at cleanup()."""
        full = key if unprefixed else self._key(key)
        self._tracked.append(full)
        self._raw.set(full, value, ex=ttl)

    def hset(
        self,
        key: str,
        fields: dict[str, Any],
        *,
        ttl: int | None = None,
        unprefixed: bool = False,
    ) -> None:
        full = key if unprefixed else self._key(key)
        self._tracked.append(full)
        self._raw.hset(full, mapping=fields)
        if ttl:
            self._raw.expire(full, ttl)

    def delete_unprefixed(self, key: str) -> int:
        """Delete a literal key (one the service under test wrote)."""
        return int(cast(int, self._raw.delete(key)))

    def get(self, key: str, *, _allow_unprefixed: bool = False) -> Any:
        if _allow_unprefixed:
            v = self._raw.get(key)
            return v.decode() if isinstance(v, bytes) else v
        full = self._key(key)
        v = self._raw.get(full)
        if v is None and self._raw.exists(key):
            raise IsolationError(
                f"key {key!r} exists outside case prefix {self._prefix!r}; "
                "use unprefixed=True to opt in"
            )
        return v.decode() if isinstance(v, bytes) else v

    def exists(self, key: str, *, _allow_unprefixed: bool = False) -> bool:
        full = key if _allow_unprefixed else self._key(key)
        return bool(self._raw.exists(full))

    def ttl(self, key: str, *, _allow_unprefixed: bool = False) -> int:
        full = key if _allow_unprefixed else self._key(key)
        return int(cast(int, self._raw.ttl(full)))

    def cleanup(self) -> int:
        if not self._tracked:
            return 0
        n = self._raw.delete(*self._tracked)
        self._tracked.clear()
        return int(cast(int, n))

    def close(self) -> None:
        self._raw.close()

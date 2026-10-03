import os

import pytest

from apitest.exceptions import IsolationError
from apitest.fixtures.redis import RedisClient
from apitest.profile import Profile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_REDIS_URL"), reason="no TEST_REDIS_URL"),
]


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="test",
        redis={"default": {"url": os.environ["TEST_REDIS_URL"]}},
    )


def test_set_and_get_with_prefix(profile: Profile) -> None:
    rc = RedisClient(profile, case_id_short="cafebabe1234")
    rc.set("order:1", "PENDING", ttl=30)
    raw = rc.raw.get("t:cafebabe1234:order:1")
    assert raw == b"PENDING"
    rc.cleanup()
    assert rc.raw.get("t:cafebabe1234:order:1") is None
    rc.close()


def test_unprefixed_read_raises(profile: Profile) -> None:
    rc = RedisClient(profile, case_id_short="cafebabe1234")
    rc.raw.set("foo:bar", "x")
    with pytest.raises(IsolationError):
        rc.get("foo:bar", _allow_unprefixed=False)
    rc.raw.delete("foo:bar")
    rc.close()


def test_explicit_unprefixed_read_allowed(profile: Profile) -> None:
    rc = RedisClient(profile, case_id_short="cafebabe1234")
    rc.raw.set("svc:singleton", "x")
    assert rc.get("svc:singleton", _allow_unprefixed=True) == "x"
    rc.raw.delete("svc:singleton")
    rc.close()

"""MqClient contract with pika, without a live broker.

Design: no consumer thread. Sniffer queues are exclusive to the declaring
connection; the broker buffers messages from bind time, and drain() pulls
them with basic_get on that same connection. Broker errors during drain
propagate on the calling thread — there is no background death to mask.
"""

from typing import Any

import pytest

import apitest.fixtures.rabbitmq as rmq
from apitest.profile import Profile


class FakeMethod:
    def __init__(self, routing_key: str) -> None:
        self.routing_key = routing_key


class FakeProps:
    def __init__(self, headers: dict[str, Any] | None) -> None:
        self.headers = headers


class FakeChannel:
    def __init__(self, fail_basic_get: bool = False) -> None:
        self.declares: list[dict[str, Any]] = []
        self.queues: dict[str, list[tuple[str, dict[str, Any], bytes]]] = {}
        self._fail_basic_get = fail_basic_get

    def queue_declare(self, queue: str, **kwargs: Any) -> None:
        self.declares.append({"queue": queue, **kwargs})
        self.queues.setdefault(queue, [])

    def queue_bind(self, **kwargs: Any) -> None:
        pass

    def push(self, queue: str, routing_key: str, headers: dict[str, Any], body: bytes) -> None:
        self.queues[queue].append((routing_key, headers, body))

    def basic_get(self, queue: str, auto_ack: bool = False) -> Any:
        if self._fail_basic_get:
            raise RuntimeError("simulated broker error")
        pending = self.queues.get(queue) or []
        if not pending:
            return (None, None, None)
        rk, headers, body = pending.pop(0)
        return (FakeMethod(rk), FakeProps(headers), body)


class FakeConnection:
    fail_basic_get = False
    instances: list["FakeConnection"] = []

    def __init__(self, params: Any) -> None:
        self.chan = FakeChannel(fail_basic_get=type(self).fail_basic_get)
        self.closed = False
        type(self).instances.append(self)

    def channel(self) -> FakeChannel:
        return self.chan

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_pika(monkeypatch: pytest.MonkeyPatch) -> type[FakeConnection]:
    FakeConnection.instances = []
    FakeConnection.fail_basic_get = False
    monkeypatch.setattr(rmq.pika, "URLParameters", lambda url: url)
    monkeypatch.setattr(rmq.pika, "BlockingConnection", FakeConnection)
    return FakeConnection


def _profile() -> Profile:
    return Profile(name="test", rabbitmq={"default": {"url": "amqp://x"}})


def test_sniffer_queue_is_exclusive_for_broker_managed_cleanup(
    fake_pika: type[FakeConnection],
) -> None:
    mq = rmq.MqClient(_profile(), case_id_short="abcdef012345")
    mq.declare_sniffer(alias="orders", exchange="ex", routing_key="rk")
    declare = fake_pika.instances[0].chan.declares[0]
    assert declare["exclusive"] is True


def test_drain_pulls_buffered_messages_on_declaring_connection(
    fake_pika: type[FakeConnection],
) -> None:
    mq = rmq.MqClient(_profile(), case_id_short="abcdef012345")
    mq.declare_sniffer(alias="orders", exchange="ex", routing_key="rk")
    chan = fake_pika.instances[0].chan
    qname = chan.declares[0]["queue"]
    chan.push(qname, "rk", {"h": "1"}, b'{"id": 7}')
    chan.push(qname, "rk", {}, b'{"id": 8}')

    msgs = mq.drain("orders")

    assert len(fake_pika.instances) == 1, "drain must not open a second connection"
    assert [m.body_json["id"] for m in msgs] == [7, 8]
    assert msgs[0].routing_key == "rk"
    assert msgs[0].headers == {"h": "1"}
    assert mq.drain("orders") == []


def test_drain_error_propagates_instead_of_silent_zero(
    fake_pika: type[FakeConnection],
) -> None:
    fake_pika.fail_basic_get = True
    mq = rmq.MqClient(_profile(), case_id_short="abcdef012345")
    mq.declare_sniffer(alias="orders", exchange="ex", routing_key="rk")
    with pytest.raises(RuntimeError, match="simulated broker error"):
        mq.drain("orders")


def test_close_closes_connection(fake_pika: type[FakeConnection]) -> None:
    mq = rmq.MqClient(_profile(), case_id_short="abcdef012345")
    mq.declare_sniffer(alias="orders", exchange="ex", routing_key="rk")
    mq.close()
    assert fake_pika.instances[0].closed is True

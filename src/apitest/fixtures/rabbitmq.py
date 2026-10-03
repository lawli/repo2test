"""RabbitMQ engine — sync pika.

No consumer thread: sniffer queues are exclusive to the declaring connection,
the broker buffers messages from bind time, and drain() pulls them with
basic_get on that same connection at verify time. Exclusive queues are
deleted by the broker the moment the connection closes, so cleanup needs no
bookkeeping and survives crashes/interrupts by construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pika

from apitest.profile import Profile


@dataclass
class CapturedMessage:
    body: bytes
    routing_key: str
    headers: dict[str, Any] = field(default_factory=dict)

    @property
    def body_json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None


@dataclass
class Sniffer:
    alias: str
    queue_name: str


class MqClient:
    def __init__(self, profile: Profile, *, case_id_short: str, name: str = "default") -> None:
        url = profile.rabbitmq[name]["url"]
        self._params = pika.URLParameters(str(url))
        self._conn = pika.BlockingConnection(self._params)
        self._chan = self._conn.channel()
        self._case_id_short = case_id_short
        self._sniffers: dict[str, Sniffer] = {}

    def declare_sniffer(self, *, alias: str, exchange: str, routing_key: str) -> None:
        qname = f"apitest.sniffer.{alias}.{self._case_id_short}"
        self._chan.queue_declare(queue=qname, exclusive=True, auto_delete=True, durable=False)
        self._sniffers[alias] = Sniffer(alias=alias, queue_name=qname)
        self._chan.queue_bind(queue=qname, exchange=exchange, routing_key=routing_key)

    def publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        headers: dict[str, Any] | None = None,
    ) -> None:
        props = pika.BasicProperties(headers=headers or {})
        self._chan.basic_publish(
            exchange=exchange, routing_key=routing_key, body=body, properties=props
        )

    def drain(self, alias: str) -> list[CapturedMessage]:
        """basic_get until empty; broker errors propagate on the calling thread."""
        s = self._sniffers[alias]
        out: list[CapturedMessage] = []
        while True:
            method, props, body = self._chan.basic_get(queue=s.queue_name, auto_ack=True)
            if method is None:
                return out
            out.append(
                CapturedMessage(
                    body=body,
                    routing_key=method.routing_key,
                    headers=dict(props.headers or {}),
                )
            )

    def close(self) -> None:
        self._conn.close()

    def pending_queues(self) -> dict[str, str]:
        """Queues whose deletion is uncertain if connection closure fails."""
        return {alias: sniffer.queue_name for alias, sniffer in self._sniffers.items()}

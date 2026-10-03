import json
import os
import time

import pytest

from apitest.fixtures.rabbitmq import MqClient
from apitest.profile import Profile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_AMQP_URL"), reason="no TEST_AMQP_URL"),
]


@pytest.fixture
def profile() -> Profile:
    return Profile(
        name="test",
        rabbitmq={"default": {"url": os.environ["TEST_AMQP_URL"]}},
    )


def test_sniffer_captures_published_messages(profile: Profile) -> None:
    mq = MqClient(profile, case_id_short="abcdef012345")
    mq.declare_sniffer(
        alias="orders", exchange="amq.topic", routing_key="order.created.abcdef012345"
    )
    mq.publish("amq.topic", "order.created.abcdef012345", json.dumps({"id": 7}).encode())
    time.sleep(0.5)
    msgs = mq.drain("orders")
    assert len(msgs) == 1
    assert msgs[0].body_json == {"id": 7}
    mq.close()

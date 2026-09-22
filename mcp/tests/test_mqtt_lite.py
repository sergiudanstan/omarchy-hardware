import pytest
from ming_fakes import FakeBroker, publish_packet, wait_for

from omarchy_hardware import mqtt_lite
from omarchy_hardware.mqtt_lite import Client, MqttError, Options, filter_covers, topic_matches


@pytest.fixture
def broker_factory():
    brokers = []

    def make(**kwargs):
        broker = FakeBroker(**kwargs)
        brokers.append(broker)
        return broker

    yield make
    for broker in brokers:
        broker.close()


def _options(port, **kwargs):
    return Options(host="127.0.0.1", port=port, tls=False, timeout=2.0, **kwargs)


@pytest.mark.parametrize(
    ("topic_filter", "topic", "expected"),
    [
        ("plant/+/temp", "plant/line1/temp", True),
        ("plant/+/temp", "plant/line1/extra/temp", False),
        ("plant/#", "plant", True),  # 4.7.1.2: '#' also matches the parent level
        ("plant/#", "plant/a/b/c", True),
        ("plant/temp", "plant/temp/x", False),
        ("#", "$SYS/broker/uptime", False),
        ("+/broker/uptime", "$SYS/broker/uptime", False),
        ("$SYS/#", "$SYS/broker/uptime", True),
        ("a//b", "a//b", True),
    ],
)
def test_topic_matches(topic_filter, topic, expected):
    assert topic_matches(topic_filter, topic) is expected


@pytest.mark.parametrize(
    ("allowed", "requested", "expected"),
    [
        ("plant/#", "plant/line1/+", True),
        ("plant/#", "plant/#", True),
        ("plant/#", "plant", True),
        ("plant/+/temp", "plant/line1/temp", True),
        ("plant/+/temp", "plant/+/temp", True),
        ("plant/+/temp", "plant/#", False),  # widening
        ("plant/+/temp", "plant/+/+", False),
        ("plant/line1/temp", "plant/+/temp", False),
        ("plant/temp", "plant/temp/#", False),
        ("#", "$SYS/#", False),
        ("#", "anything/at/all", True),
        ("sensors/#", "other/#", False),
    ],
)
def test_filter_covers_only_ever_narrows(allowed, requested, expected):
    assert filter_covers(allowed, requested) is expected


def test_subscribe_collects_retained_and_live_messages(broker_factory):
    broker = broker_factory(
        retained=(("plant/line1/temp", b"21.5"),),
        deliver=(("plant/line2/temp", b"\xff\x00"),),
    )
    with Client(_options(broker.port, username="claude", password="s3cret"), max_packet=4096) as client:
        client.subscribe("plant/+/temp")
        messages, stopped = client.collect(0.5, 10)

    assert stopped == "timeout"
    assert [(m.topic, m.payload, m.retain) for m in messages] == [
        ("plant/line1/temp", b"21.5", True),
        ("plant/line2/temp", b"\xff\x00", False),
    ]
    connect = broker.connects[0]
    assert connect["protocol"] == b"MQTT" and connect["level"] == 4 and connect["clean"] is True
    assert connect["username"] == "claude" and connect["password"] == "s3cret"
    assert broker.subscriptions == ["plant/+/temp"]


def test_collect_stops_at_max_messages(broker_factory):
    broker = broker_factory(deliver=tuple((f"t/{n}", b"x") for n in range(5)))
    with Client(_options(broker.port), max_packet=4096) as client:
        client.subscribe("t/#")
        messages, stopped = client.collect(2.0, 3)
    assert stopped == "max_messages"
    assert len(messages) == 3


def test_oversized_packet_ends_collection_but_keeps_earlier_messages(broker_factory):
    broker = broker_factory(
        deliver=(("t/a", b"ok"),),
        raw_after_suback=publish_packet("t/b", b"x" * 5000),
    )
    with Client(_options(broker.port), max_packet=1024) as client:
        client.subscribe("t/#")
        messages, stopped = client.collect(2.0, 10)
    assert [m.topic for m in messages] == ["t/a"]
    assert "limit is 1024" in stopped


def test_publish_qos1_waits_for_puback(broker_factory):
    broker = broker_factory()
    with Client(_options(broker.port), max_packet=1024) as client:
        client.publish("actuators/fan", b"on", qos=1, retain=True)
        client.publish("actuators/fan", b"off")
    assert wait_for(lambda: broker.disconnects == 1)
    assert broker.published == [
        {"topic": "actuators/fan", "payload": b"on", "qos": 1, "retain": True},
        {"topic": "actuators/fan", "payload": b"off", "qos": 0, "retain": False},
    ]


def test_publish_qos1_without_ack_times_out(broker_factory):
    broker = broker_factory(ack_publish=False)
    options = Options(host="127.0.0.1", port=broker.port, tls=False, timeout=0.3)
    with Client(options, max_packet=1024) as client, pytest.raises(MqttError, match="did not acknowledge"):
        client.publish("a", b"1", qos=1)


def test_refused_connection_names_the_reason_not_the_password(broker_factory):
    broker = broker_factory(connack=4)
    options = _options(broker.port, username="u", password="hunter2")
    with pytest.raises(MqttError) as caught, Client(options, max_packet=64):
        pass
    assert "bad user name or password" in str(caught.value)
    assert "hunter2" not in str(caught.value)


def test_refused_subscription(broker_factory):
    broker = broker_factory(suback=0x80)
    with Client(_options(broker.port), max_packet=64) as client, pytest.raises(MqttError, match="refused"):
        client.subscribe("secret/#")


def test_unreachable_broker():
    with socket_closed_port() as port, pytest.raises(MqttError, match="could not connect"):
        Client(_options(port), max_packet=64).__enter__()


def test_disconnect_is_sent(broker_factory):
    broker = broker_factory()
    with Client(_options(broker.port), max_packet=64):
        pass
    assert wait_for(lambda: broker.disconnects == 1)


def test_remaining_length_encoding_round_trips():
    for value in (0, 127, 128, 16383, 16384, 2_097_151, 2_097_152, mqtt_lite.MAX_REMAINING_LENGTH):
        encoded = mqtt_lite._encode_length(value)
        decoded, shift = 0, 0
        for byte in encoded:
            decoded |= (byte & 0x7F) << shift
            shift += 7
        assert decoded == value
    with pytest.raises(MqttError):
        mqtt_lite._encode_length(mqtt_lite.MAX_REMAINING_LENGTH + 1)


class socket_closed_port:  # noqa: N801 - reads as a context manager
    """A port that was just bound and released, so nothing is listening on it."""

    def __enter__(self):
        import socket

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        return self.port

    def __exit__(self, *exc):
        return False


def test_a_long_listen_keeps_the_connection_alive_with_pings(broker_factory):
    broker = broker_factory()
    with Client(_options(broker.port), max_packet=4096) as client:
        client.subscribe("t/#")
        client._keepalive = 0.4  # ping every 0.2 s instead of every keepalive/2 seconds
        messages, stopped = client.collect(1.0, 10)
    assert stopped == "timeout"
    assert broker.pings >= 3


def test_a_malformed_publish_keeps_what_arrived_before_it(broker_factory):
    bad_topic = b"\x30\x05\x00\x02\xff\xfeX"  # topic bytes that are not UTF-8
    broker = broker_factory(deliver=(("t/a", b"ok"),), raw_after_suback=bad_topic)
    with Client(_options(broker.port), max_packet=4096) as client:
        client.subscribe("t/#")
        messages, stopped = client.collect(2.0, 10)
    assert [m.topic for m in messages] == ["t/a"]
    assert "UTF-8" in stopped


def test_a_stalled_tls_handshake_is_an_mqtt_error():
    import socket

    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen()
    try:
        options = Options(host="127.0.0.1", port=silent.getsockname()[1], tls=True, timeout=0.5)
        with pytest.raises(MqttError, match="could not connect|TLS handshake failed"):
            Client(options, max_packet=4096).__enter__()
    finally:
        silent.close()

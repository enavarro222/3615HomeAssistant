"""Le Minitel logiciel et le panneau web, face à un faux Asterisk."""

import asyncio

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from minitel_ha.line import Line, LineConfig
from minitel_ha.sip import Message, digest
from minitel_ha.web import WebConfig, make_app

from .fake_asterisk import NONCE, REALM, FakeAsterisk, check_digest

PASSWORD = "s3cret"
PAGE = b"\x0c\x1f\x42\x4d3615 MAISON\x1f\x58\x41Numero :"


def line_config(port):
    return LineConfig("127.0.0.1", port, "minitel-web", PASSWORD)


def run(coro, timeout=15):
    asyncio.run(asyncio.wait_for(coro, timeout))


async def events_until(line, predicate, timeout=5):
    seen, data = [], bytearray()
    async with asyncio.timeout(timeout):
        while True:
            kind, value = await line.events.get()
            if kind == "data":
                data += value
            else:
                seen.append(value)
            if predicate(seen, data):
                return seen, bytes(data)


def test_digest_answers_the_asterisk_challenge():
    challenge = f'Digest realm="{REALM}", nonce="{NONCE}", qop="auth", algorithm=MD5'
    header = digest("INVITE", "sip:3615@127.0.0.1", "minitel-web", PASSWORD, challenge)
    assert check_digest(header, "INVITE", PASSWORD)
    assert not check_digest(header, "INVITE", "autre")


def test_message_parse_reads_status_method_and_tag():
    msg = Message.parse(b"SIP/2.0 200 OK\r\nCSeq: 2 INVITE\r\nTo: <sip:3615@x>;tag=abc\r\n\r\nv=0")
    assert (msg.code, msg.method, msg.to_tag(), msg.body) == (200, "INVITE", "abc", "v=0")


def test_line_calls_receives_the_page_and_sends_keys():
    async def scenario():
        asterisk = FakeAsterisk(PASSWORD, PAGE)
        line = Line(line_config(await asterisk.start()))
        try:
            await line.connect()
            seen, data = await events_until(line, lambda s, d: d.endswith(b"Numero :"))
            assert seen[:2] == ["appel", "porteuse"] and "connecte" in seen
            assert data == PAGE

            line.send(b"1\x13A")  # 1 puis ENVOI : 3 caractères à 75 bauds, 0,4 s
            await asyncio.sleep(0.8)
            assert asterisk.received_keys() == b"1\x13A"

            await line.hangup()
            await asyncio.wait_for(asterisk.bye_received.wait(), 2)
        finally:
            asterisk.close()

        methods = [m.method for m in asterisk.requests if not m.is_response]
        assert methods == ["INVITE", "ACK", "INVITE", "ACK", "BYE"]

    run(scenario())


def test_line_notices_when_asterisk_hangs_up():
    async def scenario():
        asterisk = FakeAsterisk(PASSWORD, PAGE)
        line = Line(line_config(await asterisk.start()))
        try:
            await line.connect()
            await events_until(line, lambda s, d: d.endswith(b"Numero :"))
            asterisk.hangup()
            seen, _ = await events_until(line, lambda s, d: "fin" in s)
            assert line.ended.is_set()
            assert any(m.is_response and m.code == 200 and m.method == "BYE" for m in asterisk.requests)
        finally:
            asterisk.close()

    run(scenario())


def test_wrong_password_is_reported():
    async def scenario():
        asterisk = FakeAsterisk("autre", PAGE)
        line = Line(line_config(await asterisk.start()))
        try:
            try:
                await line.connect()
            except Exception as err:  # noqa: BLE001
                assert "refusé" in str(err)
            else:
                raise AssertionError("l'appel aurait dû échouer")
        finally:
            asterisk.close()

    run(scenario())


async def panel(asterisk_port, allowed=frozenset({"127.0.0.1"})):
    config = WebConfig("127.0.0.1", 0, allowed, line_config(asterisk_port))
    client = TestClient(TestServer(make_app(config)))
    await client.start_server()
    return client


def test_panel_serves_the_page_and_refuses_everyone_but_the_ingress():
    async def scenario():
        client = await panel(5060)
        try:
            page = await client.get("/")
            assert page.status == 200 and "minitel.js" in await page.text()
            assert (await client.get("/minitel.js")).status == 200
        finally:
            await client.close()

        client = await panel(5060, allowed=frozenset({"172.30.32.2"}))
        try:
            assert (await client.get("/")).status == 403
            assert (await client.get("/ws")).status == 403
        finally:
            await client.close()

    run(scenario())


def test_panel_call_from_the_browser():
    async def scenario():
        asterisk = FakeAsterisk(PASSWORD, PAGE)
        client = await panel(await asterisk.start())
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"action": "connexion"})
            statuses, screen = [], bytearray()
            async with asyncio.timeout(5):
                while not screen.endswith(b"Numero :"):
                    msg = await ws.receive()
                    if msg.type == WSMsgType.BINARY:
                        screen += msg.data
                    else:
                        statuses.append(msg.json().get("status"))
            assert bytes(screen) == PAGE
            assert statuses[:2] == ["appel", "porteuse"]

            # Un second panneau trouve la ligne occupée.
            other = await client.ws_connect("/ws")
            await other.send_json({"action": "connexion"})
            assert "occupée" in (await other.receive_json(timeout=2))["error"]
            await other.close()

            await ws.send_bytes(b"2")
            await ws.send_json({"action": "fin"})
            async with asyncio.timeout(3):
                while True:
                    msg = await ws.receive()
                    if msg.type == WSMsgType.TEXT and msg.json().get("status") == "fin":
                        break
            assert asterisk.bye_received.is_set()
            await ws.close()
        finally:
            await client.close()
            asterisk.close()

    run(scenario())

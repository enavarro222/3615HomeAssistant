"""Un faux Asterisk : défi Digest, décroché avec SDP, page en FSK 1200 bauds, BYE."""

from __future__ import annotations

import asyncio
import cmath
import hashlib
import math
import re

from minitel_ha.modem import ALAW_DECODE, FRAME, RATE, TX_BAUD, TX_MARK, TX_SPACE
from minitel_ha.sip import Message

from .test_modem import carrier_then

REALM = "asterisk"
NONCE = "1f2e3d4c"


def check_digest(header: str, method: str, password: str) -> bool:
    p = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
    h = lambda v: hashlib.md5(v.encode()).hexdigest()  # noqa: E731
    ha1 = h(f"{p['username']}:{REALM}:{password}")
    ha2 = h(f"{method}:{p['uri']}")
    return p["response"] == h(f"{ha1}:{NONCE}:{p['nc']}:{p['cnonce']}:auth:{ha2}")


class _Rtp(asyncio.DatagramProtocol):
    def __init__(self, owner: FakeAsterisk) -> None:
        self.owner = owner

    def datagram_received(self, data, addr):
        self.owner.rtp_in.append(data)
        self.owner.rtp_peer = addr


class FakeAsterisk(asyncio.DatagramProtocol):
    def __init__(self, password: str, page: bytes = b"", answer: str = "200 OK") -> None:
        self.password = password
        self.page = page
        self.answer = answer
        self.requests: list[Message] = []
        self.rtp_in: list[bytes] = []
        self.rtp_peer = None
        self.client = None
        self.invite: Message | None = None
        self.bye_received = asyncio.Event()

    async def start(self) -> int:
        loop = asyncio.get_running_loop()
        self.sip, _ = await loop.create_datagram_endpoint(lambda: self, local_addr=("127.0.0.1", 0))
        self.rtp, _ = await loop.create_datagram_endpoint(lambda: _Rtp(self), local_addr=("127.0.0.1", 0))
        return self.sip.get_extra_info("sockname")[1]

    def close(self) -> None:
        self.sip.close()
        self.rtp.close()

    def datagram_received(self, data, addr):
        msg = Message.parse(data)
        self.requests.append(msg)
        self.client = addr
        if msg.is_response:
            return
        if msg.method == "INVITE":
            auth = msg.get("Authorization")
            if not auth:
                self.reply(
                    msg,
                    "401 Unauthorized",
                    f'WWW-Authenticate: Digest realm="{REALM}", '
                    f'nonce="{NONCE}", qop="auth", algorithm=MD5\r\n',
                    tag="t1",
                )
            elif not check_digest(auth, "INVITE", self.password):
                self.reply(msg, "403 Forbidden", tag="t1")
            elif self.answer != "200 OK":
                self.reply(msg, self.answer, tag="t2")
            else:
                self.invite = msg
                rtp_port = self.rtp.get_extra_info("sockname")[1]
                sdp = (
                    f"v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=Asterisk\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
                    f"m=audio {rtp_port} RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\n"
                )
                self.reply(msg, "100 Trying")
                self.reply(
                    msg,
                    "200 OK",
                    f"Contact: <sip:127.0.0.1:{self.sip.get_extra_info('sockname')[1]}>\r\n"
                    "Content-Type: application/sdp\r\n",
                    sdp,
                    tag="as42",
                )
                media = re.search(r"m=audio (\d+)", msg.body)
                asyncio.get_running_loop().call_later(0.05, self.play, int(media.group(1)))
        elif msg.method == "BYE":
            self.reply(msg, "200 OK")
            self.bye_received.set()

    def play(self, port: int) -> None:
        """Toute la page d'un coup : le démodulateur ne regarde pas l'horloge."""
        audio = carrier_then(self.page)
        for seq, i in enumerate(range(0, len(audio), FRAME)):
            header = (
                bytes([0x80, 8]) + seq.to_bytes(2, "big") + (seq * FRAME).to_bytes(4, "big") + b"\0\0\0\1"
            )
            self.rtp.sendto(header + audio[i : i + FRAME], ("127.0.0.1", port))

    def hangup(self) -> None:
        """Asterisk raccroche, comme quand le service ferme la connexion."""
        m = self.invite
        lines = [
            f"BYE sip:minitel-web@{self.client[0]}:{self.client[1]} SIP/2.0",
            "Via: SIP/2.0/UDP 127.0.0.1:5060;branch=z9hG4bKbye1",
            f"From: {m.get('To')};tag=as42",
            f"To: {m.get('From')}",
            f"Call-ID: {m.get('Call-ID')}",
            "CSeq: 102 BYE",
            "Content-Length: 0",
        ]
        self.sip.sendto(("\r\n".join(lines) + "\r\n\r\n").encode(), self.client)

    def reply(self, req: Message, status: str, extra: str = "", body: str = "", tag: str = "") -> None:
        to = req.get("To") + (f";tag={tag}" if tag and "tag=" not in req.get("To") else "")
        lines = [f"SIP/2.0 {status}"] + [f"Via: {v}" for v in req.all("Via")]
        lines += [
            f"From: {req.get('From')}",
            f"To: {to}",
            f"Call-ID: {req.get('Call-ID')}",
            f"CSeq: {req.get('CSeq')}",
        ]
        self.sip.sendto(
            ("\r\n".join(lines) + "\r\n" + extra + f"Content-Length: {len(body)}\r\n\r\n" + body).encode(),
            self.client,
        )

    def received_keys(self) -> bytes:
        """Démodule à 75 bauds ce que le Minitel logiciel a envoyé."""
        samples = [ALAW_DECODE[a] for packet in self.rtp_in for a in packet[12:]]
        return decode75(samples)


def decode75(samples: list[int], step: int = 4) -> bytes:
    per_bit = RATE / TX_BAUD
    n = int(per_bit)
    w1 = [cmath.exp(-2j * math.pi * TX_MARK * i / RATE) for i in range(n)]
    w0 = [cmath.exp(-2j * math.pi * TX_SPACE * i / RATE) for i in range(n)]
    levels = []
    for i in range(0, len(samples) - n, step):
        win = samples[i : i + n]
        levels.append(
            1 if abs(sum(x * c for x, c in zip(win, w1))) > abs(sum(x * c for x, c in zip(win, w0))) else 0
        )
    bit = per_bit / step
    out, i = bytearray(), 1
    while i < len(levels) - int(10 * bit):
        if not (levels[i - 1] == 1 and levels[i] == 0):
            i += 1
            continue
        # Le niveau i décrit la fenêtre centrée sur i·step + n/2 : le front y est.
        bits = [levels[min(len(levels) - 1, int(i + (k + 0.5) * bit))] for k in range(10)]
        if bits[0] == 0 and bits[9] == 1:
            out.append(sum(b << k for k, b in enumerate(bits[1:8])))
            i = int(i + 9.5 * bit)
        else:
            i += 1
    return bytes(out)

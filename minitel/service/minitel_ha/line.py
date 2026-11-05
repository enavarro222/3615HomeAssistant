"""Un Minitel et son adaptateur, en logiciel : il appelle le 3615 comme le vrai.

L'appel passe par Asterisk et le softmodem, en SIP et en RTP G.711a sur la
boucle locale, avec notre propre modem V.23 : tout le chemin du vrai Minitel
est parcouru, sauf le fil de cuivre.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import struct
from dataclasses import dataclass

from .modem import FRAME, RATE, Demodulator, Modulator
from .sip import Call, SipError

log = logging.getLogger("minitel_ha")

PCMA = 8


@dataclass(frozen=True)
class LineConfig:
    sip_host: str
    sip_port: int
    sip_user: str
    sip_password: str


class _Rtp(asyncio.DatagramProtocol):
    def __init__(self, line: Line) -> None:
        self._line = line

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > 12 and data[0] >> 6 == 2 and data[1] & 0x7F == PCMA:
            # On saute l'en-tête, les CSRC et une éventuelle extension.
            offset = 12 + 4 * (data[0] & 0x0F)
            if data[0] & 0x10 and len(data) >= offset + 4:
                offset += 4 + 4 * struct.unpack("!H", data[offset + 2 : offset + 4])[0]
            self._line._audio(data[offset:])


class Line:
    """Les événements sortent par `events` : ("status", texte) ou ("data", octets)."""

    def __init__(self, config: LineConfig) -> None:
        self._config = config
        self._call = Call(config.sip_host, config.sip_port, config.sip_user, config.sip_password)
        self._modulator = Modulator()
        self._demodulator = Demodulator()
        self._rtp: asyncio.DatagramTransport | None = None
        self._media: tuple[str, int] | None = None
        self._tasks: list[asyncio.Task] = []
        self._carrier = False
        self._hung_up = False
        self.events: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

    @property
    def ended(self) -> asyncio.Event:
        return self._call.ended

    async def connect(self) -> None:
        """Décroche et compose. Lève SipError si Asterisk ne répond pas ou refuse."""
        loop = asyncio.get_running_loop()
        self._rtp, _ = await loop.create_datagram_endpoint(lambda: _Rtp(self), local_addr=("127.0.0.1", 0))
        rtp_port = self._rtp.get_extra_info("sockname")[1]
        self._status("appel")
        try:
            media = await self._call.dial(rtp_port)
        except (SipError, OSError):
            self._close_rtp()
            self._call.close()
            raise
        self._media = (media.host, media.port)
        log.info("panneau : appel établi, audio vers %s:%d", *self._media)
        self._status("porteuse")
        self._tasks = [asyncio.create_task(self._transmit()), asyncio.create_task(self._watch())]

    def send(self, data: bytes) -> None:
        """Les touches tapées : parties à 75 bauds, comme sur le vrai."""
        if self._call.established:
            self._modulator.send(data)

    async def hangup(self) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        for task in self._tasks:
            if task is not asyncio.current_task():
                task.cancel()
        await self._call.hangup()
        self._close_rtp()
        self._status("fin")

    async def _transmit(self) -> None:
        """Un paquet RTP toutes les 20 ms, sans dériver."""
        loop = asyncio.get_running_loop()
        seq, ts, ssrc = secrets.randbelow(1 << 16), secrets.randbelow(1 << 32), secrets.randbelow(1 << 32)
        start = loop.time()
        sent = 0
        while self._rtp and self._media:
            header = struct.pack("!BBHII", 0x80, PCMA, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc)
            self._rtp.sendto(header + self._modulator.frame(), self._media)
            seq += 1
            ts += FRAME
            sent += 1
            await asyncio.sleep(max(0, start + sent * FRAME / RATE - loop.time()))

    async def _watch(self) -> None:
        await self._call.ended.wait()
        log.info("panneau : Asterisk a raccroché")
        await self.hangup()

    def _audio(self, payload: bytes) -> None:
        data = self._demodulator.feed(payload)
        if self._demodulator.carrier != self._carrier:
            self._carrier = self._demodulator.carrier
            self._status("connecte" if self._carrier else "porteuse")
        if data:
            self.events.put_nowait(("data", data))

    def _status(self, status: str) -> None:
        self.events.put_nowait(("status", status))

    def _close_rtp(self) -> None:
        if self._rtp:
            with contextlib.suppress(Exception):
                self._rtp.close()
            self._rtp = None

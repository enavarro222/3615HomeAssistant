"""Modem V.23 côté Minitel, en logiciel, sur de l'audio G.711a à 8 kHz.

Le Minitel émet à 75 bauds (390 Hz = 1, 450 Hz = 0) et reçoit à 1200 bauds
(1300 Hz = 1, 2100 Hz = 0), en 7 bits, parité paire, 1 bit de stop.
"""

from __future__ import annotations

import cmath
import math
from collections import deque

RATE = 8000
FRAME = 160  # 20 ms, un paquet RTP

TX_BAUD, TX_MARK, TX_SPACE = 75, 390, 450
RX_BAUD, RX_MARK, RX_SPACE = 1200, 1300, 2100


def alaw_encode(sample: int) -> int:
    s = int(sample) >> 3
    mask = 0xD5 if s >= 0 else 0x55
    if s < 0:
        s = -s - 1
    s = min(s, 0xFFF)
    exp = 0
    for e, top in enumerate((0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF)):
        if s <= top:
            exp = e
            break
    man = (s >> (exp or 1)) & 0x0F
    return ((exp << 4) | man) ^ mask


def _alaw_decode(a: int) -> int:
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    t = t + 8 if seg == 0 else (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t


ALAW_DECODE = tuple(_alaw_decode(a) for a in range(256))


def frame_bits(byte: int) -> list[int]:
    """Un caractère 7E1 : départ, 7 bits de poids faible d'abord, parité paire, stop."""
    data = [(byte >> i) & 1 for i in range(7)]
    return [0, *data, sum(data) & 1, 1]


class Modulator:
    """Émission à 75 bauds, phase continue. Sans rien à envoyer, la porteuse reste à 1."""

    def __init__(self, amplitude: int = 6000) -> None:
        self._amplitude = amplitude
        self._phase = 0.0
        self._bits: deque[int] = deque()
        self._current = 1
        self._credit = 0.0
        self._per_bit = RATE / TX_BAUD

    @property
    def pending(self) -> int:
        """Bits encore à émettre, pour savoir si la saisie est partie."""
        return len(self._bits)

    def send(self, data: bytes) -> None:
        for byte in data:
            self._bits.extend(frame_bits(byte))

    def frame(self, samples: int = FRAME) -> bytes:
        out = bytearray()
        step = 2 * math.pi / RATE
        for _ in range(samples):
            if self._credit <= 0:
                self._current = self._bits.popleft() if self._bits else 1
                self._credit += self._per_bit
            self._credit -= 1
            self._phase += step * (TX_MARK if self._current else TX_SPACE)
            out.append(alaw_encode(self._amplitude * math.sin(self._phase)))
        self._phase %= 2 * math.pi
        return bytes(out)


class Demodulator:
    """Réception à 1200 bauds, au fil des paquets.

    Sur chaque fenêtre d'un bit, on compare l'énergie à 1300 Hz et à 2100 Hz
    (DFT glissante), puis un UART 7E1 lit les caractères. Tant que la porteuse
    n'a pas tenu 50 ms, rien n'est lu : la tonalité de réponse et le silence du
    début d'appel donneraient des caractères parasites.
    """

    WINDOW = 7  # un peu plus d'un bit à 1200 bauds
    THRESHOLD = 500 * WINDOW / 2  # amplitude ~500, soit -36 dBm0 : en dessous, pas de signal
    CARRIER_ON = RATE // 20  # 50 ms de porteuse pour se déclarer connecté
    CARRIER_OFF = RATE // 5  # 200 ms sans signal : porteuse perdue
    RESYNC = 1024  # recalcul exact périodique, contre la dérive des arrondis

    def __init__(self) -> None:
        n = self.WINDOW
        self._history: deque[float] = deque([0.0] * n, maxlen=n)
        self._rot = (cmath.exp(2j * math.pi * RX_MARK / RATE), cmath.exp(2j * math.pi * RX_SPACE / RATE))
        self._tail = (self._rot[0] ** (n - 1), self._rot[1] ** (n - 1))
        self._mark = 0j
        self._space = 0j
        self._count = 0
        self._level = 1
        self._run = 0  # échantillons de suite avec porteuse (positif) ou sans (négatif)
        self.carrier = False
        self.parity_errors = 0
        self._per_bit = RATE / RX_BAUD
        self._uart: list[int] | None = None
        self._t = 0.0
        self._next = 0.0

    def feed(self, payload: bytes) -> bytes:
        """Démodule un paquet G.711a, renvoie les caractères reçus (7 bits)."""
        out = bytearray()
        for a in payload:
            self._sample(ALAW_DECODE[a], out)
        return bytes(out)

    def _sample(self, x: float, out: bytearray) -> None:
        oldest = self._history[0]
        self._history.append(x)
        rot_m, rot_s = self._rot
        self._mark = x + rot_m * (self._mark - oldest * self._tail[0])
        self._space = x + rot_s * (self._space - oldest * self._tail[1])
        self._count += 1
        if self._count % self.RESYNC == 0:
            self._resync()

        mark, space = abs(self._mark), abs(self._space)
        signal = max(mark, space) > self.THRESHOLD
        level = 1 if (mark >= space or not signal) else 0

        if signal:
            self._run = self._run + 1 if self._run > 0 else 1
            if not self.carrier and self._run >= self.CARRIER_ON and level == 1:
                self.carrier = True
        else:
            self._run = self._run - 1 if self._run < 0 else -1
            if self.carrier and -self._run >= self.CARRIER_OFF:
                self.carrier = False
                self._uart = None

        if self.carrier:
            self._uart_sample(level, out)
        self._level = level

    def _resync(self) -> None:
        n = self.WINDOW
        samples = list(self._history)  # du plus ancien au plus récent
        for i, rot in enumerate(self._rot):
            value = sum(samples[n - 1 - m] * rot**m for m in range(n))
            if i == 0:
                self._mark = value
            else:
                self._space = value

    def _uart_sample(self, level: int, out: bytearray) -> None:
        if self._uart is None:
            if self._level == 1 and level == 0:  # front du bit de départ
                self._uart = []
                self._t = 0.0
                self._next = self._per_bit / 2
            return
        self._t += 1
        if self._t < self._next:
            return
        self._uart.append(level)
        self._next += self._per_bit
        if self._uart[0] != 0:  # faux départ : un parasite
            self._uart = None
            return
        if len(self._uart) < 10:
            return
        bits, self._uart = self._uart, None
        data, parity, stop = bits[1:8], bits[8], bits[9]
        if stop != 1:
            return
        if (sum(data) + parity) & 1:
            self.parity_errors += 1
            return
        out.append(sum(b << k for k, b in enumerate(data)))

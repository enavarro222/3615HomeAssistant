"""Faux Minitel : s'enregistre en SIP, appelle le 3615, parle V.23 7E1 en RTP/G.711a.

Teste toute la chaîne sans matériel. Il tourne sur la machine d'Asterisk, avec
LAN_CIDR=127.0.0.0/8 et le même SIP_PASSWORD :

    SIP_PASSWORD=... python tools/fake_minitel.py

Il attend la page d'accueil, appuie sur SUITE et affiche l'écran reçu. Il ne
bascule aucune lumière.
"""
import cmath
import hashlib
import math
import os
import random
import re
import socket
import struct
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "minitel" / "service"))
from tests.screen import Screen  # noqa: E402

HOST, PORT = "127.0.0.1", 5060
USER, PASSWORD = "minitel", os.environ["SIP_PASSWORD"]
RATE = 8000


def alaw_encode(s):
    s = int(s) >> 3
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


def alaw_decode(a):
    a ^= 0x55
    t = (a & 0x0F) << 4
    seg = (a & 0x70) >> 4
    t = t + 8 if seg == 0 else (t + 0x108) << (seg - 1)
    return t if a & 0x80 else -t


class Sip:
    def __init__(self, sock):
        self.s = sock
        self.tag = str(random.randrange(10**9))
        self.call = f"{random.randrange(10**9)}@fake"
        self.branch = 0
        self.cseq = 0
        self.lport = sock.getsockname()[1]

    def tx(self, method, uri, extra="", body="", to_tag="", cseq=None):
        self.branch += 1
        if cseq is None:
            self.cseq += 1
            cseq = self.cseq
        to = f"<sip:{USER}@{HOST}>" if method == "REGISTER" else f"<sip:3615@{HOST}>"
        msg = (
            f"{method} {uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {HOST}:{self.lport};branch=z9hG4bK{self.branch}x{self.tag};rport\r\n"
            f"Max-Forwards: 70\r\nFrom: <sip:{USER}@{HOST}>;tag={self.tag}\r\n"
            f"To: {to}{';tag=' + to_tag if to_tag else ''}\r\nCall-ID: {self.call}\r\n"
            f"CSeq: {cseq} {method}\r\nContact: <sip:{USER}@{HOST}:{self.lport}>\r\n{extra}"
            f"Content-Length: {len(body)}\r\n\r\n{body}"
        )
        self.s.sendto(msg.encode(), (HOST, PORT))

    def rx(self, timeout=3):
        self.s.settimeout(timeout)
        return self.s.recvfrom(65535)[0].decode(errors="replace")

    def final(self, timeout=4):
        end = time.time() + timeout
        while time.time() < end:
            m = self.rx(end - time.time())
            code = int(m.split(" ", 2)[1]) if m.startswith("SIP/2.0") else 0
            if code >= 200:
                return code, m
        raise TimeoutError

    def auth(self, method, uri, chal):
        realm = re.search(r'realm="([^"]+)"', chal).group(1)
        nonce = re.search(r'nonce="([^"]+)"', chal).group(1)
        opaque = re.search(r'opaque="([^"]+)"', chal)
        def h(x):
            return hashlib.md5(x.encode()).hexdigest()

        cnonce, nc = "abcd1234", "00000001"
        resp = h(f"{h(f'{USER}:{realm}:{PASSWORD}')}:{nonce}:{nc}:{cnonce}:auth:{h(f'{method}:{uri}')}")
        o = f', opaque="{opaque.group(1)}"' if opaque else ""
        return (
            f'Authorization: Digest username="{USER}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
            f'response="{resp}", algorithm=MD5, qop=auth, nc={nc}, cnonce="{cnonce}"{o}\r\n'
        )


class Modem:
    """Côté Minitel : émet à 75 bauds (390/450 Hz), reçoit à 1200 bauds (1300/2100 Hz)."""

    def __init__(self, rtp, dest):
        self.rtp, self.dest = rtp, dest
        self.phase = 0.0
        self.bits = []
        self.on = True
        self.seq = 0
        self.ts = 0
        self.ssrc = 0x3615
        self.samples = []
        self.lock = threading.Lock()

    def tx_loop(self):
        n = 0
        start = time.monotonic()
        per_bit = RATE / 75
        credit = 0.0
        current = 1
        while self.on:
            frame = bytearray()
            for _ in range(160):
                if credit <= 0:
                    with self.lock:
                        current = self.bits.pop(0) if self.bits else 1
                    credit += per_bit
                credit -= 1
                self.phase += 2 * math.pi * (390 if current else 450) / RATE
                frame.append(alaw_encode(6000 * math.sin(self.phase)))
            hdr = struct.pack("!BBHII", 0x80, 8, self.seq & 0xFFFF, self.ts & 0xFFFFFFFF, self.ssrc)
            self.rtp.sendto(hdr + bytes(frame), self.dest)
            self.seq += 1
            self.ts += 160
            n += 1
            time.sleep(max(0, start + n * 0.02 - time.monotonic()))

    def rx_loop(self):
        self.rtp.settimeout(0.5)
        while self.on:
            try:
                d = self.rtp.recv(2048)
            except socket.timeout:
                continue
            if len(d) > 12 and (d[1] & 0x7F) == 8:
                self.samples.extend(alaw_decode(b) for b in d[12:])

    def send(self, data):
        with self.lock:
            for byte in data:
                frame = [0] + [(byte >> i) & 1 for i in range(7)]
                frame += [bin(byte & 0x7F).count("1") & 1, 1]
                self.bits.extend(frame)


def demod(samples):
    """Discriminateur FSK 1300/2100 Hz sur une fenêtre d'un bit, puis UART 7E1."""
    n = 7
    w1 = [cmath.exp(-2j * math.pi * 1300 * i / RATE) for i in range(n)]
    w0 = [cmath.exp(-2j * math.pi * 2100 * i / RATE) for i in range(n)]
    level = []
    for i in range(len(samples) - n):
        win = samples[i : i + n]
        a = abs(sum(x * c for x, c in zip(win, w1)))
        b = abs(sum(x * c for x, c in zip(win, w0)))
        level.append(1 if a > b else 0)
    per_bit = RATE / 1200
    out, perr = bytearray(), 0
    i = 0
    while i < len(level) - int(11 * per_bit):
        if level[i] == 1:
            i += 1
            continue
        mid = i + per_bit / 2

        def bit(k):
            return level[min(len(level) - 1, int(mid + k * per_bit))]

        if bit(0) != 0:
            i += 1
            continue
        data = [bit(k) for k in range(1, 8)]
        parity, stop = bit(8), bit(9)
        if stop != 1:
            i += 1
            continue
        byte = sum(b << k for k, b in enumerate(data))
        perr += (sum(data) + parity) & 1
        out.append(byte)
        i = int(i + 9.5 * per_bit)
    return bytes(out), perr


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    sip = Sip(s)
    reg_uri = f"sip:{HOST}"
    sip.tx("REGISTER", reg_uri, "Expires: 120\r\n")
    try:
        code, msg = sip.final()
    except (TimeoutError, socket.timeout):
        print("REGISTER: pas de réponse")
        return 3
    print("REGISTER 1:", code)
    if code in (401, 407):
        sip.tx("REGISTER", reg_uri, "Expires: 120\r\n" + sip.auth("REGISTER", reg_uri, msg))
        code, msg = sip.final()
        print("REGISTER 2:", code)
    if code != 200:
        return 4

    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rtp.bind(("127.0.0.1", 0))
    rport = rtp.getsockname()[1]
    sdp = (
        f"v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=-\r\nc=IN IP4 127.0.0.1\r\nt=0 0\r\n"
        f"m=audio {rport} RTP/AVP 8\r\na=rtpmap:8 PCMA/8000\r\na=ptime:20\r\n"
    )
    uri = f"sip:3615@{HOST}"
    ct = "Content-Type: application/sdp\r\n"
    sip.call = f"{random.randrange(10**9)}@fake"
    sip.tx("INVITE", uri, ct, sdp)
    code, msg = sip.final(8)
    print("INVITE 1:", code)
    if code in (401, 407):
        to_tag = re.search(r"To:.*;tag=([^\s;]+)", msg).group(1)
        sip.tx("ACK", uri, to_tag=to_tag, cseq=sip.cseq)
        sip.tx("INVITE", uri, ct + sip.auth("INVITE", uri, msg), sdp)
        code, msg = sip.final(8)
        print("INVITE 2:", code)
    if code != 200:
        return 5
    print("codec:", re.search(r"a=rtpmap:\d+ \S+", msg).group(0))
    to_tag = re.search(r"To:.*;tag=([^\s;]+)", msg).group(1)
    sip.tx("ACK", uri, to_tag=to_tag, cseq=sip.cseq)
    media = int(re.search(r"m=audio (\d+)", msg).group(1))

    m = Modem(rtp, (HOST, media))
    for f in (m.tx_loop, m.rx_loop):
        threading.Thread(target=f, daemon=True).start()

    time.sleep(8)  # porteuse 390 Hz, puis page d'accueil
    m.send(b"\x13H")  # SUITE
    time.sleep(6)
    m.on = False
    sip.tx("BYE", uri, to_tag=to_tag)

    data, perr = demod(m.samples)
    print(f"audio: {len(m.samples) / RATE:.1f} s, octets: {len(data)}, erreurs parité: {perr}")
    print("début:", data[:48].hex(" "))
    screen = Screen()
    try:
        screen.feed(data)
    except (IndexError, StopIteration) as err:
        print("flux illisible:", repr(err))
    print("+" + "-" * 40 + "+")
    for row in range(1, 25):
        print("|" + screen.row_text(row).ljust(40) + "|")
    print("+" + "-" * 40 + "+")
    return 0


sys.exit(main())

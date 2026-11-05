"""Le strict nécessaire de SIP pour appeler Asterisk comme le ferait l'adaptateur.

Un INVITE avec authentification Digest, l'ACK, le BYE dans les deux sens. Pas
d'enregistrement : Asterisk authentifie l'appel lui-même. Prévu pour la boucle
locale, sans retransmission.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, field

log = logging.getLogger("minitel_ha")


class SipError(Exception):
    pass


@dataclass
class Message:
    first_line: str
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: str = ""

    @classmethod
    def parse(cls, data: bytes) -> Message:
        text = data.decode("utf-8", errors="replace")
        head, _, body = text.partition("\r\n\r\n")
        first, *lines = head.split("\r\n")
        headers = []
        for line in lines:
            name, _, value = line.partition(":")
            headers.append((name.strip(), value.strip()))
        return cls(first, headers, body)

    def get(self, name: str) -> str | None:
        name = name.lower()
        return next((v for n, v in self.headers if n.lower() == name), None)

    def all(self, name: str) -> list[str]:
        name = name.lower()
        return [v for n, v in self.headers if n.lower() == name]

    @property
    def is_response(self) -> bool:
        return self.first_line.startswith("SIP/2.0 ")

    @property
    def code(self) -> int:
        return int(self.first_line.split(" ", 2)[1]) if self.is_response else 0

    @property
    def method(self) -> str:
        """La méthode d'une requête, ou celle que la réponse concerne (CSeq)."""
        if not self.is_response:
            return self.first_line.split(" ", 1)[0]
        return (self.get("CSeq") or " ").split()[-1]

    def to_tag(self) -> str | None:
        match = re.search(r";\s*tag=([^;\s>]+)", self.get("To") or "")
        return match.group(1) if match else None


def digest(method: str, uri: str, user: str, password: str, challenge: str) -> str:
    """La valeur d'un en-tête Authorization en réponse à un défi Digest MD5."""
    params = {}
    for name, quoted, bare_name, bare in re.findall(r'(\w+)="([^"]*)"|(\w+)=([^,\s]+)', challenge):
        params[(name or bare_name).lower()] = quoted if name else bare
    realm, nonce = params["realm"], params["nonce"]

    def h(value: str) -> str:
        return hashlib.md5(value.encode()).hexdigest()

    ha1 = h(f"{user}:{realm}:{password}")
    ha2 = h(f"{method}:{uri}")
    fields = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    if "auth" in [q.strip() for q in params.get("qop", "").split(",")]:
        cnonce, nc = secrets.token_hex(8), "00000001"
        fields.append(f'response="{h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")}"')
        fields += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        fields.append(f'response="{h(f"{ha1}:{nonce}:{ha2}")}"')
    fields.append("algorithm=MD5")
    if "opaque" in params:
        fields.append(f'opaque="{params["opaque"]}"')
    return "Digest " + ", ".join(fields)


@dataclass(frozen=True)
class Media:
    host: str
    port: int


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, call: Call) -> None:
        self._call = call

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message = Message.parse(data)
        except (ValueError, IndexError):
            return
        self._call._received(message, addr)


class Call:
    """Un appel sortant : compose, décroche, raccroche, ou se fait raccrocher."""

    def __init__(self, host: str, port: int, user: str, password: str, number: str = "3615") -> None:
        self._server = (host, port)
        self._user = user
        self._password = password
        self._uri = f"sip:{number}@{host}"
        self._target = self._uri
        self._call_id = f"{secrets.token_hex(8)}@minitel-web"
        self._tag = secrets.token_hex(4)
        self._cseq = 0
        self._to_tag: str | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._local = ("127.0.0.1", 0)
        self._responses: asyncio.Queue[Message] = asyncio.Queue()
        self._ack: bytes | None = None
        self.ended = asyncio.Event()

    @property
    def established(self) -> bool:
        return self._to_tag is not None and not self.ended.is_set()

    async def dial(self, rtp_port: int, timeout: float = 10) -> Media:
        """Appelle, et renvoie où envoyer l'audio. Lève SipError si l'appel échoue."""
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), local_addr=("127.0.0.1", 0)
        )
        self._local = self._transport.get_extra_info("sockname")[:2]
        sdp = (
            "v=0\r\n"
            f"o=- {secrets.randbelow(10**9)} 1 IN IP4 127.0.0.1\r\n"
            "s=Minitel\r\n"
            "c=IN IP4 127.0.0.1\r\n"
            "t=0 0\r\n"
            f"m=audio {rtp_port} RTP/AVP 8\r\n"
            "a=rtpmap:8 PCMA/8000\r\n"
            "a=ptime:20\r\n"
            "a=sendrecv\r\n"
        )
        auth = ""
        for _ in range(2):
            branch = self._send_request("INVITE", self._uri, sdp, auth)
            response = await self._final("INVITE", timeout)
            if response.code in (401, 407):
                self._send_ack(response, branch)
                if auth:
                    break  # mot de passe refusé
                challenge_header = "WWW-Authenticate" if response.code == 401 else "Proxy-Authenticate"
                auth_header = "Authorization" if response.code == 401 else "Proxy-Authorization"
                challenge = response.get(challenge_header) or ""
                auth = (
                    f"{auth_header}: {digest('INVITE', self._uri, self._user, self._password, challenge)}\r\n"
                )
                continue
            if response.code >= 300:
                self._send_ack(response, branch)
                raise SipError(f"appel refusé : {response.first_line[8:]}")
            self._to_tag = response.to_tag()
            contact = re.search(r"<([^>]+)>", response.get("Contact") or "")
            if contact:
                self._target = contact.group(1)
            self._send_ack(response)
            return _media(response.body)
        raise SipError("authentification refusée")

    async def hangup(self) -> None:
        if self.established:
            self._send_request("BYE", self._target)
            try:
                await self._final("BYE", 2)
            except SipError:
                pass
        self.ended.set()
        self.close()

    def close(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    async def _final(self, method: str, timeout: float) -> Message:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise SipError(f"{method} : pas de réponse d'Asterisk")
            try:
                response = await asyncio.wait_for(self._responses.get(), remaining)
            except TimeoutError:
                continue
            if response.method == method and response.code >= 200:
                return response

    def _received(self, message: Message, addr: tuple[str, int]) -> None:
        if message.is_response:
            if message.method == "INVITE" and message.code == 200 and self._ack and self._to_tag:
                self._send(self._ack)  # le 200 OK est répété : notre ACK s'est perdu
            else:
                self._responses.put_nowait(message)
            return
        if message.method in ("BYE", "CANCEL", "OPTIONS"):
            self._reply(message, addr, "200 OK")
            if message.method != "OPTIONS":
                self.ended.set()
        elif message.method != "ACK":
            self._reply(message, addr, "501 Not Implemented")

    def _send_request(self, method: str, uri: str, body: str = "", extra: str = "") -> str:
        branch = f"z9hG4bK{secrets.token_hex(8)}"
        self._cseq += 1
        self._send(self._request(method, uri, branch, self._cseq, body, extra))
        return branch

    def _send_ack(self, response: Message, branch: str | None = None) -> None:
        """ACK d'un échec : même branche que l'INVITE. ACK d'un 200 : nouvelle branche."""
        cseq = int((response.get("CSeq") or "0").split()[0])
        to = response.get("To") or f"<{self._uri}>"
        uri = self._uri if branch else self._target
        ack = self._request("ACK", uri, branch or f"z9hG4bK{secrets.token_hex(8)}", cseq, to=to)
        if not branch:
            self._ack = ack
        self._send(ack)

    def _request(
        self, method: str, uri: str, branch: str, cseq: int, body: str = "", extra: str = "", to: str = ""
    ) -> bytes:
        host, port = self._local
        if not to:
            to = f"<{self._uri}>" + (f";tag={self._to_tag}" if self._to_tag else "")
        content_type = "Content-Type: application/sdp\r\n" if body else ""
        return (
            f"{method} {uri} SIP/2.0\r\n"
            f"Via: SIP/2.0/UDP {host}:{port};branch={branch};rport\r\n"
            "Max-Forwards: 70\r\n"
            f"From: <sip:{self._user}@{self._server[0]}>;tag={self._tag}\r\n"
            f"To: {to}\r\n"
            f"Call-ID: {self._call_id}\r\n"
            f"CSeq: {cseq} {method}\r\n"
            f"Contact: <sip:{self._user}@{host}:{port}>\r\n"
            "User-Agent: 3615 Home Assistant\r\n"
            f"{extra}{content_type}"
            f"Content-Length: {len(body.encode())}\r\n\r\n{body}"
        ).encode()

    def _reply(self, request: Message, addr: tuple[str, int], status: str) -> None:
        lines = [f"SIP/2.0 {status}"]
        lines += [f"Via: {via}" for via in request.all("Via")]
        for name in ("From", "To", "Call-ID", "CSeq"):
            value = request.get(name)
            if value is not None:
                lines.append(f"{name}: {value}")
        lines.append("Content-Length: 0")
        self._send(("\r\n".join(lines) + "\r\n\r\n").encode(), addr)

    def _send(self, data: bytes, addr: tuple[str, int] | None = None) -> None:
        if self._transport:
            self._transport.sendto(data, addr or self._server)


def _media(sdp: str) -> Media:
    host = re.search(r"^c=IN IP4 (\S+)", sdp, re.M)
    audio = re.search(r"^m=audio (\d+) RTP/AVP ([\d ]+)", sdp, re.M)
    if not host or not audio:
        raise SipError("réponse sans audio")
    if "8" not in audio.group(2).split():
        raise SipError("Asterisk n'a pas accepté le G.711a")
    return Media(host.group(1), int(audio.group(1)))

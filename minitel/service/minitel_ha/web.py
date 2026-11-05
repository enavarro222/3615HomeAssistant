"""Le panneau de test : un Minitel dans le navigateur, servi par l'ingress de Home Assistant.

Le navigateur n'affiche que l'écran et le clavier. L'appel, lui, part d'ici
par le même chemin que le vrai Minitel : SIP, RTP, softmodem d'Asterisk.

WebSocket : le navigateur envoie les touches en binaire, et en texte
{"action": "connexion"} ou {"action": "fin"}. Il reçoit l'écran en binaire,
et en texte {"status": ...} ou {"error": ...}.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from aiohttp import WSMsgType, web

from .line import Line, LineConfig
from .sip import SipError

log = logging.getLogger("minitel_ha")

STATIC = Path(__file__).parent / "static"
FILES = {"/": "index.html", "/minitel.js": "minitel.js", "/minitel.css": "minitel.css"}


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int
    allowed: frozenset[str]
    line: LineConfig


def load_web_config() -> WebConfig | None:
    """Le panneau ne démarre que si WEB_PORT est fourni : en add-on, par run.sh."""
    port = os.environ.get("WEB_PORT")
    if not port:
        return None
    allowed = os.environ.get("WEB_ALLOWED", "127.0.0.1")
    return WebConfig(
        host=os.environ.get("WEB_HOST", "127.0.0.1"),
        port=int(port),
        allowed=frozenset(a.strip() for a in allowed.split(",") if a.strip()),
        line=LineConfig(
            sip_host=os.environ.get("WEB_SIP_HOST", "127.0.0.1"),
            sip_port=int(os.environ.get("WEB_SIP_PORT", "5060")),
            sip_user=os.environ.get("WEB_SIP_USER", "minitel-web"),
            sip_password=os.environ["WEB_SIP_PASSWORD"],
        ),
    )


class _Panel:
    """Un seul appel à la fois, comme une seule ligne téléphonique."""

    def __init__(self, config: WebConfig) -> None:
        self.config = config
        self.line: Line | None = None


PANEL = web.AppKey("panel", _Panel)


def make_app(config: WebConfig) -> web.Application:
    @web.middleware
    async def only_from_ingress(request: web.Request, handler):
        # En réseau hôte, le port est ouvert sur tout le réseau local : seul le
        # proxy ingress du Supervisor (172.30.32.2) a le droit d'entrer.
        if request.remote not in config.allowed:
            log.warning("panneau : accès refusé à %s", request.remote)
            raise web.HTTPForbidden()
        return await handler(request)

    app = web.Application(middlewares=[only_from_ingress])
    app[PANEL] = _Panel(config)
    app.router.add_get("/ws", _websocket)
    for path, name in FILES.items():
        app.router.add_get(path, _static(name))
    return app


async def start_web(config: WebConfig) -> web.AppRunner:
    runner = web.AppRunner(make_app(config), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    log.info("panneau de test sur %s:%d", config.host, config.port)
    return runner


def _static(name: str):
    async def handler(request: web.Request) -> web.FileResponse:
        return web.FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})

    return handler


async def _websocket(request: web.Request) -> web.WebSocketResponse:
    panel = request.app[PANEL]
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    line: Line | None = None
    pump: asyncio.Task | None = None
    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                if line and panel.line is line:
                    line.send(msg.data)
                continue
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                action = json.loads(msg.data).get("action")
            except (ValueError, AttributeError):
                continue
            if action == "connexion":
                if panel.line is not None:
                    if panel.line is not line:
                        await ws.send_json({"error": "Ligne occupée : un autre panneau est en ligne"})
                    continue
                line = panel.line = Line(panel.config.line)
                pump = asyncio.create_task(_pump(panel, line, ws))
                await _connect(line)
            elif action == "fin" and line and panel.line is line:
                await line.hangup()
    finally:
        if line:
            await line.hangup()
        if pump:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(pump, 2)
        if line and panel.line is line:
            panel.line = None
    return ws


async def _connect(line: Line) -> None:
    try:
        await line.connect()
    except (SipError, OSError) as err:
        log.warning("panneau : l'appel a échoué : %s", err)
        line.events.put_nowait(("error", str(err)))
        line.events.put_nowait(("status", "fin"))


async def _pump(panel: _Panel, line: Line, ws: web.WebSocketResponse) -> None:
    """Fait suivre au navigateur ce que la ligne reçoit, jusqu'au raccrochage."""
    try:
        while True:
            kind, value = await line.events.get()
            if ws.closed:
                continue
            with contextlib.suppress(ConnectionError, RuntimeError):
                if kind == "data":
                    await ws.send_bytes(value)
                else:
                    await ws.send_json({kind: value})
            if kind == "status" and value == "fin":
                return
    finally:
        if panel.line is line:
            panel.line = None

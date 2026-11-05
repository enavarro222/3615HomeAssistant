"""Un faux Home Assistant : juste l'API WebSocket dont le service se sert."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiohttp import WSMsgType, web


class FakeHass:
    def __init__(self, token: str = "secret") -> None:
        self.token = token
        self.dashboards: list[dict[str, Any]] = []
        self.configs: dict[str, dict[str, Any]] = {}
        self.system_data: dict[str, Any] | None = {"default_panel": None}  # None : HA trop ancien
        self.areas: list[dict[str, Any]] = []
        self.floors: list[dict[str, Any]] = []
        self.devices: list[dict[str, Any]] = []
        self.entities: list[dict[str, Any]] = []
        self.states: dict[str, tuple[str, dict[str, Any]]] = {}
        self.service_calls: list[dict[str, Any]] = []
        # Pour l'historique : (instant Unix, état), dans l'ordre.
        self.history: dict[str, list[tuple[float, str]]] = {}
        self.history_requests: list[dict[str, Any]] = []
        self.statistics: dict[str, dict[str, float | None]] = {}
        self.statistic_requests: list[dict[str, Any]] = []
        self.commands: list[str] = []
        self.connections = 0
        self._clients: set[web.WebSocketResponse] = set()
        # (client, id de l'abonnement, type d'événement ou "entities", entités suivies)
        self._subscriptions: list[tuple[web.WebSocketResponse, int, str, frozenset[str]]] = []
        self._runner: web.AppRunner | None = None

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/api/websocket", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        return f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        await self.drop()
        if self._runner:
            await self._runner.cleanup()

    def add_dashboard(self, url_path: str, title: str, config: dict[str, Any] | None) -> None:
        self.dashboards.append({"url_path": url_path, "title": title, "mode": "storage"})
        if config is not None:
            self.configs[url_path] = config

    # --- Ce que le test fait arriver ----------------------------------------------------------

    async def set_state(self, entity_id: str, state: str, **attributes: Any) -> None:
        _, old = self.states.get(entity_id, ("", {}))
        self.states[entity_id] = (state, {**old, **attributes})
        diff = {"+": {"s": state, **({"a": attributes} if attributes else {})}}
        for client, sub_id, kind, watched in list(self._subscriptions):
            if kind == "entities" and entity_id in watched:
                await self._event(client, sub_id, {"c": {entity_id: diff}})

    async def fire(self, event_type: str, data: dict[str, Any]) -> None:
        for client, sub_id, kind, _ in list(self._subscriptions):
            if kind == event_type:
                await self._event(client, sub_id, {"event_type": event_type, "data": data})

    async def drop(self) -> None:
        for client in list(self._clients):
            await client.close()

    # --- Serveur ------------------------------------------------------------------------------

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        auth = await ws.receive_json()
        if auth.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok"})
        self.connections += 1
        self._clients.add(ws)
        try:
            async for message in ws:
                if message.type is WSMsgType.TEXT:
                    await self._command(ws, message.json())
        finally:
            self._clients.discard(ws)
            self._subscriptions = [s for s in self._subscriptions if s[0] is not ws]
        return ws

    async def _command(self, ws: web.WebSocketResponse, msg: dict[str, Any]) -> None:
        kind, msg_id = msg["type"], msg["id"]
        self.commands.append(kind)
        if kind == "lovelace/dashboards/list":
            await _result(ws, msg_id, self.dashboards)
        elif kind == "frontend/get_system_data":
            if self.system_data is None:
                await _error(ws, msg_id, "unknown_command", "Unknown command.")
            else:
                await _result(ws, msg_id, {"value": self.system_data})
        elif kind == "lovelace/config":
            config = self.configs.get(msg.get("url_path"))
            if config is None:
                await _error(ws, msg_id, "config_not_found", "No config found.")
            else:
                await _result(ws, msg_id, config)
        elif kind.startswith("config/") and kind.endswith("_registry/list"):
            name = kind.removeprefix("config/").removesuffix("_registry/list")
            await _result(
                ws,
                msg_id,
                {"area": self.areas, "floor": self.floors, "device": self.devices, "entity": self.entities}[
                    name
                ],
            )
        elif kind == "get_states":
            await _result(
                ws,
                msg_id,
                [{"entity_id": e, "state": s, "attributes": a} for e, (s, a) in self.states.items()],
            )
        elif kind == "subscribe_entities":
            watched = frozenset(msg["entity_ids"])
            self._subscriptions.append((ws, msg_id, "entities", watched))
            await _result(ws, msg_id, None)
            initial = {e: {"s": s, "a": a} for e, (s, a) in self.states.items() if e in watched}
            await self._event(ws, msg_id, {"a": initial})
        elif kind == "subscribe_events":
            self._subscriptions.append((ws, msg_id, msg["event_type"], frozenset()))
            await _result(ws, msg_id, None)
        elif kind == "unsubscribe_events":
            self._subscriptions = [
                s for s in self._subscriptions if s[1] != msg["subscription"] or s[0] is not ws
            ]
            await _result(ws, msg_id, None)
        elif kind == "history/history_during_period":
            self.history_requests.append(msg)
            start = datetime.fromisoformat(msg["start_time"]).timestamp()
            result = {}
            for entity_id in msg["entity_ids"]:
                rows = self.history.get(entity_id, [])
                before = [r for r in rows if r[0] < start][-1:]  # l'état en vigueur au début
                # Format compact : « lu » omis quand il vaut « lc », comme Home Assistant.
                result[entity_id] = [
                    {"s": s, "lc": t} for t, s in before + [r for r in rows if r[0] >= start]
                ]
            await _result(ws, msg_id, result)
        elif kind == "recorder/statistic_during_period":
            self.statistic_requests.append(msg)
            values = self.statistics.get(msg["statistic_id"], {})
            await _result(ws, msg_id, {kind_: values.get(kind_) for kind_ in msg["types"]})
        elif kind == "call_service":
            self.service_calls.append(msg)
            await _result(ws, msg_id, {"context": {"id": "x"}})
            entity_id = msg["target"]["entity_id"]
            if msg["service"] == "toggle" and entity_id in self.states:
                await self.set_state(entity_id, "off" if self.states[entity_id][0] == "on" else "on")
        else:
            await _error(ws, msg_id, "unknown_command", "Unknown command.")

    async def _event(self, ws: web.WebSocketResponse, sub_id: int, event: dict[str, Any]) -> None:
        if not ws.closed:
            await ws.send_json({"id": sub_id, "type": "event", "event": event})


async def _result(ws: web.WebSocketResponse, msg_id: int, result: Any) -> None:
    await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": result})


async def _error(ws: web.WebSocketResponse, msg_id: int, code: str, message: str) -> None:
    await ws.send_json(
        {"id": msg_id, "type": "result", "success": False, "error": {"code": code, "message": message}}
    )

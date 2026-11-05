"""Home Assistant en direct : une connexion WebSocket pour tout le service.

Elle charge le tableau de bord à afficher, tient à jour l'état de ses entités, et prévient
les appels en cours quand quelque chose change. Si Home Assistant redémarre, elle se
reconnecte seule, puis recharge tout.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Iterator, Mapping

import aiohttp

from .dashboard import Dashboard, DashboardNotFound, from_areas, from_lovelace, pick_dashboard

log = logging.getLogger("minitel_ha")

CALL_TIMEOUT = 10
RECONNECT_MIN = 1
RECONNECT_MAX = 30
# L'éditeur de Home Assistant enregistre par rafales : on attend que ça se calme.
RELOAD_DELAY = 1.0
AREAS_TITLE = "Pièces"

# Ce qui, en mode « pièces », change les pages.
_REGISTRY_EVENTS = (
    "area_registry_updated",
    "floor_registry_updated",
    "device_registry_updated",
    "entity_registry_updated",
)


class HAError(Exception):
    """Home Assistant injoignable, ou commande refusée."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EntityState:
    state: str
    attributes: Mapping[str, Any]


class Change(enum.Enum):
    DASHBOARD = "dashboard"
    CONNECTION = "connection"


# Ce que reçoivent les appels en cours : les entités qui ont changé, ou un changement plus large.
Event = frozenset[str] | Change


def websocket_url(ha_url: str) -> str:
    """`http://supervisor/core` ou `http://ha:8123` : l'API WebSocket est sous `/api/websocket`."""
    scheme, rest = ha_url.split("://", 1)
    return ("wss" if scheme == "https" else "ws") + "://" + rest.rstrip("/") + "/api/websocket"


class Hass:
    def __init__(self, url: str, token: str, dashboard: str = "") -> None:
        """`dashboard` : le réglage de l'app."""
        self._url = websocket_url(url)
        self._token = token
        self._option = dashboard
        self._http: aiohttp.ClientSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._handlers: dict[int, Callable[[Mapping[str, Any]], None]] = {}
        self._entities_subscription: int | None = None
        self._source: str | None = None  # le tableau de bord traduit
        self._areas_mode = False
        self._reload: asyncio.TimerHandle | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._listeners: set[asyncio.Queue[Event]] = set()
        self._ready = asyncio.Event()
        self.connected = False
        self.dashboard = Dashboard("", ())
        self.states: dict[str, EntityState] = {}

    # --- Cycle de vie ------------------------------------------------------------------------

    async def start(self) -> None:
        self._http = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._run())

    async def wait_ready(self, timeout: float) -> bool:
        """Attend le premier chargement complet. Faux si Home Assistant ne répond pas à temps."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), timeout)
        return self._ready.is_set()

    async def close(self) -> None:
        for task in (self._task, *self._background):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._http:
            await self._http.close()

    # --- Pour les appels ---------------------------------------------------------------------

    def state_values(self) -> dict[str, str]:
        """Les états seuls, pour évaluer la visibilité des cartes."""
        return {entity_id: s.state for entity_id, s in self.states.items()}

    @contextlib.contextmanager
    def listen(self) -> Iterator[asyncio.Queue[Event]]:
        """Une file des changements, le temps d'un appel."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._listeners.add(queue)
        try:
            yield queue
        finally:
            self._listeners.discard(queue)

    async def call_service(self, domain: str, service: str, entity_id: str, **data: Any) -> None:
        """Le nouvel état arrive ensuite par l'abonnement, comme pour tout autre changement."""
        await self._call(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": data,
                "target": {"entity_id": entity_id},
            }
        )

    async def history(self, entity_ids: list[str], start: float) -> dict[str, list[tuple[float, str]]]:
        """Les états depuis `start` (secondes Unix), avec celui en vigueur à cet instant."""
        result = await self._call(
            {
                "type": "history/history_during_period",
                "start_time": datetime.fromtimestamp(start, UTC).isoformat(),
                "entity_ids": entity_ids,
                "minimal_response": True,
                "no_attributes": True,
                "significant_changes_only": False,
            }
        )
        history: dict[str, list[tuple[float, str]]] = {}
        for entity_id, rows in (result or {}).items():
            # Format compact : « lu » (mis à jour), ou seulement « lc » (changé) quand c'est le même.
            history[entity_id] = [
                (float(r.get("lu", r.get("lc", start))), str(r.get("s"))) for r in rows if "s" in r
            ]
        return history

    # --- Connexion ---------------------------------------------------------------------------

    async def _run(self) -> None:
        assert self._http is not None
        delay = RECONNECT_MIN
        while True:
            try:
                async with self._http.ws_connect(self._url, heartbeat=30, max_msg_size=0) as ws:
                    await self._authenticate(ws)
                    reader = asyncio.create_task(self._read(ws))
                    self._ws = ws
                    try:
                        await self._setup()
                        self._set_connected(True)
                        delay = RECONNECT_MIN
                        await reader
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await reader
                log.warning("Home Assistant a fermé la connexion")
            except (aiohttp.ClientError, OSError, TimeoutError, HAError) as err:
                log.warning("Home Assistant injoignable : %s", err or type(err).__name__)
            except Exception:
                log.exception("connexion à Home Assistant")
            self._disconnected()
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    async def _authenticate(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        hello = await ws.receive_json(timeout=CALL_TIMEOUT)
        if hello.get("type") != "auth_required":
            raise HAError(f"réponse inattendue : {hello.get('type')}")
        await ws.send_json({"type": "auth", "access_token": self._token})
        reply = await ws.receive_json(timeout=CALL_TIMEOUT)
        if reply.get("type") != "auth_ok":
            raise HAError(f"jeton refusé : {reply.get('message', reply.get('type'))}")

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    continue
                data = message.json()
                for item in data if isinstance(data, list) else (data,):
                    self._dispatch(item)
        finally:
            self._fail_pending()  # sinon un chargement en cours attendrait son délai

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HAError("connexion perdue"))
        self._pending.clear()

    def _dispatch(self, message: Mapping[str, Any]) -> None:
        msg_id = message.get("id")
        if message.get("type") == "event":
            handler = self._handlers.get(msg_id)
            if handler:
                handler(message["event"])
            return
        future = self._pending.get(msg_id)
        if future is None or future.done():
            return
        if message.get("success"):
            future.set_result(message.get("result"))
        else:
            error = message.get("error") or {}
            future.set_exception(HAError(error.get("message", "erreur"), error.get("code", "")))

    def _disconnected(self) -> None:
        self._ws = None
        if self._reload:
            self._reload.cancel()
        self._fail_pending()
        self._handlers.clear()
        self._entities_subscription = None
        self._set_connected(False)

    def _set_connected(self, connected: bool) -> None:
        if connected != self.connected:
            self.connected = connected
            self._notify(Change.CONNECTION)

    async def _call(
        self, payload: Mapping[str, Any], handler: Callable[[Mapping[str, Any]], None] | None = None
    ) -> Any:
        """Envoie une commande et attend son résultat. Avec `handler`, c'est un abonnement."""
        ws = self._ws
        if ws is None:
            raise HAError("Home Assistant injoignable")
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        if handler:
            self._handlers[msg_id] = handler
        try:
            await ws.send_json({"id": msg_id, **payload})
            await asyncio.wait_for(future, CALL_TIMEOUT)
        except (TimeoutError, aiohttp.ClientError, ConnectionError) as err:
            self._handlers.pop(msg_id, None)
            raise HAError(f"{payload['type']} : {err or type(err).__name__}") from err
        except HAError:
            self._handlers.pop(msg_id, None)
            raise
        finally:
            self._pending.pop(msg_id, None)
        return msg_id if handler else future.result()

    # --- Chargement --------------------------------------------------------------------------

    async def _setup(self) -> None:
        await self._load()
        await self._call(
            {"type": "subscribe_events", "event_type": "lovelace_updated"}, self._on_lovelace_updated
        )
        for event_type in _REGISTRY_EVENTS:
            await self._call(
                {"type": "subscribe_events", "event_type": event_type}, self._on_registry_updated
            )
        self._ready.set()

    async def _load(self) -> None:
        dashboards = await self._call({"type": "lovelace/dashboards/list"})
        titles = {d["url_path"]: d.get("title") or d["url_path"] for d in dashboards}
        try:
            system = await self._call({"type": "frontend/get_system_data", "key": "core"})
            default_panel = ((system or {}).get("value") or {}).get("default_panel")
        except HAError:
            default_panel = None  # Home Assistant trop ancien pour ce réglage

        try:
            source = pick_dashboard(self._option, default_panel, titles)
        except DashboardNotFound as err:
            log.error("tableau de bord « %s » introuvable : pages tirées des pièces", err)
            source = None

        dashboard = None
        if source:
            try:
                config = await self._call({"type": "lovelace/config", "url_path": source})
                dashboard = from_lovelace(titles[source], config)
            except HAError as err:
                if err.code != "config_not_found":
                    raise
                log.warning("tableau de bord « %s » jamais enregistré : pages tirées des pièces", source)
                source = None
        if dashboard is None:
            dashboard = await self._from_areas()

        log.info(
            "tableau de bord : %s, %d rubriques",
            f"« {dashboard.title} »" if source else "généré à partir des pièces",
            len(dashboard.views),
        )
        for note in dashboard.notes:
            log.info("  %s", note)
        self._source = source
        self._areas_mode = source is None
        self.dashboard = dashboard
        await self._watch(dashboard.entity_ids())

    async def _from_areas(self) -> Dashboard:
        areas = await self._call({"type": "config/area_registry/list"})
        floors = await self._call({"type": "config/floor_registry/list"})
        devices = await self._call({"type": "config/device_registry/list"})
        entities = await self._call({"type": "config/entity_registry/list"})
        states = {s["entity_id"]: s for s in await self._call({"type": "get_states"})}
        return from_areas(AREAS_TITLE, areas, floors, devices, entities, states)

    async def _watch(self, entity_ids: set[str]) -> None:
        """S'abonne aux états de ces entités, et attend le premier envoi."""
        if self._entities_subscription is not None:
            old, self._entities_subscription = self._entities_subscription, None
            self._handlers.pop(old, None)
            with contextlib.suppress(HAError):
                await self._call({"type": "unsubscribe_events", "subscription": old})
        self.states = {k: v for k, v in self.states.items() if k in entity_ids}
        if not entity_ids:
            return
        first: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def on_event(event: Mapping[str, Any]) -> None:
            self._on_entities(event)
            if not first.done():
                first.set_result(None)

        self._entities_subscription = await self._call(
            {"type": "subscribe_entities", "entity_ids": sorted(entity_ids)}, on_event
        )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(first, CALL_TIMEOUT)
        for entity_id in sorted(entity_ids - self.states.keys()):
            log.warning("entité inconnue de Home Assistant : %s", entity_id)

    def _on_entities(self, event: Mapping[str, Any]) -> None:
        """Le format compact de `subscribe_entities` : ajouts, différences, suppressions."""
        changed = set()
        for entity_id, state in event.get("a", {}).items():
            self.states[entity_id] = EntityState(state["s"], state.get("a", {}))
            changed.add(entity_id)
        for entity_id, diff in event.get("c", {}).items():
            old = self.states.get(entity_id)
            if old is None:
                continue
            added = diff.get("+", {})
            attributes = {**old.attributes, **added.get("a", {})}
            for key in diff.get("-", {}).get("a", []):
                attributes.pop(key, None)
            self.states[entity_id] = EntityState(added.get("s", old.state), attributes)
            changed.add(entity_id)
        for entity_id in event.get("r", []):
            self.states.pop(entity_id, None)
            changed.add(entity_id)
        if changed:
            self._notify(frozenset(changed))

    def _on_lovelace_updated(self, event: Mapping[str, Any]) -> None:
        if self._source is not None and event.get("data", {}).get("url_path") == self._source:
            self._schedule_reload()

    def _on_registry_updated(self, event: Mapping[str, Any]) -> None:
        if self._areas_mode:
            self._schedule_reload()

    def _schedule_reload(self) -> None:
        if self._reload:
            self._reload.cancel()
        self._reload = asyncio.get_running_loop().call_later(RELOAD_DELAY, self._start_reload)

    def _start_reload(self) -> None:
        task = asyncio.create_task(self._reload_dashboard())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _reload_dashboard(self) -> None:
        try:
            await self._load()
        except HAError as err:
            log.warning("rechargement du tableau de bord : %s", err)
            return
        self._notify(Change.DASHBOARD)

    def _notify(self, event: Event) -> None:
        for queue in self._listeners:
            queue.put_nowait(event)

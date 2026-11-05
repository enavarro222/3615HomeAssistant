"""Un appel Minitel : une connexion TCP, les écrans du tableau de bord, des touches.

L'écran suit Home Assistant en direct : à chaque changement, on recalcule les rangées et on
n'envoie que celles qui diffèrent de ce qui est affiché. À 1200 bauds, c'est ce qui compte.
Les courbes défilent seules, un point à la fois, et leurs entités sont relevées à chaque point
quand la courbe est courte. Les jauges sont relevées toutes les 10 s. Les statistiques sont
redemandées quand leur entité change, et toutes les 5 min pour suivre leur période.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass

from . import graph, render
from . import videotex as vt
from .config import DEFAULT_TITLE
from .dashboard import Item
from .hass import Change, Event, HAError, Hass
from .render import CONTENT_ROWS, CONTENT_TOP, Action, Screen
from .videotex import Key

log = logging.getLogger("minitel_ha")

# Un appel oublié garderait le Minitel en ligne : on raccroche au bout de 10 min.
IDLE_TIMEOUT = 600

GAUGE_PERIOD = 10.0
"""Une jauge est relevée toutes les 10 s : bien des intégrations ne répondent pas plus vite."""

STAT_PERIOD = 300.0
"""Une statistique sur 48 h glissantes bouge aussi sans que son entité change."""

SUBTITLE_ROW = 3
RULE_ROW = CONTENT_TOP + CONTENT_ROWS
MESSAGE_ROW = RULE_ROW + 1
HELP_ROW = MESSAGE_ROW + 1
PROMPT_ROW = HELP_ROW + 1
PROMPT = "Votre choix :"
FIELD_COL = len(PROMPT) + 2
FIELD_WIDTH = 2

OFFLINE = "Home Assistant injoignable"
GUIDE = (
    ("numero + ENVOI", "commander"),
    ("numero + SUITE", "plus, ouvrir"),
    ("numero + RETOUR", "moins, fermer"),
    ("SUITE / RETOUR", "ecran suivant / prec."),
    ("SOMMAIRE", "liste des rubriques"),
    ("REPETITION", "redessiner l'ecran"),
    ("CORRECTION", "effacer un chiffre"),
    ("ANNULATION", "effacer la saisie"),
    ("CONNEXION/FIN", "raccrocher"),
)
# Ce qui ne change pas d'état visible : on dit que c'est parti.
_LAUNCHED = {"scene", "script", "button", "input_button"}


@dataclass
class _Place:
    view: int | None = None  # None : le sommaire
    screen: int = 0
    guide: bool = False


class Session:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        hass: Hass,
        title: str = DEFAULT_TITLE,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._hass = hass
        self._title = title[: vt.WIDTH // 2]
        self._dashboard = hass.dashboard
        self._place = _Place()
        self._screen = Screen()
        self._rows: dict[int, bytes] = {}
        self._field = ""
        self._confirm: tuple[Item, Action] | None = None
        self._series: dict[str, graph.Series] = {}
        self._polls: dict[str, asyncio.Task[None]] = {}
        self._polled: dict[str, float] = {}
        self._stats: dict[Item, float | None] = {}
        self._stats_at = 0.0

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        decoder = vt.KeyDecoder()
        with self._hass.listen() as events:
            await self._go_home()
            deadline = loop.time() + IDLE_TIMEOUT
            read = asyncio.ensure_future(self._reader.read(64))
            event = asyncio.ensure_future(events.get())
            tick = asyncio.ensure_future(self._next_point())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {read, event, tick},
                        timeout=max(0.0, deadline - loop.time()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        await self._goodbye("Deconnexion automatique")
                        return
                    if event in done:
                        batch = [event.result()]
                        while not events.empty():  # on regroupe ce qui est arrivé pendant l'envoi
                            batch.append(events.get_nowait())
                        event = asyncio.ensure_future(events.get())
                        await self._refresh(batch)
                    if read in done:
                        data = read.result()
                        if not data:
                            return
                        read = asyncio.ensure_future(self._reader.read(64))
                        deadline = loop.time() + IDLE_TIMEOUT
                        for key in decoder.feed(data):
                            if key is Key.CONNEXION_FIN:
                                await self._goodbye("Au revoir !")
                                return
                            await self._handle(key)
                    if tick in done:
                        await self._advance()
                    # L'écran a pu changer, et avec lui le prochain point des courbes.
                    tick.cancel()
                    tick = asyncio.ensure_future(self._next_point())
            finally:
                for task in (read, event, tick, *self._polls.values()):
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                        await task

    # --- Touches -----------------------------------------------------------------------------

    async def _handle(self, key: Key | str) -> None:
        if self._place.guide:  # n'importe quelle touche referme l'aide
            self._place.guide = False
            await self._draw()
            return
        confirm, self._confirm = self._confirm, None
        if key is Key.ENVOI:
            await self._envoi(confirm)
        elif key is Key.SOMMAIRE:
            await self._go_home()
        elif key is Key.GUIDE:
            self._place.guide = True
            await self._draw()
        elif key in (Key.SUITE, Key.RETOUR):
            direction = 1 if key is Key.SUITE else -1
            if self._field and self._place.view is not None:
                await self._adjust(direction)
            else:
                await self._turn(direction)
        elif key is Key.CORRECTION:
            if self._field:
                self._field = self._field[:-1]
                await self._send(vt.backspace())
        elif key is Key.ANNULATION:
            self._field = ""
            await self._send(_field_position() + bytes((vt.CAN,)))
        elif key is Key.REPETITION:
            await self._draw()
        elif isinstance(key, str) and key.isdigit() and len(self._field) < FIELD_WIDTH:
            self._field += key
            await self._send(vt.text(key))  # pas d'écho local en ligne : c'est à nous de le faire

    async def _envoi(self, confirm: tuple[Item, Action] | None) -> None:
        field, self._field = self._field, ""
        if not field and confirm:
            await self._execute(*confirm)
            return
        number = int(field) if field else 0
        views = self._hass.dashboard.views
        if self._place.view is None:
            if 1 <= number <= len(views):
                await self._open(number - 1)
            else:
                await self._show_message("Numero inconnu")
            return
        item = self._screen.numbered.get(number)
        if item is None:
            await self._show_message("Numero inconnu")
            return
        state = await self._available(item)
        if state is None:
            return
        action = render.primary(item.entity_id, state)
        if action is None:
            await self._show_message(f"{self._name(item)} : SUITE ou RETOUR")
        elif action.confirm:
            self._confirm = (item, action)
            await self._show_message(f"{self._name(item)} : ENVOI pour confirmer")
        else:
            await self._execute(item, action)

    async def _adjust(self, direction: int) -> None:
        """Numéro + SUITE/RETOUR. Le numéro reste saisi : on peut appuyer encore."""
        item = self._screen.numbered.get(int(self._field))
        if item is None:
            await self._show_message("Numero inconnu")
            return
        state = await self._available(item)
        if state is None:
            return
        action = render.adjust(item.entity_id, state, direction)
        if action is None:
            await self._show_message(f"{self._name(item)} : pas de reglage")
            return
        await self._execute(item, action, keep_field=True)

    async def _available(self, item: Item) -> render.State | None:
        state = self._hass.states.get(item.entity_id)
        if state is None or state.state == "unavailable":
            await self._show_message(f"{self._name(item)} : indisponible")
            return None
        return state

    async def _execute(self, item: Item, action: Action, keep_field: bool = False) -> None:
        try:
            await self._hass.call_service(action.domain, action.service, item.entity_id, **action.data)
        except HAError as err:
            log.warning("%s.%s %s : %s", action.domain, action.service, item.entity_id, err)
            message = f"Refuse : {err}" if self._hass.connected else OFFLINE
            await self._show_message(message, keep_field)
            return
        log.info("%s.%s %s", action.domain, action.service, item.entity_id)
        # Le nouvel état arrive par Home Assistant, et redessine la ligne tout seul.
        launched = action.domain in _LAUNCHED
        await self._show_message(f"{self._name(item)} : lance" if launched else "", keep_field)

    def _name(self, item: Item) -> str:
        return render.name_of(item, self._hass.states.get(item.entity_id))

    # --- Navigation ----------------------------------------------------------------------------

    async def _go_home(self) -> None:
        views = self._hass.dashboard.views
        self._place = _Place(0 if len(views) == 1 else None)
        await self._draw()

    async def _open(self, view: int, screen: int = 0) -> None:
        self._place = _Place(view, screen)
        await self._draw()

    async def _turn(self, direction: int) -> None:
        """SUITE et RETOUR parcourent les écrans, puis passent à la rubrique voisine."""
        views = self._hass.dashboard.views
        place = self._place
        if not views:
            return
        if place.view is None:
            if direction > 0 and place.screen + 1 < self._menu_screens():
                await self._open_menu(place.screen + 1)
            elif direction > 0:
                await self._open(0)
            elif place.screen > 0:
                await self._open_menu(place.screen - 1)
            return
        count = len(render.screens(views[place.view], self._hass.states))
        if direction > 0:
            if place.screen + 1 < count:
                await self._open(place.view, place.screen + 1)
            else:
                await self._open((place.view + 1) % len(views))
        elif place.screen > 0:
            await self._open(place.view, place.screen - 1)
        else:
            previous = (place.view - 1) % len(views)
            last = len(render.screens(views[previous], self._hass.states)) - 1
            await self._open(previous, last)

    async def _open_menu(self, screen: int) -> None:
        self._place = _Place(None, screen)
        await self._draw()

    def _menu_screens(self) -> int:
        return max(1, -(-len(self._hass.dashboard.views) // CONTENT_ROWS))

    # --- Écran ---------------------------------------------------------------------------------

    def _compose(self) -> dict[int, bytes]:
        """Les rangées qui dépendent du tableau de bord et des états."""
        dashboard = self._hass.dashboard
        place = self._place
        if place.view is not None and place.view >= len(dashboard.views):
            place.view, place.screen = None, 0
        if place.guide:
            return _guide()
        if not dashboard.views:
            self._screen = Screen()
            return {
                CONTENT_TOP + 6: vt.text("  Aucune page a afficher."),
                CONTENT_TOP + 7: vt.text("  Voir le journal de l'app."),
            }
        if place.view is None:
            return self._menu()

        view = dashboard.views[place.view]
        screens = render.screens(view, self._hass.states)
        place.screen = min(place.screen, len(screens) - 1)
        self._screen = screens[place.screen]
        rows = render.render(self._screen, self._hass.states, self._graph_points(), self._stats)
        if not self._screen.lines:
            rows[CONTENT_TOP + 6] = vt.text("  Rien a afficher pour l'instant.")
        counter = f"{place.screen + 1}/{len(screens)}" if len(screens) > 1 else ""
        rows[SUBTITLE_ROW] = _subtitle(view.title, counter)
        if self._screen.numbered:
            rows[HELP_ROW] = vt.text("Numero + ENVOI : commander  GUIDE : aide")
        else:
            rows[HELP_ROW] = vt.text("SUITE/RETOUR : pages   GUIDE : aide")
        return rows

    def _menu(self) -> dict[int, bytes]:
        views = self._hass.dashboard.views
        count = self._menu_screens()
        self._place.screen = min(self._place.screen, count - 1)
        first = self._place.screen * CONTENT_ROWS
        rows = {
            CONTENT_TOP + index: vt.text(f" {first + index + 1:>2}  {view.title[: vt.WIDTH - 6]}")
            for index, view in enumerate(views[first : first + CONTENT_ROWS])
        }
        counter = f"{self._place.screen + 1}/{count}" if count > 1 else ""
        rows[SUBTITLE_ROW] = _subtitle(self._hass.dashboard.title, counter)
        rows[HELP_ROW] = vt.text("Numero + ENVOI : choisir  GUIDE : aide")
        self._screen = Screen()
        return rows

    async def _draw(self, message: str = "") -> None:
        """Redessine tout l'écran."""
        self._field = ""
        self._dashboard = self._hass.dashboard
        rows = self._compose()
        series = await self._load_series()
        stats = await self._load_stats()
        if series or stats:
            rows = self._compose()
        page = bytearray(vt.clear())
        if not self._hass.connected:
            page += _status()
        page += vt.pos(2, (vt.WIDTH - len(self._title)) // 2 + 1) + vt.double_height(self._title)
        page += vt.rule(SUBTITLE_ROW + 1)
        for row in sorted(rows):
            if rows[row]:
                page += vt.pos(row) + rows[row]
        page += vt.rule(RULE_ROW)
        if message:
            page += vt.pos(MESSAGE_ROW) + vt.text(message[: vt.WIDTH - 1])
        page += vt.pos(PROMPT_ROW) + vt.text(PROMPT) + _field_position() + vt.cursor(True)
        self._rows = rows
        await self._send(bytes(page))

    async def _refresh(self, batch: list[Event]) -> None:
        """Après un changement dans Home Assistant : seulement ce qui a changé à l'écran."""
        if self._hass.dashboard != self._dashboard:  # rechargé, et pas à l'identique
            await self._draw("Tableau de bord mis a jour")
            return
        self._record(batch)
        changed = {e for event in batch if isinstance(event, frozenset) for e in event}
        await self._load_stats(changed)
        update = bytearray()
        if Change.CONNECTION in batch:
            update += _status() if not self._hass.connected else vt.pos(0) + bytes((vt.CAN,))
        if not self._place.guide:
            update += self._diff(self._compose())
        await self._send_update(bytes(update))

    async def _send_update(self, update: bytes) -> None:
        if update:
            await self._send(vt.cursor(False) + update + self._cursor_back() + vt.cursor(True))

    def _diff(self, rows: dict[int, bytes]) -> bytes:
        old, self._rows = self._rows, rows
        dirty = {row for row in old.keys() | rows.keys() if old.get(row, b"") != rows.get(row, b"")}
        for row in list(dirty):
            # Une rangée en double taille déborde sur celle du dessus : on la reprend aussi.
            if render.is_double(rows.get(row, b"")) or render.is_double(old.get(row, b"")):
                dirty.add(row - 1)
        return b"".join(_row_update(row, old.get(row, b""), rows.get(row, b"")) for row in sorted(dirty))

    # --- Courbes ---------------------------------------------------------------------------------

    def _graph_items(self) -> list[Item]:
        return [
            line.element
            for line in self._screen.lines
            if isinstance(line.element, Item) and line.element.style == "graph"
        ]

    def _graph_points(self) -> render.Graphs:
        now = time.time()
        return {
            item: graph.points(self._series.get(item.entity_id), graph.period(item.hours), now)
            for item in self._graph_items()
        }

    async def _load_series(self) -> bool:
        """Les courbes de l'écran partent de l'historique. Vrai s'il a fallu le charger."""
        items = self._graph_items()
        self._series = {e: s for e, s in self._series.items() if e in {i.entity_id for i in items}}
        now = time.time()
        starts: dict[str, float] = {}
        for item in items:
            step = graph.period(item.hours)
            start = now - step * graph.POINTS
            series = self._series.get(item.entity_id)
            if series is None or series.since > start + step:
                starts[item.entity_id] = min(start, starts.get(item.entity_id, start))
        if not starts:
            return False
        try:
            history = await self._hass.history(sorted(starts), min(starts.values()))
        except HAError as err:
            log.warning("historique de %s : %s", ", ".join(sorted(starts)), err)
            history = {}
        for entity_id, start in starts.items():
            series = graph.Series(start)
            for moment, state in history.get(entity_id, []):
                series.add(moment, graph.value(state))
            current = self._hass.states.get(entity_id)
            series.add(now, graph.value(current.state) if current else None)
            self._series[entity_id] = series
        return True

    def _record(self, batch: list[Event]) -> None:
        now = time.time()
        for event in batch:
            if isinstance(event, frozenset):
                for entity_id in event & self._series.keys():
                    state = self._hass.states.get(entity_id)
                    self._series[entity_id].add(now, graph.value(state.state) if state else None)

    def _live(self) -> dict[str, float]:
        """Les entités à relever d'elles-mêmes, et tous les combien : jauges, courbes courtes."""
        live: dict[str, float] = {}
        for line in self._screen.lines:
            item = line.element
            if not isinstance(item, Item):
                continue
            if item.style == "bar":
                every = GAUGE_PERIOD
            elif item.style == "graph" and graph.period(item.hours) <= graph.LIVE_PERIOD:
                every = graph.period(item.hours)
            else:
                continue
            live[item.entity_id] = min(every, live.get(item.entity_id, every))
        return live

    async def _next_point(self) -> None:
        """Attend le prochain point d'une courbe, ou le prochain relevé. Sinon, attend sans fin."""
        steps = [graph.period(item.hours) for item in self._graph_items()]
        steps += self._live().values()
        if self._stat_items():
            steps.append(STAT_PERIOD)
        if not steps or self._place.guide:
            await asyncio.Event().wait()
        now = time.time()
        await asyncio.sleep(min(step - now % step for step in steps) + 0.01)

    async def _advance(self) -> None:
        """Les courbes défilent d'un point, et ce qui doit l'être est relevé."""
        now = time.time()
        for entity_id, every in self._live().items():
            if now - self._polled.get(entity_id, 0) >= every * 0.9:
                self._polled[entity_id] = now
                self._poll(entity_id)
        if self._stat_items() and now - self._stats_at >= STAT_PERIOD * 0.9:
            await self._load_stats()
        windows: dict[str, float] = {}
        for item in self._graph_items():
            step = graph.period(item.hours)
            windows[item.entity_id] = max(windows.get(item.entity_id, 0), step * (graph.POINTS + 1))
        for entity_id, window in windows.items():
            if entity_id in self._series:
                self._series[entity_id].forget_before(now - window)
        if not self._place.guide:
            await self._send_update(self._diff(self._compose()))

    # --- Statistiques ----------------------------------------------------------------------------

    def _stat_items(self) -> list[Item]:
        return [
            line.element
            for line in self._screen.lines
            if isinstance(line.element, Item) and line.element.style == "stat"
        ]

    async def _load_stats(self, entity_ids: set[str] | None = None) -> bool:
        """Demande les statistiques de l'écran, ou celles de ces entités. Vrai s'il y en avait."""
        items = self._stat_items()
        self._stats = {item: value for item, value in self._stats.items() if item in items}
        if entity_ids is None:
            wanted = items
            self._stats_at = time.time()
        else:
            wanted = [i for i in items if i.entity_id in entity_ids or i not in self._stats]
        for item in wanted:
            assert item.stat is not None
            kind, period = item.stat
            try:
                self._stats[item] = await self._hass.statistic(item.entity_id, kind, json.loads(period))
            except HAError as err:
                log.warning("statistique de %s : %s", item.entity_id, err)
                self._stats[item] = None
        return bool(wanted)

    def _poll(self, entity_id: str) -> None:
        """Demande à Home Assistant de relever l'entité, sans attendre : le nouvel état suivra."""
        task = self._polls.get(entity_id)
        if task is None or task.done():
            self._polls[entity_id] = asyncio.create_task(self._update_entity(entity_id))

    async def _update_entity(self, entity_id: str) -> None:
        try:
            await self._hass.call_service("homeassistant", "update_entity", entity_id)
        except HAError as err:
            log.debug("relevé de %s : %s", entity_id, err)

    async def _show_message(self, message: str, keep_field: bool = False) -> None:
        if not keep_field:
            self._field = ""
        await self._send(
            vt.cursor(False)
            + vt.pos(MESSAGE_ROW)
            + vt.text(message[: vt.WIDTH - 1])
            + bytes((vt.CAN,))
            + _field_position()
            + vt.text(self._field)
            + bytes((vt.CAN,))
            + vt.cursor(True)
        )

    def _cursor_back(self) -> bytes:
        return vt.pos(PROMPT_ROW, FIELD_COL + len(self._field))

    async def _goodbye(self, message: str) -> None:
        await self._send(vt.clear() + vt.pos(12, 13) + vt.text(message) + vt.disconnect())

    async def _send(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()


def _row_update(row: int, old: bytes, new: bytes) -> bytes:
    """Ce qu'il faut envoyer pour passer de l'ancienne rangée à la nouvelle."""
    if isinstance(old, render.Mosaic) and isinstance(new, render.Mosaic):
        update = _mosaic_update(row, old.cells, new.cells)
        if new.tail != old.tail:
            update += vt.pos(row, len(new.cells) + 1) + new.tail
        return update
    if (
        isinstance(old, render.Zoned)
        and isinstance(new, render.Zoned)
        and [col for col, _ in old.zones] == [col for col, _ in new.zones]
    ):
        return b"".join(
            vt.pos(row, col) + content
            for (col, content), (_, before) in zip(new.zones, old.zones)
            if content != before
        )
    return vt.pos(row) + bytes((vt.CAN,)) + new


def _mosaic_update(row: int, old: bytes, new: bytes) -> bytes:
    """Les caractères qui changent, par paquets : un écart de moins de 4 coûte moins qu'un saut."""
    out = bytearray()
    changed = [i for i in range(len(new)) if i >= len(old) or old[i] != new[i]]
    start = end = None
    for index in changed + [None]:
        if index is not None and end is not None and index - end < 4:
            end = index + 1
            continue
        if start is not None and end is not None:
            out += vt.pos(row, start + 1) + bytes((vt.SO,)) + new[start:end]
        if index is not None:
            start, end = index, index + 1
    return bytes(out + bytes((vt.SI,))) if out else b""


def _subtitle(title: str, counter: str) -> bytes:
    out = vt.text(title[: vt.WIDTH - len(counter) - 1])
    if counter:
        out += vt.pos(SUBTITLE_ROW, vt.WIDTH - len(counter) + 1) + vt.text(counter)
    return out


def _guide() -> dict[int, bytes]:
    rows = {SUBTITLE_ROW: vt.text("Aide")}
    for index, (keys, effect) in enumerate(GUIDE):
        rows[CONTENT_TOP + 1 + index] = vt.text(f" {keys:<17}{effect}")
    rows[HELP_ROW] = vt.text("Une touche pour revenir")
    return rows


def _status() -> bytes:
    """La rangée 0, au-dessus de l'écran : Home Assistant ne répond plus."""
    return vt.pos(0) + vt.text(OFFLINE) + bytes((vt.CAN,))


def _field_position() -> bytes:
    return vt.pos(PROMPT_ROW, FIELD_COL)

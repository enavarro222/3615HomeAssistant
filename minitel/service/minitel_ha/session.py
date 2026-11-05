"""Un appel Minitel : une connexion TCP, les écrans du tableau de bord, des touches.

L'écran suit Home Assistant en direct : à chaque changement, on recalcule les rangées et on
n'envoie que celles qui diffèrent de ce qui est affiché. À 1200 bauds, c'est ce qui compte.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from . import render
from . import videotex as vt
from .config import DEFAULT_TITLE
from .dashboard import Item
from .hass import Change, Event, HAError, Hass
from .render import CONTENT_ROWS, CONTENT_TOP, Action, Screen
from .videotex import Key

log = logging.getLogger("minitel_ha")

# Un appel oublié garderait le Minitel en ligne : on raccroche au bout de 10 min.
IDLE_TIMEOUT = 600

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

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        decoder = vt.KeyDecoder()
        with self._hass.listen() as events:
            await self._go_home()
            deadline = loop.time() + IDLE_TIMEOUT
            read = asyncio.ensure_future(self._reader.read(64))
            event = asyncio.ensure_future(events.get())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {read, event},
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
            finally:
                for task in (read, event):
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
        rows = render.render(self._screen, self._hass.states)
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
        update = bytearray()
        if Change.CONNECTION in batch:
            update += _status() if not self._hass.connected else vt.pos(0) + bytes((vt.CAN,))
        if not self._place.guide:
            update += self._diff(self._compose())
        if update:
            await self._send(vt.cursor(False) + bytes(update) + self._cursor_back() + vt.cursor(True))

    def _diff(self, rows: dict[int, bytes]) -> bytes:
        old, self._rows = self._rows, rows
        dirty = {row for row in old.keys() | rows.keys() if old.get(row, b"") != rows.get(row, b"")}
        for row in list(dirty):
            # Une rangée en double taille déborde sur celle du dessus : on la reprend aussi.
            if render.is_double(rows.get(row, b"")) or render.is_double(old.get(row, b"")):
                dirty.add(row - 1)
        return b"".join(vt.pos(row) + bytes((vt.CAN,)) + rows.get(row, b"") for row in sorted(dirty))

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

"""Ce que le Minitel affiche d'une vue, et ce que font ses touches. Sans entrée ni sortie.

Une vue s'étale sur un ou plusieurs écrans de 16 rangées. Chaque écran numérote ses entités
qu'on peut commander, de 1 à n. Le contenu de chaque rangée est rendu en octets Vidéotex :
la session compare avec ce qui est déjà à l'écran, et n'envoie que ce qui change. Les rangées
qui bougent sans cesse (une courbe, sa valeur) sont découpées en zones, pour n'en renvoyer
qu'un morceau.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from . import graph
from . import videotex as vt
from .dashboard import Element, Glance, Heading, Item, View, is_visible, without_empty_headings

CONTENT_TOP = 5
CONTENT_ROWS = 16
GLANCE_COLUMNS = 2
BAR_WIDTH = 10
VALUE_WIDTH = 16


class State(Protocol):
    @property
    def state(self) -> str: ...

    @property
    def attributes(self) -> Mapping[str, Any]: ...


# --- Découpage en écrans -------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """Une ligne d'écran : un titre, une entité, ou une rangée de coup d'œil."""

    element: Heading | Item | tuple[Item, ...]
    number: int | None = None

    @property
    def height(self) -> int:
        if isinstance(self.element, Item) and self.element.style == "big":
            return 2
        if isinstance(self.element, Item) and self.element.style == "graph":
            return 1 + graph.ROWS
        return 1


@dataclass(frozen=True)
class Screen:
    lines: tuple[Line, ...] = ()
    numbered: dict[int, Item] = field(default_factory=dict)


def screens(view: View, states: Mapping[str, State]) -> list[Screen]:
    """Les écrans de la vue, avec ce qui est visible maintenant. Au moins un, même vide."""
    values = {entity_id: s.state for entity_id, s in states.items()}
    elements = without_empty_headings([e for e in view.elements if is_visible(e.visible_if, values)])
    lines = [line for element in elements for line in _lines(element)]

    pages: list[list[Line]] = [[]]
    used = 0
    heading: Line | None = None
    for index, line in enumerate(lines):
        is_heading = isinstance(line.element, Heading)
        # Un titre ne finit pas un écran : il passe avec ce qu'il annonce.
        needed = line.height + (1 if is_heading and index + 1 < len(lines) else 0)
        if used and used + needed > CONTENT_ROWS:
            pages.append([])
            used = 0
            if heading and not is_heading:  # on rappelle le titre en haut de l'écran suivant
                pages[-1].append(heading)
                used = heading.height
        pages[-1].append(line)
        used += line.height
        if is_heading:
            heading = line
    return [_numbered(page) for page in pages]


def _lines(element: Element) -> list[Line]:
    if isinstance(element, Glance):
        items = element.items
        return [Line(items[i : i + GLANCE_COLUMNS]) for i in range(0, len(items), GLANCE_COLUMNS)]
    return [Line(element)]


def _numbered(lines: list[Line]) -> Screen:
    out = []
    numbered: dict[int, Item] = {}
    for line in lines:
        element = line.element
        if isinstance(element, Item) and commandable(element.entity_id):
            number = len(numbered) + 1
            numbered[number] = element
            line = Line(element, number)
        out.append(line)
    return Screen(tuple(out), numbered)


# --- Rendu des rangées ---------------------------------------------------------------------------


class Zoned(bytes):
    """Une rangée faite de zones de largeur fixe, chacune remplie jusqu'au bout.

    Si l'écran montre une rangée découpée de la même façon, il suffit de renvoyer les zones qui
    ont changé : leur largeur fixe efface ce qui restait de l'ancien contenu.
    """

    zones: tuple[tuple[int, bytes], ...]

    def __new__(cls, row: int, zones: Sequence[tuple[int, bytes]]) -> Zoned:
        data = b"".join((b"" if col == 1 else vt.pos(row, col)) + content for col, content in zones)
        self = super().__new__(cls, data)
        self.zones = tuple(zones)
        return self


class Mosaic(bytes):
    """Une rangée de mosaïques, un caractère par colonne : on ne renvoie que ceux qui changent."""

    cells: bytes

    def __new__(cls, cells: bytes) -> Mosaic:
        self = super().__new__(cls, bytes((vt.SO,)) + cells + bytes((vt.SI,)))
        self.cells = cells
        return self


Graphs = Mapping[Item, Sequence[float | None]]
"""Les points de chaque courbe à l'écran, tels que `graph.points` les donne."""


def render(screen: Screen, states: Mapping[str, State], graphs: Graphs | None = None) -> dict[int, bytes]:
    """Le contenu de chaque rangée occupée, sans le positionnement en début de rangée."""
    rows: dict[int, bytes] = {}
    row = CONTENT_TOP
    for line in screen.lines:
        element = line.element
        if isinstance(element, Heading):
            rows[row] = _heading(row, element, states)
        elif isinstance(element, tuple):
            rows[row] = _glance(row, element, states)
        elif element.style == "big":
            rows[row] = b""
            rows[row + 1] = _big(row + 1, element, states)
        elif element.style == "bar":
            rows[row] = _bar(row, line, states)
        elif element.style == "graph":
            points = list((graphs or {}).get(element, [None] * graph.POINTS))
            rows.update(_graph(row, line, states, points))
        else:
            rows[row] = _item(row, line, states)
        row += line.height
    return rows


def is_double(content: bytes) -> bool:
    """Une rangée en double taille déborde sur celle du dessus."""
    return bytes((vt.ESC, 0x4F)) in content


def _item(row: int, line: Line, states: Mapping[str, State]) -> bytes:
    item = line.element
    assert isinstance(item, Item)
    state = states.get(item.entity_id)
    value, active = describe(item.entity_id, state)
    value = _fit(value, 16)
    col = vt.WIDTH - len(value) + 1
    prefix = f"{line.number:>2} " if line.number else "   "
    name = _fit(name_of(item, state), col - len(prefix) - 2)
    return vt.text(prefix + name) + vt.pos(row, col) + (vt.inverse(value) if active else vt.text(value))


def _heading(row: int, heading: Heading, states: Mapping[str, State]) -> bytes:
    badges = "  ".join(describe(b.entity_id, states.get(b.entity_id))[0] for b in heading.badges)
    badges = _fit(badges, vt.WIDTH // 2)
    out = vt.inverse(" " + _fit(heading.text, vt.WIDTH - len(badges) - 3) + " ")
    if badges:
        out += vt.pos(row, vt.WIDTH - len(badges) + 1) + vt.text(badges)
    return out


def _glance(row: int, items: tuple[Item, ...], states: Mapping[str, State]) -> bytes:
    width = vt.WIDTH // GLANCE_COLUMNS
    out = b""
    for index, item in enumerate(items):
        state = states.get(item.entity_id)
        value = _fit(describe(item.entity_id, state)[0], width - 4)
        name = _fit(name_of(item, state), width - len(value) - 2)
        left = index * width + 1
        out += vt.pos(row, left) + vt.text(name) + vt.pos(row, left + width - len(value) - 1) + vt.text(value)
    return out


def _big(row: int, item: Item, states: Mapping[str, State]) -> bytes:
    state = states.get(item.entity_id)
    value = _fit(describe(item.entity_id, state)[0], 9)
    col = vt.WIDTH - 2 * len(value) + 1
    name = _fit(name_of(item, state), col - 5)
    return vt.text("   " + name) + vt.pos(row, col) + vt.double_size(value)


def _bar(row: int, line: Line, states: Mapping[str, State]) -> bytes:
    item = line.element
    assert isinstance(item, Item)
    state = states.get(item.entity_id)
    value = _fit(describe(item.entity_id, state)[0], 8)
    value_col = vt.WIDTH - len(value) + 1
    bar_col = value_col - BAR_WIDTH - 1
    name = _fit(name_of(item, state), bar_col - 5)
    number = _number(state.state) if state else None
    low, high = item.bounds
    fraction = (number - low) / (high - low) if number is not None and high > low else 0.0
    return (
        vt.text("   " + name)
        + vt.pos(row, bar_col)
        + vt.bar(fraction, BAR_WIDTH)
        + vt.pos(row, value_col)
        + vt.text(value)
    )


def _graph(row: int, line: Line, states: Mapping[str, State], points: list[float | None]) -> dict[int, bytes]:
    """Le nom, l'échelle et la valeur, puis la courbe en dessous."""
    item = line.element
    assert isinstance(item, Item)
    state = states.get(item.entity_id)
    low, high = graph.scale(points)
    value, active = describe(item.entity_id, state)
    value = _fit(value, VALUE_WIDTH).rjust(VALUE_WIDTH)
    left = vt.WIDTH - VALUE_WIDTH
    prefix = f"{line.number:>2} " if line.number else "   "
    bounds = f" ({_format_number(low, f'{low:g}')} a {_format_number(high, f'{high:g}')})"
    name = _fit(name_of(item, state), left - len(prefix) - len(bounds) - 1)
    label = _fit(prefix + name + bounds, left).ljust(left)
    rows: dict[int, bytes] = {
        row: Zoned(row, ((1, vt.text(label)), (left + 1, vt.inverse(value) if active else vt.text(value))))
    }
    for index, cells in enumerate(graph.mosaic(points, low, high), 1):
        rows[row + index] = Mosaic(cells)
    return rows


def _fit(value: str, width: int) -> str:
    return value[: max(width, 0)]


def name_of(item: Item, state: State | None) -> str:
    if item.name:
        return item.name
    if state and state.attributes.get("friendly_name"):
        return str(state.attributes["friendly_name"])
    return item.entity_id


# --- Les états en texte --------------------------------------------------------------------------

UNAVAILABLE = "HS"
UNKNOWN = "--"

_ON_OFF = {
    "light": ("ALLUME", "ETEINT"),
    "switch": ("ALLUME", "ETEINT"),
    "fan": ("MARCHE", "ARRET"),
    "humidifier": ("MARCHE", "ARRET"),
    "siren": ("MARCHE", "ARRET"),
    "input_boolean": ("OUI", "NON"),
    "automation": ("ACTIVE", "INACTIVE"),
    "schedule": ("ACTIF", "INACTIF"),
    "remote": ("MARCHE", "ARRET"),
}

_BINARY_CLASSES = {
    ("door", "window", "opening", "garage_door"): ("OUVERT", "FERME"),
    ("lock",): ("OUVERT", "VERROUILLE"),
    ("motion", "occupancy", "presence", "moving", "vibration", "sound"): ("DETECTE", "RAS"),
    ("moisture",): ("HUMIDE", "SEC"),
    ("smoke", "gas", "carbon_monoxide", "safety", "problem", "tamper", "heat", "cold"): ("ALERTE", "OK"),
    ("battery",): ("FAIBLE", "OK"),
    ("connectivity",): ("CONNECTE", "DECONNECTE"),
    ("plug", "power", "running", "light"): ("ALLUME", "ETEINT"),
}

_WORDS = {
    "cover": {"open": "OUVERT", "closed": "FERME", "opening": "OUVRE...", "closing": "FERME..."},
    "valve": {"open": "OUVERTE", "closed": "FERMEE", "opening": "OUVRE...", "closing": "FERME..."},
    "lock": {
        "locked": "VERROUILLE",
        "unlocked": "OUVERT",
        "locking": "VERROU...",
        "unlocking": "OUVRE...",
        "jammed": "BLOQUE",
        "open": "OUVERT",
    },
    "media_player": {
        "on": "MARCHE",
        "off": "ARRET",
        "playing": "LECTURE",
        "paused": "PAUSE",
        "idle": "ATTENTE",
        "standby": "VEILLE",
        "buffering": "CHARGE...",
    },
    "person": {"home": "MAISON", "not_home": "ABSENT"},
    "device_tracker": {"home": "MAISON", "not_home": "ABSENT"},
    "alarm_control_panel": {
        "disarmed": "DESACTIVEE",
        "armed_home": "ACTIVEE (MAISON)",
        "armed_away": "ACTIVEE",
        "armed_night": "ACTIVEE (NUIT)",
        "armed_vacation": "ACTIVEE",
        "arming": "ACTIVATION...",
        "pending": "ATTENTE...",
        "triggered": "ALERTE",
    },
    "vacuum": {"cleaning": "NETTOYAGE", "docked": "A QUAI", "returning": "RETOUR", "idle": "ATTENTE"},
    "sun": {"above_horizon": "JOUR", "below_horizon": "NUIT"},
    "climate": {
        "off": "ARRET",
        "heat": "CHAUFFE",
        "cool": "CLIM",
        "auto": "AUTO",
        "heat_cool": "AUTO",
        "dry": "SEC",
        "fan_only": "VENTIL",
    },
    "script": {"on": "EN COURS", "off": "LANCER"},
}

_WEATHER = {
    "sunny": "Soleil",
    "clear-night": "Nuit claire",
    "partlycloudy": "Eclaircies",
    "cloudy": "Nuageux",
    "fog": "Brouillard",
    "rainy": "Pluie",
    "pouring": "Averses",
    "snowy": "Neige",
    "snowy-rainy": "Neige/pluie",
    "hail": "Grele",
    "lightning": "Orage",
    "lightning-rainy": "Orage",
    "windy": "Vent",
    "windy-variant": "Vent",
    "exceptional": "Alerte",
}

# Allumé, ouvert, en alerte : en vidéo inverse, pour attirer l'œil.
_ACTIVE = {
    "on",
    "open",
    "opening",
    "unlocked",
    "unlocking",
    "jammed",
    "playing",
    "heat",
    "cool",
    "heat_cool",
    "auto",
    "triggered",
}


def describe(entity_id: str, state: State | None) -> tuple[str, bool]:
    """Le texte de l'état, et s'il est actif (affiché en inverse)."""
    if state is None or state.state == "unavailable":
        return UNAVAILABLE, False
    if state.state == "unknown":
        return UNKNOWN, False
    domain = entity_id.split(".", 1)[0]
    value, attributes = state.state, state.attributes
    active = value in _ACTIVE

    if domain in _ON_OFF and value in ("on", "off"):
        return _ON_OFF[domain][value != "on"], active
    if domain == "binary_sensor":
        device_class = attributes.get("device_class")
        words = next((w for classes, w in _BINARY_CLASSES.items() if device_class in classes), ("OUI", "NON"))
        return words[value != "on"], active
    if domain == "cover":
        position = attributes.get("current_position")
        if value == "open" and isinstance(position, (int, float)) and 0 < position < 100:
            return f"OUVERT {position:.0f}%", True
    if domain == "climate":
        return _climate(value, attributes), active
    if domain == "weather":
        temperature = attributes.get("temperature")
        text = _WEATHER.get(value, value)
        return (f"{text} {_format_number(temperature)}°" if temperature is not None else text), False
    if domain in ("scene", "button", "input_button"):
        return "LANCER", False
    if domain in _WORDS:
        return _WORDS[domain].get(value, value), active
    if attributes.get("device_class") == "timestamp":
        return _timestamp(value), False
    number = _number(value)
    if number is not None:
        return _with_unit(_format_number(number, value), attributes.get("unit_of_measurement")), False
    return value, active


def _climate(value: str, attributes: Mapping[str, Any]) -> str:
    current = attributes.get("current_temperature")
    target = attributes.get("temperature")
    mode = _WORDS["climate"].get(value, value.upper())
    if value == "off" or target is None:
        return f"{mode} {_format_number(current)}°" if current is not None else mode
    if current is None:
        return f"> {_format_number(target)}°"
    return f"{_format_number(current)}° > {_format_number(target)}°"


def _timestamp(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo:
        moment = moment.astimezone()
    return moment.strftime("%d/%m %H:%M")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # pas NaN


def _format_number(number: Any, raw: str | None = None) -> str:
    """À la française, une décimale au plus : « 20,4 ». Un entier reste entier."""
    value = _number(number)
    if value is None:
        return str(number)
    text = str(raw if raw is not None else number)
    if value.is_integer() and "." not in text:
        return str(int(value))
    return f"{value:.1f}".replace(".", ",")


def _with_unit(value: str, unit: Any) -> str:
    if not unit:
        return value
    return f"{value}{unit}" if unit in ("°C", "°F", "%", "°") else f"{value} {unit}"


# --- Les touches ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    domain: str
    service: str
    data: Mapping[str, Any] = field(default_factory=dict)
    confirm: bool = False
    """Une serrure ne s'ouvre pas sur une faute de frappe : ENVOI une seconde fois."""


_TOGGLE = {
    "light",
    "switch",
    "fan",
    "input_boolean",
    "automation",
    "humidifier",
    "siren",
    "media_player",
    "valve",
    "cover",
    "remote",
}
_PRESS = {
    "scene": "turn_on",
    "script": "turn_on",
    "button": "press",
    "input_button": "press",
    "input_select": "select_next",
    "select": "select_next",
}
_ADJUST_ONLY = {"input_number", "number"}


def commandable(entity_id: str) -> bool:
    domain = entity_id.split(".", 1)[0]
    return domain in _TOGGLE or domain in _PRESS or domain in _ADJUST_ONLY or domain in ("climate", "lock")


def primary(entity_id: str, state: State) -> Action | None:
    """Numéro + ENVOI."""
    domain = entity_id.split(".", 1)[0]
    if domain in _TOGGLE:
        return Action(domain, "toggle")
    if domain in _PRESS:
        return Action(domain, _PRESS[domain])
    if domain == "climate":
        return Action(domain, "turn_on" if state.state == "off" else "turn_off")
    if domain == "lock":
        return Action(domain, "lock" if state.state == "unlocked" else "unlock", confirm=True)
    return None


def adjust(entity_id: str, state: State, direction: int) -> Action | None:
    """Numéro + SUITE (direction 1) ou RETOUR (direction -1)."""
    domain = entity_id.split(".", 1)[0]
    attributes = state.attributes
    up = direction > 0
    if domain == "light":
        return Action(domain, "turn_on", {"brightness_step_pct": 10 * direction})
    if domain == "cover":
        position = attributes.get("current_position")
        if isinstance(position, (int, float)):
            return Action(
                domain, "set_cover_position", {"position": _clamp(position + 10 * direction, 0, 100)}
            )
        return Action(domain, "open_cover" if up else "close_cover")
    if domain == "climate":
        target = _number(attributes.get("temperature"))
        if target is None:
            return None
        step = _number(attributes.get("target_temp_step")) or 0.5
        low = _number(attributes.get("min_temp"))
        high = _number(attributes.get("max_temp"))
        value = _clamp(target + step * direction, low, high)
        return Action(domain, "set_temperature", {"temperature": value})
    if domain == "fan":
        return Action(domain, "increase_speed" if up else "decrease_speed")
    if domain == "media_player":
        return Action(domain, "volume_up" if up else "volume_down")
    if domain == "input_number":
        return Action(domain, "increment" if up else "decrement")
    if domain == "number":
        value = _number(state.state)
        if value is None:
            return None
        step = _number(attributes.get("step")) or 1
        low = _number(attributes.get("min"))
        high = _number(attributes.get("max"))
        return Action(domain, "set_value", {"value": _clamp(value + step * direction, low, high)})
    if domain in ("input_select", "select"):
        return Action(domain, "select_next" if up else "select_previous")
    return None


def _clamp(value: float, low: float | None, high: float | None) -> float:
    if low is not None:
        value = max(value, low)
    if high is not None:
        value = min(value, high)
    return value

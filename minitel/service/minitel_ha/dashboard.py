"""Tableaux de bord : ce que le Minitel affiche, tiré de Home Assistant.

Deux sources, sans rien écrire dans Home Assistant :

- un tableau de bord enregistré (`lovelace/config`), dont on traduit les vues et les cartes ;
- à défaut, les pièces : une page par pièce, les entités rangées par type.

Le résultat ne dépend que des données reçues : la mise en page et les états viennent à l'affichage.
La visibilité des cartes est gardée sous forme de conditions, évaluées avec les états du moment.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping
from urllib.parse import urlparse

UNAVAILABLE = "unavailable"

# --- Conditions de visibilité ------------------------------------------------------------------


@dataclass(frozen=True)
class StateIs:
    """Vraie si l'état est l'un de `states` (ou aucun d'eux, avec `negate`)."""

    entity_id: str
    states: tuple[str, ...]
    negate: bool = False


@dataclass(frozen=True)
class NumericState:
    """Vraie si l'état est un nombre strictement entre les bornes.

    Une borne peut être un nombre, ou une entité dont on lit l'état.
    """

    entity_id: str
    above: float | str | None = None
    below: float | str | None = None


@dataclass(frozen=True)
class AllOf:
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class AnyOf:
    conditions: tuple[Condition, ...]


Condition = StateIs | NumericState | AllOf | AnyOf


def is_visible(condition: Condition | None, states: Mapping[str, str]) -> bool:
    """Évalue une condition comme le fait l'interface de Home Assistant."""
    if condition is None:
        return True
    if isinstance(condition, AllOf):
        return all(is_visible(c, states) for c in condition.conditions)
    if isinstance(condition, AnyOf):
        return any(is_visible(c, states) for c in condition.conditions)
    state = states.get(condition.entity_id, UNAVAILABLE)
    if isinstance(condition, StateIs):
        return (state in condition.states) != condition.negate
    value = _number(state)
    if value is None:
        return False
    above = _bound(condition.above, states)
    below = _bound(condition.below, states)
    if condition.above is not None and (above is None or value <= above):
        return False
    if condition.below is not None and (below is None or value >= below):
        return False
    return True


def _bound(bound: float | str | None, states: Mapping[str, str]) -> float | None:
    if isinstance(bound, str):
        return _number(states.get(bound, UNAVAILABLE))
    return bound


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _all(*conditions: Condition | None) -> Condition | None:
    present = tuple(c for c in conditions if c is not None)
    if not present:
        return None
    return present[0] if len(present) == 1 else AllOf(present)


# --- Ce qu'affiche une page ----------------------------------------------------------------------

Style = Literal["row", "big", "bar", "graph", "stat"]

PERIOD_KEYS = ("calendar", "fixed_period", "rolling_window")
"""Les périodes d'une carte Statistique, telles que les prend `recorder/statistic_during_period`."""


@dataclass(frozen=True)
class Item:
    """Une entité. Sans `name`, on prendra son nom dans Home Assistant."""

    entity_id: str
    name: str | None = None
    style: Style = "row"
    visible_if: Condition | None = None
    hours: float = 24  # style "graph" : la durée couverte par la courbe
    bounds: tuple[float, float] = (0, 100)  # style "bar"
    stat: tuple[str, str] | None = None  # style "stat" : (type, période en JSON)


@dataclass(frozen=True)
class Heading:
    """Un titre de section, avec des valeurs à sa droite (la température de la pièce…)."""

    text: str
    badges: tuple[Item, ...] = ()
    visible_if: Condition | None = None


@dataclass(frozen=True)
class Glance:
    """Des valeurs en colonnes, sans numéro."""

    items: tuple[Item, ...]
    visible_if: Condition | None = None


Element = Heading | Item | Glance


@dataclass(frozen=True)
class View:
    """Une rubrique du sommaire. Elle peut s'étaler sur plusieurs écrans."""

    title: str
    elements: tuple[Element, ...]


@dataclass(frozen=True)
class Dashboard:
    title: str
    views: tuple[View, ...]
    notes: tuple[str, ...] = ()
    """Ce qui n'a pas pu être traduit, pour le journal de l'app."""

    def entity_ids(self) -> set[str]:
        """Toutes les entités dont l'affichage dépend, conditions comprises."""
        found: set[str] = set()
        for view in self.views:
            for element in view.elements:
                _collect(element, found)
        return found


def _collect(node: Element | Condition, found: set[str]) -> None:
    if isinstance(node, (AllOf, AnyOf)):
        for condition in node.conditions:
            _collect(condition, found)
        return
    if isinstance(node, StateIs):
        found.add(node.entity_id)
        return
    if isinstance(node, NumericState):
        found.add(node.entity_id)
        found.update(b for b in (node.above, node.below) if isinstance(b, str))
        return
    if isinstance(node, Item):
        found.add(node.entity_id)
    for child in node.badges if isinstance(node, Heading) else node.items if isinstance(node, Glance) else ():
        _collect(child, found)
    if node.visible_if is not None:
        _collect(node.visible_if, found)


# --- Choix de la source --------------------------------------------------------------------------


class DashboardNotFound(ValueError):
    pass


def pick_dashboard(option: str, default_panel: str | None, url_paths: Iterable[str]) -> str | None:
    """Le tableau de bord à traduire, ou None pour générer les pages à partir des pièces.

    `option` est le réglage de l'app : un chemin (`maison-famille`) ou une adresse copiée du
    navigateur. Vide, on suit le tableau de bord par défaut de Home Assistant, s'il est enregistré :
    le tableau de bord intégré n'a pas de configuration à lire.
    """
    known = set(url_paths)
    wanted = _url_path(option)
    if wanted:
        if wanted not in known:
            raise DashboardNotFound(wanted)
        return wanted
    return default_panel if default_panel in known else None


def _url_path(value: str) -> str:
    value = value.strip()
    if "://" in value:
        value = urlparse(value).path
    return value.strip("/").split("/", 1)[0]


# --- Traduction d'un tableau de bord enregistré --------------------------------------------------

# Cartes qui montrent une entité, et le style qu'elles prennent.
_ENTITY_CARDS: dict[str, Style] = {
    "tile": "row",
    "button": "row",
    "light": "row",
    "thermostat": "row",
    "humidifier": "row",
    "media-control": "row",
    "alarm-panel": "row",
    "weather-forecast": "row",
    "plant-status": "row",
    "entity": "big",
}
_GRAPH_CARDS = {"history-graph", "statistics-graph", "custom:mini-graph-card"}
_STACKS = {"vertical-stack", "horizontal-stack", "grid"}


@dataclass
class _Report:
    view: str = ""
    skipped: Counter[tuple[str, str]] = field(default_factory=Counter)
    approximated: Counter[tuple[str, str]] = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    def lines(self) -> tuple[str, ...]:
        out = list(self.notes)
        for (view, kind), count in self.skipped.items():
            out.append(f"{view} : {_cards(count)} « {kind} » {_plural(count, 'ignorée')}")
        for (view, kind), count in self.approximated.items():
            out.append(f"{view} : {_cards(count)} « {kind} » {_plural(count, 'simplifiée')}")
        return tuple(out)


def _cards(count: int) -> str:
    return "1 carte" if count == 1 else f"{count} cartes"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else word + "s"


def from_lovelace(title: str, config: Mapping[str, Any]) -> Dashboard:
    """Traduit la configuration d'un tableau de bord, telle que la renvoie `lovelace/config`."""
    report = _Report()
    views = []
    for index, raw in enumerate(config.get("views", []), 1):
        name = raw.get("title") or raw.get("path") or f"Vue {index}"
        report.view = name
        if raw.get("subview"):
            report.notes.append(f"{name} : sous-vue, absente du sommaire")
            continue
        elements = _view_elements(raw, report)
        if elements:
            views.append(View(name, tuple(elements)))
        else:
            report.notes.append(f"{name} : rien à afficher")
    return Dashboard(title, tuple(views), report.lines())


def _view_elements(view: Mapping[str, Any], report: _Report) -> list[Element]:
    elements: list[Element] = []
    badges = tuple(_badges(view.get("badges"), report))
    if badges:
        elements.append(Glance(badges))
    for section in view.get("sections", []):
        condition = _visibility(section.get("visibility"), report)
        for card in section.get("cards", []):
            elements += _card(card, condition, report)
    for card in view.get("cards", []):
        elements += _card(card, None, report)
    return without_empty_headings(elements)


def without_empty_headings(elements: list[Element]) -> list[Element]:
    """Retire les titres dont tout le contenu a été ignoré (une liste de courses…)."""
    kept: list[Element] = []
    for index, element in enumerate(elements):
        following = elements[index + 1] if index + 1 < len(elements) else None
        if (
            isinstance(element, Heading)
            and not element.badges
            and (following is None or isinstance(following, Heading))
        ):
            continue
        kept.append(element)
    return kept


def _card(card: Mapping[str, Any], parent: Condition | None, report: _Report) -> list[Element]:
    kind = card.get("type", "?")
    condition = _all(parent, _visibility(card.get("visibility"), report))

    if kind == "heading":
        badges = tuple(_badges(card.get("badges"), report))
        return [Heading(card.get("heading", ""), badges, condition)]
    if kind == "conditional":
        inner = _all(condition, _visibility(card.get("conditions"), report))
        return _card(card["card"], inner, report) if isinstance(card.get("card"), Mapping) else []
    if kind in _STACKS:
        out: list[Element] = []
        if card.get("title"):
            out.append(Heading(card["title"], visible_if=condition))
        for sub in card.get("cards", []):
            out += _card(sub, condition, report)
        return out
    if kind == "entities":
        return _entities_card(card, condition, report)
    if kind == "glance":
        items = tuple(i for i in (_row(r, report, kind) for r in card.get("entities", [])) if i)
        title = [Heading(card["title"], visible_if=condition)] if card.get("title") else []
        return title + ([Glance(items, condition)] if items else [])
    if kind == "sensor" and card.get("entity"):
        style: Style = "graph" if card.get("graph") in ("line", "bar") else "big"
        hours = _hours(card.get("hours_to_show"), 24)
        return [Item(card["entity"], card.get("name"), style, condition, hours=hours)]
    if kind == "gauge" and card.get("entity"):
        bounds = (float(card.get("min", 0)), float(card.get("max", 100)))
        return [Item(card["entity"], card.get("name"), "bar", condition, bounds=bounds)]
    if kind == "statistic" and card.get("entity"):
        return [Item(card["entity"], _name(card), "stat", condition, stat=_stat(card))]
    if kind in _GRAPH_CARDS:
        return _graph_card(card, condition, report)
    if kind in _ENTITY_CARDS:
        if not card.get("entity"):
            report.skipped[report.view, kind] += 1
            return []
        return [Item(card["entity"], _name(card), _ENTITY_CARDS[kind], condition)]
    if kind.startswith("custom:") and kind != "custom:auto-entities":
        # Au mieux : une carte tierce qui nomme ses entités devient des lignes.
        items = []
        if isinstance(card.get("entity"), str):
            items.append(Item(card["entity"], _name(card), visible_if=condition))
        for row in card.get("entities", []) or []:
            item = _row(row, report, kind)
            if item:
                items.append(Item(item.entity_id, item.name, visible_if=condition))
        if items:
            report.approximated[report.view, kind] += 1
            return items
    report.skipped[report.view, kind] += 1
    return []


def _entities_card(card: Mapping[str, Any], condition: Condition | None, report: _Report) -> list[Element]:
    out: list[Element] = []
    if card.get("title"):
        out.append(Heading(card["title"], visible_if=condition))
    for row in card.get("entities", []):
        if isinstance(row, Mapping) and row.get("type") == "section":
            if row.get("label"):
                out.append(Heading(row["label"], visible_if=condition))
            continue
        item = _row(row, report, "entities")
        if item:
            out.append(Item(item.entity_id, item.name, visible_if=condition))
    return out


def _graph_card(card: Mapping[str, Any], condition: Condition | None, report: _Report) -> list[Element]:
    hours = _hours(card.get("hours_to_show"), 24 * _hours(card.get("days_to_show"), 1))
    items = [i for i in (_row(r, report, card["type"]) for r in card.get("entities", []) or []) if i]
    if card.get("entity"):
        items.insert(0, Item(card["entity"], _name(card)))
    # Une seule courbe par graphique : les autres entités restent en lignes.
    return [
        Item(item.entity_id, item.name, "graph" if index == 0 else "row", condition, hours=hours)
        for index, item in enumerate(items)
    ]


def _stat(card: Mapping[str, Any]) -> tuple[str, str]:
    """Le type et la période d'une carte Statistique ; par défaut, la variation sur le mois."""
    period = card.get("period")
    if not isinstance(period, Mapping) or not any(key in period for key in PERIOD_KEYS):
        period = {"calendar": {"period": "month"}}
    period = {key: value for key, value in period.items() if key in PERIOD_KEYS}
    return str(card.get("stat_type") or "change"), json.dumps(period, sort_keys=True)


def _hours(value: Any, default: float) -> float:
    """Une durée de carte. Les fractions passent : 0.02 h, c'est 72 secondes."""
    try:
        hours = float(value)
    except (TypeError, ValueError):
        return default
    return hours if hours > 0 else default


def _row(row: Any, report: _Report, kind: str) -> Item | None:
    """Une ligne de carte Entités, ou une entité de carte Coup d'œil ou de graphique."""
    if isinstance(row, str):
        return Item(row)
    if isinstance(row, Mapping) and isinstance(row.get("entity"), str):
        return Item(row["entity"], _name(row))
    if isinstance(row, Mapping) and row.get("type") != "divider":
        report.skipped[report.view, f"{kind} > {row.get('type', '?')}"] += 1
    return None


def _badges(badges: Any, report: _Report) -> Iterable[Item]:
    for badge in badges or []:
        item = _row(badge, report, "badge")
        if item:
            yield item


def _name(card: Mapping[str, Any]) -> str | None:
    name = card.get("name")
    return name if isinstance(name, str) and name else None


def _visibility(conditions: Any, report: _Report) -> Condition | None:
    if not conditions:
        return None
    return _all(*(_condition(c, report) for c in conditions))


def _condition(raw: Any, report: _Report) -> Condition | None:
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("condition")
    if kind is None and "entity" in raw:  # ancienne forme de la carte conditionnelle
        kind = "state_not" if "state_not" in raw else "state"
    if kind in ("state", "state_not") and raw.get("entity"):
        negate = "state_not" in raw
        wanted = raw.get("state_not" if negate else "state")
        states = tuple(str(s) for s in wanted) if isinstance(wanted, list) else (str(wanted),)
        return StateIs(raw["entity"], states, negate)
    if kind == "numeric_state" and raw.get("entity"):
        return NumericState(raw["entity"], _threshold(raw.get("above")), _threshold(raw.get("below")))
    if kind in ("and", "or"):
        inner = tuple(c for c in (_condition(c, report) for c in raw.get("conditions", [])) if c)
        if not inner:
            return None
        return AllOf(inner) if kind == "and" else AnyOf(inner)
    # Écran, utilisateur, lieu, horaire : sans objet ou pas encore gérées, la carte reste visible.
    note = f"{report.view} : condition « {kind} » ignorée"
    if note not in report.notes:
        report.notes.append(note)
    return None


def _threshold(value: Any) -> float | str | None:
    """Un seuil : un nombre, ou une entité (`input_number.seuil`) dont l'état fait le seuil."""
    if isinstance(value, str) and _number(value) is None:
        return value
    return _number(value)


# --- Génération à partir des pièces --------------------------------------------------------------

# Les rubriques d'une pièce, dans l'ordre. Une entité va dans la première qui la prend.
_GROUPS: tuple[tuple[str, frozenset[str], frozenset[str] | None], ...] = (
    ("Lumières", frozenset({"light"}), None),
    ("Chauffage", frozenset({"climate", "water_heater", "humidifier", "fan"}), None),
    ("Ouvertures", frozenset({"cover", "lock", "valve"}), None),
    ("Ouvertures", frozenset({"binary_sensor"}), frozenset({"door", "window", "garage_door", "opening"})),
    ("Appareils", frozenset({"switch", "media_player", "vacuum", "lawn_mower"}), None),
    ("Scènes", frozenset({"scene", "script"}), None),
)
# Les mesures d'une pièce, en tête de page, nommées par ce qu'elles mesurent : le nom de
# l'entité est souvent celui du capteur (« ATC_Bureau Température »).
_MEASURES = {
    "temperature": "Température",
    "humidity": "Humidité",
    "co2": "CO2",
    "pm25": "PM2,5",
    "illuminance": "Lumière",
}


def from_areas(
    title: str,
    areas: list[Mapping[str, Any]],
    floors: list[Mapping[str, Any]],
    devices: list[Mapping[str, Any]],
    entities: list[Mapping[str, Any]],
    states: Mapping[str, Mapping[str, Any]],
) -> Dashboard:
    """Une page par pièce, dans l'ordre des étages, comme le tableau de bord par pièces de HA.

    `areas`, `floors`, `devices` et `entities` viennent des registres ; `states` donne, par
    entité, l'objet d'état (pour le nom et le type de capteur).
    """
    device_area = {d["id"]: d.get("area_id") for d in devices}
    by_area: dict[str, list[Mapping[str, Any]]] = {}
    for entity in entities:
        if entity.get("disabled_by") or entity.get("hidden_by") or entity.get("entity_category"):
            continue
        if entity["entity_id"] not in states:
            continue
        area = entity.get("area_id") or device_area.get(entity.get("device_id"))
        if area:
            by_area.setdefault(area, []).append(entity)

    levels = {f["floor_id"]: f.get("level") for f in floors}

    def floor_order(area: Mapping[str, Any]) -> tuple[bool, float]:
        level = levels.get(area.get("floor_id"))
        return (level is None, level if level is not None else 0)

    views = []
    for area in sorted(areas, key=floor_order):  # sorted est stable : l'ordre des pièces reste
        elements = _area_elements(area["name"], by_area.get(area["area_id"], []), states)
        if elements:
            views.append(View(area["name"], tuple(elements)))
    notes = () if views else ("aucune pièce n'a d'entité à afficher",)
    return Dashboard(title, tuple(views), notes)


def _area_elements(
    area_name: str, entities: list[Mapping[str, Any]], states: Mapping[str, Mapping[str, Any]]
) -> list[Element]:
    measures: dict[str, Item] = {}
    groups: dict[str, list[Item]] = {}
    for entity in sorted(entities, key=lambda e: _friendly(e["entity_id"], states).casefold()):
        entity_id = entity["entity_id"]
        domain = entity_id.split(".", 1)[0]
        device_class = states[entity_id].get("attributes", {}).get("device_class")
        name = _strip_area(_friendly(entity_id, states), area_name)
        if domain == "sensor" and device_class in _MEASURES:
            measures.setdefault(device_class, Item(entity_id, _MEASURES[device_class]))
            continue
        for label, domains, classes in _GROUPS:
            if domain in domains and (classes is None or device_class in classes):
                groups.setdefault(label, []).append(Item(entity_id, name))
                break

    elements: list[Element] = []
    if measures:
        elements.append(Glance(tuple(measures[m] for m in _MEASURES if m in measures)))
    for label in dict.fromkeys(g[0] for g in _GROUPS):
        if label in groups:
            elements.append(Heading(label))
            elements += groups[label]
    return elements


def _friendly(entity_id: str, states: Mapping[str, Mapping[str, Any]]) -> str:
    return states[entity_id].get("attributes", {}).get("friendly_name") or entity_id


def _strip_area(name: str, area: str) -> str:
    """« Salon Plafonnier » devient « Plafonnier » dans la page du salon."""
    if name.casefold().startswith(area.casefold()):
        rest = name[len(area) :].lstrip(" -_:")
        if rest:
            return rest[0].upper() + rest[1:]
    return name

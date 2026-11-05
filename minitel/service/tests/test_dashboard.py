import json

import pytest

from minitel_ha.dashboard import (
    AllOf,
    AnyOf,
    DashboardNotFound,
    Glance,
    Heading,
    Item,
    NumericState,
    StateIs,
    from_areas,
    from_lovelace,
    is_visible,
    pick_dashboard,
)

# Un tableau de bord en sections, comme ceux que crée l'éditeur de Home Assistant.
SECTIONS = {
    "views": [
        {
            "title": "Accueil",
            "type": "sections",
            "badges": [
                {"type": "entity", "entity": "sensor.dehors", "name": "Dehors"},
                "sensor.dedans",
            ],
            "sections": [
                {
                    "type": "grid",
                    "visibility": [
                        {
                            "condition": "or",
                            "conditions": [
                                {"condition": "state", "entity": "binary_sensor.porte", "state": "on"},
                                {"condition": "state", "entity": "input_boolean.courrier", "state": "on"},
                            ],
                        }
                    ],
                    "cards": [
                        {"type": "heading", "heading": "À voir"},
                        {
                            "type": "tile",
                            "entity": "binary_sensor.porte",
                            "name": "Porte",
                            "visibility": [
                                {"condition": "state", "entity": "binary_sensor.porte", "state": "on"}
                            ],
                        },
                    ],
                },
                {
                    "type": "grid",
                    "cards": [
                        {"type": "heading", "heading": "Courses"},
                        {"type": "todo-list", "entity": "todo.courses"},
                    ],
                },
            ],
        },
        {
            "title": "Pièces",
            "type": "sections",
            "sections": [
                {
                    "type": "grid",
                    "cards": [
                        {
                            "type": "heading",
                            "heading": "Salon",
                            "badges": [{"type": "entity", "entity": "sensor.salon_temp"}],
                        },
                        {"type": "tile", "entity": "light.salon", "name": "Plafonnier"},
                        {"type": "tile", "entity": "cover.salon"},
                    ],
                }
            ],
        },
        {"title": "Détail", "subview": True, "cards": [{"type": "tile", "entity": "light.salon"}]},
        {"title": "Plan", "cards": [{"type": "map"}]},
    ]
}


def test_sections_view():
    dashboard = from_lovelace("Maison", SECTIONS)
    assert dashboard.title == "Maison"
    assert [v.title for v in dashboard.views] == ["Accueil", "Pièces"]
    rooms = dashboard.views[1].elements
    assert rooms == (
        Heading("Salon", (Item("sensor.salon_temp"),)),
        Item("light.salon", "Plafonnier"),
        Item("cover.salon"),
    )


def test_view_badges_come_first_as_a_glance():
    first = from_lovelace("M", SECTIONS).views[0].elements[0]
    assert first == Glance((Item("sensor.dehors", "Dehors"), Item("sensor.dedans")))


def test_section_visibility_applies_to_each_card():
    elements = from_lovelace("M", SECTIONS).views[0].elements
    section = AnyOf((StateIs("binary_sensor.porte", ("on",)), StateIs("input_boolean.courrier", ("on",))))
    assert elements[1] == Heading("À voir", visible_if=section)
    assert elements[2].visible_if == AllOf((section, StateIs("binary_sensor.porte", ("on",))))


def test_a_heading_left_empty_is_dropped():
    titles = [e.text for e in from_lovelace("M", SECTIONS).views[0].elements if isinstance(e, Heading)]
    assert titles == ["À voir"]


def test_notes_say_what_was_left_out():
    notes = from_lovelace("M", SECTIONS).notes
    assert "Détail : sous-vue, absente du sommaire" in notes
    assert "Plan : rien à afficher" in notes
    assert "Accueil : 1 carte « todo-list » ignorée" in notes
    assert "Plan : 1 carte « map » ignorée" in notes


def test_masonry_view_with_stacks_and_entities_card():
    config = {
        "views": [
            {
                "path": "maison",
                "cards": [
                    {
                        "type": "vertical-stack",
                        "cards": [
                            {
                                "type": "entities",
                                "title": "Lumières",
                                "entities": [
                                    "light.a",
                                    {"entity": "light.b", "name": "Bé"},
                                    {"type": "divider"},
                                    {"type": "section", "label": "Prises"},
                                    {"entity": "switch.c"},
                                    {"type": "weblink", "url": "http://x"},
                                ],
                            },
                            {
                                "type": "grid",
                                "title": "Boutons",
                                "cards": [{"type": "button", "entity": "scene.s"}],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    dashboard = from_lovelace("M", config)
    view = dashboard.views[0]
    assert view.title == "maison"  # sans titre, le chemin de la vue
    assert view.elements == (
        Heading("Lumières"),
        Item("light.a"),
        Item("light.b", "Bé"),
        Heading("Prises"),
        Item("switch.c"),
        Heading("Boutons"),
        Item("scene.s"),
    )
    assert dashboard.notes == ("maison : 1 carte « entities > weblink » ignorée",)


def test_card_styles():
    config = {
        "views": [
            {
                "title": "V",
                "cards": [
                    {"type": "sensor", "entity": "sensor.t", "graph": "line", "hours_to_show": 12},
                    {"type": "sensor", "entity": "sensor.h"},
                    {"type": "gauge", "entity": "sensor.batt", "min": 10, "max": 50},
                    {"type": "entity", "entity": "sensor.co2"},
                    {"type": "custom:mini-graph-card", "entities": ["sensor.x", {"entity": "sensor.y"}]},
                    {"type": "statistics-graph", "entities": ["sensor.e"], "days_to_show": 2},
                    {"type": "glance", "title": "Coup d'œil", "entities": ["sensor.g1", "sensor.g2"]},
                ],
            }
        ]
    }
    assert from_lovelace("M", config).views[0].elements == (
        Item("sensor.t", style="graph", hours=12),
        Item("sensor.h", style="big"),
        Item("sensor.batt", style="bar", bounds=(10, 50)),
        Item("sensor.co2", style="big"),
        Item("sensor.x", style="graph"),
        Item("sensor.y"),
        Item("sensor.e", style="graph", hours=48),
        Heading("Coup d'œil"),
        Glance((Item("sensor.g1"), Item("sensor.g2"))),
    )


def test_custom_cards():
    config = {
        "views": [
            {
                "title": "V",
                "cards": [
                    {"type": "custom:slider-button-card", "entity": "light.l", "name": "L"},
                    {"type": "custom:auto-entities", "filter": {}, "entities": ["light.never"]},
                    {"type": "custom:apexcharts-card", "series": []},
                ],
            }
        ]
    }
    dashboard = from_lovelace("M", config)
    assert dashboard.views[0].elements == (Item("light.l", "L"),)
    assert set(dashboard.notes) == {
        "V : 1 carte « custom:slider-button-card » simplifiée",
        "V : 1 carte « custom:auto-entities » ignorée",
        "V : 1 carte « custom:apexcharts-card » ignorée",
    }


def test_conditional_card_and_legacy_conditions():
    config = {
        "views": [
            {
                "title": "V",
                "cards": [
                    {
                        "type": "conditional",
                        "conditions": [{"entity": "sun.sun", "state_not": "above_horizon"}],
                        "card": {"type": "tile", "entity": "light.jardin"},
                    },
                    {
                        "type": "tile",
                        "entity": "light.x",
                        "visibility": [{"condition": "screen", "media_query": "(min-width: 0px)"}],
                    },
                ],
            }
        ]
    }
    dashboard = from_lovelace("M", config)
    assert dashboard.views[0].elements == (
        Item("light.jardin", visible_if=StateIs("sun.sun", ("above_horizon",), negate=True)),
        Item("light.x"),
    )
    assert dashboard.notes == ("V : condition « screen » ignorée",)


def test_numeric_condition_with_entity_threshold():
    config = {
        "views": [
            {
                "title": "V",
                "cards": [
                    {
                        "type": "tile",
                        "entity": "light.x",
                        "visibility": [
                            {
                                "condition": "numeric_state",
                                "entity": "sensor.lux",
                                "above": "300",
                                "below": "input_number.max",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    dashboard = from_lovelace("M", config)
    assert dashboard.views[0].elements[0].visible_if == NumericState("sensor.lux", 300.0, "input_number.max")
    assert dashboard.entity_ids() == {"light.x", "sensor.lux", "input_number.max"}


def test_entity_ids_include_badges_and_conditions():
    assert from_lovelace("M", SECTIONS).entity_ids() == {
        "sensor.dehors",
        "sensor.dedans",
        "binary_sensor.porte",
        "input_boolean.courrier",
        "sensor.salon_temp",
        "light.salon",
        "cover.salon",
    }


@pytest.mark.parametrize(
    ("condition", "states", "expected"),
    [
        (None, {}, True),
        (StateIs("a.a", ("on",)), {"a.a": "on"}, True),
        (StateIs("a.a", ("on", "home")), {"a.a": "home"}, True),
        (StateIs("a.a", ("on",)), {"a.a": "off"}, False),
        (StateIs("a.a", ("on",), negate=True), {"a.a": "off"}, True),
        (StateIs("a.a", ("unavailable",)), {}, True),  # entité absente : indisponible
        (NumericState("s.s", above=10), {"s.s": "10.5"}, True),
        (NumericState("s.s", above=10), {"s.s": "10"}, False),
        (NumericState("s.s", below=10), {"s.s": "9"}, True),
        (NumericState("s.s", above=0), {"s.s": "unknown"}, False),
        (NumericState("s.s", above="s.min"), {"s.s": "5", "s.min": "4"}, True),
        (NumericState("s.s", above="s.min"), {"s.s": "5"}, False),
        (AllOf((StateIs("a.a", ("on",)), StateIs("b.b", ("on",)))), {"a.a": "on", "b.b": "off"}, False),
        (AnyOf((StateIs("a.a", ("on",)), StateIs("b.b", ("on",)))), {"a.a": "on", "b.b": "off"}, True),
    ],
)
def test_is_visible(condition, states, expected):
    assert is_visible(condition, states) is expected


@pytest.mark.parametrize(
    ("option", "expected"),
    [
        ("", "dashboard-home"),
        ("maison-famille", "maison-famille"),
        ("/maison-famille/", "maison-famille"),
        ("http://homeassistant.local:8123/maison-famille/accueil", "maison-famille"),
    ],
)
def test_pick_dashboard(option, expected):
    assert pick_dashboard(option, "dashboard-home", ["dashboard-home", "maison-famille"]) == expected


def test_pick_dashboard_falls_back_to_areas_when_the_default_cannot_be_read():
    # Le tableau de bord intégré (« home », « lovelace ») n'a pas de configuration enregistrée.
    assert pick_dashboard("", "home", ["maison-famille"]) is None
    assert pick_dashboard("", None, []) is None


def test_pick_dashboard_rejects_an_unknown_one():
    with pytest.raises(DashboardNotFound, match="inconnu"):
        pick_dashboard("inconnu", None, ["maison-famille"])


def _state(name, device_class=None):
    attributes = {"friendly_name": name}
    if device_class:
        attributes["device_class"] = device_class
    return {"state": "on", "attributes": attributes}


def test_from_areas():
    floors = [{"floor_id": "etage", "level": 1}, {"floor_id": "rdc", "level": 0}]
    areas = [
        {"area_id": "chambre", "name": "Chambre", "floor_id": "etage"},
        {"area_id": "garage", "name": "Garage", "floor_id": None},
        {"area_id": "salon", "name": "Salon", "floor_id": "rdc"},
        {"area_id": "vide", "name": "Vide", "floor_id": "rdc"},
    ]
    devices = [{"id": "d1", "area_id": "salon"}]
    entities = [
        {"entity_id": "light.salon_plafonnier"},  # sans pièce
        {"entity_id": "light.lampe", "device_id": "d1"},  # la pièce de son appareil
        {"entity_id": "light.plafonnier", "device_id": "d1", "area_id": "salon"},
        {"entity_id": "sensor.temp", "device_id": "d1"},
        {"entity_id": "sensor.batterie", "device_id": "d1"},
        {"entity_id": "binary_sensor.fenetre", "device_id": "d1"},
        {"entity_id": "binary_sensor.mouvement", "device_id": "d1"},
        {"entity_id": "cover.volet", "device_id": "d1"},
        {"entity_id": "switch.cache", "device_id": "d1", "hidden_by": "user"},
        {"entity_id": "switch.diag", "device_id": "d1", "entity_category": "config"},
        {"entity_id": "switch.off", "device_id": "d1", "disabled_by": "user"},
        {"entity_id": "switch.fantome", "device_id": "d1"},  # sans état
        {"entity_id": "light.chambre", "area_id": "chambre"},
        {"entity_id": "light.garage", "area_id": "garage"},
    ]
    states = {
        "light.salon_plafonnier": _state("Salon Plafonnier"),
        "light.lampe": _state("Salon - lampe"),
        "light.plafonnier": _state("Salon Plafonnier"),
        "sensor.temp": _state("Capteur Température", "temperature"),
        "sensor.batterie": _state("Capteur Batterie", "battery"),
        "binary_sensor.fenetre": _state("Fenêtre", "window"),
        "binary_sensor.mouvement": _state("Mouvement", "motion"),
        "cover.volet": _state("Volet"),
        "switch.cache": _state("Caché"),
        "switch.diag": _state("Diag"),
        "switch.off": _state("Off"),
        "light.chambre": _state("Chambre"),
        "light.garage": _state("Garage"),
    }
    dashboard = from_areas("3615 MAISON", areas, floors, devices, entities, states)
    # Rez-de-chaussée, étage, puis les pièces sans étage ; les pièces vides n'ont pas de page.
    assert [v.title for v in dashboard.views] == ["Salon", "Chambre", "Garage"]
    assert dashboard.views[0].elements == (
        Glance((Item("sensor.temp", "Température"),)),
        Heading("Lumières"),
        Item("light.lampe", "Lampe"),
        Item("light.plafonnier", "Plafonnier"),
        Heading("Ouvertures"),
        Item("binary_sensor.fenetre", "Fenêtre"),
        Item("cover.volet", "Volet"),
    )
    # Le nom réduit au nom de la pièce reste entier.
    assert dashboard.views[1].elements == (Heading("Lumières"), Item("light.chambre", "Chambre"))
    assert dashboard.notes == ()


def test_from_areas_without_anything():
    dashboard = from_areas("3615 MAISON", [], [], [], [], {})
    assert dashboard.views == ()
    assert dashboard.notes == ("aucune pièce n'a d'entité à afficher",)


def test_statistic_card_keeps_its_type_and_period():
    rolling = {"rolling_window": {"duration": {"hours": 48}}}
    config = {
        "views": [
            {
                "title": "V",
                "cards": [
                    {"type": "statistic", "entity": "sensor.cafes", "name": "Cafes", "period": rolling},
                    {"type": "statistic", "entity": "sensor.gaz", "stat_type": "max"},
                ],
            }
        ]
    }
    cafes, gaz = from_lovelace("M", config).views[0].elements
    assert cafes == Item(
        "sensor.cafes", "Cafes", "stat", stat=("change", json.dumps(rolling, sort_keys=True))
    )
    # Sans période : le mois en cours, comme la carte de Home Assistant.
    assert gaz.stat == ("max", json.dumps({"calendar": {"period": "month"}}))

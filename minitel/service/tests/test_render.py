import pytest

from minitel_ha import render
from minitel_ha import videotex as vt
from minitel_ha.dashboard import Glance, Heading, Item, StateIs, View
from minitel_ha.hass import EntityState
from minitel_ha.render import Action, Line, adjust, describe, primary, screens

from .screen import Screen


def S(state, **attributes):
    return EntityState(state, attributes)


@pytest.mark.parametrize(
    ("entity_id", "state", "expected"),
    [
        ("light.a", S("on"), ("ALLUME", True)),
        ("light.a", S("off"), ("ETEINT", False)),
        ("switch.a", S("on"), ("ALLUME", True)),
        ("fan.a", S("off"), ("ARRET", False)),
        ("input_boolean.a", S("on"), ("OUI", True)),
        ("light.a", S("unavailable"), ("HS", False)),
        ("light.a", None, ("HS", False)),
        ("sensor.a", S("unknown"), ("--", False)),
        ("binary_sensor.a", S("on", device_class="door"), ("OUVERT", True)),
        ("binary_sensor.a", S("off", device_class="window"), ("FERME", False)),
        ("binary_sensor.a", S("on", device_class="motion"), ("DETECTE", True)),
        ("binary_sensor.a", S("off", device_class="smoke"), ("OK", False)),
        ("binary_sensor.a", S("on"), ("OUI", True)),
        ("cover.a", S("open", current_position=60), ("OUVERT 60%", True)),
        ("cover.a", S("open", current_position=100), ("OUVERT", True)),
        ("cover.a", S("closed", current_position=0), ("FERME", False)),
        ("cover.a", S("closing"), ("FERME...", False)),
        ("climate.a", S("heat", current_temperature=20.6, temperature=21), ("20,6° > 21°", True)),
        ("climate.a", S("off", current_temperature=19), ("ARRET 19°", False)),
        ("lock.a", S("locked"), ("VERROUILLE", False)),
        ("lock.a", S("unlocked"), ("OUVERT", True)),
        ("sensor.t", S("20.42", unit_of_measurement="°C"), ("20,4°C", False)),
        ("sensor.t", S("20", unit_of_measurement="°C"), ("20°C", False)),
        ("sensor.h", S("48.0", unit_of_measurement="%"), ("48,0%", False)),
        ("sensor.p", S("1234.56", unit_of_measurement="W"), ("1234,6 W", False)),
        ("sensor.s", S("Filtration"), ("Filtration", False)),
        ("sensor.d", S("2026-11-04T20:15:00", device_class="timestamp"), ("04/11 20:15", False)),
        ("weather.a", S("partlycloudy", temperature=7.6), ("Eclaircies 7,6°", False)),
        ("scene.a", S("2026-11-04T20:15:00+00:00"), ("LANCER", False)),
        ("script.a", S("on"), ("EN COURS", True)),
        ("person.a", S("not_home"), ("ABSENT", False)),
        ("person.a", S("Travail"), ("Travail", False)),
        ("media_player.a", S("playing"), ("LECTURE", True)),
    ],
)
def test_describe(entity_id, state, expected):
    assert describe(entity_id, state) == expected


@pytest.mark.parametrize(
    ("entity_id", "state", "expected"),
    [
        ("light.a", S("on"), Action("light", "toggle")),
        ("cover.a", S("open"), Action("cover", "toggle")),
        ("scene.a", S("x"), Action("scene", "turn_on")),
        ("button.a", S("x"), Action("button", "press")),
        ("input_select.a", S("x"), Action("input_select", "select_next")),
        ("climate.a", S("off"), Action("climate", "turn_on")),
        ("climate.a", S("heat"), Action("climate", "turn_off")),
        ("lock.a", S("locked"), Action("lock", "unlock", confirm=True)),
        ("lock.a", S("unlocked"), Action("lock", "lock", confirm=True)),
        ("input_number.a", S("3"), None),
        ("sensor.a", S("3"), None),
    ],
)
def test_primary(entity_id, state, expected):
    assert primary(entity_id, state) == expected


@pytest.mark.parametrize(
    ("entity_id", "state", "direction", "expected"),
    [
        ("light.a", S("on"), 1, Action("light", "turn_on", {"brightness_step_pct": 10})),
        ("light.a", S("on"), -1, Action("light", "turn_on", {"brightness_step_pct": -10})),
        (
            "cover.a",
            S("open", current_position=95),
            1,
            Action("cover", "set_cover_position", {"position": 100}),
        ),
        ("cover.a", S("open"), -1, Action("cover", "close_cover")),
        (
            "climate.a",
            S("heat", temperature=21, target_temp_step=1, max_temp=21.5),
            1,
            Action("climate", "set_temperature", {"temperature": 21.5}),
        ),
        (
            "climate.a",
            S("heat", temperature=21),
            -1,
            Action("climate", "set_temperature", {"temperature": 20.5}),
        ),
        ("climate.a", S("off"), 1, None),
        ("number.a", S("4", step=2, min=0, max=5), 1, Action("number", "set_value", {"value": 5})),
        ("input_number.a", S("4"), -1, Action("input_number", "decrement")),
        ("select.a", S("x"), -1, Action("select", "select_previous")),
        ("switch.a", S("on"), 1, None),
    ],
)
def test_adjust(entity_id, state, direction, expected):
    assert adjust(entity_id, state, direction) == expected


def test_screens_number_only_what_can_be_commanded():
    view = View("V", (Heading("Salon"), Item("light.a"), Item("sensor.t"), Item("switch.b")))
    [screen] = screens(view, {})
    assert screen.lines == (
        Line(Heading("Salon")),
        Line(Item("light.a"), 1),
        Line(Item("sensor.t")),
        Line(Item("switch.b"), 2),
    )
    assert screen.numbered == {1: Item("light.a"), 2: Item("switch.b")}


def test_screens_follow_visibility():
    door_open = StateIs("binary_sensor.porte", ("on",))
    view = View(
        "V", (Heading("A voir", visible_if=door_open), Item("binary_sensor.porte", visible_if=door_open))
    )
    assert screens(view, {"binary_sensor.porte": S("off")}) == [render.Screen()]
    [screen] = screens(view, {"binary_sensor.porte": S("on")})
    assert len(screen.lines) == 2


def test_long_views_continue_on_the_next_screen_with_their_heading():
    lights = tuple(Item(f"light.l{i}") for i in range(20))
    view = View("V", (Heading("Salon"), *lights))
    first, second = screens(view, {})
    assert len(first.lines) == 16
    assert second.lines[0] == Line(Heading("Salon"))
    assert second.lines[1] == Line(Item("light.l15"), 1)  # chaque écran numérote depuis 1
    assert len(second.lines) == 6


def test_a_heading_never_ends_a_screen():
    view = View("V", (*(Item(f"sensor.s{i}") for i in range(15)), Heading("Suite"), Item("light.a")))
    first, second = screens(view, {})
    assert len(first.lines) == 15
    assert second.lines == (Line(Heading("Suite")), Line(Item("light.a"), 1))


def test_glance_spreads_on_two_columns_and_big_takes_two_rows():
    view = View(
        "V",
        (
            Glance((Item("sensor.a"), Item("sensor.b"), Item("sensor.c"))),
            *(Item(f"sensor.big{i}", style="big") for i in range(8)),
        ),
    )
    first, second = screens(view, {})
    assert first.lines[:2] == (Line((Item("sensor.a"), Item("sensor.b"))), Line((Item("sensor.c"),)))
    assert sum(line.height for line in first.lines) == 16
    assert len(second.lines) == 1


def draw(screen, states):
    rows = render.render(screen, states)
    out = Screen()
    out.feed(b"".join(vt.pos(row) + content for row, content in rows.items()))
    return rows, out


def test_render_rows():
    states = {
        "light.a": S("on", friendly_name="Plafonnier du salon"),
        "sensor.t": S("21.5", unit_of_measurement="°C", friendly_name="Temp"),
        "sensor.batt": S("40", unit_of_measurement="%"),
    }
    view = View(
        "V",
        (
            Heading("Salon", (Item("sensor.t"),)),
            Item("light.a"),
            Item("sensor.t", "Dedans"),
            Glance((Item("sensor.t", "Ici"), Item("light.a", "La"))),
            Item("sensor.batt", "Pile", style="bar"),
            Item("light.absente", "Absente"),
        ),
    )
    [screen] = screens(view, states)
    rows, out = draw(screen, states)
    assert out.row_text(5) == " Salon                            21,5?C"
    assert out.is_inverse(5, 1) and not out.is_inverse(5, 35)
    assert out.row_text(6) == " 1 Plafonnier du salon            ALLUME"
    assert out.is_inverse(6, 40)
    assert out.row_text(7) == "   Dedans                         21,5?C"
    assert out.row_text(8) == "Ici          21,5?C La           ALLUME"
    # La jauge : 40 % de 10 cases, en mosaïque pleine (0x7F).
    assert out.row_text(9).startswith("   Pile")
    assert out.row_text(9).endswith(chr(0x7F) * 4 + " " * 6 + " 40%")
    assert out.row_text(10) == " 2 Absente                            HS"
    assert rows[9].count(bytes((vt.SO,))) == 1 and rows[9].count(bytes((vt.SI,))) == 1


def test_render_big_value_leaves_the_row_above_blank():
    states = {"sensor.t": S("21.5", unit_of_measurement="°C")}
    [screen] = screens(View("V", (Item("sensor.t", "Dehors", style="big"),)), states)
    rows, out = draw(screen, states)
    assert rows[5] == b""
    assert render.is_double(rows[6])
    assert out.row_text(6).startswith("   Dehors")


def test_long_names_never_overflow_on_the_value():
    states = {"cover.a": S("open", current_position=55, friendly_name="n" * 60)}
    [screen] = screens(View("V", (Item("cover.a"),)), states)
    _, out = draw(screen, states)
    assert out.row_text(5) == " 1 " + "n" * 26 + " OUVERT 55%"


def test_a_graph_is_a_label_then_four_mosaic_rows():
    item = Item("sensor.debit", "Debit", "graph", hours=1)
    screen = screens(View("Reseau", (item,)), {})[0]
    assert screen.lines[0].height == 5
    states = {"sensor.debit": S("1117.6", unit_of_measurement="KiB/s")}
    points = [1117.6, 1117.6, 3.0] + [None] * 77
    rows = render.render(screen, states, {item: points})
    assert sorted(rows) == [5, 6, 7, 8, 9]
    label = rows[5]
    assert isinstance(label, render.Zoned)
    assert [col for col, _ in label.zones] == [1, 25]
    assert all(isinstance(rows[row], render.Mosaic) for row in range(6, 10))
    screen_ = Screen()
    for row, content in rows.items():
        screen_.feed(vt.pos(row) + content)
    assert screen_.row_text(5) == "   Debit (0 a 1500)".ljust(28) + "1117,6 KiB/s"
    assert screen_.mosaic_code(9, 1) == 0x7F  # 1117,6 sur 1500 : le bas plein…
    assert screen_.mosaic_code(9, 2) == 0x30  # 3 : un seul gros point, en bas à gauche…
    assert screen_.mosaic_code(9, 3) == 0x20  # … et rien après
    assert screen_.mosaic_code(6, 1) == 0x20  # 9 niveaux sur 12 : la rangée du haut vide
    assert screen_.mosaic_code(5, 1) is None  # le libellé n'est pas en mosaïque


def test_a_graph_without_points_is_empty():
    item = Item("sensor.debit", "Debit", "graph")
    rows = render.render(screens(View("Reseau", (item,)), {})[0], {})
    assert rows[6] == render.Mosaic(b" " * 40)

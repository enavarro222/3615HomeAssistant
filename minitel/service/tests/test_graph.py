import pytest

from minitel_ha import graph
from minitel_ha.graph import POINTS, Series


def test_period_spreads_the_card_duration_over_the_points():
    assert POINTS == 68
    assert graph.period(24) == 24 * 3600 / POINTS
    assert graph.period(0.25) == pytest.approx(900 / 68)
    assert graph.period(0.02) == graph.MIN_PERIOD  # 1 s : pas plus d'un point toutes les 5 s


def test_series_keeps_the_last_known_value():
    series = Series(since=0)
    series.add(10, 1.0)
    series.add(20, 1.0)  # même valeur : rien de neuf
    series.add(30, None)
    assert series.samples == [(10, 1.0), (30, None)]
    assert series.at(5) is None
    assert series.at(10) == 1.0
    assert series.at(29) == 1.0
    assert series.at(31) is None


def test_series_forgets_but_keeps_the_value_in_force():
    series = Series(since=0)
    for moment in range(10):
        series.add(moment, float(moment))
    series.forget_before(5.5)
    assert series.at(5.5) == 5.0
    assert series.samples[0] == (5, 5.0)
    assert series.since == 5.5


def test_points_scroll_with_the_latest_against_the_axis():
    series = Series(since=0)
    for moment in range(200):
        series.add(moment + 0.5, float(moment))
    values = graph.points(series, 1.0, now=150.7)
    assert values[-1] == 150.0  # le dernier point : la valeur de maintenant
    assert values[-2] == 149.0  # chaque point : sa dernière valeur
    assert values[0] == 150.0 - POINTS + 1
    assert graph.points(series, 1.0, now=151.2)[:-1] == values[1:]  # un point plus tard : tout glisse


def test_points_start_with_the_series():
    series = Series(since=95)
    series.add(95, 3.0)
    values = graph.points(series, 1.0, now=100.5)
    assert values[-6:] == [3.0] * 6
    assert values[:-6] == [None] * (POINTS - 6)
    assert graph.points(None, 1.0, 100) == [None] * POINTS


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([3, 1117.6, 592], (0, 1500)),  # un débit : depuis zéro, jusqu'à un chiffre rond
        ([18.5, 22.1], (18, 23)),  # des températures : autour des valeurs
        ([-3.2, 4.1], (-4, 6)),
        ([20, 20], (18, 22)),  # plat : à mi-hauteur
        ([0, 0], (0, 1)),
        ([None, None], (0, 1)),
    ],
)
def test_scale_is_round(values, expected):
    assert graph.scale(values) == expected


def test_level_shows_any_value_above_the_bottom():
    assert graph.level(None, 0, 100) == 0
    assert graph.level(0, 0, 100) == 0
    assert graph.level(0.1, 0, 100) == 1
    assert graph.level(50, 0, 100) == 6
    assert graph.level(100, 0, 100) == 12
    assert graph.level(500, 0, 100) == 12


def test_mosaic_fills_from_the_bottom_and_ends_with_the_axis():
    values = [100.0, 50.0] + [None] * (POINTS - 2)
    rows = graph.mosaic(values, 0, 100)
    assert len(rows) == graph.ROWS
    assert all(len(row) == graph.COLUMNS + 1 == 35 for row in rows)
    # Colonne gauche pleine (12 niveaux), colonne droite à moitié (6 niveaux, les 2 rangées du bas).
    assert [row[0] for row in rows] == [0x20 | 0x01 | 0x04 | 0x10] * 2 + [0x7F] * 2
    assert rows[0][1] == 0x20  # rien d'autre
    assert all(row[-1] == graph.AXIS for row in rows)


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(0.02, "-6 min"), (0.25, "-15 min"), (1, "-60 min"), (24, "-24 h"), (24 * 7, "-7 j"), (0.01, "-6 min")],
)
def test_span(hours, expected):
    assert graph.span(hours) == expected


@pytest.mark.parametrize(("state", "expected"), [("12.5", 12.5), ("unavailable", None), ("nan", None)])
def test_value(state, expected):
    assert graph.value(state) == expected

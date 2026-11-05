"""Les courbes : des valeurs dans le temps, tracées en mosaïque. Sans entrée ni sortie.

Une courbe couvre la durée de sa carte (`hours_to_show`) en 80 points, deux par caractère, sur
4 rangées de mosaïque, soit 12 niveaux. Elle avance comme un oscilloscope : un curseur balaie
l'écran de gauche à droite et réécrit un point à la fois, ce qui ne coûte que quelques octets.
Redessiner toute la courbe à chaque point ne tiendrait pas à 1200 bauds.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

from . import videotex as vt

POINTS = vt.WIDTH * 2
ROWS = 4
LEVELS = ROWS * 3
GAP = 2
"""Les points vides devant le curseur : on voit où en est le balayage."""

MIN_PERIOD = 1.0
"""Au plus un point par seconde : le Minitel ne suivrait pas plus vite."""

LIVE_PERIOD = 10.0
"""En dessous, un point par période est trop court pour attendre le relevé de l'intégration :
on demande à Home Assistant de relever l'entité à chaque point."""


def period(hours: float) -> float:
    """La durée d'un point, en secondes."""
    return max(MIN_PERIOD, hours * 3600 / POINTS)


@dataclass
class Series:
    """Les valeurs d'une entité depuis `since` : (instant, valeur, ou None si elle n'en a pas)."""

    since: float
    samples: list[tuple[float, float | None]] = field(default_factory=list)

    def add(self, moment: float, value: float | None) -> None:
        if self.samples and moment < self.samples[-1][0]:
            moment = self.samples[-1][0]  # pas de retour en arrière
        if self.samples and self.samples[-1][1] == value:
            return
        self.samples.append((moment, value))

    def at(self, moment: float) -> float | None:
        """La valeur à cet instant : la dernière connue."""
        index = bisect_right(self.samples, moment, key=lambda sample: sample[0])
        return self.samples[index - 1][1] if index else None

    def forget_before(self, moment: float) -> None:
        """Oublie ce qui précède, sauf la valeur en vigueur à cet instant."""
        index = bisect_right(self.samples, moment, key=lambda sample: sample[0])
        if index > 1:
            del self.samples[: index - 1]
        self.since = max(self.since, moment)


def points(series: Series | None, step: float, now: float) -> list[float | None]:
    """Les 80 points à l'écran : le point n est à la place n mod 80, le plus récent au curseur.

    Chaque point prend la valeur en fin de sa période, celle de maintenant pour le dernier.
    """
    out: list[float | None] = [None] * POINTS
    if series is None:
        return out
    current = math.floor(now / step)
    for n in range(current - POINTS + 1 + GAP, current + 1):
        moment = min((n + 1) * step, now)
        if moment > series.since:
            out[n % POINTS] = series.at(moment)
    return out


def cursor(step: float, now: float) -> int:
    """La place du point le plus récent."""
    return math.floor(now / step) % POINTS


def scale(values: list[float | None]) -> tuple[float, float]:
    """Des bornes rondes autour des valeurs, qui ne changent pas au moindre écart."""
    present = [v for v in values if v is not None]
    if not present:
        return 0.0, 1.0
    low, high = min(present), max(present)
    step = _nice((high - low) / ROWS) if high > low else _nice(abs(high) / 10 or 1)
    bottom = math.floor(low / step) * step
    top = math.ceil(high / step) * step
    if top <= bottom:  # plat : la valeur à mi-hauteur
        bottom, top = bottom - step, top + step
    if low >= 0 and bottom <= top - bottom:  # tout positif et proche de zéro : on part de zéro
        bottom = 0.0
    return bottom, top


def _nice(value: float) -> float:
    """1, 2 ou 5 fois une puissance de dix, au moins égal à `value`."""
    exponent = math.floor(math.log10(value))
    for factor in (1, 2, 5, 10):
        nice = factor * 10.0**exponent
        if nice >= value * (1 - 1e-9):
            return nice
    return 10.0 ** (exponent + 1)


def level(value: float | None, low: float, high: float) -> int:
    """Le nombre de gros points allumés, de 0 à 12. Une valeur au-dessus du bas en a au moins un."""
    if value is None:
        return 0
    filled = round((value - low) / (high - low) * LEVELS)
    if value > low:
        filled = max(filled, 1)
    return max(0, min(LEVELS, filled))


# Les gros points d'un caractère mosaïque, de haut en bas : (gauche, droite).
_DOTS = ((0x01, 0x02), (0x04, 0x08), (0x10, 0x40))


def mosaic(values: list[float | None], low: float, high: float) -> list[bytes]:
    """Les 4 rangées de la courbe, en aire pleine : un octet G1 par colonne, sans SO ni SI."""
    levels = [level(v, low, high) for v in values]
    rows = []
    for row in range(ROWS):
        cells = bytearray()
        for col in range(vt.WIDTH):
            code = 0x20
            for k, (left, right) in enumerate(_DOTS):
                needed = (ROWS - 1 - row) * 3 + (3 - k)  # niveau de ce gros point, de 1 à 12
                if levels[2 * col] >= needed:
                    code |= left
                if levels[2 * col + 1] >= needed:
                    code |= right
            cells.append(code)
        rows.append(bytes(cells))
    return rows


def value(state: str) -> float | None:
    """L'état d'une entité comme valeur de courbe : un nombre, sinon un trou."""
    try:
        number = float(state)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

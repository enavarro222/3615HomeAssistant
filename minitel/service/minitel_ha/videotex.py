"""Vidéotex : ce qu'on envoie au Minitel et ce qu'il nous renvoie (spec STUM)."""

from __future__ import annotations

import enum
import unicodedata
from typing import Iterable, Iterator

WIDTH = 40
HEIGHT = 24

BS = 0x08
FF = 0x0C
SO = 0x0E
SI = 0x0F
CON = 0x11
REP = 0x12
SEP = 0x13
COFF = 0x14
CAN = 0x18
SS2 = 0x19
ESC = 0x1B
US = 0x1F

# Accents du jeu G2, dans l'ordre SS2 + accent + lettre.
_G2_ACCENTS = {
    "̀": 0x41,  # grave
    "́": 0x42,  # aigu
    "̂": 0x43,  # circonflexe
    "̈": 0x48,  # tréma
    "̧": 0x4B,  # cédille
}
_G2_SPECIAL = {"°": 0x30, "Œ": 0x6A, "œ": 0x7A}


class Key(enum.Enum):
    """Touches de fonction, reçues sous la forme SEP + code."""

    ENVOI = 0x41
    RETOUR = 0x42
    REPETITION = 0x43
    GUIDE = 0x44
    ANNULATION = 0x45
    SOMMAIRE = 0x46
    CORRECTION = 0x47
    SUITE = 0x48
    CONNEXION_FIN = 0x49


def text(value: str) -> bytes:
    """Encode du texte. Une lettre donne une case à l'écran, accent compris.

    Le Minitel n'a pas de majuscules accentuées : elles perdent leur accent.
    """
    out = bytearray()
    for char in unicodedata.normalize("NFC", value):
        if char == "\n" or char == "\r":
            out += b"?"
        elif 0x20 <= ord(char) < 0x7F:
            out.append(ord(char))
        elif char in _G2_SPECIAL:
            out += bytes((SS2, _G2_SPECIAL[char]))
        else:
            out += _accented(char)
    return bytes(out)


def _accented(char: str) -> bytes:
    base, *marks = unicodedata.normalize("NFD", char)
    if not 0x20 <= ord(base) < 0x7F:
        return b"?"
    if marks and marks[0] in _G2_ACCENTS and base.islower():
        return bytes((SS2, _G2_ACCENTS[marks[0]], ord(base)))
    return base.encode("ascii")


def pos(row: int, col: int = 1) -> bytes:
    """Place le curseur : rangées 0 à 24 (0 = ligne de service), colonnes à partir de 1."""
    return bytes((US, 0x40 + row, 0x40 + col))


def clear() -> bytes:
    """Efface l'écran et la ligne de service, curseur éteint."""
    return pos(0) + bytes((CAN, FF, COFF))


def cursor(visible: bool) -> bytes:
    return bytes((CON if visible else COFF,))


def double_height(value: str) -> bytes:
    """Le texte occupe la rangée courante et celle du dessus."""
    return bytes((ESC, 0x4D)) + text(value) + bytes((ESC, 0x4C))


def double_size(value: str) -> bytes:
    """Double hauteur et double largeur : chaque lettre prend deux colonnes, et la rangée du dessus."""
    return bytes((ESC, 0x4F)) + text(value) + bytes((ESC, 0x4C))


def inverse(value: str) -> bytes:
    return bytes((ESC, 0x5D)) + text(value) + bytes((ESC, 0x5C))


# Mosaïques G1 : un caractère = 2 × 3 gros points.
_FULL_BLOCK = 0x7F
_LEFT_HALF = 0x35  # la colonne de gauche : points 1, 3 et 5
_EMPTY_BLOCK = 0x20


def bar(fraction: float, width: int) -> bytes:
    """Une jauge en mosaïque, au demi-caractère près."""
    steps = round(max(0.0, min(1.0, fraction)) * width * 2)
    full, half = divmod(steps, 2)
    cells = [_FULL_BLOCK] * full + [_LEFT_HALF] * half
    cells += [_EMPTY_BLOCK] * (width - len(cells))
    return bytes((SO, *cells, SI))


def rule(row: int, char: str = "-") -> bytes:
    """Trait sur toute la rangée, avec la répétition pour économiser la ligne."""
    return pos(row) + text(char) + bytes((REP, 0x40 + WIDTH - 1))


def backspace() -> bytes:
    """Efface le caractère à gauche du curseur."""
    return bytes((BS, 0x20, BS))


def disconnect() -> bytes:
    """Demande au Minitel de libérer la ligne (PRO1 + déconnexion)."""
    return bytes((ESC, 0x39, 0x67))


class KeyDecoder:
    """Découpe le flux clavier. Un SEP et son code peuvent arriver séparément."""

    def __init__(self) -> None:
        self._after_sep = False

    def feed(self, data: Iterable[int]) -> Iterator[Key | str]:
        for byte in data:
            byte &= 0x7F
            if self._after_sep:
                self._after_sep = False
                try:
                    yield Key(byte)
                except ValueError:
                    pass
            elif byte == SEP:
                self._after_sep = True
            elif 0x20 <= byte < 0x7F:
                yield chr(byte)

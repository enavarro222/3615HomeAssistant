import pytest

from minitel_ha import videotex as vt
from minitel_ha.videotex import Key


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Salon", b"Salon"),
        ("é", b"\x19\x42e"),
        ("è", b"\x19\x41e"),
        ("à", b"\x19\x41a"),
        ("ù", b"\x19\x41u"),
        ("ê", b"\x19\x43e"),
        ("ï", b"\x19\x48i"),
        ("ç", b"\x19\x4bc"),
        ("œ", b"\x19\x7a"),
        ("Zoé", b"Zo\x19\x42e"),
    ],
)
def test_text_encodes_french(value, expected):
    assert vt.text(value) == expected


def test_uppercase_accents_are_dropped():
    assert vt.text("ÉTAGE À Ç") == b"ETAGE A C"


def test_text_never_emits_control_bytes():
    assert vt.text("a\nb\x1b€") == b"a?b??"


def test_text_handles_decomposed_input():
    assert vt.text("é") == b"\x19\x42e"


def test_pos_is_one_based_for_columns():
    assert vt.pos(1, 1) == b"\x1f\x41\x41"
    assert vt.pos(24, 40) == b"\x1f\x58\x68"
    assert vt.pos(0) == b"\x1f\x40\x41"


def test_rule_covers_the_row_with_a_repeat():
    assert vt.rule(4) == b"\x1f\x44\x41-\x12\x67"


def test_all_output_is_seven_bits():
    assert max(vt.text("éàç°œ") + vt.clear() + vt.rule(24) + vt.disconnect()) < 0x80


@pytest.mark.parametrize(
    ("code", "key"),
    [
        (0x41, Key.ENVOI),
        (0x42, Key.RETOUR),
        (0x43, Key.REPETITION),
        (0x44, Key.GUIDE),
        (0x45, Key.ANNULATION),
        (0x46, Key.SOMMAIRE),
        (0x47, Key.CORRECTION),
        (0x48, Key.SUITE),
        (0x49, Key.CONNEXION_FIN),
    ],
)
def test_decoder_reads_function_keys(code, key):
    assert list(vt.KeyDecoder().feed(bytes((vt.SEP, code)))) == [key]


def test_decoder_mixes_digits_and_keys():
    assert list(vt.KeyDecoder().feed(b"12\x13A")) == ["1", "2", Key.ENVOI]


def test_decoder_keeps_a_key_split_across_reads():
    decoder = vt.KeyDecoder()
    assert list(decoder.feed(b"\x13")) == []
    assert list(decoder.feed(b"A")) == [Key.ENVOI]


def test_decoder_masks_the_parity_bit():
    assert list(vt.KeyDecoder().feed(bytes((0xB1,)))) == ["1"]


def test_decoder_ignores_unknown_codes_and_controls():
    assert list(vt.KeyDecoder().feed(b"\x13\x59\x07")) == []


@pytest.mark.parametrize(
    ("fraction", "cells"),
    [
        (0, b"     "),
        (0.5, b"\x7f\x7f\x35  "),  # 5 demi-cases sur 10
        (1, b"\x7f" * 5),
        (2, b"\x7f" * 5),  # au-delà du maximum : pleine
        (-1, b"     "),
    ],
)
def test_bar_in_mosaic(fraction, cells):
    assert vt.bar(fraction, 5) == bytes((vt.SO,)) + cells + bytes((vt.SI,))


def test_double_size():
    assert vt.double_size("21°") == b"\x1bO21\x19\x30\x1bL"

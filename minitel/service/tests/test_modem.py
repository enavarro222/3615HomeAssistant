"""Modem V.23 logiciel : on le fait parler à un modulateur et un démodulateur de test."""

import cmath
import math
import random

from minitel_ha.modem import (
    ALAW_DECODE,
    FRAME,
    RATE,
    RX_BAUD,
    RX_MARK,
    RX_SPACE,
    TX_BAUD,
    TX_MARK,
    TX_SPACE,
    Demodulator,
    Modulator,
    alaw_encode,
    frame_bits,
)


def fsk(bits, baud, mark, space, amplitude=9000, phase=0.0):
    """Ce qu'émettrait le softmodem d'Asterisk : FSK à phase continue, en G.711a."""
    out = bytearray()
    per_bit = RATE / baud
    total = int(len(bits) * per_bit)
    for i in range(total):
        bit = bits[min(len(bits) - 1, int(i / per_bit))]
        phase += 2 * math.pi * (mark if bit else space) / RATE
        out.append(alaw_encode(amplitude * math.sin(phase)))
    return bytes(out)


def carrier_then(data, idle_bits=120, trailing_bits=24):
    bits = [1] * idle_bits
    for byte in data:
        bits += frame_bits(byte)
    bits += [1] * trailing_bits
    return fsk(bits, RX_BAUD, RX_MARK, RX_SPACE)


def demodulate(audio, chunk=FRAME):
    demod = Demodulator()
    out = bytearray()
    for i in range(0, len(audio), chunk):
        out += demod.feed(audio[i : i + chunk])
    return bytes(out), demod


def test_alaw_round_trip_keeps_the_sign_and_the_magnitude():
    for value in (0, 100, -100, 1000, -1000, 9000, -9000, 32000, -32000):
        decoded = ALAW_DECODE[alaw_encode(value)]
        assert (decoded >= 0) == (value >= 0)
        assert abs(decoded - value) <= max(16, abs(value) / 16)


def test_frame_bits_is_7e1_least_significant_bit_first():
    assert frame_bits(0x41) == [0, 1, 0, 0, 0, 0, 0, 1, 0, 1]  # deux bits à 1 : parité 0
    assert frame_bits(0x43) == [0, 1, 1, 0, 0, 0, 0, 1, 1, 1]  # trois bits à 1 : parité 1


def test_demodulator_reads_a_videotex_page():
    page = b"\x1f\x41\x41\x18\x0c\x14" + b"3615 MAISON" + b"\x1b\x5dALLUME\x1b\x5c" + bytes(range(0x20, 0x7F))
    data, demod = demodulate(carrier_then(page))
    assert data == page
    assert demod.parity_errors == 0
    assert demod.carrier


def test_demodulator_survives_odd_packet_sizes():
    page = b"Numero : 12"
    data, _ = demodulate(carrier_then(page), chunk=37)
    assert data == page


def test_silence_and_answer_tone_give_nothing():
    """Début d'appel : silence, bruit léger, tonalité de réponse à 2100 Hz, puis la porteuse."""
    rng = random.Random(3615)
    silence = bytes(alaw_encode(rng.randint(-60, 60)) for _ in range(RATE // 2))
    answer = fsk([0] * 300, RX_BAUD, RX_MARK, RX_SPACE)  # 250 ms de 2100 Hz
    data, demod = demodulate(silence + answer + silence[:800] + carrier_then(b"OK"))
    assert data == b"OK"


def test_carrier_loss_is_detected():
    audio = carrier_then(b"A") + bytes([alaw_encode(0)]) * (RATE // 2)
    _, demod = demodulate(audio)
    assert not demod.carrier


def tone_per_bit(audio, count):
    """Pour chaque bit à 75 bauds, la fréquence la plus présente : 390 ou 450 Hz."""
    samples = [ALAW_DECODE[a] for a in audio]
    per_bit = RATE / TX_BAUD
    bits = []
    for k in range(count):
        window = samples[int(k * per_bit) : int((k + 1) * per_bit)]

        def power(freq):
            return abs(sum(x * cmath.exp(-2j * math.pi * freq * i / RATE) for i, x in enumerate(window)))

        bits.append(1 if power(TX_MARK) > power(TX_SPACE) else 0)
    return bits


def test_modulator_sends_7e1_at_75_bauds_then_idles_on_mark():
    mod = Modulator()
    mod.send(b"\x13A")  # SEP + A : la touche ENVOI
    audio = b"".join(mod.frame() for _ in range(int(25 * RATE / TX_BAUD / FRAME) + 1))
    assert tone_per_bit(audio, 24) == frame_bits(0x13) + frame_bits(0x41) + [1] * 4
    assert mod.pending == 0


def test_modulator_idle_is_a_steady_mark():
    audio = Modulator().frame(int(4 * RATE / TX_BAUD))
    assert tone_per_bit(audio, 4) == [1, 1, 1, 1]

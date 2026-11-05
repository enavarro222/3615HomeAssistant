"""Analyse une capture d'appel (pcap) : les messages SIP, puis le niveau et les
fréquences dominantes de l'audio G.711a dans chaque sens, seconde par seconde.

    tcpdump -i <interface> -s 0 -w appel.pcap host <ip de l'adaptateur>
    python tools/rtp_analyze.py appel.pcap

Un Minitel qui répond apparaît à 390 Hz dans le sens adaptateur vers Asterisk.
Si on n'y voit que du 1300 Hz, c'est l'écho de notre porteuse : il ne l'entend pas.
"""

import cmath
import collections
import math
import struct
import sys

RATE = 8000
FREQUENCIES = (300, 390, 450, 600, 1100, 1300, 1700, 2100, 2225, 3000)
SIP_DETAILS = (b"m=", b"c=", b"a=rtpmap", b"a=sendrecv", b"a=sendonly", b"a=recvonly", b"a=inactive")


def udp_packets(data):
    """Parcourt un pcap Ethernet et donne (date, source, destination, ports, charge utile)."""
    magic = struct.unpack("<I", data[:4])[0]
    if magic not in (0xA1B2C3D4, 0xA1B23C4D):
        raise SystemExit(f"pas un pcap classique : {magic:#x}")
    offset = 24
    while offset < len(data):
        seconds, micros, length, _ = struct.unpack("<IIII", data[offset : offset + 16])
        offset += 16
        packet = data[offset : offset + length]
        offset += length
        if packet[12:14] != b"\x08\x00" or packet[23] != 17:
            continue
        header = (packet[14] & 15) * 4
        source = ".".join(map(str, packet[26:30]))
        destination = ".".join(map(str, packet[30:34]))
        ports = struct.unpack(">HH", packet[14 + header : 14 + header + 4])
        yield seconds + micros / 1e6, source, destination, ports, packet[14 + header + 8 :]


def alaw_decode(value):
    value ^= 0x55
    sample = (value & 15) << 4
    segment = (value & 0x70) >> 4
    sample = sample + 8 if segment == 0 else (sample + 0x108) << (segment - 1)
    return sample if value & 0x80 else -sample


def dbm0(amplitude):
    return 20 * math.log10(max(amplitude, 0.1) / 32768) + 3.14


def tone_level(samples, frequency):
    step = cmath.exp(-2j * math.pi * frequency / RATE)
    total, phase = 0, 1
    for sample in samples:
        total += sample * phase
        phase *= step
    return abs(total) / len(samples) * 2


def print_sip(when, source, destination, payload):
    lines = payload.split(b"\r\n")
    first = lines[0].decode("latin-1")
    details = [line.decode("latin-1") for line in lines if line.startswith(SIP_DETAILS)]
    if details or not (first.startswith(("REGISTER", "OPTIONS")) or "200 OK" in first):
        print(f"{when % 3600:8.2f} {source} > {destination} {first[:60]} {details}")


def print_stream(key, packets):
    sizes = collections.Counter(len(payload) for _, _, payload in packets)
    print(f"\n{key[0]}:{key[1]} > {key[2]}:{key[3]}, type {key[4]}")
    print(f"{len(packets)} paquets, {packets[-1][0] - packets[0][0]:.1f} s, tailles {sizes.most_common(2)}")
    numbers = [number for _, number, _ in packets]
    lost = sum(((b - a) & 0xFFFF) - 1 for a, b in zip(numbers, numbers[1:]))
    print(f"pertes : {lost}")
    if key[4] != 8:
        return
    samples = [alaw_decode(byte) for _, _, payload in packets for byte in payload]
    for second in range(len(samples) // RATE):
        window = samples[second * RATE : (second + 1) * RATE]
        rms = math.sqrt(sum(sample * sample for sample in window) / RATE)
        levels = {frequency: tone_level(window, frequency) for frequency in FREQUENCIES}
        loudest = sorted(levels.items(), key=lambda item: -item[1])[:3]
        tones = " ".join(f"{frequency} Hz : {dbm0(level):6.1f}" for frequency, level in loudest)
        print(f"t={second:3d} s  {dbm0(rms):6.1f} dBm0   {tones}")


def main():
    with open(sys.argv[1], "rb") as capture:
        data = capture.read()
    streams = collections.defaultdict(list)
    for when, source, destination, ports, payload in udp_packets(data):
        if 5060 in ports:
            print_sip(when, source, destination, payload)
        elif len(payload) > 12 and payload[0] >> 6 == 2:
            key = (source, ports[0], destination, ports[1], payload[1] & 127)
            streams[key].append((when, struct.unpack(">H", payload[2:4])[0], payload[12:]))
    for key, packets in streams.items():
        print_stream(key, packets)


main()

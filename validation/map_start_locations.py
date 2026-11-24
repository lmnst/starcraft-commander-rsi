"""Print the Start Location units of the frozen 2-player maps.

Reads staredit\\scenario.chk from each runtime map (encrypted, PKWare DCL
imploded sectors) and lists unit id 214 positions. Used once to fill
app/rsi/adjudication.MAP_STARTS. The explode routine ports Mark Adler's blast.c.
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v11_fixture_map import archive_entry, crypt, hash_name, sections

ROOT = Path(__file__).resolve().parents[1]
START_LOCATION = 214
MAXBITS = 13
LITLEN = bytes([
    11, 124, 8, 7, 28, 7, 188, 13, 76, 4, 10, 8, 12, 10, 12, 10, 8, 23, 8,
    9, 7, 6, 7, 8, 7, 6, 55, 8, 23, 24, 12, 11, 7, 9, 11, 12, 6, 7, 22, 5,
    7, 24, 6, 11, 9, 6, 7, 22, 7, 11, 38, 7, 9, 8, 25, 11, 8, 11, 9, 12,
    8, 12, 5, 38, 5, 38, 5, 11, 7, 5, 6, 21, 6, 10, 53, 8, 7, 24, 10, 27,
    44, 253, 253, 253, 252, 252, 252, 13, 12, 45, 12, 45, 12, 61, 12, 45,
    44, 173])
LENLEN = bytes([2, 35, 36, 53, 38, 23])
DISTLEN = bytes([2, 20, 53, 230, 247, 151, 248])
BASE = (3, 2, 4, 5, 6, 7, 8, 9, 10, 12, 16, 24, 40, 72, 136, 264)
EXTRA = (0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8)


def _construct(rep):
    lengths = []
    for byte in rep:
        lengths += [byte & 15] * ((byte >> 4) + 1)
    count = [0] * (MAXBITS + 1)
    for length in lengths:
        count[length] += 1
    offs = [0] * (MAXBITS + 2)
    for length in range(1, MAXBITS + 1):
        offs[length + 1] = offs[length] + count[length]
    symbols = [0] * len(lengths)
    for symbol, length in enumerate(lengths):
        if length:
            symbols[offs[length]] = symbol
            offs[length] += 1
    return count, symbols


LIT, LEN, DIST = _construct(LITLEN), _construct(LENLEN), _construct(DISTLEN)


def explode(data):
    position, buffer, available = 0, 0, 0

    def bits(need):
        nonlocal position, buffer, available
        while available < need:
            buffer |= data[position] << available
            position += 1
            available += 8
        value = buffer & ((1 << need) - 1)
        buffer >>= need
        available -= need
        return value

    def decode(table):
        count, symbols = table
        code = first = index = 0
        for length in range(1, MAXBITS + 1):
            code |= bits(1) ^ 1
            if code < first + count[length]:
                return symbols[index + code - first]
            index += count[length]
            first = (first + count[length]) << 1
            code <<= 1
        raise ValueError("invalid DCL code")

    literal_coded, dictionary = bits(8), bits(8)
    if literal_coded > 1 or not 4 <= dictionary <= 6:
        raise ValueError("invalid DCL header")
    out = bytearray()
    while True:
        if bits(1):
            symbol = decode(LEN)
            length = BASE[symbol] + bits(EXTRA[symbol])
            if length == 519:
                return bytes(out)
            shift = 2 if length == 2 else dictionary
            distance = (decode(DIST) << shift) + bits(shift) + 1
            for _ in range(length):
                out.append(out[-distance])
        else:
            out.append(decode(LIT) if literal_coded else bits(8))


def scenario_chk(path):
    (offset, _, size, flags), data, sector = archive_entry(path, "staredit\\scenario.chk")
    if flags & 0x30200 != 0x10200:  # encrypted, compressed, no key adjustment
        raise ValueError(f"unexpected MPQ flags {flags:#x}")
    key = hash_name("scenario.chk", 3)
    count = (size + sector - 1) // sector
    table = struct.unpack(f"<{count + 1}I", crypt(data[offset:offset + 4 * (count + 1)], (key - 1) & 0xFFFFFFFF, True))
    chk = bytearray()
    for index in range(count):
        raw = data[offset + table[index]:offset + table[index + 1]]
        whole = len(raw) // 4 * 4
        raw = crypt(raw[:whole], (key + index) & 0xFFFFFFFF, True) + raw[whole:]
        expected = min(sector, size - index * sector)
        if len(raw) == expected:
            chk += raw
        elif raw[0] == 0x08:
            chk += explode(raw[1:])
        else:
            raise ValueError(f"unsupported compression {raw[0]:#x}")
    return bytes(chk)


def start_locations(path):
    units = [data for tag, data in sections(scenario_chk(path)) if tag == b"UNIT"][-1]
    starts = []
    for row in range(len(units) // 36):
        _, x, y, kind = struct.unpack_from("<I3H", units, row * 36)
        if kind == START_LOCATION:
            starts.append([x, y])
    return sorted(starts)


if __name__ == "__main__":
    for name in ("(2)Destination.scx", "(2)Benzene.scx", "(2)Heartbreak Ridge.scx"):
        print(name, start_locations(ROOT / "runtime/maps" / name))

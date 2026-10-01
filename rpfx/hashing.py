"""Jenkins one-at-a-time hash, GTA name hash (NG key selection) and a bit-exact
port of the legacy .NET System.Random(seed) generator (needed to un-scramble
the embedded key blob, magic.dat)."""

M32 = 0xFFFFFFFF


def joaat(data: bytes) -> int:
    """Raw Jenkins OAAT over bytes (== CodeWalker JenkHash.GenHash(byte[]))."""
    h = 0
    for b in data:
        h = (h + b) & M32
        h = (h + (h << 10)) & M32
        h ^= h >> 6
    h = (h + (h << 3)) & M32
    h ^= h >> 11
    h = (h + (h << 15)) & M32
    return h


def jenk(text: str) -> int:
    """Hash of a lower-cased string, the convention used for GTA name hashes."""
    return joaat(text.lower().encode("utf-8"))


def gta5_name_hash(name: str, lut: bytes) -> int:
    """GTA5Hash.CalculateHash - uses the 256-byte LUT from the key blob."""
    r = 0
    for ch in name:
        t = (1025 * (lut[ord(ch) & 0xFF] + r)) & M32
        r = ((t >> 6) ^ t) & M32
    n9 = (9 * r) & M32
    return (32769 * ((n9 >> 11) ^ n9)) & M32


def ng_key_index(name: str, length: int, lut: bytes) -> int:
    return (gta5_name_hash(name, lut) + (length & M32) + (101 - 40)) % 0x65


class DotNetRandom:
    """Legacy (seeded) System.Random, as used by .NET Framework / .NET 5+ compat."""

    MBIG = 2147483647
    MSEED = 161803398

    def __init__(self, seed: int):
        seed = ((seed + 0x80000000) & M32) - 0x80000000  # to int32
        sa = [0] * 56
        subtraction = self.MBIG if seed == -2147483648 else abs(seed)
        mj = self.MSEED - subtraction
        sa[55] = mj
        mk = 1
        for i in range(1, 55):
            ii = (21 * i) % 55
            sa[ii] = mk
            mk = mj - mk
            if mk < 0:
                mk += self.MBIG
            mj = sa[ii]
        for _ in range(1, 5):
            for i in range(1, 56):
                sa[i] -= sa[1 + (i + 30) % 55]
                if sa[i] < 0:
                    sa[i] += self.MBIG
        self.sa = sa
        self.inext = 0
        self.inextp = 21

    def internal_sample(self) -> int:
        i = self.inext + 1
        if i >= 56:
            i = 1
        j = self.inextp + 1
        if j >= 56:
            j = 1
        v = self.sa[i] - self.sa[j]
        if v == self.MBIG:
            v -= 1
        if v < 0:
            v += self.MBIG
        self.sa[i] = v
        self.inext, self.inextp = i, j
        return v

    def next(self) -> int:
        return self.internal_sample()

    def next_bytes(self, n: int) -> bytes:
        # .NET Framework Random.NextBytes uses InternalSample() % 256.
        return bytes(self.internal_sample() % 256 for _ in range(n))

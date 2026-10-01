"""Key material: AES key found inside gta5.exe by hash search; NG keys/tables
unscrambled from the bundled magic.dat blob."""
import base64
import hashlib
import json
import os
import zlib
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .crypto import aes_decrypt, ng_decrypt
from .hashing import DotNetRandom, joaat, ng_key_index
from .pc_hashes import LUT_HASH, NG_KEY_HASHES, NG_TABLE_HASHES

# SHA1 of the 32-byte AES key that lives in gta5.exe (legacy build).
PC_AES_KEY_SHA1 = bytes.fromhex("A0796128A775720AC204D9819F68C172E3952C6D")

MAGIC_PATH = os.path.join(os.path.dirname(__file__), "magic.dat")
CACHE_PATH = os.path.join(os.path.expanduser("~"), ".cache", "rpfx", "aes_key.json")


def search_hash(path: str, target_sha1: bytes, length: int = 32,
                progress: Optional[Callable[[float], None]] = None) -> Optional[bytes]:
    """Slide over the file on 8-byte alignment, SHA1 every `length` window."""
    size = os.path.getsize(path)
    chunk = 1 << 24
    sha1 = hashlib.sha1
    with open(path, "rb") as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            buf = f.read(chunk + length)
            mv = memoryview(buf)
            last = min(chunk, len(buf) - length + 1)
            for i in range(0, last, 8):
                if sha1(mv[i:i + length]).digest() == target_sha1:
                    return bytes(mv[i:i + length])
            pos += chunk
            if progress:
                progress(min(1.0, pos / size))
    return None


def search_hashes(path: str, targets, length: int,
                  progress: Optional[Callable[[float], None]] = None):
    """Find multiple SHA-1-signed byte sequences in one executable scan."""
    wanted = {}
    for i, target in enumerate(targets):
        wanted.setdefault(bytes(target), []).append(i)
    found = [None] * len(targets)
    size = os.path.getsize(path)
    chunk = 1 << 24
    with open(path, "rb") as f:
        pos = 0
        while pos < size:
            f.seek(pos)
            buf = f.read(chunk + length)
            last = min(chunk, len(buf) - length + 1)
            for i in range(0, max(0, last), 8):
                digest = hashlib.sha1(buf[i:i + length]).digest()
                indexes = wanted.get(digest)
                if indexes is not None:
                    value = bytes(buf[i:i + length])
                    for index in indexes:
                        found[index] = value
            pos += chunk
            if progress:
                progress(min(1.0, pos / size))
    return found


def find_aes_key(exe_path: str, progress=None) -> bytes:
    key = search_hash(exe_path, PC_AES_KEY_SHA1, 32, progress)
    if key is None:
        raise RuntimeError(
            "AES key not found in %s. Wrong/updated exe? Pass --key <base64> "
            "(or use gta5_enhanced.exe's key) instead." % exe_path)
    return key


def cache_key(key: bytes):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump({"aes_key_b64": base64.b64encode(key).decode()}, f)


def load_cached_key() -> Optional[bytes]:
    try:
        with open(CACHE_PATH) as f:
            return base64.b64decode(json.load(f)["aes_key_b64"])
    except Exception:
        return None


def unscramble_magic(blob: bytes, aes_key: bytes) -> bytes:
    """Reverse the archive magic-data transformation."""
    rnd = DotNetRandom(joaat(aes_key))
    n = len(blob)
    rbs = [np.frombuffer(rnd.next_bytes(n), dtype=np.uint8) for _ in range(4)]
    db = (np.frombuffer(blob, dtype=np.uint8).astype(np.int32)
          - rbs[0] - rbs[1] - rbs[2] - rbs[3]) & 0xFF
    db = aes_decrypt(db.astype(np.uint8).tobytes(), aes_key)
    try:
        return zlib.decompressobj(-15).decompress(db)
    except zlib.error as e:
        raise RuntimeError("magic.dat did not inflate - wrong AES key? (%s)" % e)


@dataclass
class Keys:
    aes_key: bytes
    ng_keys: list = None          # 101 x 272 bytes
    ng_tables: np.ndarray = None  # (17,16,256) uint32
    lut: bytes = None             # 256
    awc_key: bytes = None         # 16

    @classmethod
    def from_aes_key(cls, aes_key: bytes, magic_path: str = MAGIC_PATH):
        k = cls(aes_key=aes_key)
        if os.path.exists(magic_path):
            with open(magic_path, "rb") as f:
                blob = f.read()
            b = unscramble_magic(blob, aes_key)
            k.ng_keys = [b[i * 272:(i + 1) * 272] for i in range(101)]
            p = 27472
            k.ng_tables = np.frombuffer(b[p:p + 278528], dtype="<u4").reshape(17, 16, 256).copy()
            p += 278528
            k.lut = bytes(b[p:p + 256]); p += 256
            k.awc_key = bytes(b[p:p + 16])
        return k

    @classmethod
    def from_exe(cls, exe_path: str, progress=None):
        """Extract the AES key and unwrap the NG material blob."""
        aes_key = find_aes_key(exe_path, progress)
        return cls.from_aes_key(aes_key)

    @property
    def has_ng(self) -> bool:
        return self.ng_keys is not None

    def decrypt_aes(self, data: bytes) -> bytes:
        return aes_decrypt(data, self.aes_key)

    def decrypt_ng(self, data: bytes, name: str, length: int) -> bytes:
        if not self.has_ng:
            raise RuntimeError("NG-encrypted data but no NG keys loaded (magic.dat missing).")
        idx = ng_key_index(name, length, self.lut)
        return ng_decrypt(data, self.ng_keys[idx], self.ng_tables)


def load_keys(exe: Optional[str] = None, key_b64: Optional[str] = None,
              progress=None, use_cache: bool = True) -> Keys:
    if key_b64:
        key = base64.b64decode(key_b64)
        return Keys.from_aes_key(key)
    if exe:
        return Keys.from_exe(exe, progress)
    key = load_cached_key() if use_cache else None
    if key is None:
        raise RuntimeError("Need --exe gta5.exe or --key <base64> (no cached key).")
    return Keys.from_aes_key(key)

"""RPF7 archive reader (GTA V). Pure python, random access over a file object."""
import struct
import zlib
from typing import Dict, List, Optional

from .keys import Keys

RPF7_MAGIC = 0x52504637
ENC_NONE, ENC_OPEN, ENC_AES, ENC_NG = 0, 0x4E45504F, 0x0FFFFFF9, 0x0FEFFFFF


class RpfEntry:
    __slots__ = ("name", "is_dir", "kind", "parent", "children", "offset", "size",
                 "usize", "encrypted", "sys_flags", "gfx_flags", "name_off",
                 "ent_index", "ent_count", "archive", "_child_map")

    def __init__(self):
        self.parent = None
        self.children: List["RpfEntry"] = []
        self._child_map: Dict[str, "RpfEntry"] = {}
        self.encrypted = False
        self.size = self.usize = self.offset = 0
        self.sys_flags = self.gfx_flags = 0

    @property
    def is_rpf(self):
        return self.kind == "bin" and self.name.lower().endswith(".rpf")

    def child(self, name: str) -> Optional["RpfEntry"]:
        return self._child_map.get(name.lower())

    @property
    def path(self) -> str:
        parts, e = [], self
        while e is not None and e.parent is not None:
            parts.append(e.name)
            e = e.parent
        return "/" + "/".join(reversed(parts))


def page_size_from_flags(flags: int) -> int:
    s0 = ((flags >> 27) & 1) << 0
    s1 = ((flags >> 26) & 1) << 1
    s2 = ((flags >> 25) & 1) << 2
    s3 = ((flags >> 24) & 1) << 3
    s4 = ((flags >> 17) & 0x7F) << 4
    s5 = ((flags >> 11) & 0x3F) << 5
    s6 = ((flags >> 7) & 0xF) << 6
    s7 = ((flags >> 5) & 0x3) << 7
    s8 = ((flags >> 4) & 1) << 8
    base = 0x200 << (flags & 0xF)
    return base * (s0 + s1 + s2 + s3 + s4 + s5 + s6 + s7 + s8)


class RpfArchive:
    """One RPF (top-level on disk, or nested inside a parent at `base`)."""

    def __init__(self, fobj, base: int, size: int, name: str, keys: Keys,
                 parent_entry: Optional[RpfEntry] = None):
        self.f, self.base, self.size, self.name = fobj, base, size, name
        self.keys = keys
        self.parent_entry = parent_entry
        self.encryption = ENC_NONE
        self.aes = False
        self.root: RpfEntry = None
        self.entries: List[RpfEntry] = []
        self._read_header()

    # -- low level -----------------------------------------------------
    def _read_at(self, off: int, n: int) -> bytes:
        self.f.seek(self.base + off)
        return self.f.read(n)

    def _read_header(self):
        magic, count, nlen, enc = struct.unpack("<4I", self._read_at(0, 16))
        if magic != RPF7_MAGIC:
            raise ValueError("%s: not an RPF7 archive (magic %08x)" % (self.name, magic))
        self.encryption = enc
        ent = self._read_at(16, count * 16)
        names = self._read_at(16 + count * 16, nlen)
        if enc in (ENC_NONE, ENC_OPEN):
            pass
        elif enc == ENC_AES:
            ent, names = self.keys.decrypt_aes(ent), self.keys.decrypt_aes(names)
            self.aes = True
        else:  # NG or unknown -> assume NG, like CodeWalker
            ent = self.keys.decrypt_ng(ent, self.name, self.size)
            names = self.keys.decrypt_ng(names, self.name, self.size)

        entries = []
        for i in range(count):
            y, x = struct.unpack_from("<II", ent, i * 16)
            e = RpfEntry()
            e.archive = self
            raw = ent[i * 16:(i + 1) * 16]
            if x == 0x7FFFFF00:
                e.kind, e.is_dir = "dir", True
                e.name_off, _, e.ent_index, e.ent_count = struct.unpack("<IIII", raw)
            elif (x & 0x80000000) == 0:
                e.kind, e.is_dir = "bin", False
                (buf,) = struct.unpack_from("<Q", raw, 0)
                e.name_off = buf & 0xFFFF
                e.size = (buf >> 16) & 0xFFFFFF
                e.offset = (buf >> 40) & 0xFFFFFF
                e.usize, etype = struct.unpack_from("<II", raw, 8)
                e.encrypted = etype == 1
            else:
                e.kind, e.is_dir = "res", False
                e.name_off = struct.unpack_from("<H", raw, 0)[0]
                e.size = raw[2] | (raw[3] << 8) | (raw[4] << 16)
                e.offset = (raw[5] | (raw[6] << 8) | (raw[7] << 16)) & 0x7FFFFF
                e.sys_flags, e.gfx_flags = struct.unpack_from("<II", raw, 8)
                if e.size == 0xFFFFFF:  # real size is hidden in the RSC header
                    h = self._read_at(e.offset * 512, 16)
                    e.size = h[7] | (h[14] << 8) | (h[5] << 16) | (h[2] << 24)
            end = names.find(b"\0", e.name_off)
            e.name = names[e.name_off:end if end >= 0 else None].decode("utf-8", "replace")[:256]
            entries.append(e)
        self.entries = entries
        self.root = entries[0]
        self.root.name = ""
        stack = [self.root]
        while stack:
            d = stack.pop()
            for i in range(d.ent_index, d.ent_index + d.ent_count):
                c = entries[i]
                c.parent = d
                d.children.append(c)
                d._child_map[c.name.lower()] = c
                if c.is_dir:
                    stack.append(c)
        if self.encryption == ENC_NG or self.encryption not in (ENC_NONE, ENC_OPEN, ENC_AES):
            self.ng = True
        else:
            self.ng = False

    # -- extraction ----------------------------------------------------
    def open_nested(self, e: RpfEntry) -> "RpfArchive":
        size = e.size if e.size else e.usize
        return RpfArchive(self.f, self.base + e.offset * 512, size, e.name, self.keys, e)

    def _decrypt(self, data: bytes, e: RpfEntry, ng_len: int) -> bytes:
        if self.aes:
            return self.keys.decrypt_aes(data)
        return self.keys.decrypt_ng(data, e.name, ng_len)

    @staticmethod
    def _inflate(data: bytes) -> Optional[bytes]:
        try:
            d = zlib.decompressobj(-15)
            return d.decompress(data) + d.flush()
        except zlib.error:
            return None

    def extract(self, e: RpfEntry) -> bytes:
        """Return decrypted+decompressed bytes. For resources the 16-byte RSC7
        header is *not* included (use resource.RscHeader.from_entry)."""
        if e.kind == "bin":
            length = e.size if e.size else e.usize
            if length <= 0:
                return b""
            data = self._read_at(e.offset * 512, length)
            if e.encrypted:
                data = self._decrypt(data, e, e.usize)
            if e.size > 0:
                out = self._inflate(data)
                if out is None:
                    raise ValueError("%s: failed to inflate" % e.name)
                return out
            return data
        if e.kind == "res":
            if e.size <= 0x10:
                return b""
            data = self._read_at(e.offset * 512 + 0x10, e.size - 0x10)
            if e.encrypted or e.name.lower().endswith(".ysc"):
                data = self._decrypt(data, e, e.size)
            out = self._inflate(data)
            return out if out is not None else data
        raise ValueError("cannot extract a directory")

    def resource_header(self, e: RpfEntry):
        """(version, sys_flags, gfx_flags) from the RSC7 header on disk."""
        h = self._read_at(e.offset * 512, 16)
        return struct.unpack_from("<III", h, 4)


def open_rpf(path: str, keys: Keys) -> RpfArchive:
    import os
    f = open(path, "rb")
    return RpfArchive(f, 0, os.path.getsize(path), os.path.basename(path), keys)

"""GTA V resource (RSC7) memory model: a 'system' and a 'graphics' segment addressed
through virtual pointers 0x5xxxxxxx / 0x6xxxxxxx."""
import struct
from typing import List

from .rpf import page_size_from_flags

SYS_BASE = 0x50000000
GFX_BASE = 0x60000000
RSC7_MAGIC = 0x37435352


class ResourceError(Exception):
    pass


class Rsc:
    def __init__(self, data: bytes, sys_flags: int, gfx_flags: int):
        ssz = page_size_from_flags(sys_flags)
        gsz = page_size_from_flags(gfx_flags)
        sysb = bytes(data[:ssz])
        gfxb = bytes(data[ssz:ssz + gsz])
        # tolerate truncated tails
        self.sys = sysb + b"\0" * max(0, ssz - len(sysb))
        self.gfx = gfxb + b"\0" * max(0, gsz - len(gfxb))

    def _seg(self, addr: int):
        top = addr >> 28
        if top == 5:
            return self.sys, addr & 0x0FFFFFFF
        if top == 6:
            return self.gfx, addr & 0x0FFFFFFF
        raise ResourceError("bad virtual address 0x%x" % addr)

    def bytes(self, addr: int, n: int) -> bytes:
        if n <= 0:
            return b""
        seg, off = self._seg(addr)
        if off + n > len(seg):
            raise ResourceError("read past segment: 0x%x +%d" % (addr, n))
        return seg[off:off + n]

    def unpack(self, fmt: str, addr: int):
        seg, off = self._seg(addr)
        try:
            return struct.unpack_from(fmt, seg, off)
        except struct.error:
            raise ResourceError("unpack %s past segment at 0x%x" % (fmt, addr))

    def u8(self, a): return self.unpack("<B", a)[0]
    def u16(self, a): return self.unpack("<H", a)[0]
    def u32(self, a): return self.unpack("<I", a)[0]
    def u64(self, a): return self.unpack("<Q", a)[0]
    def f32(self, a): return self.unpack("<f", a)[0]

    def ptr_array(self, addr: int, n: int) -> List[int]:
        if not addr or n <= 0:
            return []
        return list(self.unpack("<%dQ" % n, addr))

    def cstr(self, addr: int, limit: int = 512) -> str:
        if not addr:
            return ""
        seg, off = self._seg(addr)
        end = seg.find(b"\0", off, off + limit)
        return seg[off:end if end >= 0 else off + limit].decode("utf-8", "replace")

    def list_header(self, addr: int):
        """(pointer, count, capacity) of a ResourcePointerList64 / SimpleList64."""
        p, c, cap = self.unpack("<QHH", addr)
        return p, c, cap

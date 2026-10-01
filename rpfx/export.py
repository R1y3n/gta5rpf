"""Turn RPF entries into files on disk (xml / dds / png / glb / obj / raw)."""
import os
import struct
from typing import List, Optional

from .drawable import (DrawableData, read_drawable, read_drawable_dictionary, read_fragment)
from .mesh_export import summary_xml, write_glb, write_obj
from .resource import RSC7_MAGIC, Rsc, SYS_BASE
from .textures import Texture, dds_to_png, make_dds, read_texture_dictionary, texture_dictionary_xml
from .vfs import Node, Vfs

MODEL_EXT = {".ydr", ".ydd", ".yft"}
LEGACY_VERSIONS = {".ytd": 13, ".ydr": 165, ".ydd": 165, ".yft": 162}


class ExportError(Exception):
    pass


def ext_of(name: str) -> str:
    return os.path.splitext(name.lower())[1]


def load_resource(node: Node):
    e, a = node.entry, node.archive
    if e.kind != "res":
        raise ExportError("%s is not a resource file" % e.name)
    magic, version, sf, gf = struct.unpack("<IIII", a._read_at(e.offset * 512, 16))
    if magic != RSC7_MAGIC:
        raise ExportError("%s: unexpected resource magic 0x%08X (gen9/enhanced files are not supported yet)" % (e.name, magic))
    data = a.extract(e)
    return Rsc(data, e.sys_flags or sf, e.gfx_flags or gf), version


def default_formats(ext: str) -> List[str]:
    if ext == ".ytd": return ["xml", "dds"]
    if ext in MODEL_EXT: return ["glb"]
    return ["raw"]


def _find_txds(vfs: Vfs, parent_stack: List[Node], node: Node, explicit: List[Node]) -> List[Texture]:
    """Explicit --txd files first, then <name>.ytd / <name>_hi.ytd next to the model."""
    out: List[Texture] = []
    cands = list(explicit)
    base = os.path.splitext(node.name)[0]
    parent = parent_stack[-1] if parent_stack else None
    if parent is not None:
        for n in (base + ".ytd", base + "_hi.ytd"):
            c = vfs.find_child(parent, n)
            if c is not None and c.entry.kind == "res":
                cands.append(c)
    seen = set()
    for c in cands:
        if id(c.entry) in seen:
            continue
        seen.add(id(c.entry))
        try:
            rsc, _ = load_resource(c)
            out += read_texture_dictionary(rsc, SYS_BASE)
        except Exception:
            pass
    return out


def export_node(vfs: Vfs, parent_stack: List[Node], node: Node, fmts: List[str], outdir: str,
                lod: str = "high", txd_nodes: Optional[List[Node]] = None, y_up: bool = True,
                flip_winding: bool = True, textures: bool = True, log=print) -> List[str]:
    e = node.entry
    ext = ext_of(e.name)
    stem = e.name
    os.makedirs(outdir, exist_ok=True)
    written: List[str] = []
    if fmts == ["auto"]:
        fmts = default_formats(ext)
    cache = {}

    def parsed():
        """Parse the resource once per file (and warn about odd versions once)."""
        if "v" not in cache:
            rsc, version = load_resource(node)
            want = LEGACY_VERSIONS.get(ext)
            if want and version != want:
                log("  warning: %s has resource version %d (expected %d); gen9/odd layouts are not supported" % (e.name, version, want))
            if ext == ".ytd":
                cache["v"] = read_texture_dictionary(rsc, SYS_BASE)
            elif ext == ".ydr":
                d = read_drawable(rsc, SYS_BASE)
                d.name = d.name or os.path.splitext(e.name)[0]
                cache["v"] = [d]
            elif ext == ".ydd":
                cache["v"] = read_drawable_dictionary(rsc, SYS_BASE)
            else:
                cache["v"] = read_fragment(rsc, SYS_BASE)
        return cache["v"]

    for fmt in fmts:
        if fmt == "raw":
            data = node.archive.extract(e)
            p = os.path.join(outdir, stem)
            with open(p, "wb") as f:
                f.write(data)
            written.append(p)
            continue

        if ext == ".ytd":
            texs = parsed()
            ddsdir = os.path.join(outdir, os.path.splitext(stem)[0])
            if fmt == "xml":
                p = os.path.join(outdir, stem + ".xml")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(texture_dictionary_xml(texs))
                written.append(p)
            elif fmt in ("dds", "png"):
                os.makedirs(ddsdir, exist_ok=True)
                for t in texs:
                    try:
                        dds = make_dds(t)
                        if fmt == "png":
                            png = dds_to_png(dds)
                            if png is None:
                                log("  could not convert %s to PNG" % t.name)
                                continue
                            data, suffix = png, ".png"
                        else:
                            data, suffix = dds, ".dds"
                    except ValueError as ex:
                        log("  skip %s: %s" % (t.name, ex))
                        continue
                    p = os.path.join(ddsdir, (t.name or "null") + suffix)
                    with open(p, "wb") as f:
                        f.write(data)
                    written.append(p)
            else:
                raise ExportError("%s: format '%s' not available for .ytd (xml, dds, png, raw)" % (e.name, fmt))
            if fmt == "xml" and "dds" not in fmts:
                # CodeWalker's XML references <name>.dds files; write them beside it
                os.makedirs(ddsdir, exist_ok=True)
                for t in texs:
                    try:
                        with open(os.path.join(ddsdir, (t.name or "null") + ".dds"), "wb") as f:
                            f.write(make_dds(t))
                    except ValueError:
                        pass
            continue

        if ext in MODEL_EXT:
            draws = parsed()
            base = os.path.splitext(stem)[0]
            if fmt == "xml":
                p = os.path.join(outdir, stem + ".summary.xml")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(summary_xml(draws))
                written.append(p)
            elif fmt in ("glb", "obj"):
                extra = _find_txds(vfs, parent_stack, node, txd_nodes or []) if textures else []
                if ext != ".ydr":   # sibling drawables in a ydd/yft may share each other's embedded textures
                    extra = extra + [t for d in draws for t in d.embedded_textures]
                p = os.path.join(outdir, base + "." + fmt)
                fn = write_glb if fmt == "glb" else write_obj
                warns = fn(p, draws, lod=lod, extra_textures=extra, y_up=y_up,
                           flip_winding=flip_winding, with_textures=textures)
                for w in warns:
                    log("  warning: " + w)
                written.append(p)
            else:
                raise ExportError("%s: format '%s' not available for %s (glb, obj, xml, raw)" % (e.name, fmt, ext))
            continue

        raise ExportError("%s: no '%s' exporter for %s files yet (use 'raw')" % (e.name, fmt, ext or "this type"))
    return written

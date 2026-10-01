"""glTF 2.0 (.glb) and Wavefront OBJ writers for DrawableData (no third-party deps
beyond numpy; Pillow is only used to turn DDS textures into PNG)."""
import json
import os
import struct
from typing import Dict, List, Optional

import numpy as np

from .drawable import DrawableData, Geometry
from .hashing import jenk
from .textures import Texture, make_dds, dds_to_png


def _to_y_up(a: np.ndarray) -> np.ndarray:
    # GTA: Z-up, right-handed.  glTF: Y-up, right-handed.  (x,y,z) -> (x,z,-y)
    return np.stack([a[:, 0], a[:, 2], -a[:, 1]], axis=1)


def _tex_lookup(draw: DrawableData, extra: List[Texture]) -> Dict[str, Texture]:
    d: Dict[str, Texture] = {}
    for t in list(extra) + list(draw.embedded_textures):   # embedded wins
        if t.name:
            d[t.name.lower()] = t
    return d


def _png_for(tex: Texture, cache: dict) -> Optional[bytes]:
    key = id(tex)
    if key not in cache:
        try:
            cache[key] = dds_to_png(make_dds(tex))
        except Exception:
            cache[key] = None
    return cache[key]


def _tris(g: Geometry, flip: bool) -> np.ndarray:
    idx = g.indices.reshape(-1, 3)
    if flip:
        idx = idx[:, [0, 2, 1]]
    return idx.reshape(-1)


# ------------------------------------------------------------------ GLB
class _Glb:
    def __init__(self):
        self.bin = bytearray()
        self.views, self.accessors = [], []

    def view(self, data: bytes, target=None) -> int:
        while len(self.bin) % 4:
            self.bin.append(0)
        v = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            v["target"] = target
        self.bin += data
        self.views.append(v)
        return len(self.views) - 1

    def accessor(self, arr: np.ndarray, ctype: int, atype: str, target=None, normalized=False, minmax=False) -> int:
        vi = self.view(np.ascontiguousarray(arr).tobytes(), target)
        a = {"bufferView": vi, "componentType": ctype, "count": int(arr.shape[0]), "type": atype}
        if normalized:
            a["normalized"] = True
        if minmax:
            a["min"] = [float(x) for x in arr.min(axis=0)]
            a["max"] = [float(x) for x in arr.max(axis=0)]
        self.accessors.append(a)
        return len(self.accessors) - 1


def write_glb(path: str, drawables: List[DrawableData], lod: str = "high",
              extra_textures: Optional[List[Texture]] = None, y_up: bool = True,
              flip_winding: bool = True, with_textures: bool = True):
    extra_textures = extra_textures or []
    gl = _Glb()
    meshes, nodes, materials, textures, images, samplers = [], [], [], [], [], [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
    png_cache, img_index = {}, {}
    root_nodes = []
    warn = []

    for draw in drawables:
        tmap = _tex_lookup(draw, extra_textures)
        mat_of = {}          # shader index -> material index

        def material_for(si: int) -> int:
            if si in mat_of:
                return mat_of[si]
            m = {"name": "%s_shader%d" % (draw.name or "drawable", si), "doubleSided": True,
                 "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 1.0}}
            if with_textures and si < len(draw.shaders):
                tn = draw.shaders[si].diffuse_name()
                if tn:
                    tex = tmap.get(tn.lower())
                    if tex is None:
                        warn.append("texture '%s' not found (pass --txd or keep the ytd next to the model)" % tn)
                    else:
                        png = _png_for(tex, png_cache)
                        if png is None:
                            warn.append("could not decode texture '%s' (%s)" % (tn, hex(tex.fmt)))
                        else:
                            if id(tex) not in img_index:
                                vi = gl.view(png)
                                images.append({"bufferView": vi, "mimeType": "image/png", "name": tex.name})
                                textures.append({"sampler": 0, "source": len(images) - 1})
                                img_index[id(tex)] = len(textures) - 1
                            m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": img_index[id(tex)]}
                            m["name"] = tn
            materials.append(m)
            mat_of[si] = len(materials) - 1
            return mat_of[si]

        children = []
        for mi, model in enumerate(draw.models):
            if lod != "all" and model.lod != lod:
                continue
            prims = []
            for g in model.geometries:
                if len(g.indices) == 0:
                    continue
                pos = g.positions.astype(np.float32)
                attrs = {"POSITION": gl.accessor(_to_y_up(pos) if y_up else pos, 5126, "VEC3", 34962, minmax=True)}
                if g.normals is not None:
                    n = g.normals.astype(np.float32)
                    attrs["NORMAL"] = gl.accessor(_to_y_up(n) if y_up else n, 5126, "VEC3", 34962)
                for ch in sorted(g.uvs)[:2]:
                    attrs["TEXCOORD_%d" % ch] = gl.accessor(g.uvs[ch].astype(np.float32), 5126, "VEC2", 34962)
                if g.colors is not None:
                    attrs["COLOR_0"] = gl.accessor(g.colors, 5121, "VEC4", 34962, normalized=True)
                idx = _tris(g, flip_winding)
                if g.positions.shape[0] < 65536:
                    ia = gl.accessor(idx.astype(np.uint16).reshape(-1, 1), 5123, "SCALAR", 34963)
                else:
                    ia = gl.accessor(idx.astype(np.uint32).reshape(-1, 1), 5125, "SCALAR", 34963)
                prims.append({"attributes": attrs, "indices": ia, "mode": 4, "material": material_for(g.shader_index)})
            if prims:
                meshes.append({"name": "%s_%s_%d" % (draw.name or "drawable", model.lod, mi), "primitives": prims})
                nodes.append({"name": meshes[-1]["name"], "mesh": len(meshes) - 1})
                children.append(len(nodes) - 1)
        if children:
            nodes.append({"name": draw.name or "drawable", "children": children})
            root_nodes.append(len(nodes) - 1)

    if not meshes:
        raise ValueError("no mesh data to export (LOD '%s' empty?)" % lod)
    doc = {"asset": {"version": "2.0", "generator": "rpfx"},
           "scene": 0, "scenes": [{"nodes": root_nodes}], "nodes": nodes, "meshes": meshes,
           "materials": materials, "buffers": [{"byteLength": 0}],
           "bufferViews": gl.views, "accessors": gl.accessors}
    if textures:
        doc.update({"textures": textures, "images": images, "samplers": samplers})
    while len(gl.bin) % 4:
        gl.bin.append(0)
    doc["buffers"][0]["byteLength"] = len(gl.bin)
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(gl.bin)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(gl.bin), 0x004E4942) + bytes(gl.bin))
    return sorted(set(warn))


# ------------------------------------------------------------------ OBJ
def write_obj(path: str, drawables: List[DrawableData], lod: str = "high",
              extra_textures: Optional[List[Texture]] = None, y_up: bool = True,
              flip_winding: bool = True, with_textures: bool = True):
    extra_textures = extra_textures or []
    base = os.path.splitext(path)[0]
    mtl_path, texdir = base + ".mtl", base + "_textures"
    out, mtl = ["# exported by rpfx", "mtllib %s" % os.path.basename(mtl_path)], []
    vofs = 1
    mats_done, png_cache, warn = {}, {}, []
    for draw in drawables:
        tmap = _tex_lookup(draw, extra_textures)
        for mi, model in enumerate(draw.models):
            if lod != "all" and model.lod != lod:
                continue
            for gi, g in enumerate(model.geometries):
                if len(g.indices) == 0:
                    continue
                mname = "%s_s%d" % (draw.name or "drawable", g.shader_index)
                if mname not in mats_done:
                    mats_done[mname] = True
                    mtl += ["newmtl %s" % mname, "Kd 1 1 1"]
                    if with_textures and g.shader_index < len(draw.shaders):
                        tn = draw.shaders[g.shader_index].diffuse_name()
                        tex = tmap.get(tn.lower()) if tn else None
                        png = _png_for(tex, png_cache) if tex else None
                        if png:
                            os.makedirs(texdir, exist_ok=True)
                            fn = (tex.name or "tex") + ".png"
                            with open(os.path.join(texdir, fn), "wb") as f:
                                f.write(png)
                            mtl.append("map_Kd %s/%s" % (os.path.basename(texdir), fn))
                        elif tn:
                            warn.append("texture '%s' unavailable" % tn)
                    mtl.append("")
                out.append("o %s_%s_%d_%d" % (draw.name or "drawable", model.lod, mi, gi))
                out.append("usemtl %s" % mname)
                pos = _to_y_up(g.positions) if y_up else g.positions
                out += ["v %.6f %.6f %.6f" % tuple(p) for p in pos]
                has_uv = 0 in g.uvs
                if has_uv:
                    out += ["vt %.6f %.6f" % (u, 1.0 - v) for u, v in g.uvs[0]]
                has_n = g.normals is not None
                if has_n:
                    nn = _to_y_up(g.normals) if y_up else g.normals
                    out += ["vn %.5f %.5f %.5f" % tuple(n) for n in nn]
                tri = _tris(g, flip_winding).reshape(-1, 3) + vofs
                for a, b, c in tri:
                    def ref(i):
                        if has_uv and has_n: return "%d/%d/%d" % (i, i, i)
                        if has_uv: return "%d/%d" % (i, i)
                        if has_n: return "%d//%d" % (i, i)
                        return "%d" % i
                    out.append("f %s %s %s" % (ref(a), ref(b), ref(c)))
                vofs += g.positions.shape[0]
    if vofs == 1:
        raise ValueError("no mesh data to export (LOD '%s' empty?)" % lod)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    with open(mtl_path, "w") as f:
        f.write("\n".join(mtl) + "\n")
    return sorted(set(warn))


# ------------------------------------------------------------------ summary XML
def summary_xml(drawables: List[DrawableData]) -> str:
    from .textures import _esc
    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         "<!-- rpfx summary: NOT the CodeWalker round-trip format -->", "<DrawableSet>"]
    for d in drawables:
        L.append(' <Drawable name="%s">' % _esc(d.name))
        for i, s in enumerate(d.shaders):
            L.append('  <Shader index="%d" name="0x%08X" file="0x%08X">' % (i, s.name_hash, s.file_hash))
            for h, n in s.textures.items():
                L.append('   <Texture param="0x%08X" name="%s" />' % (h, _esc(n)))
            L.append("  </Shader>")
        for m in d.models:
            L.append('  <Model lod="%s" boneIndex="%d" skinned="%s">' % (m.lod, m.bone_index, str(m.has_skin).lower()))
            for g in m.geometries:
                L.append('   <Geometry shader="%d" vertices="%d" triangles="%d" uvChannels="%d" normals="%s" colors="%s" />' % (
                    g.shader_index, len(g.positions), len(g.indices) // 3, len(g.uvs),
                    str(g.normals is not None).lower(), str(g.colors is not None).lower()))
            L.append("  </Model>")
        L.append(" </Drawable>")
    L.append("</DrawableSet>")
    return "\n".join(L) + "\n"

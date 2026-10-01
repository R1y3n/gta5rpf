# rpfx - standalone GTA V RPF browser / exporter (Python)

`pip install numpy cryptography Pillow`, then:

    python -m rpfx --exe gta5.exe x64a.rpf        # extract key material and open an archive
    python -m rpfx x64a.rpf                         # later runs use the cached key
    python -m rpfx --key <base64> x64a.rpf          # or pass the AES key directly
    python -m rpfx --print-key --exe gta5.exe       # just extract/cache the key

Interactive shell (nested .rpf files behave like folders, tab-completion works):

    rpfx:/> ls -l
    rpfx:/> cd levels/gta5/props.rpf
    rpfx:/> find *.ydr
    rpfx:/> info prop_bench_01.ydr
    rpfx:/> export prop_bench_01.ydr --as glb,obj -o out
    rpfx:/> export *.ytd --as xml,dds -o out
    rpfx:/> export -r /models --as auto -o out        # whole folder; auto = ytd->xml+dds, ydr/ydd/yft->glb
    rpfx:/> export car.yft --as glb --lod all --txd /vehshare.ytd

Non-interactive: `rpfx x.rpf -c "export -r /models --as auto -o out"`.

## Formats and usage
| type | formats |
|---|---|
| any | `raw` (decrypted + inflated bytes) |
| ytd | `xml` + `dds`, `png` |
| ydr / ydd / yft | `glb`, `obj` (+mtl, png textures), `xml` (summary only) |

Textures: embedded dictionaries are used automatically; otherwise `<name>.ytd` / `<name>_hi.ytd` next to the
model, or `--txd path`. Axes: Z-up -> glTF Y-up (use `--zup` to keep GTA axes). Triangle winding is flipped
by default (D3D clockwise -> glTF CCW); use `--no-flip-winding` if a model looks inside-out.

For the roles of `.ydr`, `.ydd`, `.yft`, `.ytd`, `.ymt`, and other GTA V
resources, see [GTA5_ASSETS.md](GTA5_ASSETS.md).

## Enhanced extracted assets

Enhanced resources that have already been extracted from an archive can be
processed as an asset set instead of reopening the RPF. For example, a
character set may contain matching `.ydd`, `.yft`, `.ytd`, and `.ymt` files.
The `.ydd` is the mesh conversion source, the `.ytd` provides its material
textures, and the `.yft`/`.ymt` preserve entity and gameplay data.

The generated Enhanced character exports are in `../peds/converted`:

- `.glb` files contain embedded PNG textures and can be opened directly in Blender.
- `.obj` files require their `.mtl` and sibling `_textures` directory.

See [GTA5_ASSETS.md](GTA5_ASSETS.md) for the complete resource guide,
conversion behavior, and current limitations.

## License
This project is distributed under GPL-3.0-or-later.

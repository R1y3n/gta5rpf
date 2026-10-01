# GTA V asset formats

This document describes the GTA V resources processed by `rpfx`, including the
Enhanced assets extracted into `../peds`.

## Resource types

| Extension | Name | Purpose | Processed output |
|---|---|---|---|
| `.rpf` | Resource Package File | Archive containing game resources and nested archives. | Browse, extract, and export contained files. |
| `.ydr` | Drawable | One renderable model, such as a prop or vehicle component. | GLB or OBJ mesh export. |
| `.ydd` | Drawable Dictionary | Collection of drawables, commonly used for character components and model variants. | One combined GLB or OBJ containing all drawables. |
| `.yft` | Fragment | Physical/renderable entity resource. It contains fragment, skeleton, collision, and physics data and can reference a main drawable. | Renderable geometry when a drawable is present; physics and collision data are not represented in GLB/OBJ. |
| `.ytd` | Texture Dictionary | Named texture collection used by drawable materials. | Embedded GLB images, DDS files, PNG files, or XML metadata. |
| `.ymt` | Meta data | Gameplay and asset metadata, including character, component, archetype, and configuration data. | Kept as raw data; it is not geometry or texture data. |
| `.ybn` | Bound | Collision geometry and physical bounds. | Raw extraction only unless a collision-specific exporter is added. |
| `.ycd` | Clip Dictionary | Animation clips and animation metadata. | Raw extraction only. |
| `.ypt` | Particle Dictionary | Particle effects and effect definitions. | Raw extraction only. |
| `.ytyp` | Archetype Definitions | Placement, archetype, and entity definition metadata. | Raw extraction only. |
| `.ymap` | Map Data | World entity placement and map metadata. | Raw extraction only. |

## Character asset sets

A typical character set contains several files with the same basename:

```text
ig_valentina.ydd   # drawable dictionary: visible body/clothing components
ig_valentina.yft   # fragment: entity, skeleton, and physics information
ig_valentina.ytd   # texture dictionary used by the drawable materials
ig_valentina.ymt   # character/component metadata
```

The `csb_valentina.*` set follows the same pattern. The `.ydd` supplies the
mesh components used for conversion. The `.ytd` supplies the named diffuse
textures referenced by the materials. The `.yft` and `.ymt` are useful for
game integration, but they do not need to be merged into a static mesh export.

## GLB and OBJ conversion

The processed Enhanced YDD files are written to `../peds/converted`:

```text
ig_valentina.glb
csb_valentina.glb
ig_valentina.obj
ig_valentina.mtl
csb_valentina.obj
csb_valentina.mtl
```

The GLB files are self-contained. Their diffuse textures are converted to PNG
images and embedded in the GLB binary, with each material's
`pbrMetallicRoughness.baseColorTexture` linked to the appropriate image. They
can therefore be opened in Blender without copying a separate texture folder.

OBJ cannot store image data inside the `.obj` file. Its `.mtl` file references
external texture files, so keep the corresponding generated texture directory
beside the OBJ/MTL pair:

```text
ig_valentina_textures/
csb_valentina_textures/
```

The exporter converts GTA's Z-up coordinates to glTF's Y-up coordinates and
flips triangle winding for the target renderer. The GLB export intentionally
does not carry the source vertex-color tint when the source values are not
safe for standard glTF material multiplication.

## Texture lookup

For archive-backed exports, embedded texture dictionaries are preferred.
Otherwise the exporter looks for a matching `.ytd` beside the model, then a
`_hi.ytd` variant, or uses the file passed with `--txd`.

Diffuse maps are assigned to glTF base color. Normal and specular maps remain
available in the exported texture dictionary, but are not automatically mapped
to additional glTF material slots by the current simple material exporter.

## Limitations

- GLB and OBJ exports contain renderable mesh data, not GTA skeleton,
  skinning, collision, physics, animation, or gameplay metadata.
- `.ymt` data is not interpreted by the mesh exporter.
- Collision, animation, particle, map, and archetype resources currently need
  dedicated exporters.
- Enhanced resources use layouts that differ from older resource versions.
  Direct conversion should use the Enhanced-compatible parsing path rather than
  assuming legacy resource offsets.

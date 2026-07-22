# Headless commands

Every command is `pwsh headless/ue.ps1 <command> -ArgsJson '<json object>'`.
Output goes to `headless/_exports/`, always with a `_result.json`:

```json
{ "ok": true, "command": "material", "log": ["..."], "data": { ... } }
```

For big or nested payloads use `-ArgsFile steps.json` — inline JSON quoting
across shells is a losing game.

---

## `probe`

No args. **Run this first on any new engine version.** Reports the engine
version, the embedded Python version, which exporter classes exist
(`TextureExporterPNG`, `StaticMeshExporterFBX`, …), the full method list on
`MaterialEditingLibrary`, total asset count, and a tally of assets by class.

UE's Python surface shifts between releases; this is how you find out what your
build actually supports before trusting anything below.

## `manifest`

List assets to `_exports/manifest_<tag>.json`.

| arg | default | meaning |
| --- | --- | --- |
| `path_prefix` | `/Game` | only assets under this package path |
| `class` | *(all)* | exact class name — `Material`, `Texture2D`, `StaticMesh`, … |
| `contains` | *(all)* | case-insensitive substring of the package name |
| `limit` | `2000` | max rows; the result flags `truncated` |
| `out` | derived | filename tag |

```powershell
pwsh headless/ue.ps1 manifest -ArgsJson '{"class":"Texture2D","path_prefix":"/Game/Art"}'
```

## `texture`

Export to PNG via `TextureExporterPNG`, falling back to the generic asset
exporter (which may write `.TGA`/`.EXR` instead) if that fails.

| arg | meaning |
| --- | --- |
| `asset` | one `/Game/...` path |
| `assets` | a list of paths (preferred — one boot, many exports) |
| `outdir` | subfolder under `_exports` (default `textures`) |

## `mesh`

Export to **FBX** (binary), picking `SkeletalMeshExporterFBX` or
`StaticMeshExporterFBX` by asset class. Same `asset` / `assets` / `outdir` args
(default outdir `meshes`).

FBX rather than glTF on purpose: UE's `GLTFExporter` class is abstract and not
usable from Python. Import the FBX wherever you actually want to look at it.

## `material`

Dump to `_exports/<name>.material.json`. Arg: `asset`.

**For a base `Material`:** domain / shading model / blend mode / two-sided, every
scalar, vector, texture and static-switch parameter with its default, the
resolved `used_textures` list, shader statistics (instruction and sampler
counts), and a backwards walk of the node graph from each connected material
output.

**For a `MaterialInstanceConstant`:** the parent, plus every overridden parameter
value. Complete and reliable.

The graph walk has a real limit — see [LIMITATIONS.md](LIMITATIONS.md). It reaches
only nodes that feed a connected output, so always read `graph.node_count`
against `graph.diag.num_expressions` before assuming the dump is complete. When
nothing walks at all, the JSON says so explicitly in `graph.note` rather than
implying an empty material.

## `blueprint`

Dump to `_exports/<name>.blueprint.json`. Args: `asset`, optional `props` (a list
of CDO property names to read).

| field | source |
| --- | --- |
| `parent_class` | asset-registry `ParentClass` tag — the **immediate** parent, which may itself be a Blueprint |
| `native_parent_class` | asset-registry `NativeParentClass` tag |
| `blueprint_type` | `BPTYPE_Normal`, `BPTYPE_FunctionLibrary`, `BPTYPE_Interface`, … |
| `native_type` | `unreal.get_type_from_class` — nearest **native** ancestor |
| `generated_class` | the loaded `<Name>_C` |
| `components` | component tree from `SubobjectDataSubsystem`: class, variable name, and any static/skeletal mesh referenced |
| `cdo_props` | only if you pass `props` |

A `component_count` of `0` is usually correct rather than a failure — check
`blueprint_type`, since function/macro libraries and interfaces have no component
tree.

Event-graph logic is not extracted.

## `batch`

Run several commands in **one** engine session, amortising the boot cost. One bad
step never aborts the rest — each gets its own `ok` / `error` / `traceback`.

```json
{
  "steps": [
    { "command": "manifest", "args": { "class": "Material" } },
    { "command": "texture",  "args": { "assets": ["/Game/Art/T_A", "/Game/Art/T_B"] } },
    { "command": "material", "args": { "asset": "/Game/Art/M_Master" } }
  ]
}
```

```powershell
pwsh headless/ue.ps1 batch -ArgsFile steps.json
```

---

## Adding a command

`headless/bridge.py` is one file. Write `cmd_yourthing(args)`, return a
JSON-serializable dict, register it in the `COMMANDS` map at the bottom. It runs
inside UE with `unreal` imported, and anything it writes under `EXPORTS` comes
back to the host. Be defensive — wrap engine calls and report failures in the
returned data rather than raising, so a partial answer still gets through.

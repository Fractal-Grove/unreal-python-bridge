# Reading material node graphs

**Yes, this toolkit reads material node graphs — the complete graph, including
disconnected nodes and comment boxes, without touching the editor UI.**

That is worth stating plainly, because the older docs buried it under a page
titled "limitations" and readers reasonably concluded there was no answer. There
is. It is one command:

```powershell
pwsh headless/ue.ps1 graph -ArgsJson '{"asset":"/Game/Art/M_Foo"}'
```

which writes `_exports/graphs/M_Foo.T3D` (the raw text) and
`_exports/graphs/M_Foo.graph.json` (parsed nodes, edges and material outputs).

---

## Two routes, and when to use which

There are two ways to get at a material's structure, and they are good at
different things.

### 1. The API walk — `material` command

`MaterialEditingLibrary` can be asked what drives each material output, and the
bridge follows those connections backwards. It gives you parameters with their
defaults, resolved textures and shader statistics in the same pass, all as tidy
JSON.

**What it cannot do:** reach a node that does not feed an output. A backwards
walk starts at the outputs, so disconnected experiments, orphaned branches and
comment boxes are invisible to it. It is also blind to the graph on engines where
attribute routing defeats the walk (5.6 and earlier).

### 2. The T3D dump — `graph` command *(use this for topology)*

Asks the engine to serialize the whole asset to text. Nothing is walked, so
nothing is missed.

Measured on UE 5.7.4, engine content:

| material | `graph` (T3D) | `material` (API walk) | engine's own count |
| --- | --- | --- | --- |
| `DefaultMaterial` | **41 nodes** | 35 | 36 |
| `WorldGridMaterial` | **44 nodes** | 35 | 36 |

T3D finds *more* than the engine's `num_expressions` reports, because that count
excludes comment boxes and T3D includes them.

**Rule of thumb:** `material` to understand the parameters and what's bound;
`graph` to understand the wiring.

---

## What the parsed output looks like

Real output, trimmed, from `/Engine/EngineMaterials/DefaultMaterial`:

```
$ python tools/parse_t3d.py _exports/graphs/DefaultMaterial.T3D

dialect: export   nodes: 41
types: {'MaterialExpressionLinearInterpolate': 10, 'MaterialExpressionConstant': 6,
        'MaterialExpressionComment': 5, 'MaterialExpressionMultiply': 4, ...}

---- material outputs ----
  BaseColor              <- MaterialExpressionMultiply_18
  Roughness              <- MaterialExpressionClamp_3
  Normal                 <- MaterialExpressionMultiply_19

---- nodes ----
MaterialExpressionClamp_3        Clamp                    [Input<-MaterialExpressionAdd_9]
MaterialExpressionAdd_8          Add ConstB=0.000000      [A<-MaterialExpressionTextureSample_15; B<-MaterialExpressionLinearInterpolate_33]
MaterialExpressionTextureSample_16  TextureSample Texture=T_Default_Material_Grid_N SamplerType=SAMPLERTYPE_Normal  [Coordinates<-MaterialExpressionDivide_11]
MaterialExpressionComment_16     Comment Text="Roughness"  []
MaterialExpressionDivide_11      Divide ConstB=0.050000   [A<-MaterialExpressionDivide_10]
```

Each line is `node name`, `type + the properties that carry meaning`, then
`[pin<-source; pin<-source]`. That is enough to trace the blend logic by hand.

### Other output modes

```bash
python tools/parse_t3d.py graph.T3D --json   # machine-readable: nodes, props, edges, outputs
python tools/parse_t3d.py graph.T3D --dot    # Graphviz; pipe to `dot -Tsvg` to actually see it
```

`--json` gives you per node:

```json
"MaterialExpressionTextureSample_16": {
  "type": "MaterialExpressionTextureSample",
  "props": { "Texture": "...T_Default_Material_Grid_N...", "SamplerType": "SAMPLERTYPE_Normal" },
  "inputs": { "Coordinates": ["MaterialExpressionDivide_11"] }
}
```

---

## Two T3D dialects

`parse_t3d.py` auto-detects which one it is looking at. They are **not**
interchangeable, and a parser written for one reads zero nodes from the other —
which is exactly the trap this tool used to fall into.

**`export`** — what the `graph` command produces. Expressions are forward-declared
as empty objects, then filled in by a second pass of `Begin Object Name="..."`
property blocks; material outputs live on the `MaterialEditorOnlyData` block;
cross-references use `'Package:NodeName'` (colon).

**`clipboard`** — what you get by selecting nodes in the material graph editor and
pressing Ctrl+C. Every node is wrapped in a `/Script/UnrealEd.MaterialGraphNode`
holding an inner expression, and references use `'MaterialGraphNode_7.Expr'`
(dot).

Use `export` by default: it is scriptable, covers the whole asset, and needs no
editor interaction. Reach for `clipboard` when you want a *hand-picked subset* of
a graph you are already looking at, or a material function's inner graph as
selected on screen — paste it into a `.txt` and run the same parser.

---

## Doing it from the live bridge instead

If the editor is already open, skip the headless boot:

```bash
python live/ue_live.py -c "
import unreal
t = unreal.AssetExportTask()
t.set_editor_property('object', unreal.load_asset('/Game/Art/M_Foo'))
t.set_editor_property('filename', 'D:/tmp/M_Foo.T3D')
t.set_editor_property('automated', True)
t.set_editor_property('prompt', False)
result = unreal.Exporter.run_asset_export_task(t)
"
python tools/parse_t3d.py D:/tmp/M_Foo.T3D
```

### ⚠️ Do not set the exporter by hand

Let UE choose from the `.T3D` extension, and always pass
`automated=True` / `prompt=False`.

On 5.7 the only exporter registered for `Material` is **`GLTFMaterialExporter`**.
Selecting it explicitly — or calling
`AssetTools.export_assets_with_dialog` — opens a **modal glTF Export Options
window**. In the editor that parks the game thread until somebody clicks it (it
will look exactly like the bridge has hung). In a headless run there is nobody to
click it and the process waits forever.

Also note `AssetTools.export_assets` takes asset **path strings**, not loaded
objects; passing objects raises a parameter-conversion `TypeError`.

---

## Blueprint graphs

The same `AssetExportTask` route produces T3D for Blueprints, so you can capture
event-graph structure this way. `parse_t3d.py` is written for *material*
expressions and will not give you a useful node list from it — the object classes
are entirely different (`K2Node_*`). The raw T3D is still perfectly readable, and
extending the parser is a contained job: the block scanner and connection regex
are dialect-agnostic; only the node-classification step is material-specific.

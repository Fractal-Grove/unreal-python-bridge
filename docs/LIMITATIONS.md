# Known limits

Written down honestly, because most of these look like bugs until you know they
aren't. The numbers and API failures below were **measured on UE 5.7.4** against a
17,000-asset project; differences on earlier engines are called out where they
matter. Run `pwsh headless/ue.ps1 probe` to see what your build exposes.

---

## Material node graphs come back partial, never complete

The engine will not enumerate a material's nodes for you. `UMaterial.expressions`
is **protected** from the Python API — on 5.7, all three of these raise:

```
mat.expressions                          -> AttributeError
mat.get_expressions()                    -> AttributeError
mat.get_editor_property("expressions")   -> Exception: Property 'Expressions' ...
```

So the bridge walks the graph *backwards* instead, from every connected material
output property (base colour, roughness, normal, … and the material-attributes
pin) via `get_inputs_for_material_expression`.

**That recovers most of the graph, but never all of it.** Measured on 5.7:

| material | nodes walked | nodes the engine reports |
| --- | --- | --- |
| plain, small | 25 | 27 |
| plain, small | 21 | 23 |
| attribute-routed | 88 | 99 |
| attribute-routed | 274 | 297 |

The shortfall is definitional: a backwards walk from the outputs can only reach
nodes that **feed an output**. Disconnected experiments, orphaned branches and
comment boxes are invisible to it. `graph.diag.num_expressions` (what the engine
says) against `graph.node_count` (what was walked) tells you the size of the gap
for any given material — always check both before assuming you have the whole
picture.

> **Engine-version note.** On UE 5.6 and earlier, materials with
> `use_material_attributes = True` walked to **zero** nodes — everything routed
> through one attributes pin the walk couldn't follow. On 5.7 they walk fine
> (the table above). The zero case is still handled: when nothing walks but the
> engine reports nodes, the JSON says so explicitly in `graph.note` rather than
> pretending the material is empty.

What you get reliably, regardless: every parameter with its default, the resolved
`used_textures` list, shader statistics, and — on material instances — the parent
chain and every override.

### Getting the complete graph anyway

The editor's copy buffer *is* the graph — all of it, including the nodes no
backwards walk can reach. Open the material, select all in the graph editor,
Ctrl+C, paste into a text file. That text is **T3D**, the engine's own
object-serialization format, with every expression node and every connection in
it. Then:

```bash
python tools/parse_t3d.py graph.txt
```

which prints a compact node list plus edges: node type, the properties that carry
meaning (parameter names, constants, defaults, referenced material functions),
and each input pin resolved back to the node feeding it. Enough to trace the real
blend logic without writing a shader compiler.

Bulk alternative: `AssetTools.export_assets` writes the same full node graphs
(engine content included) alongside texture PNGs, with no manual copy-paste. The
tradeoff is that the copy route needs the editor open and a human at the
keyboard — a deliberate escape hatch for one material you actually need to
understand, not a sweep across the whole content tree.

---

## Blueprints: structure yes, logic no

`blueprint` returns the parent class, the component tree (classes, variable
names, mesh references) and any CDO defaults you ask for. **Event-graph and
function-graph logic is not extracted.** The same T3D copy-paste trick works on
Blueprint graphs if you need the nodes, though `parse_t3d.py` is written for
material graphs and would need extending.

Getting the parent class takes three separate dead ends into account — all of
these fail on 5.7:

```
bp.get_editor_property("parent_class")      -> Exception: Failed to find property
bp.get_editor_property("generated_class")   -> Exception: Failed to find property
generated_class.get_super_class()           -> AttributeError
```

The `Blueprint` asset locks those down, and `BlueprintGeneratedClass` has no
`get_super_class` in the Python API at all. The bridge therefore reads the
**asset registry tags** (`ParentClass`, `NativeParentClass`, `BlueprintType`),
which are authoritative and — unlike `unreal.get_type_from_class`, reported
separately as `native_type` — give the *immediate* parent even when a Blueprint
is parented to another Blueprint.

A `component_count` of `0` is usually not an error. Check `blueprint_type`:
`BPTYPE_FunctionLibrary`, `BPTYPE_Interface` and `BPTYPE_MacroLibrary` genuinely
have no component tree.

---

## Meshes come out as FBX, not glTF

UE's `GLTFExporter` class is abstract and not instantiable from Python.
`StaticMeshExporterFBX` / `SkeletalMeshExporterFBX` are the concrete, reliable
path, so that's what `mesh` uses. Import the FBX in Blender or your DCC of choice
to actually inspect the geometry.

---

## NullRHI can produce empty exports

The headless driver passes `-nullrhi` by default because it roughly halves boot
time. Some exporters need a real render backend and will silently write nothing.
If a texture or mesh comes back empty:

```powershell
pwsh headless/ue.ps1 texture -ArgsJson '{"asset":"/Game/Art/T_Foo"}' -NullRhi:$false
```

---

## The headless bridge needs the editor closed

A running editor holds the project lock, and UnrealEditor-Cmd will refuse to
start. Symptom: no `_result.json` at all. The driver prints the tail of the UE
log when that happens, which will say so.

---

## The live bridge executes arbitrary Python

That is the point of it, but it means: it binds to `127.0.0.1` only, and you
should keep it that way. `UE_BRIDGE_HOST` exists for unusual setups, not as an
invitation. There is no authentication — anything that can reach the port can run
code in your editor with your user's permissions.

Set `UE_BRIDGE_DISABLE=1` to keep the hook from starting it at all.

---

## Long-running code blocks the editor

Requests execute on the **game thread**, inside a slate post-tick callback. A
snippet that takes ten seconds freezes the editor for ten seconds. The client
gives up after `--timeout` (120 s default) but the editor keeps going until the
code finishes — the timeout ends your wait, not the work.

For anything heavy, chunk it across calls (the namespace persists, so you can
keep state between them) rather than sending one long-running block.

---

## Engine API drift

UE's Python surface changes between releases: `AssetData.asset_class` became
`asset_class_path`, `MaterialEditingLibrary` gains and loses methods, subsystem
accessors move. The bridge is written defensively — each command degrades and
reports rather than throwing — but on a new engine version, run `probe` first and
believe it over this document.

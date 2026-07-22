# Known limits

Written down honestly, because most of these look like bugs until you know they
aren't. Verified against UE 5.5–5.7; run `pwsh headless/ue.ps1 probe` to see what
your build exposes.

---

## Material node topology is not extractable from Python

The engine will not hand back a material's node graph:

- `UMaterial.expressions` is **protected** from the Python API — you cannot
  enumerate the nodes directly.
- The bridge works around this by walking *backwards* from each connected
  material-output property (base colour, roughness, normal, …) via
  `get_inputs_for_material_expression`. That works on ordinary materials.
- It returns **nothing** on any material with `use_material_attributes = True` —
  everything routes through a single attributes pin, so there are no per-property
  connections to walk.

What you *do* get reliably: every parameter with its default, the resolved
`used_textures` list, shader statistics, and — on material instances — the parent
chain and every override. That's usually enough to port or reason about a
material.

When it isn't, the JSON tells you so in `graph.note` rather than pretending the
material has zero nodes.

### Getting exact topology anyway

The editor's copy buffer *is* the graph. Open the material, select all in the
graph editor, Ctrl+C, paste into a text file — that text is **T3D**, the engine's
own object-serialization format, with every expression node and every connection
in it. Then:

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

`blueprint` returns the generated class, its parent, the component tree (classes,
variable names, mesh references) and any CDO defaults you ask for. **Event-graph
and function-graph logic is not extracted.** The same T3D copy-paste trick works
on Blueprint graphs if you need the nodes, though `parse_t3d.py` is written for
material graphs and would need extending.

Note also that the `Blueprint` asset itself locks down `parent_class` /
`generated_class` / the SCS in UE 5.x Python — the bridge loads the generated
`<Name>_C` class and reads its CDO instead. That's why you sometimes see a
`generated_class` field and no `parent_class`: the `_C` load failed.

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

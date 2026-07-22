"""
Headless asset bridge -- runs INSIDE UnrealEditor-Cmd via PythonScriptPlugin.

Do not run this with the system python. It is launched by ue.ps1, which starts a
headless UnrealEditor-Cmd against your .uproject and points -ExecutePythonScript
at this file.

Contract:
  * ue.ps1 writes a command file:  _exports/_cmd.json -> {"command": ..., "args": {...}}
  * this script reads it, dispatches, writes results into _exports/, and always
    writes _exports/_result.json = {"ok": bool, "command": str, "log": [...], "data": {...}}
  * host-side code (you, or an AI agent) reads _result.json + the produced files

Everything is defensive: UE's Python surface shifts between engine versions, so
each command degrades gracefully and reports what happened rather than throwing.
Run `probe` first on a new engine version to see what is actually exposed.

Set UE_BRIDGE_EXPORTS to relocate the output directory (default: _exports/ next
to this file).
"""

import json
import os
import sys
import traceback

# Auto-generated materials can be very deep expression graphs; the walker recurses.
sys.setrecursionlimit(20000)

try:
    import unreal
except Exception:  # pragma: no cover - only meaningful inside UE
    unreal = None

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORTS = os.environ.get("UE_BRIDGE_EXPORTS") or os.path.join(HERE, "_exports")
CMD_FILE = os.path.join(EXPORTS, "_cmd.json")
RESULT_FILE = os.path.join(EXPORTS, "_result.json")

_LOG = []


def log(msg):
    line = str(msg)
    _LOG.append(line)
    if unreal:
        unreal.log("[bridge] " + line)
    else:
        print("[bridge] " + line)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _asset_registry():
    return unreal.AssetRegistryHelpers.get_asset_registry()


def _class_name(asset_data):
    """Robustly pull the class name across UE 5.x AssetData shapes."""
    for getter in (
        lambda a: str(a.asset_class_path.asset_name),          # 5.1+
        lambda a: str(a.get_editor_property("asset_class")),   # older
    ):
        try:
            v = getter(asset_data)
            if v:
                return v
        except Exception:
            pass
    return "?"


def _clean_class_tag(value):
    """Asset-registry class tags arrive wrapped:
        /Script/CoreUObject.Class'/Script/Engine.Actor'
    Return just the inner object path, leaving anything unwrapped as-is."""
    if "'" in value:
        inner = value.split("'")
        if len(inner) >= 2 and inner[1]:
            return inner[1]
    return value


def _ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


# Create the output directory at import, not just in main(). The commands write
# into EXPORTS directly, and they must not depend on main() having run first --
# a batch step, or a caller importing this module to drive cmd_* itself, would
# otherwise fail on a missing directory.
_ensure_dir(EXPORTS)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_probe(args):
    """Report what this UE build actually exposes so later commands can rely on it."""
    data = {}
    data["unreal_version"] = unreal.SystemLibrary.get_engine_version()
    data["python"] = sys.version

    # exporters available for texture/mesh
    exporter_names = [
        "TextureExporterPNG", "TextureExporterTGA", "TextureExporterHDR",
        "StaticMeshExporterFBX", "SkeletalMeshExporterFBX", "GLTFExporter",
        "StaticMeshExporterGLTF", "AnimSequenceExporterFBX",
    ]
    data["exporters_present"] = {n: hasattr(unreal, n) for n in exporter_names}

    # material introspection surface
    mel = unreal.MaterialEditingLibrary
    data["material_editing_library_methods"] = sorted(
        [m for m in dir(mel) if not m.startswith("_")]
    )

    # asset registry sanity
    ar = _asset_registry()
    try:
        all_assets = ar.get_all_assets(include_only_on_disk_assets=True)
        data["asset_count"] = len(all_assets)
        tally = {}
        for a in all_assets:
            c = _class_name(a)
            tally[c] = tally.get(c, 0) + 1
        data["class_tally"] = dict(sorted(tally.items(), key=lambda kv: -kv[1])[:40])
    except Exception as e:
        data["asset_registry_error"] = repr(e)

    return data


def cmd_manifest(args):
    """
    List assets. args:
      path_prefix : only /Game/... under this (default '/Game')
      class       : filter to this class name (e.g. 'Material', 'Texture2D', 'StaticMesh')
      contains    : substring match on package name (case-insensitive)
      limit       : max rows (default 2000)
    Writes _exports/manifest_<tag>.json and returns a small summary.
    """
    path_prefix = args.get("path_prefix", "/Game")
    want_class = args.get("class")
    contains = (args.get("contains") or "").lower()
    limit = int(args.get("limit", 2000))

    ar = _asset_registry()
    rows = []
    for a in ar.get_all_assets(include_only_on_disk_assets=True):
        pkg = str(a.package_name)
        if not pkg.startswith(path_prefix):
            continue
        cls = _class_name(a)
        if want_class and cls != want_class:
            continue
        if contains and contains not in pkg.lower():
            continue
        rows.append({"path": pkg, "name": str(a.asset_name), "class": cls})
        if len(rows) >= limit:
            break

    tag = args.get("out") or ("%s_%s" % (want_class or "any", contains or "all")).replace("/", "_")
    out = os.path.join(EXPORTS, "manifest_%s.json" % tag)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return {"count": len(rows), "file": out, "truncated": len(rows) >= limit,
            "sample": [r["path"] for r in rows[:60]]}


def cmd_texture(args):
    """
    Export texture(s) to PNG. args:
      asset : single /Game path  OR
      assets: list of /Game paths
      outdir: subfolder under _exports (default 'textures')
    """
    paths = args.get("assets") or ([args["asset"]] if args.get("asset") else [])
    outdir = _ensure_dir(os.path.join(EXPORTS, args.get("outdir", "textures")))
    results = []
    for p in paths:
        obj = unreal.load_asset(p)
        if obj is None:
            results.append({"asset": p, "ok": False, "why": "load failed"})
            continue
        base = p.split("/")[-1]
        out = os.path.join(outdir, base + ".png")
        ok, why = _export_via_task(obj, out, "TextureExporterPNG")
        if not ok:
            # fall back to the generic exporter (may yield .TGA/.EXR next to out)
            ok2, why2 = _export_generic(obj, outdir)
            results.append({"asset": p, "ok": ok2, "out": outdir, "why": why + " | " + why2})
        else:
            results.append({"asset": p, "ok": True, "out": out})
    return {"results": results}


def cmd_mesh(args):
    """
    Export mesh(es) to FBX -- the concrete, reliable exporter. UE's GLTFExporter
    class is abstract and not usable from Python. Import the FBX in Blender (or
    anything else) to actually look at the geometry.
    args: asset/assets, outdir (default 'meshes').
    """
    paths = args.get("assets") or ([args["asset"]] if args.get("asset") else [])
    outdir = _ensure_dir(os.path.join(EXPORTS, args.get("outdir", "meshes")))
    results = []
    for p in paths:
        obj = unreal.load_asset(p)
        if obj is None:
            results.append({"asset": p, "ok": False, "why": "load failed"})
            continue
        if isinstance(obj, unreal.SkeletalMesh):
            exporter = "SkeletalMeshExporterFBX"
        else:
            exporter = "StaticMeshExporterFBX"
        base = p.split("/")[-1]
        out = os.path.join(outdir, base + ".fbx")
        ok, why = _export_via_task(obj, out, exporter)
        results.append({"asset": p, "ok": ok, "out": out if ok else None, "why": why})
    return {"results": results}


def cmd_material(args):
    """
    Dump a material (or material instance) to JSON. args: asset.
    For a Material: domain/shading/blend + all parameters w/ defaults + used
    textures + shader statistics + a walk of the node graph from each connected
    material-output property.
    For a MaterialInstanceConstant: parent + overridden parameter values.
    """
    path = args["asset"]
    mat = unreal.load_asset(path)
    if mat is None:
        return {"ok": False, "why": "load failed", "asset": path}

    mel = unreal.MaterialEditingLibrary
    info = {"asset": path, "class": type(mat).__name__}

    is_instance = isinstance(mat, unreal.MaterialInstanceConstant)

    for prop in ("material_domain", "shading_model", "blend_mode", "two_sided"):
        try:
            info[prop] = str(mat.get_editor_property(prop))
        except Exception:
            pass

    if is_instance:
        info.update(_dump_material_instance(mat))
    else:
        info["parameters"] = _dump_material_parameters(mat, mel)
        try:
            info["used_textures"] = [str(t.get_path_name()) for t in mel.get_used_textures(mat)]
        except Exception as e:
            info["used_textures_error"] = repr(e)
        try:
            info["statistics"] = str(mel.get_statistics(mat))
        except Exception:
            pass
        info["graph"] = _walk_material_graph(mat, mel)

    out = os.path.join(EXPORTS, path.split("/")[-1] + ".material.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    summary = {"ok": True, "file": out, "is_instance": is_instance}
    if not is_instance:
        summary["node_count"] = len(info.get("graph", {}).get("nodes", []))
    return summary


def _dump_material_parameters(mat, mel):
    params = {}
    getters = {
        "scalar": ("get_scalar_parameter_names", "get_material_default_scalar_parameter_value"),
        "vector": ("get_vector_parameter_names", "get_material_default_vector_parameter_value"),
        "texture": ("get_texture_parameter_names", "get_material_default_texture_parameter_value"),
        "static_switch": ("get_static_switch_parameter_names",
                          "get_material_default_static_switch_parameter_value"),
    }
    for kind, (names_fn, val_fn) in getters.items():
        try:
            names = getattr(mel, names_fn)(mat)
            d = {}
            for n in names:
                try:
                    d[str(n)] = str(getattr(mel, val_fn)(mat, n))
                except Exception as e:
                    d[str(n)] = "<err %s>" % e
            params[kind] = d
        except Exception as e:
            params[kind + "_error"] = repr(e)
    return params


def _dump_material_instance(mic):
    out = {}
    try:
        out["parent"] = str(mic.get_editor_property("parent").get_path_name())
    except Exception as e:
        out["parent_error"] = repr(e)
    # overridden parameter values live in the *_parameter_values arrays
    for arr_prop in ("scalar_parameter_values", "vector_parameter_values",
                     "texture_parameter_values", "static_switch_parameters"):
        try:
            vals = mic.get_editor_property(arr_prop)
            out[arr_prop] = [str(v) for v in vals]
        except Exception:
            pass
    return out


_MATERIAL_PROPERTIES = [
    "MP_BASE_COLOR", "MP_METALLIC", "MP_ROUGHNESS", "MP_SPECULAR",
    "MP_NORMAL", "MP_EMISSIVE_COLOR", "MP_OPACITY", "MP_OPACITY_MASK",
    "MP_AMBIENT_OCCLUSION", "MP_WORLD_POSITION_OFFSET", "MP_SUBSURFACE_COLOR",
    "MP_MATERIAL_ATTRIBUTES",
]


def _walk_material_graph(mat, mel):
    """
    Bulk expression enumeration is protected in UE 5.x Python, so instead we walk
    the graph *backwards* from each connected material-output property, following
    get_inputs_for_material_expression recursively. Records nodes + input edges +
    which node drives each property. Emits diagnostics when nothing connects.

    See docs/LIMITATIONS.md -- materials that route through material attributes
    return nothing here, and the T3D route (tools/parse_t3d.py) is the answer.
    """
    graph = {"connected_properties": {}, "nodes": [], "diag": {}}
    seen = {}

    def id_of(expr):
        try:
            return expr.get_name()
        except Exception:
            return "expr_%d" % id(expr)

    def visit(expr):
        if expr is None:
            return None
        key = id_of(expr)
        if key in seen:
            return key
        seen[key] = True
        node = _dump_expression(expr)
        node["id"] = key
        try:
            input_names = list(mel.get_material_expression_input_names(expr))
        except Exception:
            input_names = []
        try:
            inputs = list(mel.get_inputs_for_material_expression(mat, expr))
        except Exception:
            inputs = []
        edges = []
        for i, up in enumerate(inputs):
            child = visit(up)
            if child is not None:
                pin = input_names[i] if i < len(input_names) else str(i)
                edges.append({"pin": str(pin), "from": child})
        if edges:
            node["inputs"] = edges
        graph["nodes"].append(node)
        return key

    # diagnostics: how many nodes does UE think this material has?
    try:
        graph["diag"]["num_expressions"] = mel.get_num_material_expressions(mat)
    except Exception as e:
        graph["diag"]["num_expressions_error"] = repr(e)
    try:
        graph["diag"]["use_material_attributes"] = str(
            mat.get_editor_property("use_material_attributes"))
    except Exception:
        pass

    prop_enum = getattr(unreal, "MaterialProperty", None)
    for pname in _MATERIAL_PROPERTIES:
        prop = getattr(prop_enum, pname, None) if prop_enum else None
        if prop is None:
            continue
        try:
            node = mel.get_material_property_input_node(mat, prop)
            graph["diag"][pname] = repr(node)
            if node:
                graph["connected_properties"][pname] = visit(node)
        except Exception as e:
            graph["diag"][pname] = "ERR " + repr(e)

    graph["node_count"] = len(graph["nodes"])
    if graph["node_count"] == 0 and graph["diag"].get("num_expressions"):
        graph["note"] = (
            "Per-node graph not enumerable: UE marks Material.expressions protected, "
            "and this material uses material-attributes routing so the per-property "
            "walk finds no direct connections. Use 'parameters', 'used_textures' and "
            "'statistics' above; for exact node topology copy the graph in-editor and "
            "run tools/parse_t3d.py (see docs/LIMITATIONS.md)."
        )
    return graph


def cmd_blueprint(args):
    """
    Introspect a Blueprint: parent class, component tree (SCS), and selected
    default values from the CDO. args: asset (/Game path), props (optional list
    of CDO property names to read). Event-graph logic is NOT extracted.
    """
    path = args["asset"]
    bp = unreal.load_asset(path)
    if bp is None:
        return {"ok": False, "why": "load failed", "asset": path}
    info = {"asset": path, "class": type(bp).__name__}

    # The Blueprint asset locks down parent_class / generated_class / SCS in UE 5.x
    # Python, so load the generated *_C class and read its CDO instead.
    shortname = path.split("/")[-1]
    gen = None
    for loader in (
        lambda: unreal.load_object(None, path + "." + shortname + "_C"),
        lambda: unreal.load_class(None, path + "." + shortname + "_C"),
    ):
        try:
            gen = loader()
            if gen:
                break
        except Exception:
            pass
    if gen:
        info["generated_class"] = str(gen)
        try:
            # Nearest NATIVE ancestor, as a Python type. Cannot express a BP
            # parented to another BP -- the registry tags below can.
            info["native_type"] = str(unreal.get_type_from_class(gen))
        except Exception:
            pass

    # Parent class comes from the asset registry, not from the class object.
    # BlueprintGeneratedClass has no get_super_class() in UE 5.x Python, and the
    # Blueprint asset rejects get_editor_property('parent_class') outright --
    # both raise, which is why this used to come back empty. The registry tags
    # are authoritative and give the immediate parent even when it is itself a
    # Blueprint.
    try:
        ad = _asset_registry().get_asset_by_object_path(path + "." + shortname)
        for tag, key in (("ParentClass", "parent_class"),
                         ("NativeParentClass", "native_parent_class"),
                         ("BlueprintType", "blueprint_type")):
            v = ad.get_tag_value(tag)
            if v:
                info[key] = _clean_class_tag(str(v))
    except Exception as e:
        info["parent_class_error"] = repr(e)

    # component tree via the SubobjectDataSubsystem (the 5.x-correct path)
    try:
        sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
        handles = sds.k2_gather_subobject_data_for_blueprint(bp)
        comps = []
        for h in handles:
            entry = {}
            try:
                data = sds.k2_find_subobject_data_from_handle(h)
                obj = unreal.SubobjectDataBlueprintFunctionLibrary.get_object(data)
                if obj is None:
                    continue
                entry["component_class"] = type(obj).__name__
                try:
                    entry["name"] = str(obj.get_name())
                except Exception:
                    pass
                for mp in ("static_mesh", "skeletal_mesh"):
                    try:
                        mv = obj.get_editor_property(mp)
                        if mv:
                            entry[mp] = str(mv.get_path_name())
                    except Exception:
                        pass
            except Exception as e:
                entry["error"] = repr(e)
            if entry:
                comps.append(entry)
        info["components"] = comps
    except Exception as e:
        info["components_error"] = repr(e)

    # a few CDO defaults (opt-in via args.props)
    want = args.get("props") or []
    if want and gen:
        try:
            cdo = unreal.get_default_object(gen)
            vals = {}
            for name in want:
                try:
                    vals[name] = str(cdo.get_editor_property(name))
                except Exception as e:
                    vals[name] = "ERR " + repr(e)
            info["cdo_props"] = vals
        except Exception as e:
            info["cdo_error"] = repr(e)

    out = os.path.join(EXPORTS, path.split("/")[-1] + ".blueprint.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    return {"ok": True, "file": out,
            "component_count": len(info.get("components", []))}


# --------------------------------------------------------------------------- #
# export plumbing
# --------------------------------------------------------------------------- #
def _export_via_task(obj, out_path, exporter_name):
    if not hasattr(unreal, exporter_name):
        return False, "no exporter " + exporter_name
    try:
        task = unreal.AssetExportTask()
        task.set_editor_property("object", obj)
        task.set_editor_property("filename", out_path)
        task.set_editor_property("automated", True)
        task.set_editor_property("prompt", False)
        task.set_editor_property("replace_identical", True)
        exporter_cls = getattr(unreal, exporter_name)
        task.set_editor_property("exporter", exporter_cls())
        ok = unreal.Exporter.run_asset_export_task(task)
        return bool(ok) and os.path.exists(out_path), ("run_asset_export_task=%s" % ok)
    except Exception as e:
        return False, repr(e)


def _export_generic(obj, outdir):
    """AssetTools.export_assets -- also the way to get full material node graphs
    as T3D text, engine content included."""
    try:
        tools = unreal.AssetToolsHelpers.get_asset_tools()
        tools.export_assets([obj], outdir)
        return True, "export_assets ok"
    except Exception as e:
        return False, repr(e)


def _dump_expression(ex):
    node = {"type": type(ex).__name__}
    # union of interesting props across the common expression types
    for prop in (
        "parameter_name", "group",                       # parameters
        "constant", "r", "g", "b", "a",                  # constants
        "default_value", "default_value_x", "default_value_y",
        "default_value_z", "default_value_w",
        "texture", "sampler_type",                       # texture samples
        "material_function",                             # function calls
        "function_name", "func",                         # custom/HLSL nodes
        "code", "output_type",                           # custom HLSL
        "coordinate_index", "u_tiling", "v_tiling",      # tex coords
        "desc",                                          # node comment
    ):
        try:
            v = ex.get_editor_property(prop)
            if v not in (None, ""):
                node[prop] = str(v)
        except Exception:
            pass
    return node


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
def cmd_batch(args):
    """
    Run several commands in ONE UE session (amortises the ~30-90s boot).
    args: {"steps": [{"command": "...", "args": {...}}, ...]}
    Returns per-step results; never aborts the batch on a single failure.
    """
    steps = args.get("steps", [])
    results = []
    for i, step in enumerate(steps):
        name = step.get("command")
        sargs = step.get("args", {})
        entry = {"step": i, "command": name}
        try:
            if name not in COMMANDS or name == "batch":
                raise ValueError("bad step command: %s" % name)
            entry["ok"] = True
            entry["data"] = COMMANDS[name](sargs)
        except Exception as e:
            entry["ok"] = False
            entry["error"] = repr(e)
            entry["traceback"] = traceback.format_exc()
        log("batch step %d (%s) ok=%s" % (i, name, entry.get("ok")))
        results.append(entry)
    return {"steps": results}


COMMANDS = {
    "probe": cmd_probe,
    "manifest": cmd_manifest,
    "texture": cmd_texture,
    "mesh": cmd_mesh,
    "material": cmd_material,
    "blueprint": cmd_blueprint,
    "batch": cmd_batch,
}


def main():
    _ensure_dir(EXPORTS)
    result = {"ok": False, "command": None, "log": _LOG, "data": None}
    try:
        with open(CMD_FILE, "r", encoding="utf-8") as f:
            cmd = json.load(f)
        name = cmd.get("command")
        args = cmd.get("args", {})
        result["command"] = name
        log("command=%s args=%s" % (name, args))
        if name not in COMMANDS:
            raise ValueError("unknown command: %s (have %s)" % (name, list(COMMANDS)))
        result["data"] = COMMANDS[name](args)
        result["ok"] = True
        log("done")
    except Exception as e:
        result["error"] = repr(e)
        result["traceback"] = traceback.format_exc()
        log("ERROR " + repr(e))
    finally:
        with open(RESULT_FILE, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)


main()

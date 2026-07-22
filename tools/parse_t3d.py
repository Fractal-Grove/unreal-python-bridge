#!/usr/bin/env python3
"""Parse a UE material node graph out of T3D text.

Prints a compact node list plus edges, so the actual blend logic can be traced.
Not a shader compiler -- enough to read constants, parameter names, node types
and how they connect.

Why this exists: the engine's Python API will not enumerate a material's nodes
(`Material.expressions` is protected), and the backwards walk the bridge does
from connected outputs can only reach nodes that feed an output. T3D is the
complete picture.

    python parse_t3d.py <file.t3d> [--json] [--dot]

TWO T3D DIALECTS, auto-detected -- they are not interchangeable:

  export     Written by `ue.ps1 graph` (an AssetExportTask with a .T3D
             filename). Expressions are forward-declared as empty
             `MaterialExpression*` objects, then filled in by a second pass of
             `Begin Object Name="..."` property blocks. Material outputs live on
             the MaterialEditorOnlyData block. Cross-references look like
             `Expression="...'Package:NodeName'"` (colon).

  clipboard  What you get by selecting nodes in the material graph editor and
             pressing Ctrl+C. Every node is wrapped in a
             `/Script/UnrealEd.MaterialGraphNode` holding an inner expression,
             and references look like `'MaterialGraphNode_N.Expr'` (dot).

The export dialect is fully automatable and should be your default. The
clipboard dialect is for a hand-picked selection, or a graph you are looking at
in the editor anyway.
"""
import argparse
import collections
import json
import re
import sys

# Properties worth surfacing in the compact view, in display order.
INTERESTING = (
    "ParameterName", "InputName", "OutputName", "InputType", "ParameterType",
    "Constant", "ConstA", "ConstB", "ConstantR", "ConstantG", "ConstantB",
    "R", "G", "B", "A", "DefaultValue", "MinDefault", "MaxDefault",
    "Texture", "SamplerType", "MaterialFunction", "Code", "Desc", "Text",
)

# Material output pins, so we can show what the graph actually drives.
OUTPUT_PINS = (
    "BaseColor", "Metallic", "Specular", "Roughness", "Anisotropy", "Normal",
    "Tangent", "EmissiveColor", "Opacity", "OpacityMask", "WorldPositionOffset",
    "SubsurfaceColor", "AmbientOcclusion", "Refraction", "PixelDepthOffset",
    "Displacement", "MaterialAttributes",
)


# --------------------------------------------------------------------------- #
# block scanner -- T3D is a nested Begin Object / End Object tree
# --------------------------------------------------------------------------- #
class Block(object):
    __slots__ = ("header", "lines", "children")

    def __init__(self, header):
        self.header = header
        self.lines = []      # property lines directly in this block
        self.children = []


def scan_blocks(text):
    """Parse the whole file into a tree of Blocks. Returns a synthetic root."""
    root = Block("")
    stack = [root]
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Begin Object"):
            blk = Block(line)
            stack[-1].children.append(blk)
            stack.append(blk)
        elif line.startswith("End Object"):
            if len(stack) > 1:
                stack.pop()
        elif line:
            stack[-1].lines.append(line)
    return root


def walk(block):
    """Yield every block in the tree, depth first."""
    for child in block.children:
        yield child
        for sub in walk(child):
            yield sub


# --------------------------------------------------------------------------- #
# shared property / connection extraction
# --------------------------------------------------------------------------- #
_RE_CLASS_NAME = re.compile(r'Begin Object Class=(?:/Script/\w+\.)?(\w+) Name="([^"]+)"')
_RE_NAME_ONLY = re.compile(r'Begin Object Name="([^"]+)"')
# A connection: Key=( ... Expression="<type>'<package>:<node>'" ... )
# or the clipboard form  Expression="<type>'MaterialGraphNode_7.Expr'"
_RE_CONN = re.compile(r'Expression="[^"]*?\'[^\':]*[:.]([^\'\.]+?)\'')
_RE_KEY = re.compile(r"^(\w+)=")
_RE_SIMPLE = re.compile(r'^(\w+)=(.+)$')


def _extract(lines):
    """Return (props, inputs) from a block's property lines."""
    props = {}
    inputs = {}
    for line in lines:
        key_match = _RE_KEY.match(line)
        if not key_match:
            continue
        key = key_match.group(1)

        targets = _RE_CONN.findall(line)
        if targets:
            inputs.setdefault(key, []).extend(targets)
            # A pin can carry both a connection and a fallback constant; keep the
            # constant too when it is spelled out.
            const = re.search(r"Constant=\(([^)]*)\)|Constant=([-\d.]+)", line)
            if const and key in OUTPUT_PINS:
                props.setdefault(key + ".Constant", (const.group(1) or const.group(2)))
            continue

        simple = _RE_SIMPLE.match(line)
        if simple:
            k, v = simple.group(1), simple.group(2).strip().rstrip(",")
            if k in INTERESTING:
                props[k] = v
    return props, inputs


def _short_ref(value):
    """'/Script/Engine.Texture2D'/Engine/Foo/T_Bar.T_Bar'' -> T_Bar"""
    if "'" in value:
        inner = value.split("'")
        if len(inner) >= 2 and inner[1]:
            return inner[1].split(".")[-1].split("/")[-1]
    return value.strip('"')


# --------------------------------------------------------------------------- #
# dialect: asset export (ue.ps1 graph / AssetExportTask -> .T3D)
# --------------------------------------------------------------------------- #
def parse_export(root):
    types = {}       # node name -> expression type
    bodies = {}      # node name -> property lines
    outputs = {}     # material output pin -> node name
    out_props = {}

    for blk in walk(root):
        m = _RE_CLASS_NAME.match(blk.header)
        if m:
            cls, name = m.group(1), m.group(2)
            if cls.startswith("MaterialExpression"):
                types.setdefault(name, cls)
                if blk.lines:
                    bodies.setdefault(name, []).extend(blk.lines)
            elif cls.endswith("EditorOnlyData"):
                props, ins = _extract(blk.lines)
                out_props.update(props)
                for pin, tgt in ins.items():
                    outputs[pin] = tgt[0]
            continue

        m = _RE_NAME_ONLY.match(blk.header)
        if m:
            name = m.group(1)
            if name.endswith("EditorOnlyData"):
                props, ins = _extract(blk.lines)
                out_props.update(props)
                for pin, tgt in ins.items():
                    outputs[pin] = tgt[0]
            else:
                bodies.setdefault(name, []).extend(blk.lines)

    nodes = {}
    for name, cls in types.items():
        props, inputs = _extract(bodies.get(name, []))
        nodes[name] = {"type": cls, "props": props, "inputs": inputs}
    return nodes, outputs, out_props


# --------------------------------------------------------------------------- #
# dialect: graph-editor clipboard (Ctrl+C in the material graph)
# --------------------------------------------------------------------------- #
def parse_clipboard(root):
    nodes = {}
    for blk in walk(root):
        m = re.match(r'Begin Object Class=/Script/UnrealEd\.MaterialGraphNode Name="([^"]+)"',
                     blk.header)
        if not m:
            continue
        gname = m.group(1)
        etype, ename = "?", gname
        lines = []
        for child in walk(blk):
            cm = _RE_CLASS_NAME.match(child.header)
            if cm and cm.group(1).startswith("MaterialExpression"):
                etype, ename = cm.group(1), cm.group(2)
                lines.extend(child.lines)
            else:
                nm = _RE_NAME_ONLY.match(child.header)
                if nm and nm.group(1) == ename:
                    lines.extend(child.lines)
        props, inputs = _extract(lines)
        nodes[gname] = {"type": etype, "props": props, "inputs": inputs}
    return nodes, {}, {}


def parse(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    root = scan_blocks(text)
    if "/Script/UnrealEd.MaterialGraphNode" in text:
        nodes, outputs, out_props = parse_clipboard(root)
        return nodes, outputs, out_props, "clipboard"
    nodes, outputs, out_props = parse_export(root)
    return nodes, outputs, out_props, "export"


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def short(node):
    bits = [node["type"].replace("MaterialExpression", "") or "?"]
    for key in INTERESTING:
        if key in node["props"]:
            value = node["props"][key]
            if key in ("Texture", "MaterialFunction"):
                value = _short_ref(value)
            bits.append("%s=%s" % (key, value[:40]))
    return " ".join(bits)


def sort_key(name):
    m = re.search(r"_(\d+)$", name)
    return (0, int(m.group(1))) if m else (1, 0)


def main():
    ap = argparse.ArgumentParser(description="Parse a UE material graph from T3D.")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    ap.add_argument("--dot", action="store_true", help="Emit Graphviz DOT.")
    args = ap.parse_args()

    nodes, outputs, out_props, dialect = parse(args.file)

    if args.json:
        json.dump({"dialect": dialect, "outputs": outputs,
                   "output_constants": out_props, "nodes": nodes},
                  sys.stdout, indent=2)
        print()
        return

    if args.dot:
        print("digraph material {")
        print('  rankdir=LR; node [shape=box, fontname="monospace"];')
        for name, node in nodes.items():
            print('  "%s" [label="%s"];' % (name, short(node).replace('"', "'")))
        for name, node in nodes.items():
            for pin, targets in node["inputs"].items():
                for tgt in targets:
                    print('  "%s" -> "%s" [label="%s"];' % (tgt, name, pin))
        for pin, tgt in outputs.items():
            print('  "%s" [shape=doubleoctagon];' % pin)
            print('  "%s" -> "%s";' % (tgt, pin))
        print("}")
        return

    print("FILE %s" % args.file)
    print("dialect: %s   nodes: %d" % (dialect, len(nodes)))
    tally = collections.Counter(n["type"] for n in nodes.values())
    print("types: %s" % dict(tally.most_common()))

    if outputs:
        print("\n---- material outputs ----")
        for pin in OUTPUT_PINS:
            if pin in outputs:
                print("  %-22s <- %s" % (pin, outputs[pin]))
        for pin, tgt in outputs.items():
            if pin not in OUTPUT_PINS:
                print("  %-22s <- %s" % (pin, tgt))

    print("\n---- nodes ----")
    for name in sorted(nodes, key=sort_key):
        node = nodes[name]
        ins = "; ".join("%s<-%s" % (pin, ",".join(t))
                        for pin, t in sorted(node["inputs"].items()))
        print("%-42s %-46s [%s]" % (name, short(node), ins))


if __name__ == "__main__":
    main()

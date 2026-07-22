#!/usr/bin/env python3
"""Minimal UE material-graph T3D parser.

Reads a copied-as-text material / material-function graph and prints a compact
node list plus edges, so the actual blend logic can be traced. Not a full
compiler -- enough to read constants, parameter names, node types and how they
connect.

Why this exists: the engine's Python API will not hand back a material's node
topology (the expression list is protected, and attribute-routed materials return
nothing from a per-property walk). But the editor's copy buffer will. Open the
material, select all in the graph, Ctrl+C, paste into a .txt file -- that text is
T3D, the engine's own object-serialization format, with every expression node and
every connection in it.

Usage: python parse_t3d.py <file.txt>

Bulk alternative: AssetTools.export_assets writes the same full graphs (engine
content included) alongside texture PNGs, with no manual copy-paste.
"""
import re
import sys
import collections


def parse(path):
    txt = open(path, encoding='utf-8', errors='replace').read()
    # Top-level graph nodes: Begin Object Class=.../MaterialGraphNode Name="MaterialGraphNode_N"
    # Each contains an inner expression data object: Begin Object Name="MaterialExpression..._M"
    nodes = {}   # graphnode_name -> dict(type, props, inputs)
    parts = re.split(r'(?=^Begin Object Class=/Script/UnrealEd\.MaterialGraphNode )',
                     txt, flags=re.M)
    for p in parts:
        m = re.match(r'Begin Object Class=/Script/UnrealEd\.MaterialGraphNode Name="([^"]+)"', p)
        if not m:
            continue
        gname = m.group(1)
        # inner class decl -> expression type
        mt = re.search(r'Begin Object Class=/Script/Engine\.(MaterialExpression\w+) Name="([^"]+)"', p)
        etype = mt.group(1) if mt else '?'
        ename = mt.group(2) if mt else gname
        # the DATA object: second "Begin Object Name=..." (no Class=) for that expression
        dm = re.search(r'Begin Object Name="%s"[^\n]*\n(.*?)\n\s*End Object' % re.escape(ename),
                       p, flags=re.S)
        body = dm.group(1) if dm else ''
        props = {}
        inputs = {}
        for line in body.split('\n'):
            line = line.strip()
            # connection:  Key=(Expression="/Script/...'MaterialGraphNode_N.Expr'"...)
            cm = re.match(r'(\w+)=\(.*?Expression="[^\']*\'([^.\']+)\.', line)
            if cm:
                inputs.setdefault(cm.group(1), []).append(cm.group(2))
                continue
            # FunctionInputs(k)=(...Input=(Expression=...'MaterialGraphNode_N...'...InputName="..."))
            fim = re.search(r"Input=\(.*?Expression=\"[^']*'([^.']+)\..*?InputName=\"([^\"]+)\"", line)
            if fim:
                inputs.setdefault('in:' + fim.group(2), []).append(fim.group(1))
                continue
            # scalar / string props
            sm = re.match(r'(\w+)=(.+)$', line)
            if sm:
                k, v = sm.group(1), sm.group(2).strip()
                if k in ('R', 'G', 'B', 'A', 'Constant', 'ConstA', 'ConstB', 'DefaultValue',
                         'ParameterName', 'InputName', 'OutputName', 'InputType', 'SortPriority',
                         'MaterialFunction', 'Texture', 'ConstantR', 'ConstantG', 'ConstantB',
                         'ConstClamp', 'MinDefault', 'MaxDefault', 'ClampMode'):
                    props[k] = v
        nodes[gname] = dict(type=etype, ename=ename, props=props, inputs=inputs)
    return nodes


def short(n):
    p = n['props']
    bits = [n['type'].replace('MaterialExpression', '')]
    for k in ('ParameterName', 'InputName', 'OutputName', 'InputType', 'R', 'G', 'B',
              'Constant', 'ConstA', 'ConstB', 'DefaultValue', 'MaterialFunction'):
        if k in p:
            v = p[k]
            if k == 'MaterialFunction':
                v = v.split('/')[-1].strip('"\'')
            bits.append('%s=%s' % (k, v))
    return ' '.join(bits)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    nodes = parse(path)
    print('FILE %s : %d graph nodes' % (path, len(nodes)))
    tally = collections.Counter(n['type'] for n in nodes.values())
    print('types:', dict(tally.most_common()))
    print('---- nodes ----')
    for g, n in sorted(nodes.items(), key=lambda kv: int(kv[0].split('_')[-1])):
        ins = '; '.join('%s<-%s' % (k, ','.join(v)) for k, v in n['inputs'].items())
        print('%-22s %s   [%s]' % (g, short(n), ins))


if __name__ == '__main__':
    main()

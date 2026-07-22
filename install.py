#!/usr/bin/env python3
"""
One-shot installer: wire this bridge into an Unreal project.

    python install.py --project path/to/YourGame.uproject

What it does:
  1. Makes sure PythonScriptPlugin is enabled in the .uproject (--no-plugin skips;
     the file is backed up to <name>.uproject.bak before it is touched).
  2. Copies editor_hook/init_unreal.py to <Project>/Content/Python/init_unreal.py,
     stamping the absolute path of this checkout into it, so the live bridge
     starts on every editor launch.

It never overwrites an existing init_unreal.py. If one is there, you get a
snippet to paste into it instead.

Uninstall is manual and trivial: delete Content/Python/init_unreal.py.
"""

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK_SRC = os.path.join(HERE, "editor_hook", "init_unreal.py")


def find_uproject(explicit):
    if explicit:
        p = os.path.abspath(explicit)
        if os.path.isdir(p):
            found = [f for f in os.listdir(p) if f.endswith(".uproject")]
            if len(found) == 1:
                return os.path.join(p, found[0])
            sys.exit("expected exactly one .uproject in %s, found %d" % (p, len(found)))
        if not os.path.isfile(p):
            sys.exit("not found: %s" % p)
        return p

    # Walk up from the working directory.
    d = os.getcwd()
    while True:
        found = [f for f in os.listdir(d) if f.endswith(".uproject")]
        if len(found) == 1:
            return os.path.join(d, found[0])
        if len(found) > 1:
            sys.exit("several .uproject files in %s -- pass --project" % d)
        parent = os.path.dirname(d)
        if parent == d:
            sys.exit("no .uproject found walking up from %s -- pass --project" % os.getcwd())
        d = parent


def ensure_python_plugin(uproject):
    """Add PythonScriptPlugin to the .uproject's Plugins list if it is missing."""
    with open(uproject, "r", encoding="utf-8-sig") as fh:
        text = fh.read()
    data = json.loads(text)

    plugins = data.setdefault("Plugins", [])
    for entry in plugins:
        if entry.get("Name") == "PythonScriptPlugin":
            if entry.get("Enabled"):
                print("  PythonScriptPlugin: already enabled")
                return False
            entry["Enabled"] = True
            break
    else:
        plugins.append({"Name": "PythonScriptPlugin", "Enabled": True})

    backup = uproject + ".bak"
    shutil.copy2(uproject, backup)
    with open(uproject, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.write("\n")
    print("  PythonScriptPlugin: enabled (backup at %s)" % os.path.basename(backup))
    return True


def install_hook(uproject, force=False):
    project_dir = os.path.dirname(uproject)
    dest_dir = os.path.join(project_dir, "Content", "Python")
    dest = os.path.join(dest_dir, "init_unreal.py")

    with open(HOOK_SRC, "r", encoding="utf-8") as fh:
        hook = fh.read()
    # Stamp this checkout's location so the hook can find the server. Forward
    # slashes on purpose: they work on every platform and dodge both re.sub's
    # backslash-escape handling and the trailing-backslash raw-string trap.
    # (The lambda replacement is what stops re.sub eating "C:\Users" as \U.)
    home = HERE.replace("\\", "/")
    hook = re.sub(r'^_BRIDGE_HOME = ""$',
                  lambda _m: '_BRIDGE_HOME = "%s"' % home,
                  hook, count=1, flags=re.M)

    if os.path.isfile(dest) and not force:
        print("\n  !! %s already exists -- not overwriting." % dest)
        print("     Either re-run with --force, or add this to the existing file:\n")
        print("       import os")
        print('       _s = os.path.join("%s", "live", "ue_live_server.py")'
              % HERE.replace("\\", "/"))
        print('       exec(compile(open(_s, encoding="utf-8").read(), _s, "exec"),')
        print('            {"__file__": _s, "__name__": "ue_live_server"})\n')
        return False

    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(hook)
    print("  editor hook: %s" % dest)
    return True


def main():
    ap = argparse.ArgumentParser(description="Install the UE Python bridge into a project.")
    ap.add_argument("--project", help="Path to the .uproject (or its folder). "
                                      "Default: search upward from the cwd.")
    ap.add_argument("--no-plugin", action="store_true",
                    help="Do not touch the .uproject; enable PythonScriptPlugin yourself.")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing Content/Python/init_unreal.py.")
    args = ap.parse_args()

    if not os.path.isfile(HOOK_SRC):
        sys.exit("missing %s -- is this checkout complete?" % HOOK_SRC)

    uproject = find_uproject(args.project)
    print("bridge : %s" % HERE)
    print("project: %s\n" % uproject)

    if not args.no_plugin:
        ensure_python_plugin(uproject)
    install_hook(uproject, force=args.force)

    port = os.environ.get("UE_BRIDGE_PORT", "6767")
    print("\ndone. Open the editor, then from a shell:")
    print("  python %s --ping" % os.path.join(HERE, "live", "ue_live.py"))
    print("  (expects the in-editor server on 127.0.0.1:%s)" % port)


if __name__ == "__main__":
    main()

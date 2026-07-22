"""
Editor startup hook -- copy this to <YourProject>/Content/Python/init_unreal.py.

UE runs any init_unreal.py it finds on the Python path automatically at editor
startup, once the Python Editor Script Plugin is enabled. Its only job here is to
start the live bridge server so an open editor is always drivable from a shell:

    python live/ue_live.py --ping

`python install.py --project <path/to/Your.uproject>` copies this file into place
and stamps the absolute path to the bridge into _BRIDGE_HOME below. You can also
copy it by hand and set the UE_BRIDGE_HOME environment variable instead.

The server is deliberately exec'd from source rather than imported: it lives
outside Content/ (it is tooling, not game content), and re-running it is safe --
it reuses an existing server instead of starting a second one.

Environment:
    UE_BRIDGE_HOME     path to the bridge checkout (overrides _BRIDGE_HOME)
    UE_BRIDGE_DISABLE  set to 1 to skip starting the bridge entirely
    UE_BRIDGE_PORT     listen port (default 6767)

If your project already has an init_unreal.py, don't overwrite it -- paste the
_start_live_bridge() call and its helpers into the existing file instead.
"""

import os
import traceback

import unreal

# Filled in by install.py. Leave empty to rely on UE_BRIDGE_HOME or the
# relative-location search below.
_BRIDGE_HOME = ""

# <Project>/Content/Python/init_unreal.py -> <Project>
_PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Checked in order when _BRIDGE_HOME and UE_BRIDGE_HOME are both unset. Covers
# the usual "cloned it next to / inside the project" layouts.
_RELATIVE_GUESSES = (
    os.path.join(_PROJECT, "tools", "unreal-python-bridge"),
    os.path.join(_PROJECT, "tools", "ue_bridge"),
    os.path.join(_PROJECT, "unreal-python-bridge"),
    os.path.join(os.path.dirname(_PROJECT), "unreal-python-bridge"),
)


def _find_server():
    """Absolute path to live/ue_live_server.py, or None."""
    roots = []
    if os.environ.get("UE_BRIDGE_HOME"):
        roots.append(os.environ["UE_BRIDGE_HOME"])
    if _BRIDGE_HOME:
        roots.append(_BRIDGE_HOME)
    roots.extend(_RELATIVE_GUESSES)

    for root in roots:
        candidate = os.path.join(root, "live", "ue_live_server.py")
        if os.path.isfile(candidate):
            return candidate
        # Tolerate someone pointing straight at the live/ directory.
        candidate = os.path.join(root, "ue_live_server.py")
        if os.path.isfile(candidate):
            return candidate
    return None


def _start_live_bridge():
    if os.environ.get("UE_BRIDGE_DISABLE"):
        unreal.log("[init_unreal] live bridge skipped (UE_BRIDGE_DISABLE set)")
        return

    server = _find_server()
    if not server:
        unreal.log_warning(
            "[init_unreal] live bridge server not found. Set UE_BRIDGE_HOME to the "
            "bridge checkout, or re-run install.py to stamp the path in.")
        return

    try:
        with open(server, "r", encoding="utf-8") as fh:
            code = fh.read()
        exec(compile(code, server, "exec"),
             {"__file__": server, "__name__": "ue_live_server"})
        unreal.log("[init_unreal] live bridge started (%s)" % server)
    except Exception:
        # Never let tooling break an editor launch.
        unreal.log_warning("[init_unreal] live bridge failed to start:\n"
                           + traceback.format_exc())


_start_live_bridge()

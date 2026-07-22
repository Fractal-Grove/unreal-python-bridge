# Setup

From zero to a drivable editor. Ten minutes, most of it engine boot.

## Requirements

- **Unreal Engine 5.x.** Developed against 5.5–5.7; the headless commands are
  defensive about API drift, and `probe` tells you what your build exposes.
- **Python Editor Script Plugin**, enabled in your project. `install.py` does
  this for you.
- **Python 3.8+** on the host machine (the one running your shell). This is *not*
  the same interpreter UE ships — the client is plain stdlib, no pip installs.
- **PowerShell 7** (`pwsh`) for the headless bridge and the fallback launcher.
  Cross-platform: it works on macOS and Linux too. The live client is pure Python
  and needs no PowerShell at all.

## Install

```bash
git clone https://github.com/Fractal-Grove/unreal-python-bridge
cd unreal-python-bridge
python install.py --project path/to/YourGame.uproject
```

The installer:

1. Enables `PythonScriptPlugin` in the `.uproject` — backing the file up to
   `YourGame.uproject.bak` before touching it. Skip with `--no-plugin` if you'd
   rather do it in the editor's Plugins panel.
2. Copies `editor_hook/init_unreal.py` to `YourGame/Content/Python/init_unreal.py`,
   stamping in the absolute path of this checkout.

`--project` is optional if you run the installer from inside your project — it
walks up looking for a single `.uproject`.

**If your project already has an `init_unreal.py`**, the installer stops and
prints a snippet to paste into the existing file instead. Don't let it clobber
yours; `--force` exists but you rarely want it.

Then open the editor normally and check:

```bash
python live/ue_live.py --ping
# -> pong
```

The editor's Output Log will show `[init_unreal] live bridge started (...)` near
the end of startup.

## Manual install (no installer)

1. Enable **Python Editor Script Plugin** in Edit ▸ Plugins, restart the editor.
2. Copy `editor_hook/init_unreal.py` into `<YourProject>/Content/Python/`.
3. Set `UE_BRIDGE_HOME` to this checkout so the hook can find the server — or
   clone the repo to one of the locations the hook already checks:
   `<Project>/tools/unreal-python-bridge`, `<Project>/tools/ue_bridge`,
   `<Project>/unreal-python-bridge`, or beside the project folder.

## Without the hook

You don't have to install anything into the project. Either:

**Launch the editor with the bridge:**

```powershell
pwsh live/ue_live.ps1
```

This starts the GUI editor with `-ExecutePythonScript` pointed at the server
(which implicitly enables the Python plugin — no toggling needed) and returns
your shell immediately. `-Wait` blocks until the editor exits.

**Or start it in an already-open editor** — paste into the editor's Python
console (Window ▸ Developer Tools ▸ Output Log, switch the input dropdown to
Python):

```python
exec(open(r"C:/path/to/unreal-python-bridge/live/ue_live_server.py").read())
```

Re-running any of these is safe. The server is stashed on the `unreal` module, so
a second start reuses the first rather than fighting over the port.

## Uninstall

Delete `<YourProject>/Content/Python/init_unreal.py`. That's the whole footprint
(plus the plugin entry in the `.uproject`, if you want that gone too).

## Two projects at once

Give each its own port:

```powershell
$env:UE_BRIDGE_PORT = '6768'; pwsh live/ue_live.ps1 -UProject D:/OtherGame/Other.uproject
python live/ue_live.py --port 6768 --ping
```

The port is read by the in-editor server when it starts, so set it in the
environment the *editor* inherits — or pass `-Port` to `ue_live.ps1`.

## Troubleshooting

**`cannot reach the live UE bridge at 127.0.0.1:6767`**
- Is the editor actually open and finished booting?
- Check the Output Log for `[init_unreal]`. A warning there tells you whether the
  hook ran and failed to find the server (set `UE_BRIDGE_HOME`) or never ran at
  all (Python plugin disabled, or the file isn't in `Content/Python/`).
- Wrong port? `--port` / `UE_BRIDGE_PORT`.

**The hook logs "live bridge server not found"**
Set `UE_BRIDGE_HOME` to the checkout, or re-run `install.py` to re-stamp the path
(it changes if you move the repo).

**`could not locate Engine/Binaries/.../UnrealEditor-Cmd.exe`**
Discovery missed your install. Set `UE_ROOT` to the engine root (the folder
containing `Engine/`), or `UE_CMD` to the exact binary.

**Headless run produces no `_result.json`**
Almost always the editor is still open — it holds the project lock. Close it. The
tail of `headless/_exports/_ue.log` is printed on failure and will say so.

**A headless texture or mesh export is empty**
Re-run with `-NullRhi:$false`. Some exporters need a real render backend, and the
headless driver defaults to NullRHI for speed.

**Everything hangs on the first call after editor launch**
The server only starts once the editor's slate tick is running. Wait for the
editor to be interactive, then retry.

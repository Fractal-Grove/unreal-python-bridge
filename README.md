# unreal-python-bridge

Drive a **running** Unreal Editor from your shell — or export assets from a
headless one — over a small, dependency-free Python bridge.

`.uasset` files are opaque binary. You cannot read a texture, a material graph, a
mesh, or a Blueprint off disk, which means any tool outside the editor — a build
script, a docs generator, an AI coding assistant — is blind to everything in
`Content/`. This toolkit drives the **real engine** to answer those questions, so
what you get back is what UE actually loads, not a third-party parser's guess.

```bash
# editor is open — instant, REPL-like
python live/ue_live.py -c "unreal.SystemLibrary.get_engine_version()"
python live/ue_live.py -f examples/effective_materials.py

# editor is closed — bulk export
pwsh headless/ue.ps1 texture -ArgsJson '{"asset":"/Game/Art/T_Rock_D"}'
```

No pip installs, no engine modifications, no C++ plugin. Two Python files and a
PowerShell driver.

---

## The two bridges

|                 | **live** (`live/`)                              | **headless** (`headless/`)              |
| --------------- | ----------------------------------------------- | --------------------------------------- |
| editor          | must be **open**                                 | must be **closed** (it holds the lock)  |
| latency         | instant                                          | ~30–90 s engine boot *per call*         |
| what it runs    | arbitrary `unreal` Python, REPL-style            | a fixed set of export commands          |
| good for        | querying the live level, resolving what's really bound, iterating | texture / mesh / material / Blueprint dumps |

Use the live bridge by default. Reach for the headless one when you want files on
disk and no editor session.

---

## How the live bridge works

An editor-startup hook execs a small socket server **inside** the editor process.
The tricky part is threading: UE's `unreal` objects are only safe to touch on the
game thread, but a socket accept loop must never block the editor. So:

```
host  ──framed JSON request──►  accept thread (background)
                                   └─ enqueue ─► inbox Queue
editor game thread (slate tick) ── drain inbox ─► exec code ─► set result
host  ◄─framed JSON response──  handler thread
```

Every request is marshalled onto a `register_slate_post_tick_callback` that runs
on the game thread, executed there, and the result handed back to the waiting
handler. Messages are 4-byte big-endian length prefix + UTF-8 JSON, both
directions. It binds to `127.0.0.1` only — it executes arbitrary Python, so it is
never exposed off-machine.

The execution namespace **persists across calls**, with `unreal` preloaded, so it
behaves like a REPL: a bare expression is returned automatically, statement code
returns whatever it assigns to `result`, and state you set in one call is still
there in the next. `--reset-ns` clears it.

---

## Setup

**Requirements:** Unreal Engine 5.x, the **Python Editor Script Plugin** enabled,
Python 3.8+ on the host, and PowerShell 7 (`pwsh`) if you want the headless
bridge or the fallback launcher. Windows, macOS and Linux.

```bash
git clone https://github.com/Fractal-Grove/unreal-python-bridge
cd unreal-python-bridge
python install.py --project path/to/YourGame.uproject
```

`install.py` enables `PythonScriptPlugin` in the `.uproject` (backing it up
first) and copies the editor hook to `YourGame/Content/Python/init_unreal.py`,
stamping in the path to this checkout. UE runs `init_unreal.py` automatically at
editor startup, so from then on **an open editor is always drivable**:

```bash
python live/ue_live.py --ping          # -> pong
```

If you already have an `init_unreal.py`, the installer won't overwrite it — it
prints a three-line snippet to paste into yours instead.

Full walkthrough, manual install, and troubleshooting: **[docs/SETUP.md](docs/SETUP.md)**.

---

## Using it

### Live

```bash
python live/ue_live.py -c "<code>"     # inline
python live/ue_live.py -f script.py    # a file
echo "print(1+1)" | python live/ue_live.py --stdin
python live/ue_live.py -c "..." --json         # full JSON response
python live/ue_live.py -c "..." --out big.txt  # spill large output to a file
python live/ue_live.py --reset-ns              # clear the persistent namespace
python live/ue_live.py --shutdown              # stop the in-editor server
```

Exit codes: `0` success, `1` a Python error inside the editor (traceback comes
back on stderr), `2` the server is unreachable.

See `examples/` for real snippets — listing the selection, resolving the
effective material parameters actually bound to an actor, counting actors by
class.

### Headless

```powershell
pwsh headless/ue.ps1 probe                                            # what does this engine expose?
pwsh headless/ue.ps1 manifest -ArgsJson '{"class":"Material","contains":"Wall"}'
pwsh headless/ue.ps1 texture  -ArgsJson '{"asset":"/Game/Art/T_Foo_D"}'
pwsh headless/ue.ps1 mesh     -ArgsJson '{"asset":"/Game/Art/SM_Foo"}'
pwsh headless/ue.ps1 material -ArgsJson '{"asset":"/Game/Art/M_Foo"}'
pwsh headless/ue.ps1 blueprint -ArgsJson '{"asset":"/Game/BP_Foo"}'
```

Results land in `headless/_exports/`, always alongside a `_result.json`. Since
every call pays the boot cost, **batch** related work:

```powershell
pwsh headless/ue.ps1 batch -ArgsFile steps.json
# steps.json: { "steps": [ {"command":"texture","args":{...}}, {"command":"material","args":{...}} ] }
```

Run `probe` first on any new engine version — UE's Python surface shifts between
releases, and `probe` reports which exporters and material APIs actually exist.

Per-command arguments and output shapes: **[docs/COMMANDS.md](docs/COMMANDS.md)**.

---

## Configuration

Nothing is hardcoded. Engine and project are auto-discovered — the `.uproject` by
walking up from the working directory, the engine from the Windows registry (both
launcher installs and registered source builds) or the conventional install
locations. Override any of it:

| Variable            | Meaning                                                    |
| ------------------- | ---------------------------------------------------------- |
| `UPROJECT`          | path to the `.uproject`                                     |
| `UE_ROOT`           | engine root (the folder containing `Engine/`)               |
| `UE_CMD` / `UE_EDITOR` | exact binary paths, if discovery gets it wrong           |
| `UE_BRIDGE_PORT`    | live-bridge port (default `6767`)                           |
| `UE_BRIDGE_HOME`    | where this checkout lives, for the editor hook              |
| `UE_BRIDGE_DISABLE` | set to `1` to stop the hook starting the bridge             |
| `UE_BRIDGE_EXPORTS` | relocate the headless output directory                      |

The PowerShell scripts also take `-UProject`, `-UeCmd` / `-UeEditor` and `-Port`
directly. Running two editors at once? Give each project its own
`UE_BRIDGE_PORT` and pass `--port` to the client.

---

## Known limits

Honest, and worth reading before you file a bug: **[docs/LIMITATIONS.md](docs/LIMITATIONS.md)**.
The short version —

- **Material node graphs come back partial.** The expression list is protected, so
  the bridge walks backwards from the connected outputs — which reaches most of
  the graph (measured 25/27, 274/297 on 5.7) but structurally cannot see nodes
  that don't feed an output. Compare `graph.node_count` against
  `graph.diag.num_expressions`. For the complete graph, copy it in-editor and run
  `tools/parse_t3d.py` on the pasted T3D.
- **Blueprint event-graph logic is not extracted** — parent class, component tree
  and CDO defaults are.
- **Meshes export as FBX**, not glTF; UE's `GLTFExporter` is abstract and unusable
  from Python.
- NullRHI is on by default for headless runs. If an export comes back empty,
  re-run with `-NullRhi:$false` — some exporters need a real render backend.

## Shelf life

Newer Unreal versions ship an in-editor MCP server plugin that supersedes much of
the live bridge. If your engine has it, prefer it, and keep this around for the
headless bulk export and for engines that don't.

## License

MIT — see [LICENSE](LICENSE).

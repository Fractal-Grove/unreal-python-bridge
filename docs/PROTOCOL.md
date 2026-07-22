# Live bridge protocol

Small enough to reimplement in any language in an afternoon. Useful if you want
to drive the editor from something other than `live/ue_live.py` — a Node script,
an editor plugin, an agent harness.

## Transport

TCP on `127.0.0.1:6767` (override with `UE_BRIDGE_PORT`). Both directions:

```
[4 bytes: big-endian uint32 payload length][payload: UTF-8 JSON]
```

One connection may carry many request/response pairs; responses come back in the
order requests were sent on that connection.

## Request

```json
{
  "op": "exec",            // "exec" | "ping" | "shutdown"
  "id": "<uuid>",          // echoed back; yours to correlate
  "code": "1 + 1",         // exec only
  "timeout": 120.0,        // exec only, seconds the SERVER waits for the game thread
  "reset_ns": false        // exec only, clear the persistent namespace first
}
```

## Response

```json
{
  "id": "<uuid>",
  "ok": true,
  "value_repr": "2",       // repr() of the value, truncated at 8 MB
  "value_json": 2,         // JSON round-trip of the value, or null if not serializable
  "stdout": "",            // captured during execution
  "stderr": "",
  "error": null,           // "TypeError: ..." on failure
  "traceback": null        // full Python traceback on failure
}
```

`ping` responds immediately with `value_repr: "pong"` without touching the game
thread — a true liveness check for the socket, not the editor.

`shutdown` stops the server cleanly (it unregisters the tick callback on the game
thread first, then closes the listener).

## Execution semantics

Code is compiled as an **expression** first; if that raises `SyntaxError` it is
re-compiled and exec'd as **statements**, and the value returned is whatever the
code assigned to `result`. So:

| you send | you get back |
| --- | --- |
| `unreal.SystemLibrary.get_engine_version()` | the version string |
| `x = 5` | `null` |
| `x = 5\nresult = x * 2` | `10` |
| `print("hi")` | `null`, with `"hi\n"` in `stdout` |

The namespace persists between requests (`unreal` preloaded), so `x` above is
still there on the next call until someone sends `reset_ns`.

## Threading contract

The accept loop and per-connection handlers run on **background threads**. Every
`exec` is pushed onto a queue that a `register_slate_post_tick_callback` drains
on the **game thread**, which is the only thread where touching `unreal` objects
is safe. The handler blocks on an `Event` until the tick sets the result.

Two consequences worth knowing:

- **Your code freezes the editor while it runs.** There is no preemption.
- **The client-side `--timeout` doesn't cancel anything.** It stops the *server*
  waiting on the event and returns a timeout response; the queued code still runs
  when the game thread gets to it. Requests that time out before ever reaching
  the tick are marked cancelled and skipped, but one already executing runs to
  completion.

## Security

There is none, by design — the whole surface is "run this Python in my editor."
It binds to loopback. Anything that can reach the port has your user's
permissions inside the editor process. Don't bind it to `0.0.0.0`, don't forward
the port, and set `UE_BRIDGE_DISABLE=1` on machines where it shouldn't run.

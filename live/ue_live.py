#!/usr/bin/env python3
"""
Host-side client for the live in-editor UE bridge (ue_live_server.py).

Runs with SYSTEM python (not UE's). Connects to the socket the server opened
inside the running editor, executes Python there, and prints the result.

Examples:
    python ue_live.py --ping
    python ue_live.py -c "unreal.SystemLibrary.get_engine_version()"
    python ue_live.py -f snippet.py
    echo "print(1+1)" | python ue_live.py --stdin
    python ue_live.py -c "..." --json          # full JSON response
    python ue_live.py -c "..." --out big.txt   # write value/stdout to a file

Code runs in a namespace that persists across calls (REPL-like) with `unreal`
preloaded. A bare expression is returned automatically; in statement code, set
`result = <value>` to return something. `--reset-ns` clears that namespace.

Configuration:
    UE_BRIDGE_HOST / UE_BRIDGE_PORT env vars, or --host / --port.

Exit code: 0 on success, 1 on a Python error inside the editor, 2 if the server
is unreachable.
"""

import argparse
import json
import os
import socket
import struct
import sys
import uuid

HOST = os.environ.get("UE_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("UE_BRIDGE_PORT", "6767"))


def _send(conn, obj):
    body = json.dumps(obj).encode("utf-8")
    conn.sendall(struct.pack(">I", len(body)) + body)


def _recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _recv(conn):
    header = _recv_exact(conn, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    body = _recv_exact(conn, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description="Talk to the live UE editor bridge.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("-c", "--code", help="Python code to run in the editor.")
    src.add_argument("-f", "--file", help="Path to a .py file to run in the editor.")
    src.add_argument("--stdin", action="store_true", help="Read code from stdin.")
    ap.add_argument("--ping", action="store_true", help="Health check.")
    ap.add_argument("--shutdown", action="store_true", help="Stop the in-editor server.")
    ap.add_argument("--reset-ns", action="store_true", help="Clear the persistent namespace.")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--json", action="store_true", help="Print the full JSON response.")
    ap.add_argument("--out", help="Write value_repr (or stdout) to this file instead of printing.")
    args = ap.parse_args()

    if args.ping:
        req = {"op": "ping", "id": str(uuid.uuid4())}
    elif args.shutdown:
        req = {"op": "shutdown", "id": str(uuid.uuid4())}
    else:
        if args.file:
            with open(args.file, "r", encoding="utf-8") as fh:
                code = fh.read()
        elif args.stdin:
            code = sys.stdin.read()
        elif args.code is not None:
            code = args.code
        else:
            ap.error("one of -c/-f/--stdin (or --ping/--shutdown) is required")
        req = {"op": "exec", "id": str(uuid.uuid4()), "code": code,
               "timeout": args.timeout, "reset_ns": args.reset_ns}

    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout + 5) as conn:
            _send(conn, req)
            resp = _recv(conn)
    except (ConnectionRefusedError, OSError) as e:
        sys.stderr.write(
            "cannot reach the live UE bridge at %s:%d (%s)\n"
            "  * is the editor open?\n"
            "  * is the bridge running in it? (install the Content/Python/init_unreal.py\n"
            "    hook with `python install.py --project <path/to/Your.uproject>`,\n"
            "    or launch via `pwsh live/ue_live.ps1`)\n"
            "  * different port? set UE_BRIDGE_PORT or pass --port\n"
            % (args.host, args.port, e))
        return 2

    if resp is None:
        sys.stderr.write("no response (connection closed)\n")
        return 2

    if args.json:
        print(json.dumps(resp, indent=2))
        return 0 if resp.get("ok") else 1

    # Human/agent-friendly default: stdout, then value, then errors.
    out = resp.get("stdout") or ""
    err = resp.get("stderr") or ""
    val = resp.get("value_repr")

    if args.out:
        payload = out if out else (val or "")
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print("[wrote %d chars to %s]" % (len(payload), args.out))
    else:
        if out:
            sys.stdout.write(out if out.endswith("\n") else out + "\n")
        if val is not None:
            print(val)

    if err:
        sys.stderr.write(err if err.endswith("\n") else err + "\n")
    if not resp.get("ok"):
        sys.stderr.write((resp.get("error") or "error") + "\n")
        if resp.get("traceback"):
            sys.stderr.write(resp["traceback"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

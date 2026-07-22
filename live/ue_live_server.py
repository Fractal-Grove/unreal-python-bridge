"""
Live in-editor Python bridge -- runs INSIDE a running UnrealEditor (GUI) session.

Exposes an interactive Python REPL over a local socket, so a shell (or an AI
coding agent) can execute arbitrary `unreal` code against the live editor with
~zero latency -- no 30-90s cold engine boot per call like the headless bridge
(headless/bridge.py) pays.

  host  --framed JSON request-->  accept thread (background)
                                     \\- enqueue --> inbox Queue
  editor game thread (slate tick) -- drain inbox --> exec code --> set result
  host  <--framed JSON response--  handler thread

Why the tick dance: UE's `unreal` objects are only safe to touch on the game
thread. The socket runs on background threads (it must never block the editor),
so every request is marshalled onto a slate post-tick callback that runs on the
game thread, executed there, and the result handed back to the waiting handler.

Ways to start it, in order of preference:

  1. Copy editor_hook/init_unreal.py into <YourProject>/Content/Python/ (see
     install.py). UE then starts this automatically on every editor launch.
  2. pwsh live/ue_live.ps1   -- launches the GUI editor with this auto-run.
  3. In an already-open editor with the Python Editor Script Plugin enabled,
     paste into the Python console:

       exec(open(r"<repo>/live/ue_live_server.py").read())

Re-running is always safe: it reuses the existing server rather than starting a
second one.

Configuration (environment variables, read at start):
  UE_BRIDGE_PORT   listen port (default 6767)
  UE_BRIDGE_HOST   bind address (default 127.0.0.1 -- localhost only, on
                   purpose: this executes arbitrary Python, never expose it)

Protocol (both directions: 4-byte big-endian length prefix + UTF-8 JSON):
  request : {"op": "exec"|"ping"|"shutdown", "code": str, "id": str,
             "timeout": float, "reset_ns": bool}
  response: {"id", "ok", "value_repr", "value_json", "stdout", "stderr",
             "error", "traceback"}
"""

import json
import socket
import struct
import sys
import os
import threading
import queue
import traceback
import io
import contextlib

try:
    import unreal
except Exception:  # only meaningful inside UE
    unreal = None

HOST = os.environ.get("UE_BRIDGE_HOST", "127.0.0.1")
# Override with UE_BRIDGE_PORT when you want two editors drivable at once --
# give each project its own port and point the client at it with --port.
PORT = int(os.environ.get("UE_BRIDGE_PORT", "6767"))
# Cap the serialized value we ship back so a runaway repr can't wedge the socket.
MAX_VALUE_BYTES = 8 * 1024 * 1024


# --------------------------------------------------------------------------- #
# framed socket I/O
# --------------------------------------------------------------------------- #
def _recv_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _recv_msg(conn):
    header = _recv_exact(conn, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    body = _recv_exact(conn, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def _send_msg(conn, obj):
    body = json.dumps(obj).encode("utf-8")
    conn.sendall(struct.pack(">I", len(body)) + body)


# --------------------------------------------------------------------------- #
# the server (singleton, stashed on the `unreal` module so re-exec reuses it)
# --------------------------------------------------------------------------- #
class LiveBridge:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.inbox = queue.Queue()        # (request_dict, done_event, result_holder)
        self.tick_handle = None
        self.listener = None
        self.accept_thread = None
        self.running = False
        # Persistent namespace so state survives across calls (REPL-like).
        self.ns = {"__name__": "ue_live", "unreal": unreal}

    # -- lifecycle ---------------------------------------------------------- #
    def start(self):
        if self.running:
            _ulog("already running on %s:%d" % (self.host, self.port))
            return
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.host, self.port))
        self.listener.listen(8)
        self.listener.settimeout(1.0)
        self.running = True
        self.accept_thread = threading.Thread(
            target=self._accept_loop, name="ue_live_accept", daemon=True
        )
        self.accept_thread.start()
        # Game-thread pump.
        self.tick_handle = unreal.register_slate_post_tick_callback(self._tick)
        _ulog("listening on %s:%d" % (self.host, self.port))

    def stop(self):
        self.running = False
        try:
            if self.tick_handle is not None:
                unreal.unregister_slate_post_tick_callback(self.tick_handle)
        except Exception:
            pass
        self.tick_handle = None
        try:
            if self.listener:
                self.listener.close()
        except Exception:
            pass
        _ulog("stopped")

    # -- network (background threads) --------------------------------------- #
    def _accept_loop(self):
        while self.running:
            try:
                conn, _addr = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_conn, args=(conn,), daemon=True
            ).start()

    def _handle_conn(self, conn):
        try:
            conn.settimeout(None)
            while self.running:
                req = _recv_msg(conn)
                if req is None:
                    return
                resp = self._dispatch(req)
                _send_msg(conn, resp)
        except Exception:
            try:
                _send_msg(conn, {"ok": False, "error": traceback.format_exc()})
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(self, req):
        op = req.get("op", "exec")
        rid = req.get("id")
        if op == "ping":
            return {"id": rid, "ok": True, "value_repr": "pong",
                    "value_json": "pong", "stdout": "", "stderr": "",
                    "error": None, "traceback": None}
        if op == "shutdown":
            # Schedule stop on the game thread so tick unregister is clean.
            holder = {}
            done = threading.Event()
            self.inbox.put(({"op": "shutdown"}, done, holder))
            done.wait(timeout=5.0)
            return {"id": rid, "ok": True, "value_repr": "shutting down",
                    "value_json": None, "stdout": "", "stderr": "",
                    "error": None, "traceback": None}
        # op == exec: marshal to game thread and wait.
        done = threading.Event()
        holder = {}
        timeout = float(req.get("timeout", 120.0))
        req["_cancelled"] = False
        self.inbox.put((req, done, holder))
        if not done.wait(timeout=timeout):
            req["_cancelled"] = True  # tick will skip if not yet run
            return {"id": rid, "ok": False, "value_repr": None,
                    "value_json": None, "stdout": "", "stderr": "",
                    "error": "timeout after %.1fs (still queued or long-running)"
                    % timeout, "traceback": None}
        return holder["resp"]

    # -- game thread -------------------------------------------------------- #
    def _tick(self, _delta):
        # Drain everything queued this frame; never let one bad request throw
        # out of the callback (that would silently unregister the pump).
        while True:
            try:
                req, done, holder = self.inbox.get_nowait()
            except queue.Empty:
                return
            try:
                if req.get("op") == "shutdown":
                    holder["resp"] = {"ok": True}
                    done.set()
                    self.stop()
                    return
                if req.get("_cancelled"):
                    continue
                holder["resp"] = self._exec_on_game_thread(req)
            except Exception:
                holder["resp"] = {
                    "id": req.get("id"), "ok": False, "value_repr": None,
                    "value_json": None, "stdout": "", "stderr": "",
                    "error": "internal tick error", "traceback": traceback.format_exc(),
                }
            finally:
                done.set()

    def _exec_on_game_thread(self, req):
        rid = req.get("id")
        code = req.get("code", "")
        if req.get("reset_ns"):
            self.ns = {"__name__": "ue_live", "unreal": unreal}
        out, err = io.StringIO(), io.StringIO()
        value = None
        error = None
        tb = None
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    # Expression? eval it and capture the value (REPL-style).
                    compiled = compile(code, "<ue_live>", "eval")
                    value = eval(compiled, self.ns)
                except SyntaxError:
                    # Statements: exec, then surface `result` if the code set it.
                    exec(compile(code, "<ue_live>", "exec"), self.ns)
                    value = self.ns.get("result")
        except Exception as e:
            error = "%s: %s" % (type(e).__name__, e)
            tb = traceback.format_exc()

        value_repr, value_json = self._encode_value(value)
        return {
            "id": rid, "ok": error is None,
            "value_repr": value_repr, "value_json": value_json,
            "stdout": out.getvalue(), "stderr": err.getvalue(),
            "error": error, "traceback": tb,
        }

    @staticmethod
    def _encode_value(value):
        if value is None:
            return None, None
        # Prefer a JSON round-trip so the host can consume structured data.
        value_json = None
        try:
            s = json.dumps(value, default=str)
            if len(s) <= MAX_VALUE_BYTES:
                value_json = json.loads(s)
        except Exception:
            value_json = None
        try:
            r = repr(value)
        except Exception:
            r = "<unrepr-able %s>" % type(value).__name__
        if len(r) > MAX_VALUE_BYTES:
            r = r[:MAX_VALUE_BYTES] + "...<truncated>"
        return r, value_json


def _ulog(msg):
    line = "[ue_live] " + str(msg)
    if unreal:
        unreal.log(line)
    else:
        print(line)


def start_server():
    if unreal is None:
        raise RuntimeError("must run inside UnrealEditor (no `unreal` module)")
    existing = getattr(unreal, "_ue_live_bridge", None)
    if existing is not None and getattr(existing, "running", False):
        _ulog("reusing existing server on %s:%d" % (existing.host, existing.port))
        return existing
    bridge = LiveBridge()
    bridge.start()
    unreal._ue_live_bridge = bridge
    return bridge


# Auto-start when exec'd (via -ExecutePythonScript, init_unreal.py, or a console
# paste). Safe to run repeatedly -- start_server() reuses a live instance.
start_server()

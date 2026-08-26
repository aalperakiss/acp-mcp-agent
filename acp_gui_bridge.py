"""
acp_gui_bridge - command listener running inside the ACP-Pre GUI.

Copyright 2026 A. Alper Akis
SPDX-License-Identifier: Apache-2.0

Pattern borrowed from Blender-MCP: a background thread listens on a localhost
socket, hands the incoming command to the MAIN thread via wx.CallAfter, and
waits for the result. The C++/wx side therefore always runs on its own thread,
and the model tree and 3D viewport refresh normally.

Loading (one line in the ACP-Pre Python console):

    exec(open('<path to repo>/acp_gui_bridge.py').read())

Re-running that line stops the previous listener and starts a fresh one.
To stop it by hand:  sys._acp_bridge.stop()

For a listener that starts by itself, embed acp_gui_autoload.py in the model as
a Script object - see install_autoload.py and the README.

Protocol: TCP 127.0.0.1:47800, newline-delimited JSON.
    request  : {"op": "get_layup", ...}\n
    response : {"ok": true, "result": ...}\n
             | {"ok": false, "error": "...", "traceback": "..."}\n

This speaks the legacy ACP console API, not PyACP:
    db.active_model -> Model
    model.modeling_groups['X'].plies['Y'].ply_angle / .number_of_layers / .active
"""

import json
import socket
import sys
import threading
import traceback

import wx

HOST = "127.0.0.1"
PORT = 47800
MAIN_TIMEOUT = 300.0  # seconds - an update or solve can take a while


# --------------------------------------------------------------------------- #
# Handing work to the main thread
# --------------------------------------------------------------------------- #


def _run_on_main(fn, timeout=MAIN_TIMEOUT):
    """Run fn on the main (GUI) thread and return its result."""
    if threading.current_thread() is threading.main_thread():
        return fn()

    box = {}
    done = threading.Event()

    def _wrapped():
        try:
            box["value"] = fn()
            box["ok"] = True
        except Exception as e:  # noqa: BLE001
            box["ok"] = False
            box["error"] = "%s: %s" % (type(e).__name__, e)
            box["traceback"] = traceback.format_exc()
        finally:
            done.set()

    wx.CallAfter(_wrapped)

    if not done.wait(timeout):
        raise TimeoutError(
            "Main thread did not respond within %.0f s. The GUI may be busy "
            "(open dialog, update in progress)." % timeout
        )
    if not box.get("ok"):
        raise _RemoteError(box.get("error", "unknown error"), box.get("traceback", ""))
    return box["value"]


class _RemoteError(RuntimeError):
    def __init__(self, msg, tb=""):
        super().__init__(msg)
        self.tb = tb


# --------------------------------------------------------------------------- #
# Model helpers - all of these are called on the main thread
# --------------------------------------------------------------------------- #


def _model():
    m = db.active_model  # noqa: F821 - injected by the console namespace
    if m is None:
        raise RuntimeError("No active model. Open a model in ACP-Pre.")
    return m


def _iter_plies(m):
    """Return (group_name, ply) pairs in stacking order."""
    out = []
    for g in m.modeling_groups.values():
        for p in g.plies.values():
            out.append((g.name, p))
    return out


def _ply_row(group_name, p):
    mat = getattr(p, "ply_material", None)
    return {
        "group": group_name,
        "name": p.name,
        "angle": getattr(p, "ply_angle", None),
        "layers": getattr(p, "number_of_layers", None),
        "material": getattr(mat, "name", None),
        "active": getattr(p, "active", True),
        "status": getattr(p, "status", None),
        "global_ply_nr": getattr(p, "global_ply_nr", None),
    }


def _find_ply(m, name):
    for gname, p in _iter_plies(m):
        if p.name == name:
            return p
    raise KeyError(
        "No modeling ply named '%s'. Available: %s"
        % (name, [p.name for _, p in _iter_plies(m)])
    )


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #


def _op_ping(_):
    m = db.active_model  # noqa: F821
    return {
        "pong": True,
        "model": getattr(m, "name", None),
        "thread": threading.current_thread().name,
        "acp_pid": None,
    }


def _op_get_layup(payload):
    m = _model()
    rows = [_ply_row(g, p) for g, p in _iter_plies(m)]
    if payload.get("modeling_group"):
        rows = [r for r in rows if r["group"] == payload["modeling_group"]]
    total = sum(int(r["layers"] or 0) for r in rows if r["active"])
    return {"plies": rows, "total_layers": total, "model": m.name}


def _op_set_ply_angles(payload):
    m = _model()
    angles = payload.get("angles") or {}
    applied = {}
    for name, angle in angles.items():
        p = _find_ply(m, name)
        p.ply_angle = float(angle)
        applied[name] = float(angle)
    if payload.get("update", True):
        m.update()
    return {"applied": applied, "updated": bool(payload.get("update", True))}


def _op_set_ply_counts(payload):
    m = _model()
    counts = payload.get("counts") or {}
    applied = {}
    for name, n in counts.items():
        p = _find_ply(m, name)
        n = int(n)
        if n == 0:
            p.active = False
        else:
            p.active = True
            p.number_of_layers = n
        applied[name] = n
    if payload.get("update", True):
        m.update()
    return {"applied": applied}


def _op_add_ply(payload):
    """Append modeling plies, inheriting material and OSS from a template ply.

    The legacy create_modeling_ply needs a ply_material and an oriented
    selection set. Rather than asking the caller for object references it
    cannot hold, both are copied from an existing ply in the same group.
    """
    m = _model()

    gname = payload.get("modeling_group")
    if gname:
        group = m.modeling_groups.get(gname)
        if group is None:
            raise KeyError(
                "No modeling group named '%s'. Available: %s"
                % (gname, list(m.modeling_groups.keys()))
            )
    else:
        groups = list(m.modeling_groups.values())
        if not groups:
            raise RuntimeError("The model has no modeling groups.")
        if len(groups) > 1:
            raise RuntimeError(
                "The model has %d modeling groups (%s). Name one with "
                "modeling_group." % (len(groups), [g.name for g in groups])
            )
        group = groups[0]

    plies = list(group.plies.values())
    copy_from = payload.get("copy_from")
    if copy_from:
        template = group.plies.get(copy_from)
        if template is None:
            raise KeyError(
                "No ply named '%s' in group '%s'. Available: %s"
                % (copy_from, group.name, [p.name for p in plies])
            )
    elif plies:
        template = plies[-1]
    else:
        raise RuntimeError(
            "Group '%s' has no ply to copy material and oriented selection set "
            "from. Create the first ply in ACP-Pre, then add more here."
            % group.name
        )

    layers = int(payload.get("layers", 1))
    created = []
    for angle in payload.get("angles") or []:
        p = group.create_modeling_ply(
            ply_material=template.ply_material,
            ply_angle=float(angle),
            number_of_layers=layers,
            oriented_selection_sets=tuple(template.oriented_selection_sets),
        )
        created.append(_ply_row(group.name, p))

    if payload.get("update", True):
        m.update()

    return {
        "created": created,
        "copied_from": template.name,
        "group": group.name,
        "total_plies": len(group.plies.values()),
    }


def _op_update(payload):
    m = _model()
    m.update()
    return {"updated": True, "model": m.name}


def _op_save(payload):
    m = _model()
    path = payload.get("path")
    if path:
        m.save(path)
    else:
        m.save()
    return {"saved": path or getattr(m, "save_path", None)}


def _op_export_analysis_model(payload):
    m = _model()
    path = payload["path"]
    m.save_analysis_model(path)
    return {"analysis_model": path}


def _op_export_composite_definitions(payload):
    m = _model()
    path = payload["path"]
    m.export_h5_composite_definitions(path)
    return {"composite_definitions": path}


def _op_exec(payload):
    """Free-form code with db/model in scope. For exploration and one-offs."""
    import io
    import contextlib

    code = payload["code"]
    scope = {"db": db, "wx": wx}  # noqa: F821
    scope["model"] = db.active_model  # noqa: F821
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, scope)
    return {"stdout": buf.getvalue(), "result": repr(scope.get("result"))}


OPS = {
    "ping": _op_ping,
    "get_layup": _op_get_layup,
    "set_ply_angles": _op_set_ply_angles,
    "set_ply_counts": _op_set_ply_counts,
    "add_ply": _op_add_ply,
    "update": _op_update,
    "save": _op_save,
    "export_analysis_model": _op_export_analysis_model,
    "export_composite_definitions": _op_export_composite_definitions,
    "exec": _op_exec,
}


# --------------------------------------------------------------------------- #
# Socket server
# --------------------------------------------------------------------------- #


class Bridge(object):
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle --------------------------------------------------------- #

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._serve, name="acp-bridge", daemon=True)
        self._thread.start()
        print("[acp_gui_bridge] listening on %s:%d" % (self.host, self.port))

    def stop(self):
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        print("[acp_gui_bridge] stopped")

    # -- loop -------------------------------------------------------------- #

    def _serve(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(MAIN_TIMEOUT + 30.0)
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            if not data.strip():
                return
            response = self._dispatch(data.decode("utf-8"))
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except Exception:
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": "bridge handler",
                                 "traceback": traceback.format_exc()}) + "\n").encode("utf-8")
                )
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _dispatch(self, raw):
        try:
            payload = json.loads(raw)
        except Exception as e:
            return {"ok": False, "error": "invalid JSON: %s" % e}

        op = payload.get("op")
        fn = OPS.get(op)
        if fn is None:
            return {"ok": False, "error": "unknown op '%s'. Available: %s"
                                          % (op, sorted(OPS))}
        try:
            result = _run_on_main(lambda: fn(payload))
            return {"ok": True, "result": result}
        except _RemoteError as e:
            return {"ok": False, "error": str(e), "traceback": e.tb}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                    "traceback": traceback.format_exc()}


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #

_old = getattr(sys, "_acp_bridge", None)
if _old is not None:
    try:
        _old.stop()
    except Exception:
        pass

sys._acp_bridge = Bridge()
sys._acp_bridge.start()
print("[acp_gui_bridge] ops:", sorted(OPS))
print("[acp_gui_bridge] to stop: sys._acp_bridge.stop()")

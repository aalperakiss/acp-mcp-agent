"""
Stage 1 probe - run in the ACP-Pre GUI Python console.

Copyright 2026 A. Alper Akis
SPDX-License-Identifier: Apache-2.0

Usage (paste one line into the console):
    exec(open('<repo>/probes/gui_probe.py').read())

Output goes to the console and to a text file (see OUT below).
Purpose: map the legacy API names and measure whether db can be read
from a background thread. Makes NO changes to the model.
"""

import os
import sys
import tempfile
import threading
import time
import traceback

OUT = os.environ.get(
    "ACP_PROBE_OUT", os.path.join(tempfile.gettempdir(), "gui_probe_out.txt")
)
_lines = []


def log(*parts):
    line = " ".join(str(p) for p in parts)
    _lines.append(line)
    try:
        print(line)
    except Exception:
        pass


def flush():
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")


# --- 1. environment -------------------------------------------------------------
log("== environment ==")
log("python:", sys.version.replace("\n", " "))
log("executable:", sys.executable)
log("main thread:", threading.current_thread().name)

for mod in ("socket", "threading", "queue", "json"):
    try:
        __import__(mod)
        log("import %s: ok" % mod)
    except Exception as e:
        log("import %s: FAILED %s" % (mod, e))

# --- 2. db / model tree --------------------------------------------------
log("")
log("== db ==")
try:
    log("db type:", type(db).__name__)
    log("db attrs:", [a for a in dir(db) if not a.startswith("_")])
    m = db.active_model
    log("model:", m.name, "| type:", type(m).__name__)
except Exception:
    log("db access FAILED:")
    log(traceback.format_exc())
    flush()
    raise

# modeling group / ply traversal syntax may differ in the legacy API
log("")
log("== tree traversal ==")
groups = None
for expr in ("m.modeling_groups", "m.modeling_groups.values()"):
    try:
        obj = eval(expr)
        log("%s -> %s" % (expr, type(obj).__name__))
    except Exception as e:
        log("%s -> FAILED %s: %s" % (expr, type(e).__name__, e))

try:
    try:
        groups = list(m.modeling_groups.values())
    except AttributeError:
        groups = list(m.modeling_groups)
    log("groups:", [g.name for g in groups])
except Exception:
    log("group listing FAILED:")
    log(traceback.format_exc())

ply = None
if groups:
    g = groups[0]
    log("group attrs:", [a for a in dir(g) if not a.startswith("_")])
    try:
        try:
            plies = list(g.modeling_plies.values())
        except AttributeError:
            plies = list(g.modeling_plies)
        log("plies:", [p.name for p in plies])
        ply = plies[0] if plies else None
    except Exception:
        log("ply listing FAILED:")
        log(traceback.format_exc())

# --- 3. ply attribute names -------------------------------------------
log("")
log("== ply ==")
if ply is not None:
    log("ply type:", type(ply).__name__)
    log("ply public attrs:", [a for a in dir(ply) if not a.startswith("_")])
    for name in ("ply_angle", "angle", "orientation_angle",
                 "number_of_layers", "num_layers", "n_layers",
                 "ply_material", "material", "active"):
        if hasattr(ply, name):
            try:
                log("  %s = %r" % (name, getattr(ply, name)))
            except Exception as e:
                log("  %s unreadable: %s" % (name, e))
else:
    log("no ply found")

# --- 4. update / refresh mechanism -------------------------------------
log("")
log("== update/refresh candidates ==")
for name in ("update", "update_model", "refresh", "solve"):
    log("  model.%s: %s" % (name, hasattr(m, name)))
log("db update/refresh candidates:",
    [a for a in dir(db) if any(k in a.lower() for k in ("update", "refresh", "redraw", "gui"))])

# --- 5. READ from a background thread (no writes) --------------------------
log("")
log("== background thread ==")
_res = {}


def _bg():
    try:
        _res["name"] = db.active_model.name
        _res["thread"] = threading.current_thread().name
        _res["ok"] = True
    except Exception as e:
        _res["ok"] = False
        _res["err"] = "%s: %s" % (type(e).__name__, e)
        _res["tb"] = traceback.format_exc()


t = threading.Thread(target=_bg, name="probe-bg")
t.start()
t.join(5.0)

if t.is_alive():
    log("bg thread did not return within 5 s -> main thread is probably required (queue needed)")
elif _res.get("ok"):
    log("bg read ok:", _res.get("name"), "|", _res.get("thread"))
else:
    log("bg read FAILED:", _res.get("err"))
    log(_res.get("tb", ""))

log("")
log("== done ==")
flush()
print("output written:", OUT)

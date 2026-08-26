"""
Stage 1b probe - ply attribute names plus GUI/main-thread hook discovery.

Copyright 2026 A. Alper Akis
SPDX-License-Identifier: Apache-2.0

Usage (paste one line into the console):
    exec(open('<repo>/probes/gui_probe2.py').read())

Read only. Makes NO changes to the model.
"""

import os
import sys
import tempfile
import threading
import traceback

OUT = os.environ.get(
    "ACP_PROBE2_OUT", os.path.join(tempfile.gettempdir(), "gui_probe2_out.txt")
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


m = db.active_model

# --- 1. ply traversal: the correct name is 'plies' ----------------------------------
log("== plies ==")
groups = list(m.modeling_groups.values())
g = groups[0]
try:
    plies = list(g.plies.values())
except AttributeError:
    plies = list(g.plies)
log("plies:", [p.name for p in plies])

ply = plies[0] if plies else None
if ply is not None:
    log("ply type:", type(ply).__name__)
    log("ply public attrs:", sorted(a for a in dir(ply) if not a.startswith("_")))
    log("")
    log("-- readable values --")
    for name in sorted(a for a in dir(ply) if not a.startswith("_")):
        if name in ("serialize", "visit", "remove", "clear", "add", "update", "solve"):
            continue
        try:
            val = getattr(ply, name)
        except Exception as e:
            log("  %s -> unreadable (%s)" % (name, type(e).__name__))
            continue
        if callable(val):
            continue
        log("  %s = %r" % (name, val))

# --- 2. global names injected into the console ----------------------------
log("")
log("== console globals ==")
try:
    names = sorted(k for k in globals().keys() if not k.startswith("_"))
    log("globals:", names)
except Exception:
    log(traceback.format_exc())

# --- 3. GUI framework / main-thread hook candidates -----------------------
log("")
log("== loaded GUI modules ==")
for mod in ("wx", "PyQt5", "PyQt6", "PySide2", "PySide6", "tkinter", "vtk", "acp"):
    log("  %s: %s" % (mod, mod in sys.modules))

log("")
log("== sys.modules entries matching acp/gui/app ==")
hits = sorted(k for k in sys.modules
              if any(t in k.lower() for t in ("acp", "gui", "app", "wx", "qt")))
log(hits[:60])

# --- 4. update/refresh surface on model and db ----------------------
log("")
log("== model attrs ==")
log(sorted(a for a in dir(m) if not a.startswith("_")))

log("")
log("== db.run_script signature ==")
try:
    import inspect
    log("run_script:", inspect.signature(db.run_script))
except Exception as e:
    log("signature unavailable:", e)
log("run_script doc:", (db.run_script.__doc__ or "").strip()[:400])

# --- 5. is there a Script object (main-thread pump candidate) ------------------
log("")
log("== script object support ==")
for name in sorted(a for a in dir(m) if "script" in a.lower()):
    log("  model.%s" % name)

log("")
log("main thread:", threading.current_thread().name)
log("== done ==")
flush()
print("output written:", OUT)

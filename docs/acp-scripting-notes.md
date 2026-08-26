# ACP-Pre scripting notes

Measured on this machine against ANSYS 2026 R1, ACP-Pre GUI. These are the
findings that the bridge and the autoloader are built on. None of them are
documented in an obvious place, so they are written down here rather than
rediscovered.

## The console API is not PyACP

The ACP-Pre Python console exposes the legacy `compolyx` API, not `pyacp`:

    db.active_model                                  -> Model
    model.modeling_groups['MG'].plies['Ply'].ply_angle
                                                     .number_of_layers
                                                     .active

`type(db).__module__` is `compolyx.db`. PyACP (`ansys-acp-core`) drives a
separate headless gRPC server and **cannot attach to a running GUI** — that
asymmetry is the whole reason the socket bridge exists.

## Script objects

`model.create_script(name, source=None, active=True, uptodate=False, update_mode=...)`

- `source` is the **code as a string**, not a path. Whatever you pass is
  serialised into the `.acph5`, so a model handed to a colleague carries the
  code with it.
- `update_mode` accepts `manual`, `on_triggers`, `always`. In `always` mode the
  script runs on **every** `model.update()` — including updates the bridge
  itself triggers after a ply edit. Anything embedded must be idempotent.
- The globals of a running Script object are **empty**. Verified by dumping
  `globals()` from inside one: `names=[]`. There is no `db` and no `model`.
  The console namespace is reachable as `__main__.db`.
- Scripts run on `MainThread`, so they may touch the GUI directly.
- `ScriptDict` has no `pop` and no `remove`. Available: `append`, `clear`,
  `find`, `get`, `new`, `rename`, `reorder_scripts`, `values`. To replace a
  script, assign to `script.source`; to rename, assign to `script.name`.
- A script object has `.run()`, which executes it immediately without a model
  update — useful for testing the loader without touching the model.

## Threading

The bridge accepts sockets on a daemon thread but performs all model work on
the GUI thread via `wx.CallAfter`, waiting on an `Event` with a 300 s timeout.
Touching `db` from the socket thread is not safe — the C++/wx side expects its
own thread, and the tree and 3D view will not refresh.

## Model path under Workbench

A Workbench-driven ACP-Pre session reports a path such as

    <project>_files/dp0/ACP-Pre/ACP/../MECH/ACP-Pre.1.h5

Do not overwrite that file from the tools — it belongs to the live project and
Workbench owns its lifecycle. Save copies elsewhere via `acp_gui_save` with an
explicit path.

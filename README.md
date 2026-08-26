# acp-mcp-agent

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-lightgrey)
![ANSYS 2026 R1](https://img.shields.io/badge/ANSYS-2026%20R1-orange)

An MCP server that lets an LLM agent drive **ANSYS ACP (Composite PrepPost)** —
read a lay-up, change fibre angles and layer counts, check manufacturing rules,
and export the analysis model and composite definitions.

It talks to ACP two ways, and the difference matters:

```
agent ─┬─ acp_*      ──► PyACP ──► acp_grpcserver.exe   headless, batch / optimisation
       └─ acp_gui_*  ──► TCP 47800 ──► ACP-Pre GUI      live, visible on screen
```

PyACP launches its **own** headless ACP session and cannot attach to a running
ACP-Pre window. So a second path exists: a small socket listener running inside
the GUI's embedded Python, which executes model edits on the wx main thread.
Angles change and the viewport redraws while you watch.

The two sessions are independent. `acp_gui_*` edits the model open in the GUI;
`acp_*` edits the headless one. Pick one per task and stay there.

Scope is the upstream half of the composites loop. Solve and post-processing
stay where they already work:

```
acp-mcp-agent (lay-up) ──► analysis model ──► Mechanical (BC / mesh / solve)
                       ──► composite defs ──► PyDPF-Composites (IRF)
```

---

## Requirements

| | |
|---|---|
| ANSYS | with ACP. Developed and verified against **2026 R1** (`AWP_ROOT261`) |
| Python | 3.10+ for the server side — whichever interpreter your MCP client launches |
| Packages | `mcp`, `pydantic`, `ansys-acp-core` (see `requirements.txt`) |
| OS | Windows. The bridge itself is portable, the documented paths are not |
| Client | Any MCP client. Verified with Claude Desktop |

ANSYS is not a pip package. `ansys-acp-core` starts the ACP gRPC server from a
local ANSYS installation; without one, nothing here runs.

The GUI bridge needs nothing installed: it runs inside ACP-Pre's own embedded
Python (3.10 on 2026 R1) and uses only the standard library plus wx, which
ACP-Pre already provides.

### Paths

Nothing in this repository has a machine-specific path compiled into it. One
environment variable carries the location:

| Variable | Read by | Meaning |
|---|---|---|
| `ACP_BRIDGE_PATH` | `acp_mcp.py`, `install_autoload.py`, `acp_gui_autoload.py` | full path to `acp_gui_bridge.py` |
| `ACP_PROBE_OUT` | `00_probe_pyacp.py`, `gui_probe.py` | where to write probe output (default: next to `00_probe_pyacp.py`, temp dir for the GUI probe) |
| `ACP_PROBE2_OUT` | `gui_probe2.py` | where to write probe output (default: temp dir) |

`install_autoload.py` bakes the resolved path into the model it installs into,
so the embedded copy needs no environment variable afterwards.

---

## Repository layout

```
acp_mcp.py              the MCP server - 15 tools, stdio transport
acp_gui_bridge.py       socket listener that runs INSIDE ACP-Pre
acp_gui_autoload.py     tiny loader, embedded in a model for a persistent bridge
install_autoload.py     embeds the loader in the open model, one call
mcp_config.example.json client registration template
requirements.txt
probes/
  00_probe_pyacp.py     stage 1: what this PyACP install actually exposes
  gui_probe.py          GUI console API exploration
  gui_probe2.py         deeper GUI console API dump
docs/
  pyacp_api_report.txt  reference probe output (yours lands in probes/)
  gui_probe2_out.txt    reference GUI console dump, 2026 R1
  acp-scripting-notes.md   undocumented ACP behaviour worth knowing
```

---

## Setup from zero

### 1. Install the Python side

```bat
git clone https://github.com/aalperakiss/acp-mcp-agent.git
cd acp-mcp-agent
pip install -r requirements.txt
```

Delivered as a zip rather than a repository? Unpack it anywhere, `cd` into the
folder and run the `pip install` line - nothing here depends on git, and the
paths in this README are all relative to the folder root.

Use one interpreter and remember its absolute path — venv, Anaconda, whatever —
but it must be the exact interpreter you put in the client config. A server that
"cannot find mcp" is almost always a second Python.

### 2. Probe your ANSYS installation

```bat
python probes\00_probe_pyacp.py
```

Session probe only: if `launch_acp()` fails here, nothing downstream matters.
The report lands in `probes\pyacp_api_report.txt`; `docs\pyacp_api_report.txt`
is the reference from the development machine, kept for comparison.
Then point it at a model:

```bat
python probes\00_probe_pyacp.py C:/path/to/your.acph5
```

PyACP renamed several methods between releases, so `acp_mcp.py` resolves each
operation at call time from the `CANDIDATES` dict near the top of the file.
Compare the probe report against `CANDIDATES`, `PLY_ANGLE_ATTRS` and
`PLY_COUNT_ATTRS`, and add any missing real names — one place, one edit.

Need an `.acph5`? Open ACP-Pre and File → Save As. Having ACP-Pre open does not
help PyACP by itself.

### 3. Register the server with your client

Claude Desktop config lives at `%APPDATA%\Claude\claude_desktop_config.json`.
Paste the `acp` entry from `mcp_config.example.json` **inside** the existing
`mcpServers` object, alongside whatever is already there. Do not replace the
file. Watch the commas, and double every backslash (or use forward slashes).

Then quit the client completely — system tray included — and reopen. **The tool
list is fixed at startup**; a running client will never see a new server.

Sanity check without a client:

```bat
npx @modelcontextprotocol/inspector python acp_mcp.py
```

### 4. Verify the headless half

Call in order, confirming each returns JSON rather than `Error:`

1. `acp_import_model`
2. `acp_get_layup`
3. `acp_set_ply_angles` — change one ply, then re-read the lay-up
4. `acp_check_layup_rules` — violations on a real model are normal
5. `acp_update_and_export`
6. `acp_save_for_gui` — open the result in ACP-Pre and eyeball it

That is already useful work: open a model, list the lay-up, change angles, check
rules, export. Worth living with for a while before automating further.

### 5. Start the live GUI bridge

Open ACP-Pre with a model, open the Python console, and paste one line:

```python
exec(open('<repo>/acp_gui_bridge.py').read())
```

You should see:

```
[acp_gui_bridge] listening on 127.0.0.1:47800
```

Now `acp_gui_status` from the agent returns `pong: true` and the name of the
open model. From there `acp_gui_set_ply_angles` redraws the viewport live.

The listener lives in the ACP-Pre process. Close ACP-Pre and it is gone — paste
the line again, or make it persistent as below.

### 6. Persistent bridge (optional)

Embed the autoloader in the model as a Script object, so ACP-Pre starts the
listener on its own. In the ACP-Pre console:

```python
import os
os.environ['ACP_BRIDGE_PATH'] = '<repo>/acp_gui_bridge.py'
exec(open('<repo>/install_autoload.py').read())
```

Then save the model. Three things make this safe rather than reckless:

- **The loader is embedded, the bridge is not.** A Script object stores source
  as a string, so embedding the whole bridge would ship a listener to every
  machine that opens the file. The loader reads the bridge from disk instead;
  no file, no listener, one printed line.
- **It is idempotent.** `always` mode fires on every `model.update()`, including
  the update the bridge itself triggers after a ply edit. The guard on
  `sys._acp_bridge` stops it rebinding port 47800 mid-request.
- **It fetches `db` itself.** Script objects run with empty globals — no `db`,
  no `model`. The loader reaches the console namespace through `__main__`.

Scripts run on model update, not on file open, so `install_autoload.py` triggers
one update to bring the listener up immediately. To remove it later, set
`model.scripts['acp_agent_bridge'].active = False` and save.

Still keep a separate agent-enabled copy of shared models. A Script object is
invisible in a design review, and a colleague opening your `.acph5` should not
inherit a socket listener by accident.

---

## Tools

### Headless (PyACP)

| Tool | Does |
|---|---|
| `acp_import_model` | launch a headless session and load a model |
| `acp_get_layup` | plies in stacking order: angle, layers, material |
| `acp_set_ply_angles` | set fibre orientations, optional snap to manufacturable set |
| `acp_set_ply_counts` | set layer counts; `0` deactivates a ply |
| `acp_check_layup_rules` | symmetry, balance, ±45 outer, ≤4 consecutive, direction fractions |
| `acp_update_and_export` | update, write analysis model and composite definitions |
| `acp_save_for_gui` | write an `.acph5` to inspect in ACP-Pre |

Design vector first, export once: the `set_*` tools do not update or export.

### Live GUI (socket bridge)

| Tool | Does |
|---|---|
| `acp_gui_status` | is the bridge reachable, which model is open |
| `acp_gui_get_layup` | read the lay-up from the GUI's model |
| `acp_gui_set_ply_angles` | set angles, redraw immediately |
| `acp_gui_set_ply_counts` | set layer counts, redraw immediately |
| `acp_gui_add_ply` | append new modeling plies, inheriting material and OSS |
| `acp_gui_save` | save the GUI's model |
| `acp_gui_export` | export analysis model / composite definitions from the GUI |
| `acp_gui_exec` | arbitrary Python in the live session, `db` and `model` in scope |

Prefer the typed tools over `acp_gui_exec` for routine edits; the free-form tool
is for exploration and one-offs.

`acp_gui_add_ply` takes a list of angles and appends one ply per entry, in
stacking order. Material and oriented selection set are inherited from an
existing ply (the last one in the group by default, or `copy_from`), because
`create_modeling_ply` needs object references an agent cannot hold. A group
with no plies at all therefore cannot be seeded from here - create the first
ply in ACP-Pre.

Angles snap to `0, ±15, ±30, ±45, ±60, 90` by default. Turn snapping off
explicitly when you want intermediate orientations.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| New tools missing after editing the config | Client not fully restarted. Tool list is fixed at startup |
| `Cannot reach the live ACP-Pre bridge` | ACP-Pre closed, or the bridge was never loaded in this process |
| Bridge call times out after 300 s | GUI busy — an open dialog blocks the main thread |
| `No active model` | ACP-Pre is running with no model loaded |
| `launch_acp()` fails | ANSYS not found, or the wrong Python. Check the probe first |
| Port 47800 in use | An orphaned listener. `sys._acp_bridge.stop()` in the console |

---

## Known gaps

- `acp_check_layup_rules` flattens everything into one stack. Multi-region parts
  need per-OSS grouping before this is trustworthy on real geometry.
- Ply creation exists on the live GUI side only (`acp_gui_add_ply`). The
  headless `acp_*` tools still edit existing plies only, so a model driven
  through PyACP must be built with enough spare plies up front.
- No delete-ply tool yet. Deactivate a ply with `acp_gui_set_ply_counts` set to 0.
- Mass is not reported by the export tool; the attribute path varies too much
  between releases to guess. Add it once your probe report shows the real one.
- Verified against one ANSYS release only. The `CANDIDATES` mechanism exists
  because older and newer releases will differ.
- No tests. The verification path is the probe plus the manual tool sequence in
  steps 4 and 5.
- Optimisation loop not started. Three decisions still open: whether the
  optimiser calls ACP directly or through MCP, whether the solve runs batch or
  through a Mechanical MCP, and which failure criteria set the constraint.
  Budget for a surrogate — one evaluation is one full solve, and 200 of those is
  a working day at minimum.

## Security note

The bridge listens on `127.0.0.1` only and has no authentication.
`acp_gui_exec` executes arbitrary Python inside ACP-Pre. Anything able to reach
that port on the machine has the same power. Do not bind it to `0.0.0.0`, and do
not run it on a shared session.

---

## Contributing

Issues and pull requests are welcome, particularly probe reports from ANSYS
releases other than 2026 R1 — that is the fastest way to fill in `CANDIDATES`.
Attach the generated `probes/pyacp_api_report.txt` and state the release.

Contributions are accepted under the Apache License 2.0 (see section 5 of the
license). No CLA.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

ANSYS, ACP, Composite PrepPost, Mechanical and Workbench are trademarks of
ANSYS, Inc. This project is an independent integration and is not affiliated
with, endorsed by, or supported by ANSYS, Inc. No ANSYS software or
documentation is redistributed here; a licensed local ANSYS installation is
required.

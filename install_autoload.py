"""
install_autoload - embed the bridge autoloader in the open ACP model.

Copyright 2026 A. Alper Akis
SPDX-License-Identifier: Apache-2.0

Run this once per model, from the ACP-Pre Python console:

    import os
    os.environ['ACP_BRIDGE_PATH'] = 'C:/path/to/acp-mcp-agent/acp_gui_bridge.py'
    exec(open('C:/path/to/acp-mcp-agent/install_autoload.py').read())

Then save the model. From that point ACP-Pre starts the listener on its own,
with no console paste. The repo location is read from ACP_BRIDGE_PATH and baked
into the embedded source, so the model needs no environment variable later.

Re-running this updates an existing script object rather than adding a second.
Scripts execute on model update, not on file open, so the install triggers one
update to bring the listener up immediately.

To remove it: model.scripts['acp_agent_bridge'].active = False, or clear the
script collection, then save.
"""

import os
import sys

SCRIPT_NAME = "acp_agent_bridge"

_bridge = os.environ.get("ACP_BRIDGE_PATH")
if not _bridge:
    raise RuntimeError(
        "Set ACP_BRIDGE_PATH to the full path of acp_gui_bridge.py before "
        "running this. Example: "
        "os.environ['ACP_BRIDGE_PATH'] = 'C:/repo/acp-mcp-agent/acp_gui_bridge.py'"
    )

_bridge = _bridge.replace("\\", "/")
if not os.path.isfile(_bridge):
    raise RuntimeError("No bridge file at %s" % _bridge)

_repo = os.path.dirname(_bridge)
_loader = os.path.join(_repo, "acp_gui_autoload.py")
if not os.path.isfile(_loader):
    raise RuntimeError(
        "acp_gui_autoload.py is expected next to acp_gui_bridge.py, not found "
        "in %s" % _repo
    )

with open(_loader) as _fh:
    _source = _fh.read().replace("@BRIDGE_PATH@", _bridge)

_model = db.active_model  # noqa: F821 - console namespace
if _model is None:
    raise RuntimeError("No active model. Open a model in ACP-Pre first.")

_existing = _model.scripts.get(SCRIPT_NAME)
if _existing is not None:
    _existing.source = _source
    _existing.active = True
    _existing.update_mode = "always"
    print("[acp-agent] updated existing script object '%s'" % SCRIPT_NAME)
else:
    _model.create_script(
        name=SCRIPT_NAME, source=_source, active=True, update_mode="always"
    )
    print("[acp-agent] created script object '%s'" % SCRIPT_NAME)

_model.update()

print(
    "[acp-agent] bridge running: %s"
    % (getattr(sys, "_acp_bridge", None) is not None)
)
print("[acp-agent] save the model to make this persistent")

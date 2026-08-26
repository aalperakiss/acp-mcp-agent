# acp-agent :: bridge autoloader
#
# Copyright 2026 A. Alper Akis
# SPDX-License-Identifier: Apache-2.0
#
# Embedded in an ACP model as a Script object (update mode "always") so that
# ACP-Pre starts the socket bridge by itself. Install it with:
#
#     import os
#     os.environ['ACP_BRIDGE_PATH'] = '<repo>/acp_gui_bridge.py'
#     exec(open('<repo>/install_autoload.py').read())
#
# install_autoload.py substitutes the placeholder below with the real path, so
# the embedded copy carries no environment dependency of its own.
#
# Measured properties of the ACP Script object environment (ANSYS 2026 R1):
#   - globals() inside a Script object is EMPTY - there is no `db` and no
#     `model`. The console namespace is reachable as __main__.db.
#   - Scripts run on the wx MainThread, and in "always" mode they run on every
#     model.update() - including the update the bridge itself triggers after a
#     ply edit. The loader must therefore be idempotent, or every update would
#     tear down the listener and rebind port 47800 mid-request.
#   - A Script object stores its source as a string, not a path. Embedding this
#     loader instead of the whole bridge keeps the real code on disk: a model
#     copied to another machine finds no bridge file and quietly does nothing.

import os
import sys

BRIDGE = os.environ.get("ACP_BRIDGE_PATH") or "@BRIDGE_PATH@"

if BRIDGE.startswith("@"):
    # Placeholder never substituted: fall back to a sibling file when this is
    # run as a real script, otherwise say what is missing and stop.
    BRIDGE = (
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "acp_gui_bridge.py")
        if "__file__" in globals()
        else ""
    )

if getattr(sys, "_acp_bridge", None) is not None:
    pass  # already listening in this ACP-Pre process
elif not BRIDGE:
    print(
        "[acp-agent] no bridge path. Set ACP_BRIDGE_PATH, or install via "
        "install_autoload.py so the path is baked in."
    )
elif not os.path.isfile(BRIDGE):
    print("[acp-agent] bridge file not found, autoload skipped: %s" % BRIDGE)
else:
    try:
        import __main__

        _scope = {"db": __main__.db, "__name__": "acp_gui_bridge", "__file__": BRIDGE}
        with open(BRIDGE) as _f:
            exec(compile(_f.read(), BRIDGE, "exec"), _scope)
        sys._acp_bridge_scope = _scope
        print("[acp-agent] bridge autoloaded from %s" % BRIDGE)
    except Exception as _e:  # noqa: BLE001 - never break a model update
        print("[acp-agent] bridge autoload failed: %r" % (_e,))

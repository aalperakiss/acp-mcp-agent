"""
00_probe_pyacp.py - dump the PyACP API surface of THIS machine to a text file.

Copyright 2026 A. Alper Akis
SPDX-License-Identifier: Apache-2.0

Why: PyACP object and method names moved between releases. Rather than guessing,
run this once and read the report. acp_mcp.py resolves names at runtime from
candidate lists, and this report tells you which candidate actually exists.

Usage:
    python 00_probe_pyacp.py                       # API surface only
    python 00_probe_pyacp.py path/to/model.acph5   # also loads a model

Output: pyacp_api_report.txt next to this script, i.e. in probes/. Override the
full path with ACP_PROBE_OUT, as the two GUI probes do with ACP_PROBE_OUT and
ACP_PROBE2_OUT. The copy under docs/ is the reference report from the machine
this was developed on - your own run does not overwrite it, so the two can be
compared.
"""

from __future__ import annotations

import datetime
import inspect
import os
import sys
from pathlib import Path

REPORT = Path(
    os.environ.get("ACP_PROBE_OUT") or Path(__file__).with_name("pyacp_api_report.txt")
)


def members(obj, keywords=None):
    """Public members of obj, optionally filtered by keyword substrings."""
    names = [m for m in dir(obj) if not m.startswith("_")]
    if keywords:
        names = [m for m in names if any(k in m.lower() for k in keywords)]
    return sorted(names)


def signature_of(obj, name):
    try:
        return f"{name}{inspect.signature(getattr(obj, name))}"
    except (TypeError, ValueError):
        return f"{name}(?)"


def main() -> int:
    out = [
        "PyACP API report",
        f"generated: {datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"python:    {sys.version.split()[0]}",
        "",
    ]

    try:
        import ansys.acp.core as pyacp
    except ImportError as e:
        out.append(f"FAIL: ansys-acp-core not importable: {e}")
        out.append("Fix: pip install ansys-acp-core")
        REPORT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        return 1

    out.append(f"ansys-acp-core version: {getattr(pyacp, '__version__', 'unknown')}")
    out.append("")
    out.append("== module-level entry points ==")
    out += [f"  {m}" for m in members(pyacp, ["launch", "connect", "acp"])]
    out.append("")

    try:
        acp = pyacp.launch_acp()
    except Exception as e:  # noqa: BLE001
        out.append(f"FAIL: launch_acp() raised {type(e).__name__}: {e}")
        out.append("Common causes: ANSYS not installed, wrong version, no license.")
        REPORT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        return 1

    out.append("== ACP session object ==")
    out += [f"  {signature_of(acp, m)}" for m in members(acp)]
    out.append("")

    if len(sys.argv) < 2:
        out.append("No model path given - stopping after session probe.")
        out.append("Re-run as: python 00_probe_pyacp.py <path to .acph5 or .cdb>")
        REPORT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        print(f"\nReport written to {REPORT}")
        return 0

    path = str(Path(sys.argv[1]).resolve())
    out.append(f"== loading model: {path} ==")
    try:
        model = acp.import_model(path)
    except Exception as e:  # noqa: BLE001
        out.append(f"FAIL: import_model raised {type(e).__name__}: {e}")
        out.append("Try inspecting the signature above - some releases require a")
        out.append("format keyword, e.g. import_model(path, format='ansys:cdb').")
        REPORT.write_text("\n".join(out), encoding="utf-8")
        print("\n".join(out))
        return 1

    out.append("  loaded OK")
    out.append("")
    out.append("== model: export / save / update methods ==")
    out += [
        f"  {signature_of(model, m)}"
        for m in members(model, ["export", "save", "update", "unit", "mass", "data"])
    ]
    out.append("")
    out.append("== model: all public members ==")
    out += [f"  {m}" for m in members(model)]
    out.append("")

    # Containers we address plies through.
    for container in ("modeling_groups", "materials", "fabrics", "rosettes",
                      "oriented_selection_sets", "element_sets"):
        if not hasattr(model, container):
            out.append(f"== {container}: ABSENT on this version ==")
            continue
        obj = getattr(model, container)
        try:
            keys = list(obj.keys())
        except Exception:  # noqa: BLE001
            keys = ["<not dict-like>"]
        out.append(f"== {container}: {len(keys)} item(s) ==")
        out += [f"  {k}" for k in keys[:25]]
        if len(keys) > 25:
            out.append(f"  ... +{len(keys) - 25} more")
        out.append("")

    # One modeling ply, fully expanded - this is the object the MCP mutates.
    try:
        group = next(iter(model.modeling_groups.values()))
        ply = next(iter(group.modeling_plies.values()))
        out.append(f"== sample modeling ply: {ply.name} ==")
        for m in members(ply):
            try:
                value = getattr(ply, m)
            except Exception as e:  # noqa: BLE001
                value = f"<raised {type(e).__name__}>"
            if callable(value):
                out.append(f"  {signature_of(ply, m)}   [method]")
            else:
                out.append(f"  {m} = {value!r}")
    except StopIteration:
        out.append("== no modeling plies found - model has no lay-up yet ==")
    except Exception as e:  # noqa: BLE001
        out.append(f"== ply probe failed: {type(e).__name__}: {e} ==")

    REPORT.write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))
    print(f"\nReport written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

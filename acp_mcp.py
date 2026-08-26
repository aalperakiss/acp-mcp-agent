# Copyright 2026 A. Alper Akis
# SPDX-License-Identifier: Apache-2.0
"""
acp_mcp - FastMCP tool group for ANSYS ACP (Composite PrepPost) via PyACP.

Scope: lay-up manipulation and export only. Solve and results stay with the
Mechanical MCP and PyDPF-Composites:

    acp_mcp (lay-up) --> analysis model --> mechanical_mcp (BC/mesh/solve)
                     --> composite defs --> dpf-composites (IRF)

Version tolerance: PyACP renamed several methods between releases. Instead of
hardcoding one name, this server resolves each operation from a candidate list
at call time (see _resolve). If none of the candidates exist, the error message
names what was tried, so you can add the real name to the list in one place.
Run 00_probe_pyacp.py first to see what your installation actually exposes.

Transport: stdio. Run: python acp_mcp.py
"""

from __future__ import annotations

import json
import os as _os
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

try:  # SDK <2: FastMCP
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError:  # SDK >=2: same class, renamed to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

mcp = FastMCP("acp_mcp")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MANUFACTURING_ANGLES = (0.0, 15.0, -15.0, 30.0, -30.0, 45.0, -45.0, 60.0, -60.0, 90.0)
MAX_CONSECUTIVE_SAME_ANGLE = 4
MIN_FRACTION_PER_DIRECTION = 0.08
PRINCIPAL_DIRECTIONS = (0.0, 90.0, 45.0, -45.0)

# Candidate names per operation, most likely first. Extend from the probe report.
CANDIDATES: Dict[str, Sequence[str]] = {
    "import_model": ("import_model", "load_model", "open_model"),
    "update": ("update", "update_model"),
    "export_analysis_model": (
        "export_analysis_model",
        "export_analysis_model_to_file",
        "save_analysis_model",
    ),
    "export_composite_definitions": (
        "export_shell_composite_definitions",
        "export_composite_definitions",
        "export_shell_composite_definitions_to_file",
    ),
    "save": ("save", "save_as", "write"),
}

# Property names on a modeling ply, in preference order.
PLY_ANGLE_ATTRS = ("ply_angle", "angle", "orientation_angle")
PLY_COUNT_ATTRS = ("number_of_layers", "num_layers", "n_layers")

_STATE: Dict[str, Any] = {"acp": None, "model": None, "source": None}


class AcpError(RuntimeError):
    """ACP session is not in a usable state, or an API name could not be resolved."""


# --------------------------------------------------------------------------- #
# Version-tolerant helpers
# --------------------------------------------------------------------------- #


def _resolve(obj: Any, operation: str) -> Callable[..., Any]:
    """Return the first existing method on obj matching CANDIDATES[operation]."""
    names = CANDIDATES[operation]
    for name in names:
        method = getattr(obj, name, None)
        if callable(method):
            return method
    raise AcpError(
        f"Could not resolve '{operation}' on {type(obj).__name__}. Tried: "
        f"{', '.join(names)}. Run 00_probe_pyacp.py, find the real method name in "
        f"pyacp_api_report.txt, and add it to CANDIDATES['{operation}'] in acp_mcp.py."
    )


def _get_attr(obj: Any, attrs: Sequence[str], default: Any = None) -> Any:
    for a in attrs:
        if hasattr(obj, a):
            return getattr(obj, a)
    return default


def _set_attr(obj: Any, attrs: Sequence[str], value: Any) -> str:
    for a in attrs:
        if hasattr(obj, a):
            setattr(obj, a, value)
            return a
    raise AcpError(
        f"None of {attrs} exist on {type(obj).__name__}. Check the sample ply "
        f"section of pyacp_api_report.txt and update the attribute list."
    )


def _require_model():
    if _STATE["model"] is None:
        raise AcpError(
            "No ACP model loaded. Call acp_import_model first with an absolute "
            "path to an .acph5 or .cdb file."
        )
    return _STATE["model"]


def _iter_plies(model) -> List[Any]:
    """Flatten modeling plies across modeling groups, in stacking order."""
    plies: List[Any] = []
    for group in model.modeling_groups.values():
        plies.extend(group.modeling_plies.values())
    return plies


def _ply_summary(ply) -> Dict[str, Any]:
    return {
        "name": ply.name,
        "angle": _get_attr(ply, PLY_ANGLE_ATTRS),
        "layers": _get_attr(ply, PLY_COUNT_ATTRS),
        "material": getattr(_get_attr(ply, ("ply_material", "material")), "name", None),
        "active": getattr(ply, "active", True),
    }


def _flat_stack(model) -> List[float]:
    """Expand the lay-up into one entry per physical layer, in stacking order."""
    stack: List[float] = []
    for p in _iter_plies(model):
        if not getattr(p, "active", True):
            continue
        angle = _get_attr(p, PLY_ANGLE_ATTRS)
        count = int(_get_attr(p, PLY_COUNT_ATTRS, 0) or 0)
        if angle is not None:
            stack.extend([float(angle)] * count)
    return stack


def _err(e: Exception) -> str:
    """Format an exception as an actionable message for the agent."""
    if isinstance(e, AcpError):
        return f"Error: {e}"
    if isinstance(e, KeyError):
        return (
            f"Error: object not found ({e}). Call acp_get_layup to list the exact "
            f"ply names in the current model - names are case-sensitive."
        )
    if isinstance(e, FileNotFoundError):
        return f"Error: file not found ({e}). Use an absolute path with forward slashes."
    if isinstance(e, ImportError):
        return (
            f"Error: PyACP not importable ({e}). Install with: "
            f"pip install ansys-acp-core"
        )
    return f"Error: {type(e).__name__}: {e}"


def _snap(angle: float) -> float:
    return min(MANUFACTURING_ANGLES, key=lambda a: abs(a - angle))


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class ImportModelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(
        ...,
        description="Absolute path to the ACP model, e.g. 'C:/work/shell.acph5'. "
        "A meshed .cdb also works on most releases.",
        min_length=1,
    )
    format: Optional[str] = Field(
        default=None,
        description="Format hint if the release requires one, e.g. 'ansys:cdb'. "
        "Omit unless import fails without it.",
    )


class LayupQueryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    modeling_group: Optional[str] = Field(
        default=None, description="Restrict to one modeling group. Omit for all."
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON)


class SetPlyAnglesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    angles: Dict[str, float] = Field(
        ...,
        description="Ply name -> fibre angle in degrees, e.g. {'ply_01': 45.0}.",
    )
    snap_to_manufacturing: bool = Field(
        default=True,
        description="Snap each angle to the nearest manufacturable orientation "
        "(0/+-15/+-30/+-45/+-60/90).",
    )

    @field_validator("angles")
    @classmethod
    def _in_range(cls, v: Dict[str, float]) -> Dict[str, float]:
        for name, a in v.items():
            if not -90.0 <= a <= 90.0:
                raise ValueError(f"{name}: angle {a} outside [-90, 90]")
        return v


class SetPlyCountsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    counts: Dict[str, int] = Field(
        ..., description="Ply name -> layer count (0 deactivates the ply)."
    )

    @field_validator("counts")
    @classmethod
    def _nonneg(cls, v: Dict[str, int]) -> Dict[str, int]:
        for name, n in v.items():
            if n < 0 or n > 200:
                raise ValueError(f"{name}: layer count {n} outside [0, 200]")
        return v


class CheckLayupInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    require_symmetry: bool = Field(default=True)
    require_balance: bool = Field(
        default=True, description="Every +theta needs a matching -theta."
    )
    require_45_outer: bool = Field(
        default=True, description="Outermost ply should be +/-45."
    )


class ExportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    analysis_model_path: str = Field(
        ..., description="Output path for the Mechanical analysis model (.h5)."
    )
    composite_definitions_path: Optional[str] = Field(
        default=None,
        description="Output path for shell composite definitions used by "
        "PyDPF-Composites. Omit to skip.",
    )


class SaveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: str = Field(..., description="Output .acph5 path for GUI inspection.")


# --------------------------------------------------------------------------- #
# Session tools
# --------------------------------------------------------------------------- #


@mcp.tool(
    name="acp_import_model",
    annotations={
        "title": "Import ACP model",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def acp_import_model(params: ImportModelInput) -> str:
    """Launch a headless ACP session and load a composite model.

    Replaces any model already loaded. Returns JSON:
        {"source": str, "modeling_groups": [str], "ply_count": int,
         "materials": [str]}
    """
    try:
        import ansys.acp.core as pyacp

        acp = _STATE["acp"] or pyacp.launch_acp()
        importer = _resolve(acp, "import_model")
        model = importer(params.path, format=params.format) if params.format \
            else importer(params.path)

        _STATE.update({"acp": acp, "model": model, "source": params.path})
        return json.dumps(
            {
                "source": params.path,
                "modeling_groups": list(model.modeling_groups.keys()),
                "ply_count": len(_iter_plies(model)),
                "materials": list(getattr(model, "materials", {}).keys()),
            },
            indent=2,
        )
    except Exception as e:  # noqa: BLE001 - returned to the agent as text
        return _err(e)


@mcp.tool(
    name="acp_get_layup",
    annotations={
        "title": "Read current lay-up",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def acp_get_layup(params: LayupQueryInput) -> str:
    """List modeling plies in stacking order with angle, layer count and material.

    Call this before any set_* tool - ply names are the addressing key.
    'layers' is the stored layer count of each ply, which an inactive ply keeps;
    'total_layers' counts active plies only, so the column and the total will
    disagree whenever a ply has been deactivated. Same convention as
    acp_set_ply_counts and acp_check_layup_rules.
    Returns JSON: {"plies": [{"name","angle","layers","material","active"}],
                   "total_layers": int}
    """
    try:
        model = _require_model()
        if params.modeling_group:
            group = model.modeling_groups[params.modeling_group]
            plies = list(group.modeling_plies.values())
        else:
            plies = _iter_plies(model)

        rows = [_ply_summary(p) for p in plies]
        total = sum(int(r["layers"] or 0) for r in rows if r["active"])

        if params.response_format is ResponseFormat.MARKDOWN:
            lines = ["| # | ply | angle | layers | material | active |",
                     "|---|---|---|---|---|---|"]
            lines += [
                f"| {i} | {r['name']} | {r['angle']} | {r['layers']} | "
                f"{r['material']} | {'yes' if r['active'] else 'NO'} |"
                for i, r in enumerate(rows)
            ]
            lines.append(f"\nTotal layers (active only): {total}")
            return "\n".join(lines)

        return json.dumps({"plies": rows, "total_layers": total}, indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------- #
# Design-variable tools
# --------------------------------------------------------------------------- #


@mcp.tool(
    name="acp_set_ply_angles",
    annotations={
        "title": "Set ply angles",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def acp_set_ply_angles(params: SetPlyAnglesInput) -> str:
    """Set the fibre orientation of one or more modeling plies.

    Does not update or export - apply the full design vector first, then call
    acp_update_and_export once. Returns JSON:
        {"applied": {ply: angle}, "attribute": str, "snapped": bool}
    """
    try:
        model = _require_model()
        by_name = {p.name: p for p in _iter_plies(model)}
        applied: Dict[str, float] = {}
        attr_used = ""

        for name, angle in params.angles.items():
            if name not in by_name:
                raise KeyError(name)
            value = _snap(angle) if params.snap_to_manufacturing else angle
            attr_used = _set_attr(by_name[name], PLY_ANGLE_ATTRS, value)
            applied[name] = value

        return json.dumps(
            {
                "applied": applied,
                "attribute": attr_used,
                "snapped": params.snap_to_manufacturing,
            },
            indent=2,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_set_ply_counts",
    annotations={
        "title": "Set ply layer counts",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def acp_set_ply_counts(params: SetPlyCountsInput) -> str:
    """Set the layer count per modeling ply; 0 deactivates the ply.

    Returns JSON: {"applied": {ply: layers}, "total_layers": int}
    """
    try:
        model = _require_model()
        by_name = {p.name: p for p in _iter_plies(model)}
        applied: Dict[str, int] = {}

        for name, n in params.counts.items():
            if name not in by_name:
                raise KeyError(name)
            ply = by_name[name]
            if n == 0:
                ply.active = False
            else:
                ply.active = True
                _set_attr(ply, PLY_COUNT_ATTRS, n)
            applied[name] = n

        return json.dumps(
            {"applied": applied, "total_layers": len(_flat_stack(model))}, indent=2
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_check_layup_rules",
    annotations={
        "title": "Check manufacturing rules",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def acp_check_layup_rules(params: CheckLayupInput) -> str:
    """Validate the current stack against standard composite design rules.

    Checks mid-plane symmetry, balance, +/-45 outer plies, no more than four
    consecutive same-angle layers, and a minimum fraction in each principal
    direction. Run before exporting - solving an unmanufacturable stack wastes
    an iteration.

    Returns JSON: {"ok": bool, "violations": [str], "fractions": {angle: float},
                   "total_layers": int}
    """
    try:
        model = _require_model()
        stack = _flat_stack(model)
        if not stack:
            return json.dumps(
                {"ok": False, "violations": ["empty stack"], "total_layers": 0},
                indent=2,
            )

        violations: List[str] = []

        if params.require_symmetry and stack != stack[::-1]:
            violations.append("stack is not symmetric about the mid-plane")

        if params.require_balance:
            off_axis = {abs(x) for x in stack if abs(x) not in (0.0, 90.0)}
            for a in sorted(off_axis):
                plus, minus = stack.count(a), stack.count(-a)
                if plus != minus:
                    violations.append(f"unbalanced: {plus}x +{a} vs {minus}x -{a}")

        if params.require_45_outer and abs(abs(stack[0]) - 45.0) > 1e-6:
            violations.append(f"outer ply is {stack[0]}, expected +/-45")

        run, prev = 1, stack[0]
        for a in stack[1:]:
            run = run + 1 if a == prev else 1
            if run > MAX_CONSECUTIVE_SAME_ANGLE:
                violations.append(
                    f"more than {MAX_CONSECUTIVE_SAME_ANGLE} consecutive layers at {a}"
                )
                break
            prev = a

        fractions = {str(a): stack.count(a) / len(stack) for a in sorted(set(stack))}
        for a in PRINCIPAL_DIRECTIONS:
            frac = fractions.get(str(a), 0.0)
            if frac < MIN_FRACTION_PER_DIRECTION:
                violations.append(
                    f"only {frac:.1%} at {a} deg, minimum is "
                    f"{MIN_FRACTION_PER_DIRECTION:.0%}"
                )

        return json.dumps(
            {
                "ok": not violations,
                "violations": violations,
                "fractions": fractions,
                "total_layers": len(stack),
            },
            indent=2,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------- #
# Export tools
# --------------------------------------------------------------------------- #


@mcp.tool(
    name="acp_update_and_export",
    annotations={
        "title": "Update model and export",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def acp_update_and_export(params: ExportInput) -> str:
    """Update the model and write the analysis model, optionally with the shell
    composite definitions consumed downstream by PyDPF-Composites.

    This is the handoff point to the Mechanical MCP. Returns JSON:
        {"analysis_model": str, "composite_definitions": str|null,
         "total_layers": int}
    """
    try:
        model = _require_model()
        _resolve(model, "update")()
        _resolve(model, "export_analysis_model")(params.analysis_model_path)

        comp = None
        if params.composite_definitions_path:
            _resolve(model, "export_composite_definitions")(
                params.composite_definitions_path
            )
            comp = params.composite_definitions_path

        return json.dumps(
            {
                "analysis_model": params.analysis_model_path,
                "composite_definitions": comp,
                "total_layers": len(_flat_stack(model)),
            },
            indent=2,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_save_for_gui",
    annotations={
        "title": "Save .acph5 for GUI inspection",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def acp_save_for_gui(params: SaveInput) -> str:
    """Write the current state to an .acph5 the user can open in ACP-Pre.

    The GUI is a viewer in this workflow, not a driver: edits made there are not
    read back into this session. Returns JSON: {"saved": str}
    """
    try:
        model = _require_model()
        _resolve(model, "save")(params.path)
        return json.dumps({"saved": params.path}, indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# --------------------------------------------------------------------------- #
# GUI bridge (live session) - acp_gui_bridge.py runs inside the ACP-Pre console
# --------------------------------------------------------------------------- #

import socket as _socket

GUI_HOST = "127.0.0.1"
GUI_PORT = 47800
GUI_CONNECT_TIMEOUT = 10.0
GUI_CALL_TIMEOUT = 330.0  # longer than the bridge's 300 s main-thread timeout

# Only used to make the connection error actionable on the local machine.
BRIDGE_HINT = _os.environ.get(
    "ACP_BRIDGE_PATH", "<repo>/acp_gui_bridge.py"
)


def _gui_call(op: str, **payload: Any) -> Dict[str, Any]:
    """Send a command to the live ACP-Pre session. Independent of the headless one."""
    request = {"op": op}
    request.update(payload)

    try:
        conn = _socket.create_connection((GUI_HOST, GUI_PORT), timeout=GUI_CONNECT_TIMEOUT)
    except OSError as e:
        raise AcpError(
            f"Cannot reach the live ACP-Pre bridge ({GUI_HOST}:{GUI_PORT}): {e}. "
            f"Is ACP-Pre open, and was the bridge loaded in its console: "
            f"exec(open(r'{BRIDGE_HINT}').read())"
        ) from e

    try:
        conn.settimeout(GUI_CALL_TIMEOUT)
        conn.sendall(json.dumps(request).encode("utf-8") + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not buf.strip():
        raise AcpError("Empty response from the bridge - the GUI may have crashed.")

    response = json.loads(buf.decode("utf-8"))
    if not response.get("ok"):
        detail = response.get("traceback") or ""
        raise AcpError(f"{response.get('error', 'unknown error')}\n{detail}".strip())
    return response["result"]


class GuiSetAnglesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    angles: Dict[str, float] = Field(
        ..., description="Ply name -> fibre angle in degrees, e.g. {'ModelingPly.1': 45.0}."
    )
    snap_to_manufacturing: bool = Field(default=True)
    update: bool = Field(
        default=True,
        description="Run model.update() afterwards so the GUI redraws. Set false "
        "when applying several changes in a row.",
    )

    @field_validator("angles")
    @classmethod
    def _in_range(cls, v: Dict[str, float]) -> Dict[str, float]:
        for name, a in v.items():
            if not -90.0 <= a <= 90.0:
                raise ValueError(f"{name}: angle {a} outside [-90, 90]")
        return v


class GuiSetCountsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    counts: Dict[str, int] = Field(..., description="Ply name -> layer count (0 deactivates).")
    update: bool = Field(default=True)

    @field_validator("counts")
    @classmethod
    def _nonneg(cls, v: Dict[str, int]) -> Dict[str, int]:
        for name, n in v.items():
            if n < 0 or n > 200:
                raise ValueError(f"{name}: layer count {n} outside [0, 200]")
        return v


class GuiAddPlyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    angles: List[float] = Field(
        ...,
        description="One fibre angle per new ply, in stacking order, e.g. "
        "[45, 0, 90].",
        min_length=1,
    )
    modeling_group: Optional[str] = Field(
        default=None,
        description="Group to append to. Required only when the model has more "
        "than one modeling group.",
    )
    copy_from: Optional[str] = Field(
        default=None,
        description="Ply whose material and oriented selection set the new "
        "plies inherit. Defaults to the last ply in the group.",
    )
    layers: int = Field(default=1, description="Layer count for each new ply.")
    snap_to_manufacturing: bool = Field(default=True)
    update: bool = Field(default=True)

    @field_validator("angles")
    @classmethod
    def _in_range(cls, v: List[float]) -> List[float]:
        for a in v:
            if not -90.0 <= a <= 90.0:
                raise ValueError(f"angle {a} outside [-90, 90]")
        return v

    @field_validator("layers")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1 or v > 200:
            raise ValueError(f"layer count {v} outside [1, 200]")
        return v


class GuiSaveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: Optional[str] = Field(
        default=None,
        description="Target .acph5 path. Omit to save over the file currently open "
        "in the GUI.",
    )


class GuiExportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    analysis_model_path: Optional[str] = Field(default=None)
    composite_definitions_path: Optional[str] = Field(default=None)


class GuiExecInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(
        ...,
        description="Python executed inside the live ACP-Pre session on the GUI "
        "thread. 'db' and 'model' are in scope. Assign to 'result' to return a value.",
        min_length=1,
    )


@mcp.tool(
    name="acp_gui_status",
    annotations={"title": "Check live ACP-Pre bridge", "readOnlyHint": True,
                 "idempotentHint": True, "openWorldHint": False},
)
async def acp_gui_status(params: Optional[Dict[str, Any]] = None) -> str:
    """Check whether the live ACP-Pre GUI bridge is reachable and which model is open.

    Call this before any other acp_gui_* tool. Returns JSON from the bridge.
    """
    try:
        return json.dumps(_gui_call("ping"), indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_get_layup",
    annotations={"title": "Read lay-up from live GUI", "readOnlyHint": True,
                 "idempotentHint": True, "openWorldHint": False},
)
async def acp_gui_get_layup(params: LayupQueryInput) -> str:
    """List modeling plies from the model open in the live ACP-Pre session.

    This reads the GUI's model, not the headless session. Ply names are the
    addressing key for acp_gui_set_*.

    Deactivating a ply with acp_gui_set_ply_counts 0 clears its 'active' flag
    but leaves 'layers' at its stored value, so read the active column before
    concluding a ply still contributes to the laminate.
    """
    try:
        result = _gui_call("get_layup", modeling_group=params.modeling_group)
        if params.response_format is ResponseFormat.MARKDOWN:
            rows = result["plies"]
            lines = ["| # | ply | angle | layers | material | active | status |",
                     "|---|---|---|---|---|---|---|"]
            lines += [
                f"| {i} | {r['name']} | {r['angle']} | {r['layers']} | "
                f"{r['material']} | {'yes' if r.get('active', True) else 'NO'} | "
                f"{r['status']} |"
                for i, r in enumerate(rows)
            ]
            lines.append(f"\nTotal layers (active only): {result['total_layers']}")
            return "\n".join(lines)
        return json.dumps(result, indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_set_ply_angles",
    annotations={"title": "Set ply angles in live GUI", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def acp_gui_set_ply_angles(params: GuiSetAnglesInput) -> str:
    """Set fibre orientations in the live ACP-Pre session; the GUI redraws immediately.

    Edits the model open in the GUI - unsaved work there is affected. Runs
    model.update() unless update=false.
    """
    try:
        angles = {
            name: (_snap(a) if params.snap_to_manufacturing else a)
            for name, a in params.angles.items()
        }
        result = _gui_call("set_ply_angles", angles=angles, update=params.update)
        result["snapped"] = params.snap_to_manufacturing
        return json.dumps(result, indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_set_ply_counts",
    annotations={"title": "Set layer counts in live GUI", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def acp_gui_set_ply_counts(params: GuiSetCountsInput) -> str:
    """Set layer counts in the live ACP-Pre session; 0 deactivates the ply."""
    try:
        return json.dumps(
            _gui_call("set_ply_counts", counts=params.counts, update=params.update),
            indent=2,
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_add_ply",
    annotations={"title": "Add modeling plies in live GUI", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
)
async def acp_gui_add_ply(params: GuiAddPlyInput) -> str:
    """Append new modeling plies to the model open in the live ACP-Pre session.

    Material and oriented selection set are inherited from an existing ply, so
    the new plies land on the same surface with the same rosette. The other
    acp_gui_* tools only edit plies that already exist; this is the one that
    creates them.
    """
    try:
        angles = (
            [_snap(a) for a in params.angles]
            if params.snap_to_manufacturing
            else list(params.angles)
        )
        result = _gui_call(
            "add_ply",
            angles=angles,
            modeling_group=params.modeling_group,
            copy_from=params.copy_from,
            layers=params.layers,
            update=params.update,
        )
        result["snapped"] = params.snap_to_manufacturing
        return json.dumps(result, indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_save",
    annotations={"title": "Save the live GUI model", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def acp_gui_save(params: GuiSaveInput) -> str:
    """Save the model open in the live ACP-Pre session.

    Omitting path overwrites the file the user currently has open - prefer an
    explicit path unless the user asked to save in place.
    """
    try:
        return json.dumps(_gui_call("save", path=params.path), indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_export",
    annotations={"title": "Export from live GUI", "readOnlyHint": False,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
)
async def acp_gui_export(params: GuiExportInput) -> str:
    """Export the analysis model and/or shell composite definitions from the live session.

    Handoff point to the Mechanical MCP and PyDPF-Composites.
    """
    try:
        out: Dict[str, Any] = {}
        if params.analysis_model_path:
            out.update(_gui_call("export_analysis_model", path=params.analysis_model_path))
        if params.composite_definitions_path:
            out.update(
                _gui_call("export_composite_definitions",
                          path=params.composite_definitions_path)
            )
        if not out:
            return "Error: at least one output path is required."
        return json.dumps(out, indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@mcp.tool(
    name="acp_gui_exec",
    annotations={"title": "Run code in live ACP-Pre", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
)
async def acp_gui_exec(params: GuiExecInput) -> str:
    """Run Python inside the live ACP-Pre session (legacy console API, 'db' in scope).

    For exploration and one-off operations the typed tools do not cover. Prefer
    the typed tools for routine lay-up edits.
    """
    try:
        return json.dumps(_gui_call("exec", code=params.code), indent=2)
    except Exception as e:  # noqa: BLE001
        return _err(e)


if __name__ == "__main__":
    mcp.run()

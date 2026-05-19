"""Recursive walker for the gphoto2 widget tree.

The widget tree is a forest of sections (containers) and leaves (text /
choice / range / toggle / date). We flatten it into a JSON-friendly dict
keyed by full path:

    {
      "path": "/main/imgsettings/iso",
      "label": "ISO",
      "type": "radio",
      "value": "400",
      "choices": ["100", "200", "400", "800"],
      "readonly": false
    }
"""

from __future__ import annotations

from typing import Any

# Widget type ids in libgphoto2 (mirrored so we don't need a runtime gphoto2 import).
WIDGET_TYPES: dict[int, str] = {
    0: "window",
    1: "section",
    2: "text",
    3: "range",
    4: "toggle",
    5: "radio",
    6: "menu",
    7: "button",
    8: "date",
}


def _widget_type(w: Any) -> str:
    try:
        t = w.get_type()
    except Exception:  # noqa: BLE001
        return "unknown"
    return WIDGET_TYPES.get(int(t), str(t))


def widget_tree_to_dict(root: Any) -> dict[str, Any]:
    """Walk a gphoto2 widget tree → flat dict keyed by full path."""
    out: dict[str, Any] = {}
    _walk(root, "", out)
    return out


def _walk(widget: Any, parent_path: str, out: dict[str, Any]) -> None:
    name = _safe(widget.get_name)
    path = f"{parent_path}/{name}" if parent_path else f"/{name}"
    wtype = _widget_type(widget)
    label = _safe(widget.get_label)
    readonly = _safe(widget.get_readonly, default=False)

    if wtype in ("window", "section"):
        out[path] = {
            "path": path,
            "label": label,
            "type": wtype,
            "readonly": bool(readonly) if readonly is not None else False,
        }
        try:
            count = widget.count_children()
        except Exception:  # noqa: BLE001
            count = 0
        for i in range(count):
            child = widget.get_child(i)
            _walk(child, path, out)
        return

    value = _safe(widget.get_value)
    entry: dict[str, Any] = {
        "path": path,
        "label": label,
        "type": wtype,
        "value": value,
        "readonly": bool(readonly) if readonly is not None else False,
    }
    if wtype in ("radio", "menu"):
        try:
            entry["choices"] = [widget.get_choice(i) for i in range(widget.count_choices())]
        except Exception:  # noqa: BLE001
            entry["choices"] = []
    if wtype == "range":
        try:
            lo, hi, step = widget.get_range()
            entry["min"] = lo
            entry["max"] = hi
            entry["step"] = step
        except Exception:  # noqa: BLE001
            pass
    out[path] = entry


def _safe(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default

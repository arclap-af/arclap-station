"""EXIF extraction helper — shared between API capture and scheduled
capture so every photo gets the same metadata regardless of which path
took it.

Pillow is optional at runtime — if it's missing or the file isn't a
real image, we return an empty dict + None dims. We never raise:
capture itself already succeeded by the time we get here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def extract_exif(path: Path) -> tuple[dict[str, Any], int | None, int | None]:
    """Read EXIF from `path`. Returns (exif_dict, width, height)."""
    try:
        from PIL import ExifTags, Image  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {}, None, None
    try:
        with Image.open(str(path)) as img:
            w, h = img.size
            raw = getattr(img, "_getexif", lambda: None)() or {}
    except Exception:  # noqa: BLE001
        return {}, None, None

    tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in raw.items()}
    out: dict[str, Any] = {}

    # ISO (two possible tag names depending on EXIF version).
    iso = tags.get("ISOSpeedRatings") or tags.get("PhotographicSensitivity")
    if iso is not None:
        try:
            out["iso"] = int(iso if not isinstance(iso, tuple | list) else iso[0])
        except (TypeError, ValueError):
            pass

    # Shutter speed — ExposureTime is Fraction-like.
    exp = tags.get("ExposureTime")
    if exp is not None:
        try:
            if isinstance(exp, tuple) and len(exp) == 2 and exp[1]:
                out["shutter"] = (
                    f"{exp[0]}/{exp[1]}" if exp[0] < exp[1] else f"{exp[0] / exp[1]:.1f}"
                )
            elif hasattr(exp, "numerator"):
                out["shutter"] = (
                    f"{exp.numerator}/{exp.denominator}"
                    if exp.numerator < exp.denominator
                    else f"{float(exp):.1f}"
                )
            else:
                v = float(exp)
                out["shutter"] = f"1/{int(round(1 / v))}" if v < 1 else f"{v:.1f}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Aperture — FNumber is float-like, ApertureValue is APEX.
    fno = tags.get("FNumber") or tags.get("ApertureValue")
    if fno is not None:
        try:
            if isinstance(fno, tuple) and len(fno) == 2 and fno[1]:
                f = fno[0] / fno[1]
            elif hasattr(fno, "numerator"):
                f = float(fno)
            else:
                f = float(fno)
            out["aperture"] = f"f/{f:.1f}"
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    # Camera identity — useful for the Gallery side panel.
    if tags.get("Make"):
        out["make"] = str(tags["Make"]).strip()
    if tags.get("Model"):
        out["model"] = str(tags["Model"]).strip()
    if tags.get("LensModel"):
        out["lens"] = str(tags["LensModel"]).strip()
    if tags.get("DateTimeOriginal"):
        out["taken_at"] = str(tags["DateTimeOriginal"]).strip()

    return out, w, h

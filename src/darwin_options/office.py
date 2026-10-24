"""The 3D office layout the owner edits in the dashboard's Teams panel (names, colours, descriptions,
parents, levels and departments). Display only: nothing here changes how Darwin trades."""
from __future__ import annotations

import re

OFFICE_KEY = "office_layout"

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-z0-9_-]{1,40}$")
LEVELS = ("top", "bottom")
MAX_TEAMS = 40
MAX_DEPTS = 20


class LayoutError(ValueError):
    pass


def _text(v, limit: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise LayoutError("names and descriptions must be text")
    v = v.strip()
    return v[:limit] if v else None


def clean_layout(raw: dict) -> dict:
    """Checks a layout from the dashboard and keeps only the known fields."""
    if not isinstance(raw, dict):
        raise LayoutError("layout must be an object")
    depts_in = raw.get("departments", [])
    teams_in = raw.get("teams", {})
    if not isinstance(depts_in, list) or not isinstance(teams_in, dict):
        raise LayoutError("layout needs 'teams' (object) and 'departments' (list)")
    if len(depts_in) > MAX_DEPTS or len(teams_in) > MAX_TEAMS:
        raise LayoutError("too many teams or departments")

    departments, seen = [], set()
    for d in depts_in:
        if not isinstance(d, dict) or not _ID.match(str(d.get("id", ""))):
            raise LayoutError("each department needs an id (lower-case letters, digits, - or _)")
        if d["id"] in seen:
            raise LayoutError(f"department '{d['id']}' appears twice")
        seen.add(d["id"])
        name = _text(d.get("name"), 40)
        if not name:
            raise LayoutError("each department needs a name")
        color = d.get("color")
        if not isinstance(color, str) or not _HEX.match(color):
            raise LayoutError(f"department '{name}' needs a colour like #22b8cf")
        level = d.get("level", "bottom")
        if level not in LEVELS:
            raise LayoutError("level must be top or bottom")
        departments.append({"id": d["id"], "name": name, "color": color.lower(), "level": level})

    teams = {}
    for tid, t in teams_in.items():
        if not _ID.match(str(tid)) or not isinstance(t, dict):
            raise LayoutError(f"bad team entry '{tid}'")
        out = {}
        for key, limit in (("name", 40), ("job", 300)):
            v = _text(t.get(key), limit)
            if v:
                out[key] = v
        color = t.get("color")
        if color:
            if not isinstance(color, str) or not _HEX.match(color):
                raise LayoutError(f"team '{tid}' colour must look like #22b8cf")
            out["color"] = color.lower()
        parent = t.get("parent")
        if parent:
            if not _ID.match(str(parent)):
                raise LayoutError(f"team '{tid}' has a bad parent")
            if parent != tid:
                out["parent"] = parent
        level = t.get("level")
        if level:
            if level not in LEVELS:
                raise LayoutError("level must be top or bottom")
            out["level"] = level
        dept = t.get("dept")
        if dept:
            if dept not in seen:
                raise LayoutError(f"team '{tid}' is in unknown department '{dept}'")
            out["dept"] = dept
        teams[tid] = out
    return {"teams": teams, "departments": departments}

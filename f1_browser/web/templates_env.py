"""Shared Jinja2Templates instance so globals are available in all routes."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="web/templates")


def _stat_color(value: float) -> str:
    """Map a 0–1 value to an HSL colour: red (0) → yellow (0.5) → green (1)."""
    try:
        v = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return "#6b7280"
    hue = int(v * 120)
    return f"hsl({hue}, 70%, 55%)"


templates.env.globals["stat_color"] = _stat_color

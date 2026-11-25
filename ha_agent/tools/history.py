from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional

from .. import ha_client
from ..config import is_valid_entity_id
from . import Attachment, ToolContext, tool


def _temperature_chart_svg(points, entity_id, name, unit, start, end):
    width, height = 900, 500
    left, right, top, bottom = 78, 28, 62, 62
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [value for _, value in points]
    low, high = min(values), max(values)
    padding = max((high - low) * 0.1, 0.5)
    low -= padding
    high += padding
    duration = max((end - start).total_seconds(), 1)

    def x_position(point_time):
        return left + (point_time - start).total_seconds() / duration * plot_width

    def y_position(value):
        return top + (high - value) / (high - low) * plot_height

    grid = []
    for index in range(5):
        value = high - (high - low) * index / 4
        y = top + plot_height * index / 4
        grid.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            'stroke="#d1d5db" stroke-width="1"/>'
            f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#374151">{value:.1f}</text>'
        )

    polyline = " ".join(
        f"{x_position(point_time):.1f},{y_position(value):.1f}"
        for point_time, value in points
    )
    start_label = start.astimezone().strftime("%Y-%m-%d %H:%M")
    end_label = end.astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="{left}" y="28" font-size="18" font-weight="bold" '
        f'fill="#111827">{escape(str(name))} ({escape(entity_id)})</text>'
        f'{"".join(grid)}'
        f'<polyline points="{polyline}" fill="none" stroke="#2563eb" '
        'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<text x="{left}" y="{height-24}" font-size="12" fill="#374151">'
        f'{escape(start_label)}</text>'
        f'<text x="{width-right}" y="{height-24}" text-anchor="end" '
        f'font-size="12" fill="#374151">{escape(end_label)}</text>'
        f'<text x="20" y="{top+plot_height/2}" text-anchor="middle" '
        f'font-size="13" fill="#374151" transform="rotate(-90 20 {top+plot_height/2})">'
        f'Температура ({escape(str(unit))})</text>'
        "</svg>"
    )


@tool(
    "plot_temperature_history",
    "Побудувати графік історії температури для сенсора sensor або термостата climate за останні години. Графік надсилається вкладенням без локального збереження.",
    {
        "entity_id": {"type": "string"},
        "hours": {"type": "integer", "minimum": 1, "maximum": 168},
    },
    ["entity_id"],
)
def plot_temperature_history(
    entity_id,
    hours=24,
    context: Optional[ToolContext] = None,
):
    if not is_valid_entity_id(entity_id):
        return {"error": "некоректний entity_id"}
    domain = entity_id.split(".", 1)[0]
    if domain not in {"sensor", "climate"}:
        return {
            "error": (
                "для графіка температури потрібен entity_id домену sensor або climate"
            )
        }
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 168:
        return {"error": "hours має бути цілим числом від 1 до 168"}

    current_state = ha_client.get_state(context.settings, entity_id)
    if current_state is None:
        return {"error": "сутність не знайдено"}
    current_attributes = current_state.get("attributes", {})
    unit = (
        current_attributes.get("temperature_unit")
        if domain == "climate"
        else current_attributes.get("unit_of_measurement")
    )
    is_temperature_sensor = (
        current_attributes.get("device_class") == "temperature"
        or unit in {"°C", "°F", "K", "C", "F"}
    )
    if domain == "sensor" and not is_temperature_sensor:
        return {"error": "ця sensor-сутність не позначена як температура"}
    if domain == "climate" and "current_temperature" not in current_attributes:
        return {"error": "у climate-сутності немає поточної температури"}
    if not unit:
        unit = "одиницю не вказано"

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    history = ha_client.get_history(context.settings, entity_id, start, end)
    if not isinstance(history, list) or not history or not isinstance(history[0], list):
        return {"error": "Home Assistant не повернув історію для цієї сутності"}

    points = []
    for record in history[0]:
        attributes = record.get("attributes", {})
        raw_value = (
            attributes.get("current_temperature")
            if domain == "climate"
            else record.get("state")
        )
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        timestamp = record.get("last_updated") or record.get("last_changed")
        if not timestamp:
            continue
        try:
            point_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if point_time.tzinfo is None:
            point_time = point_time.replace(tzinfo=timezone.utc)
        points.append((max(point_time, start), value))

    if not points:
        return {"error": "в історії немає числових значень температури за цей період"}

    points.sort(key=lambda point: point[0])
    name = current_attributes.get("friendly_name", entity_id)
    chart_svg = _temperature_chart_svg(
        points, entity_id, name, unit, start, end
    )
    minimum = min(value for _, value in points)
    maximum = max(value for _, value in points)
    caption = f"Графік температури: {len(points)} точок, {minimum}–{maximum} {unit}."
    return {
        "ok": True,
        "points": len(points),
        "unit": unit,
        "minimum": minimum,
        "maximum": maximum,
        "_attachment": Attachment(
            filename="temperature_history.svg",
            media_type="image/svg+xml",
            content=chart_svg.encode("utf-8"),
            caption=caption,
        ),
    }

from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import charts, ha_client
from ..config import is_valid_entity_id
from . import Attachment, ToolContext, tool


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
    chart_svg = charts.temperature_chart_svg(
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

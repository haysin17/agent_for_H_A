import math
from typing import Optional

from .. import ha_client
from ..config import (
    ALLOWED_SERVICES,
    CONFIRM_DOMAINS,
    CONTROL_DOMAINS,
    MAX_SET_TEMPERATURE,
    MIN_SET_TEMPERATURE,
    SERVICE_DATA_KEYS,
    is_valid_entity_id,
)
from . import ToolContext, tool


@tool(
    "call_service",
    "Виконати дію: turn_on, turn_off, toggle або set_temperature. Додаткові параметри (наприклад temperature чи brightness_pct) передавай у data.",
    {
        "domain": {"type": "string", "enum": sorted(CONTROL_DOMAINS)},
        "service": {"type": "string", "enum": sorted(ALLOWED_SERVICES)},
        "entity_id": {"type": "string"},
        "data": {"type": "object"},
    },
    ["domain", "service", "entity_id"],
)
def call_service(
    domain,
    service,
    entity_id,
    data=None,
    context: Optional[ToolContext] = None,
):
    if context is None:
        return {"error": "контекст інструмента не налаштовано"}
    if domain not in CONTROL_DOMAINS or service not in ALLOWED_SERVICES:
        return {"error": "дія заборонена"}
    if service == "set_temperature" and domain != "climate":
        return {"error": "set_temperature дозволено лише для climate"}
    if not is_valid_entity_id(entity_id):
        return {"error": "некоректний entity_id"}
    if not entity_id.startswith(domain + "."):
        return {"error": "entity_id не відповідає домену"}
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return {"error": "data має бути об'єктом"}

    allowed_data_keys = SERVICE_DATA_KEYS.get((domain, service), frozenset())
    unexpected_keys = set(data) - allowed_data_keys
    if unexpected_keys:
        return {
            "error": "заборонені параметри data: "
            + ", ".join(sorted(str(key) for key in unexpected_keys))
        }
    if service == "set_temperature":
        temperature = data.get("temperature")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not MIN_SET_TEMPERATURE <= temperature <= MAX_SET_TEMPERATURE
        ):
            return {
                "error": (
                    f"temperature має бути числом від {MIN_SET_TEMPERATURE} "
                    f"до {MAX_SET_TEMPERATURE}"
                )
            }
    if "brightness_pct" in data:
        brightness = data["brightness_pct"]
        if (
            isinstance(brightness, bool)
            or not isinstance(brightness, (int, float))
            or not math.isfinite(brightness)
            or not 0 <= brightness <= 100
        ):
            return {"error": "brightness_pct має бути числом від 0 до 100"}

    confirmation_callback = (
        context.confirmation_callback if context is not None else None
    )
    if domain in CONFIRM_DOMAINS:
        if confirmation_callback is None:
            answer = input(
                f"  [підтвердження] {domain}.{service} {entity_id} "
                f"{data or ''} (y/n): "
            )
            if answer.strip().lower() != "y":
                return {"error": "користувач відхилив дію"}
        elif not confirmation_callback(domain, service, entity_id, data):
            return {"error": "користувач не підтвердив дію"}

    ha_client.call_service(
        context.settings,
        domain,
        service,
        {**data, "entity_id": entity_id},
    )
    return {"ok": True}

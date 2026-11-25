import re
from typing import Optional

from .. import ha_client
from ..config import READ_DOMAINS, is_valid_entity_id
from . import ToolContext, tool


def _normalize_search(value):
    return re.sub(r"[\s_-]+", "", value.casefold())


@tool(
    "check_ha_connection",
    "Перевірити поточне з'єднання з Home Assistant.",
    {},
)
def check_ha_connection(context: Optional[ToolContext] = None):
    ha_client.check_api_connection(context.settings)
    return {"connected": True}


@tool(
    "list_entities",
    "Список до 100 сутностей домену зі станом. Можна відфільтрувати за entity_id або назвою; відповідь містить truncated, якщо є ще результати.",
    {
        "domain": {"type": "string", "enum": sorted(READ_DOMAINS)},
        "search": {"type": "string", "maxLength": 100},
    },
    ["domain"],
)
def list_entities(domain, search=None, context: Optional[ToolContext] = None):
    if domain not in READ_DOMAINS:
        return {"error": f"домен {domain} заборонено"}
    if search is not None and (not isinstance(search, str) or len(search) > 100):
        return {"error": "search має бути текстом до 100 символів"}
    states = [
        state for state in ha_client.get_states(context.settings)
        if state["entity_id"].startswith(domain + ".")
    ]
    if search:
        needle = _normalize_search(search)
        states = [
            state for state in states
            if needle in _normalize_search(state["entity_id"])
            or needle in _normalize_search(
                str(state["attributes"].get("friendly_name") or "")
            )
        ]
    return {
        "entities": [
            {
                "entity_id": state["entity_id"],
                "state": state["state"],
                "name": state["attributes"].get("friendly_name"),
            }
            for state in states[:100]
        ],
        "truncated": len(states) > 100,
        "total_matching": len(states),
    }


@tool(
    "list_temperature_entities",
    "Повернути поточні показники температурних sensor і climate-сутностей. "
    "Для температури в усьому будинку викликай без search; search використовуй "
    "лише якщо потрібна конкретна кімната.",
    {"search": {"type": "string", "maxLength": 100}},
)
def list_temperature_entities(search=None, context: Optional[ToolContext] = None):
    if search is not None and (not isinstance(search, str) or len(search) > 100):
        return {"error": "search має бути текстом до 100 символів"}

    needle = _normalize_search(search) if search else None
    results = []
    for state in ha_client.get_states(context.settings):
        entity_id = state["entity_id"]
        domain = entity_id.split(".", 1)[0]
        attributes = state.get("attributes", {})
        if domain == "sensor":
            unit = attributes.get("unit_of_measurement")
            if (
                attributes.get("device_class") != "temperature"
                and unit not in {"°C", "°F", "K"}
            ):
                continue
            temperature = state["state"]
        elif domain == "climate" and "current_temperature" in attributes:
            temperature = attributes["current_temperature"]
            unit = attributes.get("temperature_unit")
        else:
            continue

        name = attributes.get("friendly_name") or entity_id
        if needle and needle not in _normalize_search(
            f"{entity_id} {name}"
        ):
            continue
        results.append({
            "entity_id": entity_id,
            "name": name,
            "temperature": temperature,
            "unit": unit,
        })

    return {
        "entities": results[:100],
        "truncated": len(results) > 100,
        "total_matching": len(results),
    }


@tool(
    "get_state",
    "Поточний стан і безпечні атрибути однієї сутності за entity_id. Для camera повертається лише стан, не зображення.",
    {"entity_id": {"type": "string"}},
    ["entity_id"],
)
def get_state(entity_id, context: Optional[ToolContext] = None):
    if not is_valid_entity_id(entity_id):
        return {"error": "некоректний entity_id"}
    if entity_id.split(".", 1)[0] not in READ_DOMAINS:
        return {"error": "ця сутність недоступна агенту"}
    state = ha_client.get_state(context.settings, entity_id)
    if state is None:
        return {"error": "сутність не знайдено"}
    attributes = state["attributes"]
    if entity_id.startswith("camera."):
        attributes = {
            key: attributes[key]
            for key in ("friendly_name", "supported_features")
            if key in attributes
        }
    return {"entity_id": entity_id, "state": state["state"], "attributes": attributes}


@tool(
    "get_weather",
    "Повернути поточну зовнішню температуру, погодні умови та прогноз weather-сутності. Використовуй для питань про погоду, не для заданої температури термостата. Тип прогнозу: hourly (за замовчуванням), daily або twice_daily.",
    {
        "entity_id": {"type": "string"},
        "forecast_type": {
            "type": "string",
            "enum": ["hourly", "daily", "twice_daily"],
            "default": "hourly",
        },
    },
    ["entity_id"],
)
def get_weather(
    entity_id,
    forecast_type="hourly",
    context: Optional[ToolContext] = None,
):
    if not is_valid_entity_id(entity_id) or not entity_id.startswith("weather."):
        return {"error": "потрібен коректний entity_id домену weather"}
    if forecast_type not in {"hourly", "daily", "twice_daily"}:
        return {"error": "forecast_type має бути hourly, daily або twice_daily"}

    current_state = ha_client.get_state(context.settings, entity_id)
    if current_state is None:
        return {"error": "погодну сутність не знайдено"}
    attributes = current_state.get("attributes", {})
    current_fields = (
        "temperature",
        "temperature_unit",
        "apparent_temperature",
        "humidity",
        "pressure",
        "pressure_unit",
        "wind_speed",
        "wind_speed_unit",
        "precipitation",
        "precipitation_unit",
    )

    response_data = ha_client.call_service(
        context.settings,
        "weather",
        "get_forecasts",
        {"entity_id": entity_id, "type": forecast_type},
        return_response=True,
    )
    service_response = (
        response_data.get("service_response")
        if isinstance(response_data, dict)
        else None
    )
    entity_response = (
        service_response.get(entity_id)
        if isinstance(service_response, dict)
        else None
    )
    forecast = (
        entity_response.get("forecast")
        if isinstance(entity_response, dict)
        else None
    )
    if not isinstance(forecast, list):
        return {"error": "Home Assistant не повернув прогноз для цієї сутності"}

    forecast_fields = (
        "datetime",
        "condition",
        "temperature",
        "templow",
        "apparent_temperature",
        "precipitation",
        "precipitation_probability",
        "wind_speed",
        "humidity",
    )
    forecast_items = [
        {key: item[key] for key in forecast_fields if key in item}
        for item in forecast
        if isinstance(item, dict)
    ]
    return {
        "entity_id": entity_id,
        "condition": current_state.get("state"),
        "current": {
            key: attributes[key] for key in current_fields if key in attributes
        },
        "forecast_type": forecast_type,
        "forecast": forecast_items[:24],
        "truncated": len(forecast_items) > 24,
    }

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ha_agent import ha_client
from ha_agent.agent import (
    _serialize_tool_output,
    deliver_attachment,
    run_agent,
    trim_history,
)
from ha_agent.config import DEFAULT_OLLAMA_CA_CERT, Settings
from ha_agent.telegram_bot import TelegramBot, _format_exception_for_log
from ha_agent.tools import (
    Attachment,
    REGISTRY,
    ToolContext,
    invoke_tool,
    ollama_tools,
)
from ha_agent.tools.control import call_service
from ha_agent.tools.entities import (
    get_state,
    get_weather,
    list_entities,
    list_temperature_entities,
)


def make_context(confirmation_callback=None):
    return ToolContext(
        settings=Settings(ha_url="http://ha", ha_token="test-token"),
        confirmation_callback=confirmation_callback,
    )


def test_ca_certificate_path_defaults_to_configured_file(monkeypatch):
    monkeypatch.delenv("OLLAMA_CA_CERT", raising=False)
    assert Settings.from_env().ollama_ca_cert == DEFAULT_OLLAMA_CA_CERT

    monkeypatch.setenv("OLLAMA_CA_CERT", r"D:\custom\ca.pem")
    assert Settings.from_env().ollama_ca_cert == r"D:\custom\ca.pem"


def test_schemas_and_implementations_are_registered_together():
    names = {tool["function"]["name"] for tool in ollama_tools()}
    assert names == set(REGISTRY)


def test_oversized_entity_output_is_trimmed_to_valid_json():
    output = {
        "entities": [
            {"entity_id": f"sensor.item_{index}", "name": "x" * 1000}
            for index in range(20)
        ],
        "truncated": False,
        "total_matching": 20,
    }

    serialized = _serialize_tool_output(output, 6000)
    compact = json.loads(serialized)

    assert len(serialized) <= 6000
    assert compact["truncated"]
    assert compact["returned"] == len(compact["entities"])
    assert compact["returned"] < output["total_matching"]


def test_attachment_dispatch_is_interface_agnostic():
    attachment = Attachment(
        filename="chart.svg",
        media_type="image/svg+xml",
        content=b"<svg/>",
        caption="chart",
    )
    output = {"ok": True, "_attachment": attachment}
    delivered = []

    result = deliver_attachment(output, delivered.append)

    assert delivered == [attachment]
    assert result == {"ok": True}


def test_history_trimming_keeps_system_and_complete_user_turn():
    history = [{"role": "system", "content": "system"}]
    for index in range(12):
        history.extend([
            {"role": "user", "content": f"request {index}"},
            {"role": "assistant", "content": "tool call"},
            {"role": "tool", "content": "{}"},
            {"role": "assistant", "content": "answer"},
        ])

    trim_history(history, 12)

    assert len(history) <= 12
    assert history[0]["role"] == "system"
    assert history[1]["role"] == "user"


def test_history_trimming_preserves_an_oversized_current_turn():
    history = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "request"},
    ]
    history.extend({"role": "tool", "content": "{}"} for _ in range(20))
    history.append({"role": "assistant", "content": "answer"})

    trim_history(history, 10)

    assert history[0]["role"] == "system"
    assert history[1]["role"] == "user"
    assert len(history) == 23


def test_service_data_cannot_override_target_entity():
    with patch.object(ha_client.requests, "post") as post:
        result = call_service(
            "light",
            "turn_on",
            "light.room",
            {"entity_id": "light.all"},
            context=make_context(),
        )

    assert "error" in result
    post.assert_not_called()


def test_temperature_and_domain_validation():
    context = make_context(confirmation_callback=lambda *_: True)
    wrong_domain = call_service(
        "switch",
        "set_temperature",
        "switch.heater",
        {"temperature": 20},
        context=context,
    )
    too_hot = call_service(
        "climate",
        "set_temperature",
        "climate.room",
        {"temperature": 99},
        context=context,
    )
    mismatched_entity = call_service(
        "light",
        "turn_on",
        "switch.room",
        {},
        context=context,
    )

    assert "error" in wrong_domain
    assert "error" in too_hot
    assert "error" in mismatched_entity


def test_negative_brightness_is_rejected():
    with patch.object(ha_client.requests, "post") as post:
        result = call_service(
            "light",
            "turn_on",
            "light.room",
            {"brightness_pct": -5},
            context=make_context(),
        )

    assert "error" in result
    post.assert_not_called()


def test_path_components_are_rejected_before_home_assistant_request():
    with patch.object(ha_client, "get_state") as get:
        result = get_state("sensor.room/../../config", context=make_context())

    assert "error" in result
    get.assert_not_called()


@pytest.mark.parametrize(
    "exception_type",
    [ha_client.requests.ConnectionError, ha_client.requests.Timeout],
)
def test_home_assistant_connection_errors_are_reported_to_the_model(
    exception_type,
):
    with patch.object(
        ha_client.requests,
        "get",
        side_effect=exception_type("unreachable"),
    ):
        result = invoke_tool(
            "get_state",
            {"entity_id": "sensor.room"},
            make_context(),
        )

    assert result["error_code"] == "home_assistant_unavailable"
    assert "Home Assistant" in result["error"]


def test_health_check_reports_current_home_assistant_connection():
    response = ha_client.requests.Response()
    response.status_code = 200
    with patch.object(
        ha_client.requests,
        "get",
        return_value=response,
    ) as get:
        result = invoke_tool(
            "check_ha_connection",
            {},
            make_context(),
        )

    assert result == {"connected": True}
    get.assert_called_once_with(
        "http://ha/api/",
        headers={"Authorization": "Bearer test-token"},
        timeout=10,
    )


def test_entity_search_matches_room_names_with_spaces_and_underscores():
    states = [
        {
            "entity_id": "sensor.living_room_temperature",
            "state": "21",
            "attributes": {"friendly_name": "Living Room temperature"},
        },
        {
            "entity_id": "sensor.bathroom_temperature",
            "state": "23",
            "attributes": {"friendly_name": "Bathroom temperature"},
        },
    ]
    with patch.object(ha_client, "get_states", return_value=states):
        result = list_entities(
            "sensor",
            search="living room",
            context=make_context(),
        )

    assert [entity["entity_id"] for entity in result["entities"]] == [
        "sensor.living_room_temperature"
    ]


def test_temperature_listing_filters_sensor_and_climate_entities():
    states = [
        {
            "entity_id": "sensor.room_temp",
            "state": "21.5",
            "attributes": {
                "friendly_name": "Вітальня",
                "device_class": "temperature",
                "unit_of_measurement": "°C",
            },
        },
        {
            "entity_id": "sensor.room_humidity",
            "state": "45",
            "attributes": {
                "friendly_name": "Вологість",
                "device_class": "humidity",
                "unit_of_measurement": "%",
            },
        },
        {
            "entity_id": "climate.living_room",
            "state": "heat",
            "attributes": {
                "friendly_name": "Термостат вітальні",
                "current_temperature": 20.5,
                "temperature_unit": "°C",
            },
        },
    ]
    with patch.object(ha_client, "get_states", return_value=states):
        result = list_temperature_entities(context=make_context())

    assert result["total_matching"] == 2
    assert [entity["entity_id"] for entity in result["entities"]] == [
        "sensor.room_temp",
        "climate.living_room",
    ]
    assert result["entities"][0]["temperature"] == "21.5"
    assert result["entities"][1]["temperature"] == 20.5


def test_agent_returns_model_response_after_home_assistant_disconnect():
    function = SimpleNamespace(
        name="get_state",
        arguments={"entity_id": "sensor.room"},
    )
    tool_call = SimpleNamespace(function=function)
    responses = [
        SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[tool_call])
        ),
        SimpleNamespace(
            message=SimpleNamespace(
                content="Зараз немає зв'язку з Home Assistant.",
                tool_calls=[],
            )
        ),
    ]
    tool_result = {}

    class FakeClient:
        def chat(self, **kwargs):
            if len(responses) == 1:
                tool_result["message"] = kwargs["messages"][-1]
            return responses.pop(0)

    with patch.object(
        ha_client.requests,
        "get",
        side_effect=ha_client.requests.ConnectionError("unreachable"),
    ):
        answer = run_agent(
            "Яка температура?",
            [],
            FakeClient(),
            Settings(ha_url="http://ha", ha_token="test-token"),
        )

    assert answer == "Зараз немає зв'язку з Home Assistant."
    assert json.loads(tool_result["message"]["content"])["error_code"] == (
        "home_assistant_unavailable"
    )


def test_home_assistant_gateway_errors_are_reported_as_unavailable():
    response = ha_client.requests.Response()
    response.status_code = 503
    with patch.object(
        ha_client.requests,
        "get",
        return_value=response,
    ):
        result = invoke_tool(
            "get_state",
            {"entity_id": "sensor.room"},
            make_context(),
        )

    assert result["error_code"] == "home_assistant_unavailable"


def test_weather_tool_returns_current_conditions_and_capped_forecast():
    forecast = [
        {
            "datetime": f"2026-11-25T{index:02d}:00:00+00:00",
            "condition": "cloudy",
            "temperature": index,
            "provider_internal": "omitted",
        }
        for index in range(26)
    ]
    current_state = {
        "state": "cloudy",
        "attributes": {"temperature": 8, "temperature_unit": "°C"},
    }
    action_response = {
        "service_response": {
            "weather.home": {"forecast": forecast},
        },
    }

    with patch.object(
        ha_client, "get_state", return_value=current_state
    ), patch.object(
        ha_client, "call_service", return_value=action_response
    ) as call_action:
        result = get_weather("weather.home", context=make_context())

    assert result["condition"] == "cloudy"
    assert result["current"]["temperature"] == 8
    assert len(result["forecast"]) == 24
    assert result["truncated"]
    assert "provider_internal" not in result["forecast"][0]
    assert call_action.call_args.kwargs["return_response"]


def test_old_confirmation_button_cannot_confirm_new_action():
    bot = TelegramBot("test-token", {42})
    button_payload = {}
    api_calls = []

    def send_message(chat_id, text, reply_markup=None):
        assert chat_id == 10
        assert text
        if reply_markup:
            button_payload["yes"] = reply_markup["inline_keyboard"][0][0][
                "callback_data"
            ]
        return {"message_id": 77}

    updates = [
        {
            "update_id": 1,
            "callback_query": {
                "id": "stale",
                "from": {"id": 42},
                "message": {"chat": {"id": 10}, "message_id": 77},
                "data": "confirm:yes:old-token",
            },
        },
        {
            "update_id": 2,
            "callback_query": {
                "id": "current",
                "from": {"id": 42},
                "message": {"chat": {"id": 10}, "message_id": 77},
                "data": None,
            },
        },
        {"update_id": 3, "message": {"text": "queued"}},
    ]
    fetch_count = 0

    def fetch_updates(timeout=25):
        nonlocal fetch_count
        assert timeout > 0
        fetch_count += 1
        if fetch_count == 1:
            return updates[:1]
        updates[1]["callback_query"]["data"] = button_payload["yes"]
        return updates[1:]

    bot.send_message = send_message
    bot._fetch_updates = fetch_updates

    def api_call(method, payload=None, **kwargs):
        assert not kwargs
        api_calls.append((method, payload))
        return True

    bot._api_call = api_call

    confirmed = bot.confirm_action(
        10,
        42,
        "climate",
        "set_temperature",
        "climate.room",
        {"temperature": 20},
    )

    assert confirmed
    answered_ids = [
        payload["callback_query_id"]
        for method, payload in api_calls
        if method == "answerCallbackQuery"
    ]
    assert answered_ids == ["current"]
    assert any(method == "editMessageReplyMarkup" for method, _ in api_calls)
    assert bot.pending_updates[-1]["message"]["text"] == "queued"


def test_exception_log_includes_traceback_without_credentials():
    settings = Settings(
        telegram_bot_token="telegram-secret",
        ollama_key="ollama-secret",
        ha_token="ha-secret",
    )
    try:
        raise RuntimeError("failed telegram-secret ollama-secret ha-secret")
    except RuntimeError as exc:
        details = _format_exception_for_log(exc, settings)

    assert "RuntimeError" in details
    assert "[REDACTED]" in details
    assert "telegram-secret" not in details
    assert "ollama-secret" not in details
    assert "ha-secret" not in details

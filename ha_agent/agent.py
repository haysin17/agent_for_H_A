import json

import ollama

from .tools import ToolContext, invoke_tool, ollama_tools


SYSTEM = (
    "Ти помічник розумного дому на Home Assistant. Відповідай українською, коротко. "
    "Спершу дізнавайся стан через інструменти, не вгадуй. Якщо не знаєш entity_id, "
    "використай list_entities. Не вигадуй сутності. Для камер перевіряй лише стан сутності; "
    "не стверджуй, що відео чи зображення працює, якщо їх окремо не перевірено. "
    "Для графіка температури використовуй plot_temperature_history; якщо сенсор невідомий, "
    "спершу знайди його через list_entities. Якщо питають про погоду, температуру надворі "
    "або прогноз — використовуй get_weather, а не шукай термостат у climate. Якщо entity_id "
    "погоди невідомий, знайди його через list_entities з domain weather. Якщо питають про "
    "задану температуру термостата, шукай відповідну climate-сутність. Для рішень щодо "
    "опалення спершу перевір поточну погоду й погодинний прогноз через get_weather."
)
OLLAMA_TOOLS = ollama_tools()


def build_client(settings):
    return ollama.Client(
        host=settings.ollama_host,
        headers={"Authorization": f"Bearer {settings.ollama_key}"},
        verify=settings.ollama_ca_cert or True,
        timeout=settings.ollama_timeout,
    )


def _message_role(message):
    if isinstance(message, dict):
        return message.get("role")
    return getattr(message, "role", None)


def trim_history(history, max_messages):
    if len(history) <= max_messages:
        return
    start = max(1, len(history) - max_messages + 1)
    while start < len(history) and _message_role(history[start]) != "user":
        start += 1
    if start == len(history):
        start = max(
            (
                index for index, message in enumerate(history)
                if _message_role(message) == "user"
            ),
            default=len(history),
        )
    if start > 1:
        del history[1:start]


def _serialize_tool_output(output, max_chars):
    serialized = json.dumps(output, ensure_ascii=False)
    if len(serialized) <= max_chars:
        return serialized
    original_chars = len(serialized)

    if isinstance(output, dict) and isinstance(output.get("entities"), list):
        compact_output = {
            **output,
            "entities": list(output["entities"]),
            "truncated": True,
        }
        while True:
            compact_output["returned"] = len(compact_output["entities"])
            serialized = json.dumps(compact_output, ensure_ascii=False)
            if len(serialized) <= max_chars or not compact_output["entities"]:
                break
            compact_output["entities"].pop()
        if len(serialized) <= max_chars:
            return serialized

    return json.dumps(
        {
            "error": "результат інструмента перевищив ліміт і не переданий повністю",
            "output_chars": original_chars,
            "limit_chars": max_chars,
        },
        ensure_ascii=False,
    )


def deliver_attachment(output, callback):
    if not isinstance(output, dict):
        return output
    attachment = output.pop("_attachment", None)
    if attachment is not None:
        if callback is None:
            output["attachment_not_delivered"] = True
        else:
            callback(attachment)
    return output


def run_agent(
    user_text,
    history,
    client,
    settings,
    confirmation_callback=None,
    attachment_callback=None,
):
    if not history:
        history.append({"role": "system", "content": SYSTEM})
    trim_history(history, settings.max_history_messages)
    history.append({"role": "user", "content": user_text})
    context = ToolContext(
        settings=settings,
        confirmation_callback=confirmation_callback,
    )

    for _ in range(settings.max_steps):
        response = client.chat(
            model=settings.model,
            messages=history,
            tools=OLLAMA_TOOLS,
            options={"temperature": 0, "num_ctx": 8192},
        )
        message = response.message
        history.append(message)

        if not message.tool_calls:
            return message.content

        for call in message.tool_calls:
            name = call.function.name
            arguments = dict(call.function.arguments)
            print(f"  [tool] {name} {json.dumps(arguments, ensure_ascii=False)}")
            try:
                output = invoke_tool(name, arguments, context)
                output = deliver_attachment(output, attachment_callback)
            except Exception as exc:
                output = {"error": str(exc)}
            history.append({
                "role": "tool",
                "tool_name": name,
                "content": _serialize_tool_output(
                    output, settings.max_tool_output_chars
                ),
            })
    return "Досягнуто ліміт кроків, зупиняюсь."

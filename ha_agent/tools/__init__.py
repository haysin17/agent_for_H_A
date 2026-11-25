from dataclasses import dataclass
from typing import Callable, Optional

from ..ha_client import HomeAssistantUnavailable
from ..config import Settings


@dataclass(frozen=True)
class Attachment:
    filename: str
    media_type: str
    content: bytes
    caption: str = ""


@dataclass(frozen=True)
class ToolContext:
    settings: Settings
    confirmation_callback: Optional[Callable[..., bool]] = None


REGISTRY = {}


def tool(name, description, properties, required=()):
    def register(function):
        if name in REGISTRY:
            raise RuntimeError(f"Дублікат інструмента: {name}")
        REGISTRY[name] = {
            "function": function,
            "description": description,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(required),
            },
        }
        return function

    return register


def ollama_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": definition["description"],
                "parameters": definition["schema"],
            },
        }
        for name, definition in REGISTRY.items()
    ]


def invoke_tool(name, arguments, context):
    definition = REGISTRY.get(name)
    if definition is None:
        return {"error": "інструмент невідомий або заборонений"}
    if not isinstance(arguments, dict):
        return {"error": "аргументи інструмента мають бути об'єктом"}

    schema = definition["schema"]
    extra_args = set(arguments) - set(schema["properties"])
    missing_args = set(schema.get("required", [])) - set(arguments)
    if extra_args or missing_args:
        return {
            "error": "некоректні аргументи інструмента",
            "unexpected": sorted(extra_args),
            "missing": sorted(missing_args),
        }
    try:
        return definition["function"](**arguments, context=context)
    except HomeAssistantUnavailable as exc:
        return {
            "error_code": "home_assistant_unavailable",
            "error": str(exc),
        }


from . import control, entities, history

__all__ = [
    "Attachment",
    "REGISTRY",
    "ToolContext",
    "control",
    "entities",
    "history",
    "invoke_tool",
    "ollama_tools",
    "tool",
]

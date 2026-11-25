import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


READ_DOMAINS = frozenset({
    "light", "switch", "sensor", "binary_sensor", "climate", "camera", "weather"
})
CONTROL_DOMAINS = frozenset({"light", "switch", "climate"})
CONFIRM_DOMAINS = frozenset({"climate", "switch"})
ALLOWED_SERVICES = frozenset({"turn_on", "turn_off", "toggle", "set_temperature"})
SERVICE_DATA_KEYS = {
    ("light", "turn_on"): frozenset({"brightness_pct"}),
    ("climate", "set_temperature"): frozenset({"temperature"}),
}
MIN_SET_TEMPERATURE = 5
MAX_SET_TEMPERATURE = 30
ENTITY_ID_PATTERN = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
DEFAULT_OLLAMA_CA_CERT = r"D:\porada-access-ca.crt"


def is_valid_entity_id(entity_id):
    return (
        isinstance(entity_id, str)
        and ENTITY_ID_PATTERN.fullmatch(entity_id) is not None
    )


@dataclass(frozen=True)
class Settings:
    ollama_host: str = ""
    ollama_key: str = ""
    ollama_ca_cert: Optional[str] = None
    model: str = "qwen3:14b"
    ha_url: str = "http://homeassistant:8123"
    ha_token: str = ""
    telegram_bot_token: str = ""
    telegram_allowed_user_ids: str = ""
    ollama_timeout: int = 120
    ha_timeout: int = 10
    ha_history_timeout: int = 30
    telegram_poll_timeout: int = 25
    max_steps: int = 8
    max_history_messages: int = 40
    max_tool_output_chars: int = 6000

    @classmethod
    def from_env(cls):
        return cls(
            ollama_host=os.getenv("OLLAMA_HOST", "").strip(),
            ollama_key=os.getenv("OLLAMA_KEY", "").strip(),
            ollama_ca_cert=os.getenv(
                "OLLAMA_CA_CERT", DEFAULT_OLLAMA_CA_CERT
            ).strip() or None,
            model=os.getenv("OLLAMA_MODEL", "qwen3:14b").strip(),
            ha_url=os.getenv("HA_URL", "http://homeassistant:8123").rstrip("/"),
            ha_token=os.getenv("HA_TOKEN", "").strip(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_allowed_user_ids=os.getenv(
                "TELEGRAM_ALLOWED_USER_IDS", ""
            ).strip(),
        )

    @property
    def telegram_user_ids(self):
        if not self.telegram_allowed_user_ids:
            return frozenset()
        try:
            return frozenset(
                int(user_id.strip())
                for user_id in self.telegram_allowed_user_ids.split(",")
                if user_id.strip()
            )
        except ValueError as exc:
            raise RuntimeError(
                "TELEGRAM_ALLOWED_USER_IDS має містити числові Telegram user ID через кому."
            ) from exc

    def validate(self):
        missing = [
            name
            for name, value in (
                ("OLLAMA_HOST", self.ollama_host),
                ("OLLAMA_KEY", self.ollama_key),
                ("HA_TOKEN", self.ha_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                "Задай обов'язкові змінні середовища: " + ", ".join(missing)
            )
        if not self.ha_url:
            raise RuntimeError("HA_URL не може бути порожнім.")
        if self.ollama_ca_cert and not Path(self.ollama_ca_cert).is_file():
            raise RuntimeError("Файл OLLAMA_CA_CERT не знайдено.")
        if self.telegram_bot_token and not self.telegram_user_ids:
            raise RuntimeError(
                "Задай TELEGRAM_ALLOWED_USER_IDS зі своїм числовим Telegram user ID."
            )

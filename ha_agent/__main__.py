"""Entrypoint: run with `python -m ha_agent` after setting environment variables."""

from .agent import build_client, run_agent
from .config import Settings
from .telegram_bot import run_telegram_bot


def main():
    settings = Settings.from_env()
    settings.validate()
    client = build_client(settings)

    if settings.telegram_bot_token:
        run_telegram_bot(settings, client)
        return

    history = []
    print("Агент HA (Ollama) готовий. Порожній рядок для виходу.")
    while True:
        text = input("\nТи: ").strip()
        if not text:
            break
        print("Агент:", run_agent(text, history, client, settings))


if __name__ == "__main__":
    main()

from collections import deque
from io import BytesIO
import json
import secrets
import time
import traceback

import requests

from .agent import run_agent


def _format_exception_for_log(exc, settings):
    details = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    for secret in (
        settings.telegram_bot_token,
        settings.ollama_key,
        settings.ha_token,
    ):
        if secret:
            details = details.replace(secret, "[REDACTED]")
    return details.rstrip()


class TelegramBot:
    def __init__(self, token, allowed_user_ids, poll_timeout=25):
        if not token:
            raise RuntimeError("Задай змінну середовища TELEGRAM_BOT_TOKEN.")
        if not allowed_user_ids:
            raise RuntimeError(
                "Задай TELEGRAM_ALLOWED_USER_IDS зі своїм Telegram user ID."
            )
        self.api_url = f"https://api.telegram.org/bot{token}"
        self.allowed_user_ids = allowed_user_ids
        self.poll_timeout = poll_timeout
        self.offset = None
        self.pending_updates = deque()

    def _api_call(self, method, payload=None, files=None, timeout=35):
        try:
            response = requests.post(
                f"{self.api_url}/{method}",
                json=payload if files is None else None,
                data=payload if files is not None else None,
                files=files,
                timeout=timeout,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(
                f"Telegram API запит {method} завершився помилкою."
            ) from exc
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError(f"Telegram API відхилив запит {method}.")
        return result.get("result")

    def send_message(self, chat_id, text, reply_markup=None):
        text = str(text or "Не вдалося отримати відповідь.")
        result = None
        while text:
            chunk, text = text[:4096], text[4096:]
            payload = {"chat_id": chat_id, "text": chunk}
            if reply_markup is not None and result is None:
                payload["reply_markup"] = reply_markup
            result = self._api_call("sendMessage", payload)
        return result

    def send_attachment(self, chat_id, attachment):
        is_photo = attachment.media_type in {"image/jpeg", "image/png"}
        method = "sendPhoto" if is_photo else "sendDocument"
        field = "photo" if is_photo else "document"
        with BytesIO(attachment.content) as file:
            return self._api_call(
                method,
                {"chat_id": chat_id, "caption": attachment.caption},
                files={
                    field: (
                        attachment.filename,
                        file,
                        attachment.media_type,
                    ),
                },
            )

    def _fetch_updates(self, timeout=None):
        if timeout is None:
            timeout = self.poll_timeout
        payload = {"timeout": timeout}
        if self.offset is not None:
            payload["offset"] = self.offset
        updates = self._api_call(
            "getUpdates", payload, timeout=timeout + 10
        )
        if updates:
            self.offset = max(update["update_id"] for update in updates) + 1
        return updates or []

    def next_update(self):
        if self.pending_updates:
            return self.pending_updates.popleft()
        updates = self._fetch_updates()
        if not updates:
            return None
        self.pending_updates.extend(updates[1:])
        return updates[0]

    def confirm_action(self, chat_id, user_id, domain, service, entity_id, data):
        confirmation_token = secrets.token_urlsafe(8)
        expected_answers = {
            f"confirm:yes:{confirmation_token}": True,
            f"confirm:no:{confirmation_token}": False,
        }
        markup = {
            "inline_keyboard": [[
                {
                    "text": "Підтвердити",
                    "callback_data": f"confirm:yes:{confirmation_token}",
                },
                {
                    "text": "Скасувати",
                    "callback_data": f"confirm:no:{confirmation_token}",
                },
            ]]
        }
        confirmation_message = self.send_message(
            chat_id,
            f"Підтвердити дію {domain}.{service} для {entity_id}?\n"
            f"Параметри: {json.dumps(data, ensure_ascii=False)}",
            reply_markup=markup,
        )
        message_id = confirmation_message.get("message_id")
        if message_id is None:
            raise RuntimeError("Telegram не повернув ID повідомлення підтвердження.")
        deadline = time.monotonic() + 180
        while True:
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                self.send_message(chat_id, "Час підтвердження минув; дію не виконано.")
                return False
            updates = self._fetch_updates(timeout=min(self.poll_timeout, remaining))
            for index, update in enumerate(updates):
                query = update.get("callback_query")
                if query is None:
                    self.pending_updates.append(update)
                    continue
                message = query.get("message", {})
                callback_chat_id = message.get("chat", {}).get("id")
                callback_user_id = query.get("from", {}).get("id")
                answer = query.get("data")
                if (
                    callback_chat_id != chat_id
                    or callback_user_id != user_id
                    or message.get("message_id") != message_id
                    or answer not in expected_answers
                ):
                    self.pending_updates.append(update)
                    continue
                self.pending_updates.extend(updates[index + 1:])
                self._api_call(
                    "answerCallbackQuery",
                    {
                        "callback_query_id": query["id"],
                        "text": (
                            "Дію підтверджено."
                            if expected_answers[answer]
                            else "Дію скасовано."
                        ),
                    },
                )
                self._api_call(
                    "editMessageReplyMarkup",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
                return expected_answers[answer]


def run_telegram_bot(settings, client):
    bot = TelegramBot(
        settings.telegram_bot_token,
        settings.telegram_user_ids,
        settings.telegram_poll_timeout,
    )
    histories = {}
    print("Telegram-бот агента запущений.")
    while True:
        try:
            update = bot.next_update()
        except RuntimeError as exc:
            print(f"Помилка Telegram polling: {exc}")
            time.sleep(5)
            continue
        if update is None:
            continue

        message = update.get("message")
        if not message or not message.get("text"):
            continue
        user_id = message.get("from", {}).get("id")
        if (
            user_id not in bot.allowed_user_ids
            or message.get("chat", {}).get("type") != "private"
        ):
            continue
        chat_id = message["chat"]["id"]
        text = message["text"].strip()
        if not text:
            continue
        history = histories.setdefault((chat_id, user_id), [])
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command == "/start":
            bot.send_message(chat_id, "Привіт! Напиши запит до агента Home Assistant.")
            continue
        if command == "/reset":
            histories[(chat_id, user_id)] = []
            bot.send_message(chat_id, "Історію розмови очищено.")
            continue

        try:
            answer = run_agent(
                text,
                history,
                client,
                settings,
                confirmation_callback=lambda domain, service, entity_id, data: (
                    bot.confirm_action(
                        chat_id, user_id, domain, service, entity_id, data
                    )
                ),
                attachment_callback=lambda attachment: bot.send_attachment(
                    chat_id, attachment
                ),
            )
            bot.send_message(chat_id, answer)
        except Exception as exc:
            print(
                "Помилка обробки Telegram-повідомлення:\n"
                + _format_exception_for_log(exc, settings)
            )
            try:
                bot.send_message(
                    chat_id,
                    "Не вдалося обробити запит. Перевір журнали агента.",
                )
            except RuntimeError as send_error:
                print(f"Не вдалося надіслати повідомлення про помилку: {send_error}")

"""Терминальный интерфейс агента Melke: диалог, /state, /json, /exit."""

import asyncio
import json
import logging
import os
import sys

from agent import Agent

COMMANDS = {"/state": "показать состояние диалога", "/json": "показать полный JSON ответа", "/exit": "выход"}


async def main() -> int:
    # По умолчанию терминал показывает только ответы агента; LOG_LEVEL=INFO — служебные сообщения.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "WARNING"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    agent = Agent()

    print("Консультант Melke. Для выхода — /exit, для отладки — /state и /json.\n")
    while True:
        try:
            text = (await asyncio.to_thread(input, "Вы: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text == "/exit":
            return 0
        if text == "/state":
            print(json.dumps(agent.state_dump(), ensure_ascii=False, indent=2), "\n")
            continue
        if text == "/json":
            if agent.last_response is None:
                print("Ответов пока не было.\n")
            else:
                print(agent.last_response.model_dump_json(indent=2), "\n")
            continue

        try:
            response = await agent.ask(text)
        except Exception as error:  # ошибка уровня диалога: не скрываем, не маскируем
            logging.getLogger("main").exception("Ошибка обработки сообщения")
            print(f"\nОшибка обработки сообщения: {error}\n", file=sys.stderr)
            continue
        print(f"\nAgent: {response.message}\n")


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except RuntimeError as error:
        print(f"Не удалось запустить агента: {error}", file=sys.stderr)
        sys.exit(1)

"""Telegram front-end starter bot.

Replies to the /start command and echoes plain text messages. The bot token
comes from the TELEGRAM_BOT_TOKEN environment variable so no credential ever
lives in source control.

Requires: python-telegram-bot (see requirements.txt).
"""

from __future__ import annotations

import logging
import os
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME = "TELEGRAM_BOT_TOKEN"
LOGGING_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
START_COMMAND_REPLY = "Hello! I am your Python-powered Telegram bot."
ECHO_REPLY_TEMPLATE = "You said: {message_text}"

EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1


class TelegramBotConfigurationError(Exception):
    """The environment is not set up to run the Telegram bot."""


def bot_token_load() -> str:
    """Return the bot token from the environment. Raises
    TelegramBotConfigurationError when it is not set."""
    bot_token = os.environ.get(BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME)
    if bot_token:
        return bot_token
    raise TelegramBotConfigurationError(
        f"The {BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME} environment variable is"
        " not set. Create a bot with BotFather, then export its token before"
        " running this bot."
    )


async def command_start_handle(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Reply to the /start command with the greeting."""
    if update.message is None:
        return
    await update.message.reply_text(START_COMMAND_REPLY)


async def message_text_echo(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Echo a plain text message back to its sender."""
    if update.message is None or update.message.text is None:
        return
    await update.message.reply_text(
        ECHO_REPLY_TEMPLATE.format(message_text=update.message.text)
    )


def application_build(bot_token: str) -> Application:
    """Return the bot application with every handler registered."""
    application = Application.builder().token(bot_token).build()
    application.add_handler(CommandHandler("start", command_start_handle))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, message_text_echo)
    )
    return application


def main() -> int:
    """Run the bot until interrupted and report the outcome."""
    logging.basicConfig(format=LOGGING_FORMAT, level=logging.INFO)
    try:
        bot_token = bot_token_load()
    except TelegramBotConfigurationError as configuration_error:
        print(f"Telegram bot stopped: {configuration_error}", file=sys.stderr)
        return EXIT_CODE_FAILURE
    application = application_build(bot_token)
    print("Bot is running... Press Ctrl+C to stop.")
    application.run_polling()
    return EXIT_CODE_SUCCESS


if __name__ == "__main__":
    sys.exit(main())

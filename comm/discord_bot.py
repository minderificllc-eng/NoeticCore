"""Discord front-end starter bot.

Replies to the start command and echoes plain text messages. The bot token
comes from the DISCORD_BOT_TOKEN environment variable so no credential ever
lives in source control.

Requires: discord.py (see requirements.txt), and the Message Content Intent
enabled for the bot in the Discord Developer Portal.
"""

from __future__ import annotations

import logging
import os
import sys

import discord

BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME = "DISCORD_BOT_TOKEN"
LOGGING_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
START_COMMAND_TEXT = "!start"
START_COMMAND_REPLY = "Hello! I am your Python-powered Discord bot."
ECHO_REPLY_TEMPLATE = "You said: {message_text}"

EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1


class DiscordBotConfigurationError(Exception):
    """The environment is not set up to run the Discord bot."""


def bot_token_load() -> str:
    """Return the bot token from the environment. Raises
    DiscordBotConfigurationError when it is not set."""
    bot_token = os.environ.get(BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME)
    if bot_token:
        return bot_token
    raise DiscordBotConfigurationError(
        f"The {BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME} environment variable is"
        " not set. Create a bot in the Discord Developer Portal, then export"
        " its token before running this bot."
    )


class EchoBotClient(discord.Client):
    """Replies to the start command and echoes plain text messages."""

    async def on_ready(self) -> None:
        print(f"Bot is running as {self.user}. Press Ctrl+C to stop.")

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        if not message.content:
            return
        if message.content == START_COMMAND_TEXT:
            await message.channel.send(START_COMMAND_REPLY)
            return
        await message.channel.send(
            ECHO_REPLY_TEMPLATE.format(message_text=message.content)
        )


def client_build() -> EchoBotClient:
    """Return the bot client with the intents echoing requires."""
    client_intents = discord.Intents.default()
    client_intents.message_content = True
    return EchoBotClient(intents=client_intents)


def main() -> int:
    """Run the bot until interrupted and report the outcome."""
    logging.basicConfig(format=LOGGING_FORMAT, level=logging.INFO)
    try:
        bot_token = bot_token_load()
    except DiscordBotConfigurationError as configuration_error:
        print(f"Discord bot stopped: {configuration_error}", file=sys.stderr)
        return EXIT_CODE_FAILURE
    bot_client = client_build()
    try:
        bot_client.run(bot_token, log_handler=None)
    except discord.LoginFailure:
        print(
            "Discord bot stopped: the token was rejected. Verify"
            f" {BOT_TOKEN_ENVIRONMENT_VARIABLE_NAME} matches the bot token in"
            " the Discord Developer Portal.",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    except discord.PrivilegedIntentsRequired:
        print(
            "Discord bot stopped: the Message Content Intent is not enabled."
            " Enable it under Bot settings in the Discord Developer Portal,"
            " then rerun.",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    return EXIT_CODE_SUCCESS


if __name__ == "__main__":
    sys.exit(main())

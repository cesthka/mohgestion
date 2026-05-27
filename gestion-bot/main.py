"""Gestion Bot - main entry point.

Boots the bot, loads cogs, syncs slash commands, and handles errors globally.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from typing import Optional

import discord
from discord.ext import commands

from config.config import (
    BOT_NAME,
    BOT_VERSION,
    DB_PATH,
    DEFAULT_PREFIX,
    OWNER_ID,
    TOKEN,
)
from database.db_manager import Database
from utils.embed_builder import EmbedBuilder
from utils.logger import setup_logging

setup_logging(logging.INFO)
log = logging.getLogger(__name__)

# All cogs to load - order does not really matter, but permissions first
COGS: list[str] = [
    "cogs.permissions_system",
    "cogs.moderation",
    "cogs.automod",
    "cogs.antiraid",
    "cogs.tickets",
    "cogs.logs",
    "cogs.welcome",
    "cogs.levels",
    "cogs.reminders",
    "cogs.custom_commands",
    "cogs.messages_editor",
    "cogs.config_cog",
    "cogs.owner",
]


async def get_prefix(bot: "GestionBot", message: discord.Message) -> list[str]:
    """Dynamic prefix resolver. Mention + per-guild prefix."""
    base = [DEFAULT_PREFIX]
    if message.guild is not None and bot.db._conn is not None:
        try:
            p = await bot.db.get_prefix(message.guild.id)
            if p:
                base = [p]
        except Exception:
            pass
    return commands.when_mentioned_or(*base)(bot, message)


class GestionBot(commands.Bot):
    """Custom bot subclass."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.voice_states = True
        intents.moderation = True

        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            owner_id=OWNER_ID if OWNER_ID else None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=True
            ),
        )
        self.db: Database = Database(DB_PATH)
        self.embed: EmbedBuilder = EmbedBuilder(BOT_NAME)
        self.start_time: Optional[float] = None

    async def setup_hook(self) -> None:
        """Run once before the bot connects to the gateway."""
        await self.db.connect()
        log.info("Loading cogs...")
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("  ✓ %s", cog)
            except Exception as e:
                log.error("  ✗ %s: %s", cog, e)
                traceback.print_exc()
        # Sync slash commands globally
        try:
            synced = await self.tree.sync()
            log.info("Synced %d global slash commands.", len(synced))
        except Exception as e:
            log.error("Slash sync failed: %s", e)

    async def on_ready(self) -> None:
        import time
        if self.start_time is None:
            self.start_time = time.time()
        log.info("=" * 50)
        log.info("%s v%s connecté en tant que %s", BOT_NAME, BOT_VERSION, self.user)
        log.info("Serveurs: %d | Utilisateurs: %d",
                 len(self.guilds), sum(g.member_count or 0 for g in self.guilds))
        log.info("=" * 50)
        # Default presence
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"{DEFAULT_PREFIX}help · {len(self.guilds)} serveurs",
            )
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.db.ensure_guild(guild.id)
        log.info("Joined guild: %s (%d)", guild.name, guild.id)

    # --- Error handling -------------------------------------------------

    async def on_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        # Unwrap CommandInvokeError
        if isinstance(error, commands.CommandInvokeError):
            error = error.original  # type: ignore[assignment]

        if isinstance(error, commands.CommandNotFound):
            return  # silently ignore
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=self.embed.error(
                f"Argument manquant : `{error.param.name}`."
            ))
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=self.embed.error(f"Argument invalide : {error}"))
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=self.embed.error(str(error) or "Permission refusée."))
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=self.embed.warning(
                f"Cette commande est en cooldown. Réessaie dans {error.retry_after:.1f}s."
            ))
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send(embed=self.embed.error(
                "Cette commande ne fonctionne qu'en serveur."
            ))
            return
        if isinstance(error, discord.Forbidden):
            await ctx.send(embed=self.embed.error(
                "Je n'ai pas la permission d'effectuer cette action."
            ))
            return

        log.error("Unhandled command error in %s:", ctx.command, exc_info=error)
        try:
            await ctx.send(embed=self.embed.error(
                f"Une erreur est survenue : `{type(error).__name__}`"
            ))
        except Exception:
            pass

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        log.error("App command error: %s", error, exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=self.embed.error(f"Erreur : `{type(error).__name__}`"),
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    embed=self.embed.error(f"Erreur : `{type(error).__name__}`"),
                    ephemeral=True,
                )
        except Exception:
            pass


async def main() -> None:
    if not TOKEN:
        log.error("DISCORD_TOKEN manquant. Renseigne-le dans le fichier .env.")
        sys.exit(1)
    if not OWNER_ID:
        log.warning("OWNER_ID non défini - les commandes owner seront inaccessibles.")

    import time

    bot = GestionBot()
    bot.start_time = time.time()

    try:
        async with bot:
            await bot.start(TOKEN)
    finally:
        await bot.db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Arrêt manuel du bot.")

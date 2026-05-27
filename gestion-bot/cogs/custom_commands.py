"""Custom server commands: add/edit/delete/list and runtime dispatcher."""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import discord
from discord.ext import commands

from utils.checks import perm

log = logging.getLogger(__name__)

# Reserved names that may never become custom commands
RESERVED_NAMES: set[str] = {
    "ban", "unban", "kick", "mute", "unmute", "warn", "warns", "delwarn",
    "clear", "lock", "unlock", "slowmode",
    "antispam", "antilink", "antibadword", "antimassmention", "anticaps", "whitelist",
    "antiraid", "lockdown", "unlockdown",
    "ticket", "setlog", "dellog", "logs",
    "welcome", "goodbye", "autorole",
    "rank", "leaderboard", "setlevel", "levelroles", "xpchannel",
    "remind", "reminders", "delreminder",
    "addcmd", "delcmd", "listcmd", "editcmd",
    "setmsg", "resetmsg", "listmsg",
    "setprefix", "setcolor", "setstatus", "config",
    "set", "del", "changeall", "perms", "helpall",
    "reload", "sync", "shutdown", "eval", "stats",
}

NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


class CustomCommands(commands.Cog):
    """Per-guild user-defined commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.embed = bot.embed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def render(template: str, message: discord.Message) -> str:
        """Substitute placeholders in a custom command response."""
        guild = message.guild
        user = message.author
        replacements = {
            "{user}": user.mention if user else "?",
            "{user.name}": getattr(user, "display_name", "?"),
            "{user.id}": str(user.id) if user else "0",
            "{server}": guild.name if guild else "DM",
            "{count}": str(guild.member_count) if guild else "0",
            "{channel}": message.channel.mention if hasattr(message.channel, "mention") else "?",
        }
        out = template
        for k, v in replacements.items():
            out = out.replace(k, v)
        return out

    # ------------------------------------------------------------------
    # on_message dispatcher
    # ------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not message.content:
            return

        try:
            prefix = await self.db.get_prefix(message.guild.id)
        except Exception:
            return

        if not message.content.startswith(prefix):
            return

        # Skip if it matches a real command (let the default handler take it)
        without_prefix = message.content[len(prefix):].strip()
        if not without_prefix:
            return

        parts = without_prefix.split(None, 1)
        name = parts[0].lower()

        # If there's a real bot command with that name, do nothing
        if self.bot.get_command(name) is not None:
            return

        row = await self.db.fetchone(
            "SELECT response FROM custom_commands WHERE guild_id = ? AND name = ?",
            (message.guild.id, name),
        )
        if row is None:
            return

        response = self.render(row["response"], message)
        try:
            await message.channel.send(
                response[:2000],
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=True
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="addcmd",
        description="Crée une commande custom pour ce serveur.",
    )
    @perm(5)
    async def addcmd(
        self,
        ctx: commands.Context,
        name: str,
        *,
        response: str,
    ) -> None:
        """Add a new custom command."""
        name = name.lower().strip()
        if not NAME_RE.match(name):
            await ctx.send(
                embed=self.embed.error(
                    "Nom invalide : utilise uniquement `a-z 0-9 _ -` (32 caractères max)."
                )
            )
            return
        if name in RESERVED_NAMES or self.bot.get_command(name) is not None:
            await ctx.send(
                embed=self.embed.error(f"`{name}` est un nom réservé par le bot.")
            )
            return

        existing = await self.db.fetchone(
            "SELECT 1 FROM custom_commands WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name),
        )
        if existing:
            await ctx.send(
                embed=self.embed.error(
                    f"La commande `{name}` existe déjà. Utilise `editcmd` pour la modifier."
                )
            )
            return

        await self.db.execute(
            "INSERT INTO custom_commands (guild_id, name, response, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ctx.guild.id, name, response[:1900], ctx.author.id, int(time.time())),
        )
        await ctx.send(
            embed=self.embed.success(
                f"Commande `{name}` créée. Variables disponibles : "
                "`{user}`, `{user.name}`, `{server}`, `{count}`, `{channel}`."
            )
        )

    @commands.hybrid_command(
        name="delcmd",
        description="Supprime une commande custom.",
    )
    @perm(5)
    async def delcmd(self, ctx: commands.Context, name: str) -> None:
        """Delete a custom command."""
        name = name.lower().strip()
        row = await self.db.fetchone(
            "SELECT 1 FROM custom_commands WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name),
        )
        if row is None:
            await ctx.send(embed=self.embed.error(f"La commande `{name}` n'existe pas."))
            return
        await self.db.execute(
            "DELETE FROM custom_commands WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name),
        )
        await ctx.send(embed=self.embed.success(f"Commande `{name}` supprimée."))

    @commands.hybrid_command(
        name="editcmd",
        description="Modifie la réponse d'une commande custom.",
    )
    @perm(5)
    async def editcmd(
        self,
        ctx: commands.Context,
        name: str,
        *,
        response: str,
    ) -> None:
        """Edit an existing custom command."""
        name = name.lower().strip()
        row = await self.db.fetchone(
            "SELECT 1 FROM custom_commands WHERE guild_id = ? AND name = ?",
            (ctx.guild.id, name),
        )
        if row is None:
            await ctx.send(embed=self.embed.error(f"La commande `{name}` n'existe pas."))
            return
        await self.db.execute(
            "UPDATE custom_commands SET response = ? WHERE guild_id = ? AND name = ?",
            (response[:1900], ctx.guild.id, name),
        )
        await ctx.send(embed=self.embed.success(f"Commande `{name}` mise à jour."))

    @commands.hybrid_command(
        name="listcmd",
        description="Liste toutes les commandes custom du serveur.",
    )
    @perm(1)
    async def listcmd(self, ctx: commands.Context) -> None:
        """List all custom commands."""
        rows = await self.db.fetchall(
            "SELECT name FROM custom_commands WHERE guild_id = ? ORDER BY name ASC",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(
                embed=self.embed.info("Aucune commande custom n'est définie sur ce serveur.")
            )
            return

        names = [f"`{r['name']}`" for r in rows]
        embed = self.embed.info(
            ", ".join(names),
            title=f"📜 Commandes custom ({len(rows)})",
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomCommands(bot))

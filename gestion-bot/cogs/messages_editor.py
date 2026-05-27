"""Editor for the bot's editable messages (welcome, ban, mute, etc.)."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from config.default_messages import DEFAULT_MESSAGES
from utils.checks import perm

log = logging.getLogger(__name__)


class MessagesEditor(commands.Cog):
    """Override the bot's default text templates per guild."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.embed = bot.embed

    @commands.hybrid_command(
        name="setmsg",
        description="Personnalise un message du bot (ex: welcome, ban, mute).",
    )
    @perm(7)
    async def setmsg(
        self,
        ctx: commands.Context,
        key: str,
        *,
        message: str,
    ) -> None:
        """Override a default message template."""
        key = key.lower().strip()
        if key not in DEFAULT_MESSAGES:
            available = ", ".join(f"`{k}`" for k in DEFAULT_MESSAGES.keys())
            await ctx.send(
                embed=self.embed.error(
                    f"Clé inconnue. Clés disponibles :\n{available}"
                )
            )
            return

        if len(message) > 1900:
            await ctx.send(embed=self.embed.error("Message trop long (1900 caractères max)."))
            return

        await self.db.set_message(ctx.guild.id, key, message)
        await ctx.send(
            embed=self.embed.success(
                f"Message `{key}` mis à jour. Utilise `resetmsg {key}` pour revenir au défaut."
            )
        )

    @commands.hybrid_command(
        name="resetmsg",
        description="Restaure le message par défaut pour une clé.",
    )
    @perm(7)
    async def resetmsg(self, ctx: commands.Context, key: str) -> None:
        """Reset a custom message back to default."""
        key = key.lower().strip()
        if key not in DEFAULT_MESSAGES:
            await ctx.send(embed=self.embed.error(f"Clé `{key}` inconnue."))
            return

        await self.db.reset_message(ctx.guild.id, key)
        await ctx.send(
            embed=self.embed.success(f"Message `{key}` restauré à sa valeur par défaut.")
        )

    @commands.hybrid_command(
        name="listmsg",
        description="Liste tous les messages personnalisables.",
    )
    @perm(7)
    async def listmsg(self, ctx: commands.Context) -> None:
        """List every customizable message and whether it's been overridden."""
        rows = await self.db.fetchall(
            "SELECT key, value FROM custom_messages WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        overrides = {r["key"]: r["value"] for r in rows}

        lines: list[str] = []
        for key, default in DEFAULT_MESSAGES.items():
            current = overrides.get(key, default)
            preview = current.replace("\n", " ")
            if len(preview) > 80:
                preview = preview[:77] + "..."
            tag = "✏️" if key in overrides else "•"
            lines.append(f"{tag} `{key}` → {preview}")

        embed = self.embed.info(
            "\n".join(lines),
            title="📝 Messages personnalisables",
        )
        embed.set_footer(
            text=f"{self.embed.bot_name} • ✏️ = personnalisé · • = défaut"
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessagesEditor(bot))

"""Reminders system: persistent reminders that survive restarts."""
from __future__ import annotations

import logging
import time
from typing import Optional

import discord
from discord.ext import commands, tasks

from utils.checks import perm
from utils.time_parser import format_duration, parse_duration

log = logging.getLogger(__name__)


class Reminders(commands.Cog):
    """Set reminders that ping you when due."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.embed = bot.embed
        self.check_reminders.start()

    def cog_unload(self) -> None:
        self.check_reminders.cancel()

    # ------------------------------------------------------------------
    # Background task
    # ------------------------------------------------------------------
    @tasks.loop(seconds=15.0)
    async def check_reminders(self) -> None:
        """Poll due reminders and dispatch them."""
        try:
            now = int(time.time())
            rows = await self.db.fetchall(
                "SELECT * FROM reminders WHERE remind_at <= ? ORDER BY remind_at ASC LIMIT 50",
                (now,),
            )
            for row in rows:
                rid = int(row["id"])
                user_id = int(row["user_id"])
                channel_id = int(row["channel_id"])
                message = row["message"] or ""
                created_at = int(row["created_at"])

                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except (discord.NotFound, discord.Forbidden):
                        channel = None

                target_user = self.bot.get_user(user_id)
                if target_user is None:
                    try:
                        target_user = await self.bot.fetch_user(user_id)
                    except (discord.NotFound, discord.HTTPException):
                        target_user = None

                content = f"<@{user_id}>"
                embed = self.embed.info(
                    f"**Rappel demandé il y a {format_duration(now - created_at)} :**\n{message}",
                    title="⏰ Rappel",
                )

                sent = False
                if channel is not None:
                    try:
                        await channel.send(content=content, embed=embed)
                        sent = True
                    except (discord.Forbidden, discord.HTTPException):
                        sent = False

                if not sent and target_user is not None:
                    try:
                        await target_user.send(embed=embed)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                await self.db.execute("DELETE FROM reminders WHERE id = ?", (rid,))
        except Exception:
            log.exception("Error in reminders loop")

    @check_reminders.before_loop
    async def _before_check(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="remind",
        description="Programme un rappel après une durée donnée.",
    )
    @perm(1)
    async def remind(
        self,
        ctx: commands.Context,
        duration: str,
        *,
        message: str,
    ) -> None:
        """Set a personal reminder."""
        try:
            seconds = parse_duration(duration)
        except ValueError as e:
            await ctx.send(embed=self.embed.error(str(e)))
            return

        if seconds < 5:
            await ctx.send(embed=self.embed.error("La durée minimale est de 5 secondes."))
            return
        if seconds > 60 * 60 * 24 * 365:
            await ctx.send(embed=self.embed.error("La durée maximale est d'un an."))
            return

        now = int(time.time())
        remind_at = now + seconds
        guild_id = ctx.guild.id if ctx.guild else None

        await self.db.execute(
            "INSERT INTO reminders (guild_id, user_id, channel_id, message, remind_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (guild_id, ctx.author.id, ctx.channel.id, message[:1500], remind_at, now),
        )

        await ctx.send(
            embed=self.embed.success(
                f"Rappel programmé dans **{format_duration(seconds)}** (<t:{remind_at}:R>).\n"
                f"**Message :** {message[:200]}"
            )
        )

    @commands.hybrid_command(
        name="reminders",
        description="Liste tes rappels actifs.",
    )
    @perm(1)
    async def reminders(self, ctx: commands.Context) -> None:
        """List the user's active reminders."""
        rows = await self.db.fetchall(
            "SELECT id, message, remind_at FROM reminders "
            "WHERE user_id = ? ORDER BY remind_at ASC LIMIT 25",
            (ctx.author.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Tu n'as aucun rappel actif."))
            return

        lines: list[str] = []
        for r in rows:
            rid = int(r["id"])
            remind_at = int(r["remind_at"])
            msg = (r["message"] or "")[:80]
            lines.append(f"`#{rid}` · <t:{remind_at}:R> · {msg}")

        embed = self.embed.info(
            "\n".join(lines),
            title=f"⏰ Tes rappels ({len(rows)})",
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="delreminder",
        description="Supprime un rappel par son identifiant.",
    )
    @perm(1)
    async def delreminder(self, ctx: commands.Context, reminder_id: int) -> None:
        """Delete one of your reminders."""
        row = await self.db.fetchone(
            "SELECT id FROM reminders WHERE id = ? AND user_id = ?",
            (reminder_id, ctx.author.id),
        )
        if row is None:
            await ctx.send(
                embed=self.embed.error("Rappel introuvable ou tu n'en es pas le propriétaire.")
            )
            return

        await self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await ctx.send(embed=self.embed.success(f"Rappel `#{reminder_id}` supprimé."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))

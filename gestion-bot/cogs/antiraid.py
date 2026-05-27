"""Anti-raid system: detects mass joins and locks the server down."""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Deque

import discord
from discord.ext import commands

from config.config import (
    ANTIRAID_MIN_ACCOUNT_AGE_DAYS,
    ANTIRAID_THRESHOLD,
    ANTIRAID_WINDOW,
)
from utils.checks import perm

log = logging.getLogger(__name__)


class AntiRaid(commands.Cog, name="AntiRaid"):
    """Detects coordinated mass-join attacks."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]
        # Join timestamps per guild
        self._joins: dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=50))
        # Guilds in active raid mode
        self._raid_mode: set[int] = set()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await self.db.get_config(member.guild.id)
        if not config or not config["antiraid_enabled"]:
            return

        now = time.time()
        buf = self._joins[member.guild.id]
        buf.append(now)
        # Trim outside window
        while buf and (now - buf[0]) > ANTIRAID_WINDOW:
            buf.popleft()

        # Account age check during raid mode
        if member.guild.id in self._raid_mode:
            account_age = datetime.now(timezone.utc) - member.created_at
            if account_age < timedelta(days=ANTIRAID_MIN_ACCOUNT_AGE_DAYS):
                try:
                    await member.kick(reason="Antiraid: compte trop récent pendant un raid.")
                    log.info("Antiraid kicked %s on %s (age=%s)",
                             member, member.guild.id, account_age)
                except discord.Forbidden:
                    pass
                return

        if len(buf) >= ANTIRAID_THRESHOLD:
            await self._trigger_raid_mode(member.guild)

    async def _trigger_raid_mode(self, guild: discord.Guild) -> None:
        if guild.id in self._raid_mode:
            return
        self._raid_mode.add(guild.id)
        log.warning("Anti-raid triggered on guild %s (%d)", guild.name, guild.id)

        # Lockdown
        await self._set_lockdown(guild, True, reason="Antiraid auto-trigger")

        # Notify
        log_chan_id = await self.db.fetchval(
            "SELECT channel_id FROM logs_config WHERE guild_id = ? AND log_type = 'moderation'",
            (guild.id,),
        )
        chan = None
        if log_chan_id:
            chan = guild.get_channel(int(log_chan_id))
        if chan is None:
            chan = guild.system_channel
        if chan:
            try:
                await chan.send(embed=self.embed.error(
                    f"🚨 **ALERTE RAID** sur **{guild.name}** !\n"
                    f"Plus de {ANTIRAID_THRESHOLD} arrivées en {ANTIRAID_WINDOW}s.\n"
                    f"Le serveur est en lockdown."
                ))
            except discord.HTTPException:
                pass

        # Auto-release after 10 minutes
        await self._schedule_release(guild)

    async def _schedule_release(self, guild: discord.Guild) -> None:
        import asyncio
        async def release() -> None:
            await asyncio.sleep(600)  # 10 min
            self._raid_mode.discard(guild.id)
            log.info("Raid mode auto-released on %s", guild.id)
        self.bot.loop.create_task(release())

    async def _set_lockdown(self, guild: discord.Guild, on: bool, reason: str = "") -> int:
        """Toggle send-messages permission on all text channels."""
        count = 0
        for channel in guild.text_channels:
            try:
                overwrite = channel.overwrites_for(guild.default_role)
                overwrite.send_messages = False if on else None
                await channel.set_permissions(
                    guild.default_role,
                    overwrite=overwrite,
                    reason=reason,
                )
                count += 1
            except discord.HTTPException:
                continue
        await self.db.update_config(guild.id, lockdown_active=1 if on else 0)
        return count

    # --- Commands ------------------------------------------------------

    @commands.command(name="antiraid")
    @perm(7)
    async def antiraid_toggle(self, ctx: commands.Context, mode: str) -> None:
        """`on` ou `off` pour activer/désactiver l'anti-raid."""
        m = mode.lower()
        if m == "on":
            await self.db.update_config(ctx.guild.id, antiraid_enabled=1)
            await ctx.send(embed=self.embed.success(
                f"Anti-raid activé. Seuil : {ANTIRAID_THRESHOLD} arrivées en {ANTIRAID_WINDOW}s."
            ))
        elif m == "off":
            await self.db.update_config(ctx.guild.id, antiraid_enabled=0)
            self._raid_mode.discard(ctx.guild.id)
            await ctx.send(embed=self.embed.success("Anti-raid désactivé."))
        else:
            await ctx.send(embed=self.embed.error("Usage : `antiraid on|off`."))

    @commands.command(name="lockdown")
    @perm(7)
    @commands.bot_has_permissions(manage_channels=True)
    async def lockdown(self, ctx: commands.Context) -> None:
        """Verrouille tous les salons texte."""
        msg = await ctx.send(embed=self.embed.info("🔒 Lockdown en cours..."))
        count = await self._set_lockdown(ctx.guild, True, reason=f"Lockdown par {ctx.author}")
        await msg.edit(embed=self.embed.success(
            f"🔒 Lockdown activé sur **{count}** salons."
        ))

    @commands.command(name="unlockdown")
    @perm(7)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlockdown(self, ctx: commands.Context) -> None:
        """Lève le lockdown."""
        msg = await ctx.send(embed=self.embed.info("🔓 Levée du lockdown..."))
        count = await self._set_lockdown(ctx.guild, False, reason=f"Unlockdown par {ctx.author}")
        self._raid_mode.discard(ctx.guild.id)
        await msg.edit(embed=self.embed.success(
            f"🔓 Lockdown levé sur **{count}** salons."
        ))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaid(bot))

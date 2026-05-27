"""Levels / XP system with leaderboard and level-roles."""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

import discord
from discord.ext import commands

from config.config import LEVEL_XP_COOLDOWN, LEVEL_XP_MAX, LEVEL_XP_MIN
from utils.checks import perm

log = logging.getLogger(__name__)


def xp_required(level: int) -> int:
    """Total XP needed to reach a given level (cumulative)."""
    return 5 * (level ** 2) + 50 * level + 100


def level_from_xp(xp: int) -> int:
    """Compute level from a total XP amount."""
    level = 0
    while xp >= xp_required(level):
        xp -= xp_required(level)
        level += 1
    return level


def xp_into_level(xp: int) -> tuple[int, int, int]:
    """Return (current_level, xp_into_level, xp_needed_for_next)."""
    level = 0
    remaining = xp
    while remaining >= xp_required(level):
        remaining -= xp_required(level)
        level += 1
    return level, remaining, xp_required(level)


class Levels(commands.Cog, name="Niveaux"):
    """XP and leveling system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]

    # --- Listener ------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if message.content.startswith(("?", "!", "+", "/")) and len(message.content) > 1:
            # Likely a command - skip XP gain
            pass
        # Check disabled channels
        if await self.db.fetchone(
            "SELECT 1 FROM xp_disabled_channels WHERE guild_id = ? AND channel_id = ?",
            (message.guild.id, message.channel.id),
        ):
            return

        now = int(time.time())
        row = await self.db.fetchone(
            "SELECT xp, last_message FROM levels WHERE guild_id = ? AND user_id = ?",
            (message.guild.id, message.author.id),
        )

        if row and (now - int(row["last_message"])) < LEVEL_XP_COOLDOWN:
            return

        gain = random.randint(LEVEL_XP_MIN, LEVEL_XP_MAX)
        if row:
            old_xp = int(row["xp"])
            new_xp = old_xp + gain
        else:
            old_xp = 0
            new_xp = gain

        old_level = level_from_xp(old_xp)
        new_level = level_from_xp(new_xp)

        await self.db.execute(
            "INSERT INTO levels (guild_id, user_id, xp, level, last_message) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
            "xp = excluded.xp, level = excluded.level, last_message = excluded.last_message",
            (message.guild.id, message.author.id, new_xp, new_level, now),
        )

        if new_level > old_level and isinstance(message.author, discord.Member):
            await self._on_level_up(message, new_level)

    async def _on_level_up(self, message: discord.Message, new_level: int) -> None:
        # Optional level-up message
        try:
            await message.channel.send(
                embed=self.embed.success(
                    f"🎉 {message.author.mention} a atteint le niveau **{new_level}** !"
                ),
                delete_after=15,
            )
        except discord.HTTPException:
            pass
        # Level roles
        rows = await self.db.fetchall(
            "SELECT level, role_id FROM level_roles "
            "WHERE guild_id = ? AND level <= ? ORDER BY level",
            (message.guild.id, new_level),
        )
        member = message.author
        if not isinstance(member, discord.Member):
            return
        for r in rows:
            role = message.guild.get_role(int(r["role_id"]))
            if role and role not in member.roles and role < message.guild.me.top_role:
                try:
                    await member.add_roles(role, reason=f"Niveau {r['level']} atteint")
                except discord.Forbidden:
                    pass

    # --- Commands ------------------------------------------------------

    @commands.hybrid_command(name="rank", description="Affiche ton niveau et ton XP.")
    @perm(1)
    async def rank(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        target = member or ctx.author
        row = await self.db.fetchone(
            "SELECT xp FROM levels WHERE guild_id = ? AND user_id = ?",
            (ctx.guild.id, target.id),
        )
        xp = int(row["xp"]) if row else 0
        level, into, needed = xp_into_level(xp)

        # Rank position
        rank_row = await self.db.fetchval(
            "SELECT COUNT(*) FROM levels WHERE guild_id = ? AND xp > ?",
            (ctx.guild.id, xp),
        )
        position = (int(rank_row) if rank_row is not None else 0) + 1

        progress = min(into / needed, 1.0) if needed else 0
        bar_len = 20
        filled = int(progress * bar_len)
        bar = "▰" * filled + "▱" * (bar_len - filled)

        embed = self.embed.custom(
            title=f"🏆 Niveau de {target.display_name}",
            description=(
                f"**Niveau :** {level}\n"
                f"**XP :** {into} / {needed}\n"
                f"**Total XP :** {xp}\n"
                f"**Classement :** #{position}\n\n"
                f"`{bar}` {int(progress * 100)}%"
            ),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"], description="Top 10 du serveur.")
    @perm(1)
    async def leaderboard(self, ctx: commands.Context) -> None:
        rows = await self.db.fetchall(
            "SELECT user_id, xp FROM levels WHERE guild_id = ? "
            "ORDER BY xp DESC LIMIT 10",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun XP enregistré pour le moment."))
            return
        lines = []
        for i, r in enumerate(rows, 1):
            user = ctx.guild.get_member(int(r["user_id"]))
            name = user.display_name if user else f"<@{r['user_id']}>"
            xp = int(r["xp"])
            lvl = level_from_xp(xp)
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`#{i}`")
            lines.append(f"{medal} **{name}** — Niveau {lvl} ({xp} XP)")
        await ctx.send(embed=self.embed.custom(
            description="\n".join(lines), title="🏆 Classement du serveur"
        ))

    @commands.command(name="setlevel")
    @perm(7)
    async def setlevel(self, ctx: commands.Context, member: discord.Member, level: int) -> None:
        if level < 0 or level > 1000:
            await ctx.send(embed=self.embed.error("Niveau invalide (0-1000)."))
            return
        # Convert level to XP
        total = 0
        for i in range(level):
            total += xp_required(i)
        await self.db.execute(
            "INSERT INTO levels (guild_id, user_id, xp, level, last_message) "
            "VALUES (?, ?, ?, ?, 0) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = excluded.xp, level = excluded.level",
            (ctx.guild.id, member.id, total, level),
        )
        await ctx.send(embed=self.embed.success(
            f"{member.mention} est maintenant au niveau **{level}**."
        ))

    @commands.group(name="levelroles", invoke_without_command=True)
    @perm(5)
    async def levelroles(self, ctx: commands.Context) -> None:
        rows = await self.db.fetchall(
            "SELECT level, role_id FROM level_roles WHERE guild_id = ? ORDER BY level",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun rôle de niveau défini."))
            return
        lines = []
        for r in rows:
            role = ctx.guild.get_role(int(r["role_id"]))
            lines.append(f"• Niveau **{r['level']}** → {role.mention if role else 'introuvable'}")
        await ctx.send(embed=self.embed.custom(
            description="\n".join(lines), title="🎭 Rôles par niveau"
        ))

    @levelroles.command(name="add")
    @perm(5)
    async def levelroles_add(self, ctx: commands.Context, level: int, role: discord.Role) -> None:
        if level < 1:
            await ctx.send(embed=self.embed.error("Niveau invalide."))
            return
        await self.db.execute(
            "INSERT OR REPLACE INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?)",
            (ctx.guild.id, level, role.id),
        )
        await ctx.send(embed=self.embed.success(
            f"{role.mention} sera attribué au niveau **{level}**."
        ))

    @levelroles.command(name="del")
    @perm(5)
    async def levelroles_del(self, ctx: commands.Context, level: int) -> None:
        await self.db.execute(
            "DELETE FROM level_roles WHERE guild_id = ? AND level = ?",
            (ctx.guild.id, level),
        )
        await ctx.send(embed=self.embed.success(f"Rôle de niveau {level} retiré."))

    @commands.group(name="xpchannel", invoke_without_command=True)
    @perm(5)
    async def xpchannel(self, ctx: commands.Context) -> None:
        rows = await self.db.fetchall(
            "SELECT channel_id FROM xp_disabled_channels WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun salon désactivé pour l'XP."))
            return
        chans = [
            f"<#{r['channel_id']}>" for r in rows
        ]
        await ctx.send(embed=self.embed.custom(
            description=", ".join(chans),
            title="🚫 Salons sans XP",
        ))

    @xpchannel.command(name="disable")
    @perm(5)
    async def xpchannel_disable(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO xp_disabled_channels (guild_id, channel_id) VALUES (?, ?)",
            (ctx.guild.id, channel.id),
        )
        await ctx.send(embed=self.embed.success(f"XP désactivée dans {channel.mention}."))

    @xpchannel.command(name="enable")
    @perm(5)
    async def xpchannel_enable(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.db.execute(
            "DELETE FROM xp_disabled_channels WHERE guild_id = ? AND channel_id = ?",
            (ctx.guild.id, channel.id),
        )
        await ctx.send(embed=self.embed.success(f"XP réactivée dans {channel.mention}."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))

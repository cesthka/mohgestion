"""Permission decorators and helpers.

Implements the hierarchical 9-level permission system.
A user with level N automatically has access to commands requiring level <= N.
"""
from __future__ import annotations

from typing import Optional, Union

import discord
from discord.ext import commands

from config.config import MAX_PERM_LEVEL, OWNER_ID


async def get_user_perm_level(
    bot: commands.Bot,
    guild: discord.Guild,
    member: Union[discord.Member, discord.User],
) -> int:
    """Return the highest permission level held by a member.

    - Bot owner and guild owner always get the max level.
    - Members with the Administrator permission also get the max level.
    - Otherwise we look up the `permissions` table for both their user_id
      and any of their roles, and return the maximum found.
    """
    if member.id == OWNER_ID:
        return MAX_PERM_LEVEL
    if guild.owner_id == member.id:
        return MAX_PERM_LEVEL

    if isinstance(member, discord.Member):
        if member.guild_permissions.administrator:
            return MAX_PERM_LEVEL

    db = getattr(bot, "db", None)
    if db is None:
        return 0

    # User-specific level
    levels: list[int] = []
    row = await db.fetchone(
        "SELECT level FROM permissions "
        "WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
        (guild.id, member.id),
    )
    if row:
        levels.append(int(row["level"]))

    # Role-based levels
    if isinstance(member, discord.Member):
        role_ids = [r.id for r in member.roles]
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            rows = await db.fetchall(
                f"SELECT level FROM permissions "
                f"WHERE guild_id = ? AND target_type = 'role' "
                f"AND target_id IN ({placeholders})",
                (guild.id, *role_ids),
            )
            levels.extend(int(r["level"]) for r in rows)

    return max(levels) if levels else 0


async def get_command_required_level(
    bot: commands.Bot,
    guild: discord.Guild,
    command_name: str,
    default: int = 0,
) -> int:
    """Return the level required for a command on this guild."""
    db = getattr(bot, "db", None)
    if db is None:
        return default
    row = await db.fetchone(
        "SELECT level FROM command_perms WHERE guild_id = ? AND command_name = ?",
        (guild.id, command_name),
    )
    if row:
        return int(row["level"])
    return default


async def has_direct_command_grant(
    bot: commands.Bot,
    guild: discord.Guild,
    member: Union[discord.Member, discord.User],
    command_name: str,
) -> bool:
    """Check for a per-command override grant for this user/role."""
    db = getattr(bot, "db", None)
    if db is None:
        return False

    if await db.fetchone(
        "SELECT 1 FROM command_grants "
        "WHERE guild_id = ? AND command_name = ? "
        "AND target_id = ? AND target_type = 'user'",
        (guild.id, command_name, member.id),
    ):
        return True

    if isinstance(member, discord.Member):
        role_ids = [r.id for r in member.roles]
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            row = await db.fetchone(
                f"SELECT 1 FROM command_grants "
                f"WHERE guild_id = ? AND command_name = ? "
                f"AND target_type = 'role' AND target_id IN ({placeholders})",
                (guild.id, command_name, *role_ids),
            )
            if row:
                return True
    return False


def perm(level: int):
    """Decorator: require at least `level` permission to run the command."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage("Cette commande ne fonctionne qu'en serveur.")

        bot = ctx.bot
        member = ctx.author

        # Direct command grant always wins
        if await has_direct_command_grant(bot, ctx.guild, member, ctx.command.qualified_name):
            return True

        required = await get_command_required_level(
            bot, ctx.guild, ctx.command.qualified_name, default=level
        )
        user_level = await get_user_perm_level(bot, ctx.guild, member)

        if user_level >= required:
            return True

        raise commands.CheckFailure(
            f"Tu n'as pas la permission requise (niveau **{required}**)."
        )

    return commands.check(predicate)


def owner_only():
    """Decorator: only the bot owner can run this command."""

    async def predicate(ctx: commands.Context) -> bool:
        if ctx.author.id != OWNER_ID:
            raise commands.CheckFailure("Cette commande est réservée à l'owner du bot.")
        return True

    return commands.check(predicate)


def is_bot_owner(user: Union[discord.Member, discord.User]) -> bool:
    return user.id == OWNER_ID

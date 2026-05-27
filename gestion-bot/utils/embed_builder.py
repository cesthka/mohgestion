"""Embed factory with per-guild color support."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

import discord

from config.config import (
    BOT_NAME,
    COLOR_DEFAULT,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_MOD,
    COLOR_SUCCESS,
    COLOR_WARNING,
)


class EmbedBuilder:
    """Helper to build consistent embeds across the bot."""

    def __init__(self, bot_name: str = BOT_NAME) -> None:
        self.bot_name = bot_name

    def _base(self, color: int) -> discord.Embed:
        embed = discord.Embed(color=color, timestamp=datetime.now(timezone.utc))
        embed.set_footer(text=self.bot_name)
        return embed

    def success(self, description: str, title: Optional[str] = None) -> discord.Embed:
        embed = self._base(COLOR_SUCCESS)
        embed.description = f"✅ {description}"
        if title:
            embed.title = title
        return embed

    def error(self, description: str, title: Optional[str] = None) -> discord.Embed:
        embed = self._base(COLOR_ERROR)
        embed.description = f"❌ {description}"
        if title:
            embed.title = title
        return embed

    def warning(self, description: str, title: Optional[str] = None) -> discord.Embed:
        embed = self._base(COLOR_WARNING)
        embed.description = f"⚠️ {description}"
        if title:
            embed.title = title
        return embed

    def info(self, description: str, title: Optional[str] = None) -> discord.Embed:
        embed = self._base(COLOR_INFO)
        embed.description = f"ℹ️ {description}"
        if title:
            embed.title = title
        return embed

    def custom(
        self,
        description: str,
        color: int = COLOR_DEFAULT,
        title: Optional[str] = None,
    ) -> discord.Embed:
        embed = self._base(color)
        embed.description = description
        if title:
            embed.title = title
        return embed

    def mod_action(
        self,
        action: str,
        target: Union[discord.Member, discord.User],
        moderator: Union[discord.Member, discord.User],
        reason: Optional[str] = None,
        duration: Optional[str] = None,
        case_id: Optional[int] = None,
    ) -> discord.Embed:
        """Standardized moderation action embed."""
        embed = self._base(COLOR_MOD)
        embed.title = f"⚖️ Action de modération · {action}"
        embed.add_field(name="Cible", value=f"{target.mention} (`{target.id}`)", inline=True)
        embed.add_field(name="Modérateur", value=moderator.mention, inline=True)
        if duration:
            embed.add_field(name="Durée", value=duration, inline=True)
        embed.add_field(
            name="Raison",
            value=reason if reason else "Non spécifiée",
            inline=False,
        )
        if case_id is not None:
            embed.set_footer(text=f"{self.bot_name} • Sanction #{case_id}")
        try:
            if hasattr(target, "display_avatar"):
                embed.set_thumbnail(url=target.display_avatar.url)
        except Exception:
            pass
        return embed

"""Server-level configuration (prefix, embed color, status, recap)."""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord.ext import commands

from config.config import COLOR_DEFAULT, OWNER_ID
from utils.checks import owner_only, perm

log = logging.getLogger(__name__)

HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class ConfigCog(commands.Cog, name="Config"):
    """Per-guild configuration commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.embed = bot.embed

    @commands.hybrid_command(
        name="setprefix",
        description="Change le prefix des commandes du bot pour ce serveur.",
    )
    @perm(9)
    async def setprefix(self, ctx: commands.Context, prefix: str) -> None:
        """Change the bot prefix for this guild."""
        if len(prefix) > 5:
            await ctx.send(embed=self.embed.error("Le prefix ne peut dépasser 5 caractères."))
            return
        if any(c.isspace() for c in prefix):
            await ctx.send(embed=self.embed.error("Le prefix ne peut pas contenir d'espace."))
            return

        await self.db.set_prefix(ctx.guild.id, prefix)
        await ctx.send(
            embed=self.embed.success(
                f"Prefix changé en `{prefix}`. Exemple : `{prefix}help`."
            )
        )

    @commands.hybrid_command(
        name="setcolor",
        description="Change la couleur des embeds du bot (hex).",
    )
    @perm(9)
    async def setcolor(self, ctx: commands.Context, hex_color: str) -> None:
        """Change the embed color for this guild."""
        m = HEX_RE.match(hex_color.strip())
        if not m:
            await ctx.send(
                embed=self.embed.error(
                    "Couleur invalide. Format attendu : `#RRGGBB` (ex: `#5865F2`)."
                )
            )
            return

        color_int = int(m.group(1), 16)
        await self.db.set_color(ctx.guild.id, color_int)

        preview = discord.Embed(
            description=f"✅ Couleur des embeds mise à jour : `#{m.group(1).upper()}`",
            color=color_int,
        )
        await ctx.send(embed=preview)

    @commands.hybrid_command(
        name="setstatus",
        description="(Owner) Change le statut affiché par le bot.",
    )
    @owner_only()
    async def setstatus(
        self,
        ctx: commands.Context,
        *,
        text: str,
    ) -> None:
        """Set the bot presence text. Owner-only because it's global."""
        if len(text) > 128:
            await ctx.send(embed=self.embed.error("Statut trop long (128 caractères max)."))
            return
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.playing, name=text)
        )
        await ctx.send(embed=self.embed.success(f"Statut changé : **{text}**"))

    @commands.hybrid_command(
        name="config",
        description="Affiche la configuration actuelle du serveur.",
    )
    @perm(5)
    async def config(self, ctx: commands.Context) -> None:
        """Show a recap of the guild configuration."""
        cfg = await self.db.get_config(ctx.guild.id)
        prefix = cfg["prefix"] if cfg else "+"
        color = int(cfg["embed_color"]) if cfg else COLOR_DEFAULT

        def fmt_channel(cid: Optional[int]) -> str:
            if not cid:
                return "*non défini*"
            ch = ctx.guild.get_channel(int(cid))
            return ch.mention if ch else f"`{cid}` (introuvable)"

        def fmt_bool(val: int) -> str:
            return "✅" if val else "❌"

        embed = discord.Embed(
            title=f"⚙️ Configuration de {ctx.guild.name}",
            color=color,
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="🔧 Général",
            value=(
                f"**Prefix :** `{prefix}`\n"
                f"**Couleur embed :** `#{color:06X}`"
            ),
            inline=False,
        )

        embed.add_field(
            name="👋 Bienvenue / Départ",
            value=(
                f"**Salon bienvenue :** {fmt_channel(cfg['welcome_channel'])}\n"
                f"**MP bienvenue :** {fmt_bool(cfg['welcome_dm_enabled'])}\n"
                f"**Salon départ :** {fmt_channel(cfg['goodbye_channel'])}"
            ),
            inline=False,
        )

        embed.add_field(
            name="🛡️ Automod",
            value=(
                f"**Antispam :** {fmt_bool(cfg['antispam_enabled'])} "
                f"({cfg['antispam_messages']}/{cfg['antispam_seconds']}s)\n"
                f"**Antilink :** {fmt_bool(cfg['antilink_enabled'])} "
                f"(mode: `{cfg['antilink_mode']}`)\n"
                f"**Antibadword :** {fmt_bool(cfg['antibadword_enabled'])}\n"
                f"**Antimassmention :** {fmt_bool(cfg['antimassmention_enabled'])} "
                f"(seuil: {cfg['antimassmention_count']})\n"
                f"**Anticaps :** {fmt_bool(cfg['anticaps_enabled'])}"
            ),
            inline=False,
        )

        embed.add_field(
            name="🚨 Antiraid",
            value=(
                f"**Antiraid :** {fmt_bool(cfg['antiraid_enabled'])}\n"
                f"**Lockdown actif :** {fmt_bool(cfg['lockdown_active'])}"
            ),
            inline=False,
        )

        # Permissions summary
        perm_rows = await self.db.fetchall(
            "SELECT level, COUNT(*) AS c FROM permissions "
            "WHERE guild_id = ? GROUP BY level ORDER BY level DESC",
            (ctx.guild.id,),
        )
        if perm_rows:
            perm_lines = "\n".join(
                f"**Perm {r['level']}** · {r['c']} cible(s)" for r in perm_rows
            )
        else:
            perm_lines = "*Aucune permission custom définie*"
        embed.add_field(name="🛂 Permissions", value=perm_lines, inline=False)

        # Logs summary
        log_rows = await self.db.fetchall(
            "SELECT log_type, channel_id FROM logs_config WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        if log_rows:
            log_lines = "\n".join(
                f"**{r['log_type']}** → {fmt_channel(r['channel_id'])}" for r in log_rows
            )
        else:
            log_lines = "*Aucun salon de logs configuré*"
        embed.add_field(name="📋 Logs", value=log_lines, inline=False)

        embed.set_footer(text=f"{self.embed.bot_name} • Guild ID: {ctx.guild.id}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConfigCog(bot))

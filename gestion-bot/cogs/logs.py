"""Server logging: message edits/deletes, member joins/leaves, role changes, etc."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands

from utils.checks import perm

log = logging.getLogger(__name__)

VALID_LOG_TYPES = {"moderation", "messages", "members", "voice", "roles"}


class Logs(commands.Cog, name="Logs"):
    """Track activity in dedicated log channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]

    async def _get_log_channel(
        self, guild_id: int, log_type: str
    ) -> Optional[discord.TextChannel]:
        row = await self.db.fetchone(
            "SELECT channel_id FROM logs_config WHERE guild_id = ? AND log_type = ?",
            (guild_id, log_type),
        )
        if not row:
            return None
        chan = self.bot.get_channel(int(row["channel_id"]))
        if isinstance(chan, discord.TextChannel):
            return chan
        return None

    async def _send_log(
        self, guild_id: int, log_type: str, embed: discord.Embed
    ) -> None:
        chan = await self._get_log_channel(guild_id, log_type)
        if chan is None:
            return
        try:
            await chan.send(embed=embed)
        except discord.HTTPException:
            pass

    # --- Configuration commands ----------------------------------------

    @commands.command(name="setlog")
    @perm(7)
    async def setlog(
        self,
        ctx: commands.Context,
        log_type: str,
        channel: discord.TextChannel,
    ) -> None:
        """Définit le salon de log pour un type donné.

        Types : `moderation`, `messages`, `members`, `voice`, `roles`.
        """
        log_type = log_type.lower()
        if log_type not in VALID_LOG_TYPES:
            await ctx.send(embed=self.embed.error(
                f"Type invalide. Valides : {', '.join(VALID_LOG_TYPES)}"
            ))
            return
        await self.db.execute(
            "INSERT OR REPLACE INTO logs_config (guild_id, log_type, channel_id) "
            "VALUES (?, ?, ?)",
            (ctx.guild.id, log_type, channel.id),
        )
        await ctx.send(embed=self.embed.success(
            f"Logs `{log_type}` envoyés dans {channel.mention}."
        ))

    @commands.command(name="dellog")
    @perm(7)
    async def dellog(self, ctx: commands.Context, log_type: str) -> None:
        """Supprime la configuration de log pour un type."""
        log_type = log_type.lower()
        await self.db.execute(
            "DELETE FROM logs_config WHERE guild_id = ? AND log_type = ?",
            (ctx.guild.id, log_type),
        )
        await ctx.send(embed=self.embed.success(f"Log `{log_type}` désactivé."))

    @commands.command(name="logs")
    @perm(7)
    async def logs_list(self, ctx: commands.Context) -> None:
        """Affiche la configuration actuelle des logs."""
        rows = await self.db.fetchall(
            "SELECT log_type, channel_id FROM logs_config WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun log configuré."))
            return
        lines = []
        for r in rows:
            chan = ctx.guild.get_channel(int(r["channel_id"]))
            lines.append(
                f"• `{r['log_type']}` → {chan.mention if chan else 'salon introuvable'}"
            )
        await ctx.send(embed=self.embed.custom(
            description="\n".join(lines), title="📜 Configuration des logs"
        ))

    # --- Listeners -----------------------------------------------------

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        content = message.content[:1000] if message.content else "*(vide)*"
        embed = self.embed.custom(
            title="🗑️ Message supprimé",
            description=(
                f"**Auteur :** {message.author.mention} (`{message.author.id}`)\n"
                f"**Salon :** {message.channel.mention}\n\n"
                f"**Contenu :**\n{content}"
            ),
        )
        if message.attachments:
            embed.add_field(
                name="Pièces jointes",
                value="\n".join(a.url for a in message.attachments)[:1024],
                inline=False,
            )
        await self._send_log(message.guild.id, "messages", embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return
        embed = self.embed.custom(
            title="✏️ Message édité",
            description=(
                f"**Auteur :** {before.author.mention}\n"
                f"**Salon :** {before.channel.mention} · "
                f"[Aller au message]({after.jump_url})"
            ),
        )
        embed.add_field(
            name="Avant",
            value=(before.content[:1000] if before.content else "*(vide)*") or "*(vide)*",
            inline=False,
        )
        embed.add_field(
            name="Après",
            value=(after.content[:1000] if after.content else "*(vide)*") or "*(vide)*",
            inline=False,
        )
        await self._send_log(before.guild.id, "messages", embed)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        embed = self.embed.custom(
            title="📥 Nouveau membre",
            description=(
                f"{member.mention} a rejoint le serveur.\n"
                f"**Compte créé :** <t:{int(member.created_at.timestamp())}:R>\n"
                f"**Total membres :** {member.guild.member_count}"
            ),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self._send_log(member.guild.id, "members", embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        embed = self.embed.custom(
            title="📤 Membre parti",
            description=(
                f"{member} (`{member.id}`) a quitté le serveur.\n"
                f"**Membres restants :** {member.guild.member_count}"
            ),
        )
        if roles:
            embed.add_field(name="Rôles", value=", ".join(roles)[:1024], inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await self._send_log(member.guild.id, "members", embed)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.bot:
            return
        # Role changes
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        added = after_roles - before_roles
        removed = before_roles - after_roles
        if added or removed:
            embed = self.embed.custom(
                title="🔧 Rôles modifiés",
                description=f"**Membre :** {after.mention}",
            )
            if added:
                embed.add_field(
                    name="Ajoutés",
                    value=", ".join(r.mention for r in added),
                    inline=False,
                )
            if removed:
                embed.add_field(
                    name="Retirés",
                    value=", ".join(r.mention for r in removed),
                    inline=False,
                )
            await self._send_log(after.guild.id, "roles", embed)

        # Nickname change
        if before.nick != after.nick:
            embed = self.embed.custom(
                title="✏️ Pseudo modifié",
                description=(
                    f"**Membre :** {after.mention}\n"
                    f"**Avant :** `{before.nick or before.name}`\n"
                    f"**Après :** `{after.nick or after.name}`"
                ),
            )
            await self._send_log(after.guild.id, "members", embed)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        if before.channel == after.channel:
            return
        if before.channel is None and after.channel is not None:
            desc = f"{member.mention} a rejoint {after.channel.mention}"
        elif before.channel is not None and after.channel is None:
            desc = f"{member.mention} a quitté {before.channel.mention}"
        else:
            desc = (
                f"{member.mention} a changé de salon : "
                f"{before.channel.mention} → {after.channel.mention}"
            )
        embed = self.embed.custom(title="🔊 Salon vocal", description=desc)
        await self._send_log(member.guild.id, "voice", embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = self.embed.custom(
            title="🔨 Ban",
            description=f"{user} (`{user.id}`) a été banni du serveur.",
        )
        await self._send_log(guild.id, "moderation", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        embed = self.embed.custom(
            title="✅ Unban",
            description=f"{user} (`{user.id}`) a été débanni.",
        )
        await self._send_log(guild.id, "moderation", embed)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        embed = self.embed.custom(
            title="➕ Rôle créé",
            description=f"{role.mention} (`{role.id}`)",
        )
        await self._send_log(role.guild.id, "roles", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        embed = self.embed.custom(
            title="➖ Rôle supprimé",
            description=f"`{role.name}` (`{role.id}`)",
        )
        await self._send_log(role.guild.id, "roles", embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Logs(bot))

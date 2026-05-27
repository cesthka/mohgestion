"""Moderation commands: ban, kick, mute, warn, clear, lock, slowmode..."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands, tasks

from config.config import MAX_CLEAR_MESSAGES, MAX_WARNS_DISPLAY
from utils.checks import perm
from utils.time_parser import format_duration, parse_duration

log = logging.getLogger(__name__)


class Moderation(commands.Cog, name="Modération"):
    """Commands to enforce server rules."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]
        self.check_tempbans.start()

    def cog_unload(self) -> None:
        self.check_tempbans.cancel()

    # --- Helpers -------------------------------------------------------

    async def _record_sanction(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        s_type: str,
        reason: Optional[str],
        duration: Optional[int] = None,
        active: int = 1,
    ) -> int:
        await self.db.execute(
            "INSERT INTO sanctions "
            "(guild_id, user_id, moderator_id, type, reason, duration, timestamp, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, moderator_id, s_type, reason, duration, int(time.time()), active),
        )
        row = await self.db.fetchone("SELECT last_insert_rowid() AS id")
        return int(row["id"]) if row else 0

    @staticmethod
    def _parse_duration_or_none(arg: Optional[str]) -> tuple[Optional[int], Optional[str]]:
        """Try parsing `arg` as a duration. Returns (seconds, leftover_text)."""
        if not arg:
            return None, None
        secs = parse_duration(arg)
        return secs, None

    # --- BAN ----------------------------------------------------------

    @commands.hybrid_command(name="ban", description="Banni un membre (optionnel: durée + raison).")
    @perm(3)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.User,
        duration: Optional[str] = None,
        *,
        reason: Optional[str] = "Non spécifiée",
    ) -> None:
        """Banni un membre, optionnellement de manière temporaire."""
        # Parse duration if provided
        secs: Optional[int] = None
        if duration:
            parsed = parse_duration(duration)
            if parsed is None:
                # treat as part of reason
                reason = f"{duration} {reason}" if reason else duration
            else:
                secs = parsed

        # Self / bot protection
        if user.id == ctx.author.id:
            await ctx.send(embed=self.embed.error("Tu ne peux pas te bannir toi-même."))
            return
        if user.id == self.bot.user.id:
            await ctx.send(embed=self.embed.error("Je ne vais pas me bannir moi-même."))
            return

        member = ctx.guild.get_member(user.id)
        if member and isinstance(ctx.author, discord.Member):
            if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
                await ctx.send(embed=self.embed.error(
                    "Tu ne peux pas bannir un membre avec un rôle supérieur ou égal au tien."
                ))
                return

        try:
            await ctx.guild.ban(user, reason=f"{ctx.author}: {reason}", delete_message_days=0)
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission de bannir ce membre."))
            return

        case_id = await self._record_sanction(
            ctx.guild.id, user.id, ctx.author.id,
            "tempban" if secs else "ban", reason, secs,
        )

        await ctx.send(embed=self.embed.mod_action(
            "Ban" + (f" ({format_duration(secs)})" if secs else ""),
            user, ctx.author, reason,
            duration=format_duration(secs) if secs else None,
            case_id=case_id,
        ))

    @commands.hybrid_command(name="unban", description="Débanni un utilisateur via son ID.")
    @perm(3)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "Non spécifiée") -> None:
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"{ctx.author}: {reason}")
        except discord.NotFound:
            await ctx.send(embed=self.embed.error("Cet utilisateur n'est pas banni."))
            return
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return

        # Mark active tempbans inactive
        await self.db.execute(
            "UPDATE sanctions SET active = 0 "
            "WHERE guild_id = ? AND user_id = ? AND type IN ('ban', 'tempban') AND active = 1",
            (ctx.guild.id, user_id),
        )
        case_id = await self._record_sanction(
            ctx.guild.id, user_id, ctx.author.id, "unban", reason
        )
        await ctx.send(embed=self.embed.mod_action(
            "Unban", user, ctx.author, reason, case_id=case_id
        ))

    # --- KICK ---------------------------------------------------------

    @commands.hybrid_command(name="kick", description="Expulse un membre.")
    @perm(3)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Non spécifiée",
    ) -> None:
        if member.id == ctx.author.id:
            await ctx.send(embed=self.embed.error("Tu ne peux pas t'expulser toi-même."))
            return
        if isinstance(ctx.author, discord.Member):
            if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
                await ctx.send(embed=self.embed.error(
                    "Tu ne peux pas kick un membre avec un rôle supérieur ou égal au tien."
                ))
                return
        try:
            await member.kick(reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return

        case_id = await self._record_sanction(
            ctx.guild.id, member.id, ctx.author.id, "kick", reason
        )
        await ctx.send(embed=self.embed.mod_action(
            "Kick", member, ctx.author, reason, case_id=case_id
        ))

    # --- MUTE (Discord native timeout) --------------------------------

    @commands.hybrid_command(name="mute", description="Met un membre en timeout pour une durée donnée.")
    @perm(3)
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: str,
        *,
        reason: str = "Non spécifiée",
    ) -> None:
        secs = parse_duration(duration)
        if secs is None or secs <= 0:
            await ctx.send(embed=self.embed.error(
                "Durée invalide. Exemples : `10m`, `1h`, `2d`, `1h30m`."
            ))
            return
        # Discord caps timeout at 28 days
        max_secs = 28 * 24 * 3600
        if secs > max_secs:
            secs = max_secs

        try:
            await member.timeout(timedelta(seconds=secs), reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return

        case_id = await self._record_sanction(
            ctx.guild.id, member.id, ctx.author.id, "mute", reason, secs
        )
        await ctx.send(embed=self.embed.mod_action(
            "Mute", member, ctx.author, reason,
            duration=format_duration(secs), case_id=case_id,
        ))

    @commands.hybrid_command(name="unmute", description="Retire le timeout d'un membre.")
    @perm(3)
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Non spécifiée") -> None:
        try:
            await member.timeout(None, reason=f"{ctx.author}: {reason}")
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return
        case_id = await self._record_sanction(
            ctx.guild.id, member.id, ctx.author.id, "unmute", reason
        )
        await ctx.send(embed=self.embed.mod_action(
            "Unmute", member, ctx.author, reason, case_id=case_id
        ))

    # --- WARN ----------------------------------------------------------

    @commands.hybrid_command(name="warn", description="Avertit un membre (stocké en base).")
    @perm(3)
    async def warn(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: str = "Non spécifiée",
    ) -> None:
        if member.bot:
            await ctx.send(embed=self.embed.error("Tu ne peux pas warn un bot."))
            return
        case_id = await self._record_sanction(
            ctx.guild.id, member.id, ctx.author.id, "warn", reason
        )
        await ctx.send(embed=self.embed.mod_action(
            "Warn", member, ctx.author, reason, case_id=case_id
        ))
        # DM the user
        try:
            await member.send(embed=self.embed.warning(
                f"Tu as reçu un avertissement sur **{ctx.guild.name}**.\n"
                f"**Raison :** {reason}"
            ))
        except discord.HTTPException:
            pass

    @commands.hybrid_command(name="warns", description="Affiche l'historique des warns d'un membre.")
    @perm(1)
    async def warns(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        target = member or ctx.author
        rows = await self.db.fetchall(
            "SELECT id, moderator_id, reason, timestamp FROM sanctions "
            "WHERE guild_id = ? AND user_id = ? AND type = 'warn' AND active = 1 "
            "ORDER BY id DESC LIMIT ?",
            (ctx.guild.id, target.id, MAX_WARNS_DISPLAY),
        )
        if not rows:
            await ctx.send(embed=self.embed.info(
                f"{target.mention} n'a aucun avertissement."
            ))
            return

        embed = self.embed.custom(
            description=f"{len(rows)} avertissement(s) pour {target.mention}",
            title="📋 Historique des warns",
        )
        for r in rows:
            mod = ctx.guild.get_member(int(r["moderator_id"]))
            mod_name = mod.mention if mod else f"<@{r['moderator_id']}>"
            ts = int(r["timestamp"])
            embed.add_field(
                name=f"Warn #{r['id']}",
                value=f"**Modérateur :** {mod_name}\n"
                      f"**Date :** <t:{ts}:f>\n"
                      f"**Raison :** {r['reason'] or 'Non spécifiée'}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="delwarn", description="Supprime un avertissement par ID.")
    @perm(5)
    async def delwarn(self, ctx: commands.Context, warn_id: int) -> None:
        row = await self.db.fetchone(
            "SELECT id FROM sanctions WHERE id = ? AND guild_id = ? AND type = 'warn'",
            (warn_id, ctx.guild.id),
        )
        if not row:
            await ctx.send(embed=self.embed.error("Aucun warn trouvé avec cet ID."))
            return
        await self.db.execute(
            "UPDATE sanctions SET active = 0 WHERE id = ?",
            (warn_id,),
        )
        await ctx.send(embed=self.embed.success(f"Warn `#{warn_id}` supprimé."))

    # --- CLEAR --------------------------------------------------------

    @commands.hybrid_command(name="clear", description="Supprime un nombre de messages dans le salon.")
    @perm(3)
    @commands.bot_has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, amount: int) -> None:
        if amount <= 0:
            await ctx.send(embed=self.embed.error("Nombre invalide."))
            return
        amount = min(amount, MAX_CLEAR_MESSAGES)

        # For prefix commands, delete the invocation too
        if ctx.interaction is None:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            deleted = await ctx.channel.purge(limit=amount)
            msg = await ctx.send(embed=self.embed.success(
                f"**{len(deleted)}** message(s) supprimé(s)."
            ))
            try:
                await msg.delete(delay=4)
            except discord.HTTPException:
                pass
        else:
            await ctx.defer(ephemeral=True)
            deleted = await ctx.channel.purge(limit=amount)
            await ctx.send(embed=self.embed.success(
                f"**{len(deleted)}** message(s) supprimé(s)."
            ), ephemeral=True)

    # --- LOCK / UNLOCK -----------------------------------------------

    @commands.hybrid_command(name="lock", description="Verrouille le salon courant.")
    @perm(5)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        chan = channel or ctx.channel
        overwrite = chan.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        try:
            await chan.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Lock par {ctx.author}")
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return
        await ctx.send(embed=self.embed.success(f"🔒 {chan.mention} verrouillé."))

    @commands.hybrid_command(name="unlock", description="Déverrouille le salon courant.")
    @perm(5)
    @commands.bot_has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        chan = channel or ctx.channel
        overwrite = chan.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        try:
            await chan.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlock par {ctx.author}")
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return
        await ctx.send(embed=self.embed.success(f"🔓 {chan.mention} déverrouillé."))

    # --- SLOWMODE -----------------------------------------------------

    @commands.hybrid_command(name="slowmode", description="Définit le slowmode du salon (en secondes).")
    @perm(5)
    @commands.bot_has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, seconds: int) -> None:
        if seconds < 0 or seconds > 21600:
            await ctx.send(embed=self.embed.error("Le slowmode doit être entre 0 et 21600 secondes (6h)."))
            return
        try:
            await ctx.channel.edit(slowmode_delay=seconds, reason=f"Slowmode par {ctx.author}")
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))
            return
        if seconds == 0:
            await ctx.send(embed=self.embed.success("Slowmode désactivé."))
        else:
            await ctx.send(embed=self.embed.success(f"Slowmode défini à **{seconds}** seconde(s)."))

    # --- Background: tempban expiration ------------------------------

    @tasks.loop(seconds=30)
    async def check_tempbans(self) -> None:
        """Unban members whose tempban has expired."""
        if self.db._conn is None:
            return
        now = int(time.time())
        rows = await self.db.fetchall(
            "SELECT id, guild_id, user_id, timestamp, duration "
            "FROM sanctions WHERE type = 'tempban' AND active = 1",
        )
        for r in rows:
            duration = r["duration"]
            if duration is None:
                continue
            expires = int(r["timestamp"]) + int(duration)
            if expires <= now:
                guild = self.bot.get_guild(int(r["guild_id"]))
                if not guild:
                    continue
                try:
                    user = await self.bot.fetch_user(int(r["user_id"]))
                    await guild.unban(user, reason="Tempban expiré (automatique)")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                await self.db.execute(
                    "UPDATE sanctions SET active = 0 WHERE id = ?",
                    (int(r["id"]),),
                )

    @check_tempbans.before_loop
    async def _before_check_tempbans(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))

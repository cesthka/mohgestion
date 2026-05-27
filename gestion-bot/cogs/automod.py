"""Automoderation: anti-spam, anti-link, anti-badword, anti-mention, anti-caps."""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Deque

import discord
from discord.ext import commands

from utils.checks import perm

log = logging.getLogger(__name__)

# Discord invite regex
_DISCORD_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)/[\w-]+",
    re.IGNORECASE,
)
# Generic URL regex
_URL_RE = re.compile(r"https?://[^\s/$.?#].[^\s]*", re.IGNORECASE)


class AutoMod(commands.Cog, name="Automod"):
    """Automatic moderation."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]
        # In-memory spam tracking: {(guild_id, user_id): deque[timestamps]}
        self._msg_times: dict[tuple[int, int], Deque[float]] = defaultdict(lambda: deque(maxlen=30))

    # --- Helpers -------------------------------------------------------

    async def _is_whitelisted(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator or member.bot:
            return True
        # User in whitelist
        if await self.db.fetchone(
            "SELECT 1 FROM automod_whitelist WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
            (member.guild.id, member.id),
        ):
            return True
        # Any role in whitelist
        role_ids = [r.id for r in member.roles]
        if role_ids:
            placeholders = ",".join("?" * len(role_ids))
            row = await self.db.fetchone(
                f"SELECT 1 FROM automod_whitelist WHERE guild_id = ? AND target_type = 'role' "
                f"AND target_id IN ({placeholders})",
                (member.guild.id, *role_ids),
            )
            if row:
                return True
        return False

    async def _punish(self, message: discord.Message, reason: str) -> None:
        """Increment infractions and apply progressive sanction."""
        guild_id = message.guild.id
        user_id = message.author.id
        now = int(time.time())

        await self.db.execute(
            "INSERT INTO automod_infractions (guild_id, user_id, count, last_at) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
            "count = count + 1, last_at = excluded.last_at",
            (guild_id, user_id, now),
        )
        row = await self.db.fetchone(
            "SELECT count FROM automod_infractions WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        )
        count = int(row["count"]) if row else 1

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        try:
            warn = await message.channel.send(embed=self.embed.warning(
                f"{message.author.mention}, {reason} (infraction #{count})"
            ))
            await warn.delete(delay=6)
        except discord.HTTPException:
            pass

        # Progressive sanctions
        if isinstance(message.author, discord.Member):
            member = message.author
            try:
                if count == 3:
                    await member.timeout(timedelta(minutes=5), reason=f"Automod: {reason}")
                elif count == 5:
                    await member.timeout(timedelta(hours=1), reason=f"Automod: {reason}")
                elif count >= 7:
                    await member.kick(reason=f"Automod: trop d'infractions ({reason})")
                    await self.db.execute(
                        "DELETE FROM automod_infractions WHERE guild_id = ? AND user_id = ?",
                        (guild_id, user_id),
                    )
            except discord.Forbidden:
                pass

    # --- Listener ------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        config = await self.db.get_config(message.guild.id)
        if not config:
            return
        if await self._is_whitelisted(message.author):
            return

        content_lower = (message.content or "").lower()

        # --- Anti-link ---
        if config["antilink_enabled"]:
            mode = config["antilink_mode"]
            if mode == "discord" and _DISCORD_INVITE_RE.search(message.content):
                await self._punish(message, "les invitations Discord sont interdites.")
                return
            if mode == "all" and _URL_RE.search(message.content):
                await self._punish(message, "les liens sont interdits.")
                return

        # --- Anti-mass mention ---
        if config["antimassmention_enabled"]:
            limit = int(config["antimassmention_count"])
            unique_mentions = len(set(m.id for m in message.mentions))
            if unique_mentions >= limit:
                await self._punish(message, f"trop de mentions ({unique_mentions}).")
                return

        # --- Anti-caps ---
        if config["anticaps_enabled"] and len(message.content) >= 10:
            letters = [c for c in message.content if c.isalpha()]
            if letters:
                uppers = sum(1 for c in letters if c.isupper())
                if uppers / len(letters) > 0.7:
                    await self._punish(message, "trop de majuscules.")
                    return

        # --- Anti-badword ---
        if config["antibadword_enabled"]:
            rows = await self.db.fetchall(
                "SELECT word FROM badwords WHERE guild_id = ?",
                (message.guild.id,),
            )
            for r in rows:
                w = r["word"].lower()
                # Word boundary match to avoid false positives
                if re.search(rf"\b{re.escape(w)}\b", content_lower):
                    await self._punish(message, f"mot interdit (`{w}`).")
                    return

        # --- Anti-spam ---
        if config["antispam_enabled"]:
            limit_msgs = int(config["antispam_messages"])
            window = int(config["antispam_seconds"])
            key = (message.guild.id, message.author.id)
            now = time.time()
            buf = self._msg_times[key]
            buf.append(now)
            # Trim old
            while buf and (now - buf[0]) > window:
                buf.popleft()
            if len(buf) > limit_msgs:
                buf.clear()
                await self._punish(message, f"spam détecté ({limit_msgs}+ messages en {window}s).")
                return

    # --- Commands ------------------------------------------------------

    @commands.group(name="antispam", invoke_without_command=True)
    @perm(5)
    async def antispam(self, ctx: commands.Context, mode: str = None) -> None:
        """Active/désactive l'anti-spam, ou affiche la config.

        Exemple : `+antispam on` ou `+antispam 5/5` (5 msgs en 5 secondes).
        """
        if mode is None:
            config = await self.db.get_config(ctx.guild.id)
            status = "✅ activé" if config["antispam_enabled"] else "❌ désactivé"
            await ctx.send(embed=self.embed.info(
                f"Anti-spam : {status}\n"
                f"Limite : {config['antispam_messages']} messages / {config['antispam_seconds']}s"
            ))
            return
        if mode.lower() == "on":
            await self.db.update_config(ctx.guild.id, antispam_enabled=1)
            await ctx.send(embed=self.embed.success("Anti-spam activé."))
        elif mode.lower() == "off":
            await self.db.update_config(ctx.guild.id, antispam_enabled=0)
            await ctx.send(embed=self.embed.success("Anti-spam désactivé."))
        elif "/" in mode:
            try:
                m, s = mode.split("/")
                m_i, s_i = int(m), int(s)
                if m_i < 2 or s_i < 1:
                    raise ValueError()
                await self.db.update_config(
                    ctx.guild.id,
                    antispam_messages=m_i, antispam_seconds=s_i, antispam_enabled=1,
                )
                await ctx.send(embed=self.embed.success(
                    f"Anti-spam : {m_i} messages / {s_i} secondes."
                ))
            except ValueError:
                await ctx.send(embed=self.embed.error("Format invalide. Ex: `4/5`."))
        else:
            await ctx.send(embed=self.embed.error("Usage : `antispam on|off|<msgs>/<sec>`."))

    @commands.group(name="antilink", invoke_without_command=True)
    @perm(5)
    async def antilink(self, ctx: commands.Context, mode: str = None) -> None:
        """`on`, `off`, `discord` (invites Discord uniquement), `all`."""
        if mode is None:
            config = await self.db.get_config(ctx.guild.id)
            status = "✅" if config["antilink_enabled"] else "❌"
            await ctx.send(embed=self.embed.info(
                f"Anti-link : {status} (mode: `{config['antilink_mode']}`)"
            ))
            return
        m = mode.lower()
        if m == "on":
            await self.db.update_config(ctx.guild.id, antilink_enabled=1)
            await ctx.send(embed=self.embed.success("Anti-link activé."))
        elif m == "off":
            await self.db.update_config(ctx.guild.id, antilink_enabled=0)
            await ctx.send(embed=self.embed.success("Anti-link désactivé."))
        elif m in ("discord", "all"):
            await self.db.update_config(ctx.guild.id, antilink_mode=m, antilink_enabled=1)
            await ctx.send(embed=self.embed.success(f"Anti-link en mode `{m}`."))
        else:
            await ctx.send(embed=self.embed.error("Usage : `antilink on|off|discord|all`."))

    @commands.group(name="antibadword", invoke_without_command=True)
    @perm(5)
    async def antibadword(self, ctx: commands.Context, mode: str = None) -> None:
        """`on`, `off`, `add <mot>`, `del <mot>`, `list`."""
        if mode is None:
            config = await self.db.get_config(ctx.guild.id)
            status = "✅" if config["antibadword_enabled"] else "❌"
            await ctx.send(embed=self.embed.info(f"Anti-badword : {status}"))
            return
        m = mode.lower()
        if m == "on":
            await self.db.update_config(ctx.guild.id, antibadword_enabled=1)
            await ctx.send(embed=self.embed.success("Anti-badword activé."))
        elif m == "off":
            await self.db.update_config(ctx.guild.id, antibadword_enabled=0)
            await ctx.send(embed=self.embed.success("Anti-badword désactivé."))
        else:
            await ctx.send(embed=self.embed.error("Usage : `antibadword on|off|add <mot>|del <mot>|list`."))

    @antibadword.command(name="add")
    @perm(5)
    async def antibadword_add(self, ctx: commands.Context, *, word: str) -> None:
        word = word.strip().lower()
        if not word:
            await ctx.send(embed=self.embed.error("Mot vide."))
            return
        await self.db.execute(
            "INSERT OR IGNORE INTO badwords (guild_id, word) VALUES (?, ?)",
            (ctx.guild.id, word),
        )
        await ctx.send(embed=self.embed.success(f"Mot ajouté à la liste."))

    @antibadword.command(name="del")
    @perm(5)
    async def antibadword_del(self, ctx: commands.Context, *, word: str) -> None:
        word = word.strip().lower()
        await self.db.execute(
            "DELETE FROM badwords WHERE guild_id = ? AND word = ?",
            (ctx.guild.id, word),
        )
        await ctx.send(embed=self.embed.success("Mot retiré."))

    @antibadword.command(name="list")
    @perm(5)
    async def antibadword_list(self, ctx: commands.Context) -> None:
        rows = await self.db.fetchall(
            "SELECT word FROM badwords WHERE guild_id = ? ORDER BY word",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun mot dans la liste."))
            return
        words = ", ".join(f"||{r['word']}||" for r in rows)
        if len(words) > 4000:
            words = words[:3990] + "..."
        await ctx.send(embed=self.embed.custom(
            description=words, title="🚫 Mots interdits"
        ))

    @commands.group(name="antimassmention", invoke_without_command=True)
    @perm(5)
    async def antimassmention(self, ctx: commands.Context, value: str = None) -> None:
        """`on`, `off`, ou un nombre (seuil de mentions)."""
        if value is None:
            config = await self.db.get_config(ctx.guild.id)
            status = "✅" if config["antimassmention_enabled"] else "❌"
            await ctx.send(embed=self.embed.info(
                f"Anti mass-mention : {status} (seuil: {config['antimassmention_count']})"
            ))
            return
        v = value.lower()
        if v == "on":
            await self.db.update_config(ctx.guild.id, antimassmention_enabled=1)
            await ctx.send(embed=self.embed.success("Anti mass-mention activé."))
        elif v == "off":
            await self.db.update_config(ctx.guild.id, antimassmention_enabled=0)
            await ctx.send(embed=self.embed.success("Anti mass-mention désactivé."))
        else:
            try:
                n = int(v)
                if n < 2:
                    raise ValueError()
                await self.db.update_config(
                    ctx.guild.id, antimassmention_count=n, antimassmention_enabled=1
                )
                await ctx.send(embed=self.embed.success(f"Seuil : {n} mentions max."))
            except ValueError:
                await ctx.send(embed=self.embed.error("Usage : `antimassmention on|off|<nombre>`."))

    @commands.command(name="anticaps")
    @perm(5)
    async def anticaps(self, ctx: commands.Context, mode: str = None) -> None:
        """`on` ou `off`. Détecte les messages avec >70% de majuscules."""
        if mode is None:
            config = await self.db.get_config(ctx.guild.id)
            status = "✅" if config["anticaps_enabled"] else "❌"
            await ctx.send(embed=self.embed.info(f"Anti-caps : {status}"))
            return
        if mode.lower() == "on":
            await self.db.update_config(ctx.guild.id, anticaps_enabled=1)
            await ctx.send(embed=self.embed.success("Anti-caps activé."))
        elif mode.lower() == "off":
            await self.db.update_config(ctx.guild.id, anticaps_enabled=0)
            await ctx.send(embed=self.embed.success("Anti-caps désactivé."))
        else:
            await ctx.send(embed=self.embed.error("Usage : `anticaps on|off`."))

    @commands.group(name="whitelist", invoke_without_command=True)
    @perm(5)
    async def whitelist(self, ctx: commands.Context) -> None:
        rows = await self.db.fetchall(
            "SELECT target_id, target_type FROM automod_whitelist WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun élément dans la whitelist automod."))
            return
        lines = []
        for r in rows:
            tid, ttype = int(r["target_id"]), r["target_type"]
            if ttype == "role":
                role = ctx.guild.get_role(tid)
                lines.append(f"🛡️ {role.mention if role else f'<@&{tid}>'}")
            else:
                user = ctx.guild.get_member(tid)
                lines.append(f"👤 {user.mention if user else f'<@{tid}>'}")
        await ctx.send(embed=self.embed.custom(
            description="\n".join(lines), title="🟢 Whitelist Automod"
        ))

    @whitelist.command(name="add")
    @perm(5)
    async def whitelist_add(self, ctx: commands.Context, target: discord.Role | discord.Member) -> None:
        ttype = "role" if isinstance(target, discord.Role) else "user"
        await self.db.execute(
            "INSERT OR IGNORE INTO automod_whitelist (guild_id, target_id, target_type) "
            "VALUES (?, ?, ?)",
            (ctx.guild.id, target.id, ttype),
        )
        await ctx.send(embed=self.embed.success(f"{target.mention} ajouté à la whitelist."))

    @whitelist.command(name="del")
    @perm(5)
    async def whitelist_del(self, ctx: commands.Context, target: discord.Role | discord.Member) -> None:
        ttype = "role" if isinstance(target, discord.Role) else "user"
        await self.db.execute(
            "DELETE FROM automod_whitelist "
            "WHERE guild_id = ? AND target_id = ? AND target_type = ?",
            (ctx.guild.id, target.id, ttype),
        )
        await ctx.send(embed=self.embed.success(f"{target.mention} retiré de la whitelist."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))

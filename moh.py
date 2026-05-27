"""
╔══════════════════════════════════════════════════════════════════════════╗
║                        MOH — Bot de gestion                              ║
║   Modération + logs. Hiérarchie : Owner > Sys > WL. made by moh.         ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import discord
from discord.ext import commands, tasks
import os
import sys
import sqlite3
import json
import re
import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ========================= CONFIG =========================
BOT_TOKEN = os.environ.get("TOKEN_MOH") or os.environ.get("TOKEN")
if not BOT_TOKEN:
    print("[ERREUR CRITIQUE] Aucune variable d'environnement TOKEN_MOH ni TOKEN trouvée.")
    sys.exit(1)

PARIS_TZ = ZoneInfo("Europe/Paris")
DEFAULT_OWNER_IDS = [1279358145151373352]
DEFAULT_PREFIX = "+"

DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    print("[ERREUR CRITIQUE] DATA_DIR non défini. Configure DATA_DIR=/data dans Railway.")
    sys.exit(1)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "moh.db")

BOT_NAME = "Moh"
FOOTER_TEXT = "Moh ・ Bot de gestion ・ made by moh"
MAX_CLEAR = 100

# Couleurs (style Jinrō)
COLOR_DEFAULT = 0x2b2d31
COLOR_SUCCESS = 0x43b581
COLOR_ERROR = 0xf04747
COLOR_WARNING = 0xfaa61a
COLOR_INFO = 0x3498db
COLOR_MOD = 0xeb459e

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)
log = logging.getLogger("moh")

_prefix_cache = {"value": None}


# ========================= DATABASE =========================

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")

    # Rangs : 3 = Owner, 2 = Sys, 1 = WL, 0 = Aucun
    c.execute("CREATE TABLE IF NOT EXISTS ranks (user_id TEXT PRIMARY KEY, rank INTEGER NOT NULL)")

    c.execute("""CREATE TABLE IF NOT EXISTS bot_bans (
        user_id TEXT PRIMARY KEY, banned_by TEXT, banned_at TEXT
    )""")

    # Logs par type
    c.execute("""CREATE TABLE IF NOT EXISTS log_channels (
        guild_id TEXT NOT NULL, log_type TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        PRIMARY KEY (guild_id, log_type)
    )""")

    # Salons autorisés
    c.execute("""CREATE TABLE IF NOT EXISTS allowed_channels (
        guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
        added_by TEXT, added_at TEXT,
        PRIMARY KEY (guild_id, channel_id)
    )""")

    # Sanctions (historique)
    c.execute("""CREATE TABLE IF NOT EXISTS sanctions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        moderator_id TEXT NOT NULL,
        type TEXT NOT NULL,
        reason TEXT,
        duration INTEGER,
        timestamp TEXT NOT NULL,
        active INTEGER DEFAULT 1
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sanctions_user ON sanctions(guild_id, user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sanctions_active ON sanctions(active, type)")

    # Defaults
    c.execute("INSERT OR IGNORE INTO config VALUES ('prefix', ?)", (DEFAULT_PREFIX,))
    c.execute(
        "INSERT OR IGNORE INTO config VALUES ('owner_ids', ?)",
        (json.dumps([str(i) for i in DEFAULT_OWNER_IDS]),)
    )

    conn.commit()
    conn.close()


# ---- Config générique ----

def get_config(key):
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_config(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO config VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()
    if key == "prefix":
        _prefix_cache["value"] = str(value)


def get_prefix_cached():
    if _prefix_cache["value"] is None:
        _prefix_cache["value"] = get_config("prefix") or DEFAULT_PREFIX
    return _prefix_cache["value"]


# ---- Rangs (Owner=3, Sys=2, WL=1) ----

def get_rank_db(user_id):
    owner_ids_raw = get_config("owner_ids")
    if owner_ids_raw:
        owner_ids = json.loads(owner_ids_raw)
        if str(user_id) in owner_ids:
            return 3
    conn = get_db()
    row = conn.execute("SELECT rank FROM ranks WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row["rank"] if row else 0


def set_rank_db(user_id, rank):
    conn = get_db()
    if rank == 0:
        conn.execute("DELETE FROM ranks WHERE user_id = ?", (str(user_id),))
    else:
        conn.execute("INSERT OR REPLACE INTO ranks VALUES (?, ?)", (str(user_id), rank))
    conn.commit()
    conn.close()


def get_ranks_by_level(level):
    conn = get_db()
    rows = conn.execute("SELECT user_id FROM ranks WHERE rank = ?", (level,)).fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def has_min_rank(user_id, minimum):
    return get_rank_db(user_id) >= minimum


def rank_name(level):
    return {3: "Owner", 2: "Sys", 1: "WL", 0: "Aucun"}.get(level, "Aucun")


# ---- Ban bot ----

def is_bot_banned(user_id):
    conn = get_db()
    row = conn.execute("SELECT 1 FROM bot_bans WHERE user_id = ?", (str(user_id),)).fetchone()
    conn.close()
    return row is not None


def add_bot_ban(user_id, banned_by):
    conn = get_db()
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %Hh%M")
    conn.execute("INSERT OR REPLACE INTO bot_bans VALUES (?, ?, ?)",
                 (str(user_id), str(banned_by), now))
    conn.commit()
    conn.close()


def remove_bot_ban(user_id):
    conn = get_db()
    conn.execute("DELETE FROM bot_bans WHERE user_id = ?", (str(user_id),))
    conn.commit()
    conn.close()


# ---- Log channels (par type) ----

LOG_TYPES = ["moderation", "messages", "members", "voice", "roles"]


def get_log_channel(guild_id, log_type):
    conn = get_db()
    row = conn.execute(
        "SELECT channel_id FROM log_channels WHERE guild_id = ? AND log_type = ?",
        (str(guild_id), log_type)
    ).fetchone()
    conn.close()
    return row["channel_id"] if row else None


def set_log_channel(guild_id, log_type, channel_id):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO log_channels (guild_id, log_type, channel_id) VALUES (?, ?, ?)",
        (str(guild_id), log_type, str(channel_id))
    )
    conn.commit()
    conn.close()


def delete_log_channel(guild_id, log_type):
    conn = get_db()
    conn.execute(
        "DELETE FROM log_channels WHERE guild_id = ? AND log_type = ?",
        (str(guild_id), log_type)
    )
    conn.commit()
    conn.close()


def get_all_log_channels(guild_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT log_type, channel_id FROM log_channels WHERE guild_id = ?",
        (str(guild_id),)
    ).fetchall()
    conn.close()
    return {r["log_type"]: r["channel_id"] for r in rows}


# ---- Allowed channels ----

def add_allowed_channel(guild_id, channel_id, added_by):
    conn = get_db()
    now = datetime.now(PARIS_TZ).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO allowed_channels (guild_id, channel_id, added_by, added_at) VALUES (?, ?, ?, ?)",
        (str(guild_id), str(channel_id), str(added_by), now)
    )
    conn.commit()
    conn.close()


def remove_allowed_channel(guild_id, channel_id):
    conn = get_db()
    cur = conn.execute(
        "DELETE FROM allowed_channels WHERE guild_id = ? AND channel_id = ?",
        (str(guild_id), str(channel_id))
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def get_allowed_channels(guild_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT channel_id FROM allowed_channels WHERE guild_id = ?",
        (str(guild_id),)
    ).fetchall()
    conn.close()
    return [r["channel_id"] for r in rows]


def is_channel_allowed(guild_id, channel_id):
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM allowed_channels WHERE guild_id = ? AND channel_id = ? LIMIT 1",
        (str(guild_id), str(channel_id))
    ).fetchone()
    conn.close()
    return row is not None


# ---- Sanctions ----

def add_sanction(guild_id, user_id, moderator_id, type_, reason=None, duration=None):
    conn = get_db()
    now = datetime.now(PARIS_TZ).isoformat()
    cur = conn.execute(
        "INSERT INTO sanctions (guild_id, user_id, moderator_id, type, reason, duration, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(guild_id), str(user_id), str(moderator_id), type_, reason, duration, now)
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()
    return sid


def get_warns(guild_id, user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT id, moderator_id, reason, timestamp FROM sanctions "
        "WHERE guild_id = ? AND user_id = ? AND type = 'warn' AND active = 1 "
        "ORDER BY id DESC LIMIT 25",
        (str(guild_id), str(user_id))
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_warn(guild_id, warn_id):
    conn = get_db()
    cur = conn.execute(
        "UPDATE sanctions SET active = 0 WHERE id = ? AND guild_id = ? AND type = 'warn' AND active = 1",
        (warn_id, str(guild_id))
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def get_active_tempbans():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sanctions WHERE type = 'tempban' AND active = 1"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def deactivate_sanction(sanction_id):
    conn = get_db()
    conn.execute("UPDATE sanctions SET active = 0 WHERE id = ?", (sanction_id,))
    conn.commit()
    conn.close()


def get_history(guild_id, user_id, limit=20):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM sanctions WHERE guild_id = ? AND user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (str(guild_id), str(user_id), limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ========================= HELPERS =========================

def embed_color():
    return COLOR_DEFAULT


def base_embed(color=None):
    em = discord.Embed(color=color if color is not None else COLOR_DEFAULT)
    em.set_footer(text=FOOTER_TEXT)
    return em


def success_embed(title, desc=""):
    em = discord.Embed(title=title, description=desc, color=COLOR_SUCCESS)
    em.set_footer(text=FOOTER_TEXT)
    return em


def error_embed(title, desc=""):
    em = discord.Embed(title=title, description=desc, color=COLOR_ERROR)
    em.set_footer(text=FOOTER_TEXT)
    return em


def info_embed(title, desc=""):
    em = discord.Embed(title=title, description=desc, color=COLOR_INFO)
    em.set_footer(text=FOOTER_TEXT)
    return em


def warning_embed(title, desc=""):
    em = discord.Embed(title=title, description=desc, color=COLOR_WARNING)
    em.set_footer(text=FOOTER_TEXT)
    return em


def mod_embed(action, target_obj, target_id, moderator, reason=None, duration=None, case_id=None):
    em = discord.Embed(title=f"⚖️ {action}", color=COLOR_MOD)
    if target_obj is not None:
        em.add_field(name="Cible", value=f"{target_obj.mention} (`{target_obj.id}`)", inline=True)
        try:
            em.set_thumbnail(url=target_obj.display_avatar.url)
        except Exception:
            pass
    else:
        em.add_field(name="Cible", value=f"<@{target_id}> (`{target_id}`)", inline=True)
    em.add_field(name="Modérateur", value=moderator.mention, inline=True)
    if duration:
        em.add_field(name="Durée", value=duration, inline=True)
    em.add_field(name="Raison", value=reason if reason else "*Non spécifiée*", inline=False)
    if case_id is not None:
        em.set_footer(text=f"{FOOTER_TEXT} ・ Sanction #{case_id}")
    else:
        em.set_footer(text=FOOTER_TEXT)
    return em


def get_french_time():
    now = datetime.now(PARIS_TZ)
    JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    MOIS = ["janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    return f"{JOURS[now.weekday()]} {now.day} {MOIS[now.month-1]} {now.year} — {now.strftime('%Hh%M')}"


def format_duration_fr(seconds):
    if not seconds or seconds <= 0:
        return "0 sec"
    if seconds < 60:
        return f"{int(seconds)} sec"
    units = [
        (86400 * 365, "an", "ans"),
        (86400, "jour", "jours"),
        (3600, "heure", "heures"),
        (60, "minute", "minutes"),
        (1, "seconde", "secondes"),
    ]
    parts = []
    rem = int(seconds)
    for sec, sing, plur in units:
        n, rem = divmod(rem, sec)
        if n > 0:
            parts.append(f"{n} {sing if n == 1 else plur}")
        if len(parts) >= 2:
            break
    return ", ".join(parts) if parts else "0 sec"


DURATION_RE = re.compile(r"(\d+)\s*([smhdjwSMHDJW])", re.IGNORECASE)


def parse_duration(s):
    if not s:
        return None
    s = s.strip().lower().replace(" ", "")
    if s.isdigit():
        return int(s) * 60
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "j": 86400, "w": 604800}
    total = 0
    matched = False
    for m in DURATION_RE.finditer(s):
        n = int(m.group(1))
        u = m.group(2).lower()
        total += n * units.get(u, 60)
        matched = True
    return total if matched else None


async def resolve_member(ctx, user_input):
    if not user_input:
        return None
    try:
        member_id = int(str(user_input).strip("<@!>"))
        m = ctx.guild.get_member(member_id)
        if m:
            return m
    except (ValueError, AttributeError):
        pass
    try:
        return await commands.MemberConverter().convert(ctx, str(user_input))
    except commands.CommandError:
        return None


async def resolve_user_or_id(ctx, user_input):
    if not user_input:
        return None, None
    raw = str(user_input).strip()
    cleaned = raw.strip("<@!>")
    try:
        user_id = int(cleaned)
    except ValueError:
        try:
            m = await commands.MemberConverter().convert(ctx, raw)
            return m, m.id
        except commands.CommandError:
            pass
        try:
            u = await commands.UserConverter().convert(ctx, raw)
            return u, u.id
        except commands.CommandError:
            return None, None
    if ctx.guild:
        member = ctx.guild.get_member(user_id)
        if member:
            return member, user_id
    try:
        user = await bot.fetch_user(user_id)
        return user, user_id
    except discord.NotFound:
        return None, user_id
    except discord.HTTPException as e:
        log.warning(f"resolve_user_or_id: fetch_user({user_id}) a échoué : {e}")
        return None, user_id


async def resolve_channel(ctx, channel_input):
    clean = str(channel_input).strip("<#>")
    try:
        cid = int(clean)
        ch = ctx.guild.get_channel(cid)
        if ch:
            return ch, cid
    except ValueError:
        pass
    try:
        ch = await commands.TextChannelConverter().convert(ctx, str(channel_input))
        return ch, ch.id
    except commands.CommandError:
        return None, None


def format_user_display(display_obj, user_id):
    if display_obj is not None:
        return f"{display_obj.mention} (`{display_obj.id}`)"
    return f"<@{user_id}> (`{user_id}`) *(hors serveur)*"


async def check_ban(ctx):
    if is_bot_banned(ctx.author.id):
        em = error_embed(
            "⛔ Accès refusé",
            "Tu as été **banni du bot Moh**.\n"
            "Si tu penses que c'est une erreur, contacte un Sys ou un Owner."
        )
        await ctx.send(embed=em)
        return True
    return False


# ========================= BOT SETUP =========================

init_db()
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True


def get_prefix(bot_, message):
    return get_prefix_cached()


bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)


# ========================= GLOBAL CHECK =========================

class ChannelNotAllowedError(commands.CheckFailure):
    pass


@bot.check
async def check_allowed_channel(ctx):
    """Salon non autorisé → blocke sauf si rang Sys+ (rang >= 2)."""
    if has_min_rank(ctx.author.id, 2):
        return True
    if ctx.guild is None:
        return True
    if is_channel_allowed(ctx.guild.id, ctx.channel.id):
        return True
    raise ChannelNotAllowedError("Salon non autorisé.")


# ========================= EVENTS =========================

@bot.event
async def on_ready():
    log.info(f"Moh connecté : {bot.user} ({bot.user.id})")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.playing, name="au stake fils de hechek")
    )
    if not tempban_loop.is_running():
        tempban_loop.start()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        error = error.original
    if isinstance(error, ChannelNotAllowedError):
        try:
            await ctx.message.add_reaction("❌")
        except discord.HTTPException:
            pass
        return
    if isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
        await ctx.send(embed=error_embed("❌ Utilisateur introuvable", "Impossible de trouver cet utilisateur."))
    elif isinstance(error, commands.ChannelNotFound):
        await ctx.send(embed=error_embed("❌ Salon introuvable", "Ce salon n'existe pas."))
    elif isinstance(error, commands.RoleNotFound):
        await ctx.send(embed=error_embed("❌ Rôle introuvable", "Ce rôle n'existe pas."))
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=error_embed("❌ Argument manquant", f"Argument manquant : `{error.param.name}`."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=error_embed("❌ Argument invalide", str(error)))
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(embed=error_embed("⏰ Cooldown", f"Reviens dans {int(error.retry_after)}s."))
    elif isinstance(error, commands.NoPrivateMessage):
        try:
            await ctx.send(embed=error_embed("❌ Pas en DM", "Cette commande ne fonctionne qu'en serveur."))
        except discord.HTTPException:
            pass
    elif isinstance(error, discord.Forbidden):
        await ctx.send(embed=error_embed(
            "❌ Permissions Discord insuffisantes",
            "Le bot n'a pas les permissions Discord nécessaires."
        ))
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        log.error(
            f"Erreur '{ctx.command}' par {ctx.author} : {error}\n"
            + "".join(traceback.format_exception(type(error), error, error.__traceback__))
        )
        try:
            await ctx.send(embed=error_embed(
                "❌ Erreur interne",
                "Une erreur inattendue est survenue."
            ))
        except discord.HTTPException:
            pass


# ========================= SEND LOG =========================

async def send_log(guild, log_type, title, *, author=None, target=None,
                   desc=None, color=None, fields=None):
    channel_id = get_log_channel(guild.id, log_type)
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    em = discord.Embed(
        title=title,
        color=color if color is not None else COLOR_DEFAULT,
        timestamp=datetime.now(timezone.utc),
    )
    if author:
        em.add_field(name="Auteur", value=f"{author.mention} (`{author.id}`)", inline=True)
    if target:
        em.add_field(name="Cible", value=f"{target.mention} (`{target.id}`)", inline=True)
    if desc:
        em.description = desc
    if fields:
        for name, value, inline in fields:
            em.add_field(name=name, value=value, inline=inline)
    em.set_footer(text=FOOTER_TEXT)
    try:
        await channel.send(embed=em)
    except discord.HTTPException as e:
        log.warning(f"send_log: {e}")


# ========================= TEMPBAN LOOP =========================

@tasks.loop(minutes=1)
async def tempban_loop():
    try:
        tempbans = get_active_tempbans()
        if not tempbans:
            return
        now = datetime.now(PARIS_TZ)
        for s in tempbans:
            if not s.get("duration"):
                continue
            try:
                start = datetime.fromisoformat(s["timestamp"])
            except (ValueError, TypeError):
                continue
            if start.tzinfo is None:
                start = start.replace(tzinfo=PARIS_TZ)
            end = start + timedelta(seconds=int(s["duration"]))
            if end > now:
                continue
            guild = bot.get_guild(int(s["guild_id"]))
            if not guild:
                continue
            try:
                await guild.unban(discord.Object(id=int(s["user_id"])),
                                   reason="Tempban expiré")
                deactivate_sanction(s["id"])
                await send_log(guild, "moderation", "⏰ Tempban expiré",
                               desc=f"**Cible :** <@{s['user_id']}>\n**Sanction :** `#{s['id']}`",
                               color=COLOR_INFO)
            except discord.NotFound:
                deactivate_sanction(s["id"])
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning(f"tempban unban {s['id']}: {e}")
    except Exception as e:
        log.error(f"tempban_loop: {e}")


@tempban_loop.before_loop
async def _bef_tempban():
    await bot.wait_until_ready()


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  PARTIE 2 — MODÉRATION                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.command(name="ban")
async def _ban(ctx, user_input: str = None, *, args: str = None):
    """+ban @user [durée] [raison]"""
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not user_input:
        return await ctx.send(embed=error_embed("Usage", "`+ban @user [durée] [raison]`"))

    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))

    duration_seconds = None
    reason = args or "Aucune raison fournie"
    if args:
        first, _, rest = args.partition(" ")
        d = parse_duration(first)
        if d is not None and d > 0:
            duration_seconds = d
            reason = rest or "Aucune raison fournie"

    if uid == ctx.author.id:
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas te bannir toi-même."))
    if uid == bot.user.id:
        return await ctx.send(embed=error_embed("❌", "Je ne vais pas me bannir moi-même."))

    member = ctx.guild.get_member(uid)
    if member:
        if member.top_role >= ctx.author.top_role and not has_min_rank(ctx.author.id, 3):
            return await ctx.send(embed=error_embed("❌", "Tu ne peux pas bannir un membre de rôle égal ou supérieur."))
        try:
            await member.send(embed=error_embed(
                f"🔨 Tu as été banni de {ctx.guild.name}",
                f"**Modérateur :** {ctx.author}\n**Raison :** {reason}" +
                (f"\n**Durée :** {format_duration_fr(duration_seconds)}" if duration_seconds else "\n**Durée :** Permanent")
            ))
        except (discord.Forbidden, discord.HTTPException):
            pass

    try:
        await ctx.guild.ban(
            discord.Object(id=uid),
            reason=f"[{ctx.author}] {reason}",
            delete_message_days=0,
        )
    except discord.NotFound:
        return await ctx.send(embed=error_embed("❌", "Utilisateur introuvable sur Discord."))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission de bannir."))

    sid = add_sanction(ctx.guild.id, uid, ctx.author.id,
                       "tempban" if duration_seconds else "ban",
                       reason, duration_seconds)

    duration_str = format_duration_fr(duration_seconds) if duration_seconds else "Permanent"
    em = mod_embed("Ban", member, uid, ctx.author, reason, duration_str, sid)
    await ctx.send(embed=em)

    await send_log(ctx.guild, "moderation", "🔨 Ban",
                   author=ctx.author,
                   desc=f"**Cible :** <@{uid}> (`{uid}`)\n**Durée :** {duration_str}\n**Raison :** {reason}\n**Sanction :** `#{sid}`",
                   color=COLOR_ERROR)


@bot.command(name="unban")
async def _unban(ctx, user_id: int = None, *, reason: str = None):
    """+unban <id> [raison]"""
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not user_id:
        return await ctx.send(embed=error_embed("Usage", "`+unban <id> [raison]`"))

    try:
        await ctx.guild.unban(discord.Object(id=user_id), reason=f"[{ctx.author}] {reason or ''}")
    except discord.NotFound:
        return await ctx.send(embed=error_embed("❌", "Aucun ban trouvé pour cet ID."))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))

    conn = get_db()
    conn.execute(
        "UPDATE sanctions SET active = 0 WHERE guild_id = ? AND user_id = ? AND type IN ('ban', 'tempban') AND active = 1",
        (str(ctx.guild.id), str(user_id))
    )
    conn.commit()
    conn.close()

    await ctx.send(embed=success_embed("✅ Débanni", f"<@{user_id}> a été débanni."))
    await send_log(ctx.guild, "moderation", "✅ Unban",
                   author=ctx.author,
                   desc=f"**Cible :** <@{user_id}> (`{user_id}`)\n**Raison :** {reason or '*Aucune*'}",
                   color=COLOR_SUCCESS)


@bot.command(name="kick")
async def _kick(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not member:
        return await ctx.send(embed=error_embed("Usage", "`+kick @user [raison]`"))
    if member.id == ctx.author.id:
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas te kick toi-même."))
    if member.top_role >= ctx.author.top_role and not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌", "Rôle égal ou supérieur."))

    try:
        await member.send(embed=error_embed(
            f"👢 Tu as été expulsé de {ctx.guild.name}",
            f"**Modérateur :** {ctx.author}\n**Raison :** {reason}"
        ))
    except (discord.Forbidden, discord.HTTPException):
        pass

    try:
        await member.kick(reason=f"[{ctx.author}] {reason}")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))

    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
    await ctx.send(embed=mod_embed("Kick", member, member.id, ctx.author, reason, case_id=sid))
    await send_log(ctx.guild, "moderation", "👢 Kick",
                   author=ctx.author, target=member,
                   desc=f"**Raison :** {reason}\n**Sanction :** `#{sid}`",
                   color=COLOR_WARNING)


@bot.command(name="mute", aliases=["timeout"])
async def _mute(ctx, member: discord.Member = None, duration: str = None, *, reason: str = "Aucune raison fournie"):
    """+mute @user <durée> [raison]"""
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not member or not duration:
        return await ctx.send(embed=error_embed("Usage", "`+mute @user <durée> [raison]`\nExemples : `30m`, `2h`, `1d`"))
    if member.id == ctx.author.id:
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas te mute toi-même."))
    if member.top_role >= ctx.author.top_role and not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌", "Rôle égal ou supérieur."))

    seconds = parse_duration(duration)
    if seconds is None or seconds <= 0:
        return await ctx.send(embed=error_embed("❌ Durée invalide", "Exemples : `30m`, `2h`, `1d`"))
    if seconds > 60 * 60 * 24 * 28:
        return await ctx.send(embed=error_embed("❌ Trop long", "Maximum 28 jours (limite Discord)."))

    try:
        await member.timeout(timedelta(seconds=seconds), reason=f"[{ctx.author}] {reason}")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))
    except discord.HTTPException as e:
        return await ctx.send(embed=error_embed("❌ Erreur Discord", str(e)))

    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "mute", reason, seconds)
    duration_str = format_duration_fr(seconds)
    try:
        await member.send(embed=warning_embed(
            f"🔇 Tu as été mute sur {ctx.guild.name}",
            f"**Modérateur :** {ctx.author}\n**Durée :** {duration_str}\n**Raison :** {reason}"
        ))
    except (discord.Forbidden, discord.HTTPException):
        pass

    await ctx.send(embed=mod_embed("Mute", member, member.id, ctx.author, reason, duration_str, sid))
    await send_log(ctx.guild, "moderation", "🔇 Mute",
                   author=ctx.author, target=member,
                   desc=f"**Durée :** {duration_str}\n**Raison :** {reason}\n**Sanction :** `#{sid}`",
                   color=COLOR_WARNING)


@bot.command(name="unmute")
async def _unmute(ctx, member: discord.Member = None, *, reason: str = None):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not member:
        return await ctx.send(embed=error_embed("Usage", "`+unmute @user [raison]`"))

    try:
        await member.timeout(None, reason=f"[{ctx.author}] {reason or ''}")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))

    await ctx.send(embed=success_embed("🔊 Mute retiré", f"{member.mention} a été démuté."))
    await send_log(ctx.guild, "moderation", "🔊 Unmute",
                   author=ctx.author, target=member,
                   desc=f"**Raison :** {reason or '*Aucune*'}",
                   color=COLOR_SUCCESS)


@bot.command(name="warn")
async def _warn(ctx, member: discord.Member = None, *, reason: str = None):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not member or not reason:
        return await ctx.send(embed=error_embed("Usage", "`+warn @user <raison>`"))
    if member.id == ctx.author.id:
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas te warn toi-même."))

    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
    warn_count = len(get_warns(ctx.guild.id, member.id))
    try:
        await member.send(embed=warning_embed(
            f"⚠️ Avertissement sur {ctx.guild.name}",
            f"**Modérateur :** {ctx.author}\n**Raison :** {reason}\n**Total :** {warn_count} warn(s)"
        ))
    except (discord.Forbidden, discord.HTTPException):
        pass

    em = mod_embed("Warn", member, member.id, ctx.author, reason, case_id=sid)
    em.add_field(name="Total warns", value=f"{warn_count}", inline=True)
    await ctx.send(embed=em)
    await send_log(ctx.guild, "moderation", "⚠️ Warn",
                   author=ctx.author, target=member,
                   desc=f"**Raison :** {reason}\n**Total :** {warn_count}\n**Sanction :** `#{sid}`",
                   color=COLOR_WARNING)


@bot.command(name="warns")
async def _warns(ctx, member: discord.Member = None):
    if await check_ban(ctx):
        return
    target = member or ctx.author
    warns = get_warns(ctx.guild.id, target.id)
    if not warns:
        return await ctx.send(embed=info_embed("⚠️ Avertissements", f"{target.mention} n'a aucun warn actif."))
    lines = []
    for w in warns[:15]:
        mod = ctx.guild.get_member(int(w["moderator_id"]))
        mod_name = mod.display_name if mod else f"<@{w['moderator_id']}>"
        try:
            ts = datetime.fromisoformat(w["timestamp"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            ts = "?"
        lines.append(f"`#{w['id']}` — {ts} — par **{mod_name}**\n→ *{w['reason']}*")
    em = info_embed(f"⚠️ Avertissements de {target.display_name} ({len(warns)})", "\n\n".join(lines))
    em.set_thumbnail(url=target.display_avatar.url)
    await ctx.send(embed=em)


@bot.command(name="delwarn")
async def _delwarn(ctx, warn_id: int = None):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not warn_id:
        return await ctx.send(embed=error_embed("Usage", "`+delwarn <id>`"))

    if delete_warn(ctx.guild.id, warn_id):
        await ctx.send(embed=success_embed("✅ Warn supprimé", f"Warn `#{warn_id}` retiré."))
        await send_log(ctx.guild, "moderation", "🗑️ Warn supprimé",
                       author=ctx.author,
                       desc=f"Warn `#{warn_id}` supprimé.",
                       color=COLOR_SUCCESS)
    else:
        await ctx.send(embed=error_embed("❌", f"Warn `#{warn_id}` introuvable ou déjà supprimé."))


@bot.command(name="clear", aliases=["purge"])
async def _clear(ctx, amount: int = None):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not amount:
        return await ctx.send(embed=error_embed("Usage", f"`+clear <nombre>` (max {MAX_CLEAR})"))
    if amount < 1 or amount > MAX_CLEAR:
        return await ctx.send(embed=error_embed("❌", f"Entre 1 et {MAX_CLEAR}."))

    try:
        deleted = await ctx.channel.purge(limit=amount + 1)
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission de supprimer."))

    msg = await ctx.send(embed=success_embed(
        "🧹 Nettoyage effectué",
        f"**{len(deleted)-1}** message(s) supprimé(s)."
    ))
    await asyncio.sleep(5)
    try:
        await msg.delete()
    except discord.HTTPException:
        pass
    await send_log(ctx.guild, "moderation", "🧹 Clear",
                   author=ctx.author,
                   desc=f"**Salon :** {ctx.channel.mention}\n**Messages :** {len(deleted)-1}",
                   color=COLOR_INFO)


@bot.command(name="lock")
async def _lock(ctx, channel: discord.TextChannel = None, *, reason: str = "Pas de raison"):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    channel = channel or ctx.channel
    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                                       reason=f"[{ctx.author}] {reason}")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))

    await ctx.send(embed=warning_embed("🔒 Salon verrouillé", f"{channel.mention} est désormais en lecture seule."))
    await send_log(ctx.guild, "moderation", "🔒 Lock",
                   author=ctx.author,
                   desc=f"**Salon :** {channel.mention}\n**Raison :** {reason}",
                   color=COLOR_WARNING)


@bot.command(name="unlock")
async def _unlock(ctx, channel: discord.TextChannel = None):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    channel = channel or ctx.channel
    try:
        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite,
                                       reason=f"[{ctx.author}] unlock")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))

    await ctx.send(embed=success_embed("🔓 Salon déverrouillé", f"{channel.mention} est à nouveau ouvert."))
    await send_log(ctx.guild, "moderation", "🔓 Unlock",
                   author=ctx.author,
                   desc=f"**Salon :** {channel.mention}",
                   color=COLOR_SUCCESS)


@bot.command(name="slowmode")
async def _slowmode(ctx, seconds: int = None):
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if seconds is None:
        return await ctx.send(embed=error_embed("Usage", "`+slowmode <secondes>` (0 pour désactiver, max 21600)"))
    if seconds < 0 or seconds > 21600:
        return await ctx.send(embed=error_embed("❌", "Entre 0 et 21600 secondes."))

    try:
        await ctx.channel.edit(slowmode_delay=seconds, reason=f"[{ctx.author}] slowmode")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))

    if seconds == 0:
        await ctx.send(embed=success_embed("✅ Slowmode désactivé", f"{ctx.channel.mention} n'a plus de slowmode."))
    else:
        await ctx.send(embed=success_embed("🐌 Slowmode activé", f"{ctx.channel.mention} → **{seconds}s** entre les messages."))


@bot.command(name="history")
async def _history(ctx, member: discord.Member = None):
    """Historique des sanctions d'un membre."""
    if await check_ban(ctx):
        return
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌", "**Sys+** requis."))
    target = member or ctx.author
    rows = get_history(ctx.guild.id, target.id, limit=20)
    if not rows:
        return await ctx.send(embed=info_embed("📜 Historique", f"{target.mention} n'a aucune sanction."))
    lines = []
    type_emoji = {"ban": "🔨", "tempban": "⏰", "kick": "👢", "mute": "🔇", "warn": "⚠️"}
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            ts = "?"
        mod = ctx.guild.get_member(int(r["moderator_id"]))
        mod_name = mod.display_name if mod else f"<@{r['moderator_id']}>"
        emoji = type_emoji.get(r["type"], "•")
        active_marker = "" if r["active"] else " *(retiré)*"
        dur = f" · {format_duration_fr(r['duration'])}" if r["duration"] else ""
        lines.append(f"{emoji} `#{r['id']}` · {ts} · **{r['type']}**{dur}{active_marker}\n→ par {mod_name} : *{r['reason'] or 'sans raison'}*")
    em = discord.Embed(
        title=f"📜 Historique de {target.display_name} ({len(rows)})",
        description="\n\n".join(lines)[:4000],
        color=embed_color(),
    )
    em.set_thumbnail(url=target.display_avatar.url)
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  PARTIE 3 — LOG LISTENERS                                ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.event
async def on_message_delete(message):
    if not message.guild or message.author.bot:
        return
    desc = f"**Auteur :** {message.author.mention} (`{message.author.id}`)\n"
    desc += f"**Salon :** {message.channel.mention}\n"
    if message.content:
        content = message.content[:1024]
        desc += f"**Contenu :**\n{content}"
    if message.attachments:
        desc += f"\n**Pièces jointes :** {len(message.attachments)}"
    await send_log(message.guild, "messages", "🗑️ Message supprimé",
                   desc=desc, color=COLOR_ERROR)


@bot.event
async def on_message_edit(before, after):
    if not before.guild or before.author.bot:
        return
    if before.content == after.content:
        return
    desc = f"**Auteur :** {before.author.mention}\n**Salon :** {before.channel.mention}\n"
    desc += f"**Avant :** {before.content[:500] or '*vide*'}\n"
    desc += f"**Après :** {after.content[:500] or '*vide*'}\n"
    desc += f"[→ Jump]({after.jump_url})"
    await send_log(before.guild, "messages", "✏️ Message édité",
                   desc=desc, color=COLOR_INFO)


@bot.event
async def on_member_join(member):
    age = (datetime.now(timezone.utc) - member.created_at).days
    await send_log(member.guild, "members", "➡️ Nouveau membre",
                   target=member,
                   desc=f"**Compte créé :** il y a {age} jours\n**Membres :** {member.guild.member_count}",
                   color=COLOR_SUCCESS)


@bot.event
async def on_member_remove(member):
    roles = ", ".join(r.mention for r in member.roles if r != member.guild.default_role) or "*aucun*"
    await send_log(member.guild, "members", "⬅️ Membre parti",
                   target=member,
                   desc=f"**Rôles :** {roles[:1024]}",
                   color=COLOR_WARNING)


@bot.event
async def on_member_update(before, after):
    if not before.guild:
        return
    if before.nick != after.nick:
        await send_log(after.guild, "members", "✏️ Pseudo changé",
                       target=after,
                       desc=f"**Avant :** {before.nick or before.name}\n**Après :** {after.nick or after.name}",
                       color=COLOR_INFO)
    if set(before.roles) != set(after.roles):
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        parts = []
        if added:
            parts.append("**Ajoutés :** " + ", ".join(r.mention for r in added))
        if removed:
            parts.append("**Retirés :** " + ", ".join(r.mention for r in removed))
        if parts:
            await send_log(after.guild, "roles", "🎭 Rôles modifiés",
                           target=after,
                           desc="\n".join(parts),
                           color=COLOR_INFO)


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel == after.channel:
        return
    if before.channel is None and after.channel:
        action = "🎧 Rejoint un vocal"
        desc = f"**Salon :** {after.channel.mention}"
        color = COLOR_SUCCESS
    elif after.channel is None and before.channel:
        action = "🚪 Quitte un vocal"
        desc = f"**Salon :** {before.channel.mention}"
        color = COLOR_WARNING
    else:
        action = "🔄 Change de vocal"
        desc = f"**De :** {before.channel.mention}\n**Vers :** {after.channel.mention}"
        color = COLOR_INFO
    await send_log(member.guild, "voice", action, target=member, desc=desc, color=color)


@bot.event
async def on_guild_role_create(role):
    await send_log(role.guild, "roles", "🎭 Rôle créé",
                   desc=f"**Rôle :** {role.mention} (`{role.id}`)",
                   color=COLOR_SUCCESS)


@bot.event
async def on_guild_role_delete(role):
    await send_log(role.guild, "roles", "🗑️ Rôle supprimé",
                   desc=f"**Rôle :** `{role.name}` (`{role.id}`)",
                   color=COLOR_ERROR)


@bot.event
async def on_member_ban(guild, user):
    await send_log(guild, "moderation", "🔨 Membre banni (audit)",
                   desc=f"**Cible :** {user.mention} (`{user.id}`)",
                   color=COLOR_ERROR)


@bot.event
async def on_member_unban(guild, user):
    await send_log(guild, "moderation", "✅ Membre débanni (audit)",
                   desc=f"**Cible :** {user.mention} (`{user.id}`)",
                   color=COLOR_SUCCESS)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║          PARTIE 4 — RANGS + ADMIN + ALLOW + SETLOG + HELP + RUN          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

# ========================= OWNER =========================

@bot.command(name="owner")
async def _owner(ctx, *, user_input: str = None):
    """+owner (liste les owners) — Owner only pour modifier"""
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌", "**Sys+** requis pour voir la liste."))
    owner_ids_raw = get_config("owner_ids")
    owners = json.loads(owner_ids_raw) if owner_ids_raw else []
    if user_input is None:
        if not owners:
            return await ctx.send(embed=info_embed("📋 Liste Owners", "Aucun owner."))
        return await ctx.send(embed=info_embed(
            f"📋 Liste Owners ({len(owners)})",
            "\n".join(f"<@{uid}>" for uid in owners)
        ))
    # Sinon, redirige vers +addowner
    return await ctx.send(embed=info_embed(
        "Usage",
        "Pour ajouter un Owner : `+addowner @user`\nPour en retirer un : `+removeowner @user`"
    ))


@bot.command(name="addowner")
async def _addowner(ctx, *, user_input: str = None):
    if not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "Seul un **Owner** peut en ajouter."))
    if not user_input:
        return await ctx.send(embed=error_embed("Argument manquant", "Mention/ID requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    owner_ids_raw = get_config("owner_ids")
    owners = json.loads(owner_ids_raw) if owner_ids_raw else []
    if str(uid) in owners:
        return await ctx.send(embed=error_embed("Déjà Owner", f"{format_user_display(display, uid)} est déjà Owner."))
    owners.append(str(uid))
    set_config("owner_ids", json.dumps(owners))
    await ctx.send(embed=success_embed("✅ Owner ajouté", f"{format_user_display(display, uid)} est maintenant **Owner**."))


@bot.command(name="removeowner")
async def _removeowner(ctx, *, user_input: str = None):
    if not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "Seul un **Owner** peut en retirer."))
    if not user_input:
        return await ctx.send(embed=error_embed("Argument manquant", "Mention/ID requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    owner_ids_raw = get_config("owner_ids")
    owners = json.loads(owner_ids_raw) if owner_ids_raw else []
    if str(uid) not in owners:
        return await ctx.send(embed=error_embed("Pas Owner", f"{format_user_display(display, uid)} n'est pas Owner."))
    if len(owners) <= 1:
        return await ctx.send(embed=error_embed("Impossible", "Il doit rester au moins 1 Owner."))
    owners.remove(str(uid))
    set_config("owner_ids", json.dumps(owners))
    await ctx.send(embed=success_embed("✅ Owner retiré", f"{format_user_display(display, uid)} n'est plus Owner."))


# ========================= SYS =========================

@bot.command(name="sys")
async def _sys(ctx, *, user_input: str = None):
    if user_input is None:
        if not has_min_rank(ctx.author.id, 2):
            return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
        ids = get_ranks_by_level(2)
        if not ids:
            return await ctx.send(embed=info_embed("📋 Liste Sys", "Aucun Sys."))
        return await ctx.send(embed=info_embed(
            f"📋 Liste Sys ({len(ids)})", "\n".join(f"<@{uid}>" for uid in ids)
        ))
    if not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Owner** requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if get_rank_db(uid) == 2:
        return await ctx.send(embed=error_embed("Déjà Sys", f"{format_user_display(display, uid)} est déjà Sys."))
    if get_rank_db(uid) >= 3:
        return await ctx.send(embed=error_embed("❌", f"{format_user_display(display, uid)} est déjà Owner."))
    set_rank_db(uid, 2)
    await ctx.send(embed=success_embed("✅ Sys ajouté", f"{format_user_display(display, uid)} est maintenant **Sys**."))


@bot.command(name="unsys")
async def _unsys(ctx, *, user_input: str = None):
    if not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Owner** requis."))
    if not user_input:
        return await ctx.send(embed=error_embed("Argument manquant", "Mention/ID/nom requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if get_rank_db(uid) != 2:
        return await ctx.send(embed=error_embed("Pas Sys", f"{format_user_display(display, uid)} n'est pas Sys."))
    set_rank_db(uid, 0)
    await ctx.send(embed=success_embed("✅ Sys retiré", f"{format_user_display(display, uid)} n'est plus Sys."))


# ========================= WL =========================

@bot.command(name="wl")
async def _wl(ctx, *, user_input: str = None):
    if user_input is None:
        if not has_min_rank(ctx.author.id, 2):
            return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
        ids = get_ranks_by_level(1)
        if not ids:
            return await ctx.send(embed=info_embed("📋 Liste WL", "Aucun WL."))
        return await ctx.send(embed=info_embed(
            f"📋 Liste WL ({len(ids)})", "\n".join(f"<@{uid}>" for uid in ids)
        ))
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if get_rank_db(uid) >= 2:
        return await ctx.send(embed=error_embed("❌", f"{format_user_display(display, uid)} a un rang supérieur."))
    if get_rank_db(uid) == 1:
        return await ctx.send(embed=error_embed("Déjà WL", f"{format_user_display(display, uid)} est déjà WL."))
    set_rank_db(uid, 1)
    await ctx.send(embed=success_embed("✅ WL ajouté", f"{format_user_display(display, uid)} est maintenant **WL**."))


@bot.command(name="unwl")
async def _unwl(ctx, *, user_input: str = None):
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not user_input:
        return await ctx.send(embed=error_embed("Argument manquant", "Mention/ID/nom requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if get_rank_db(uid) != 1:
        return await ctx.send(embed=error_embed("Pas WL", f"{format_user_display(display, uid)} n'est pas WL."))
    set_rank_db(uid, 0)
    await ctx.send(embed=success_embed("✅ WL retiré", f"{format_user_display(display, uid)} n'est plus WL."))


# ========================= BOTBAN =========================

@bot.command(name="botban")
async def _botban(ctx, *, user_input: str = None):
    """Empêche un user d'utiliser Moh."""
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not user_input:
        return await ctx.send(embed=error_embed("Argument manquant", "Mention/ID/nom requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if get_rank_db(uid) >= get_rank_db(ctx.author.id):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "Tu ne peux pas bot-ban un rang égal ou supérieur."))
    if is_bot_banned(uid):
        return await ctx.send(embed=error_embed("Déjà banni", f"{format_user_display(display, uid)} est déjà bot-banni."))
    add_bot_ban(uid, ctx.author.id)
    await ctx.send(embed=success_embed("⛔ Bot-banni", f"{format_user_display(display, uid)} ne peut plus utiliser **Moh**."))


@bot.command(name="unbotban")
async def _unbotban(ctx, *, user_input: str = None):
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not user_input:
        return await ctx.send(embed=error_embed("Argument manquant", "Mention/ID/nom requis."))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if not is_bot_banned(uid):
        return await ctx.send(embed=error_embed("Pas banni", f"{format_user_display(display, uid)} n'est pas bot-banni."))
    remove_bot_ban(uid)
    await ctx.send(embed=success_embed("✅ Bot-débanni", f"{format_user_display(display, uid)} peut à nouveau utiliser **Moh**."))


# ========================= ALLOW =========================

@bot.command(name="allow")
async def _allow(ctx, *, channel_input: str = None):
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if channel_input is None:
        allowed = get_allowed_channels(ctx.guild.id)
        if not allowed:
            return await ctx.send(embed=info_embed(
                "📋 Aucun salon autorisé",
                f"Utilise `{get_prefix_cached()}allow #salon` pour autoriser un salon.\n"
                f"*Note : les Sys+ peuvent utiliser les commandes partout.*"
            ))
        lines = []
        for cid in allowed:
            ch = ctx.guild.get_channel(int(cid))
            lines.append(f"• {ch.mention} (`{cid}`)" if ch else f"• *Salon inaccessible* (`{cid}`)")
        return await ctx.send(embed=info_embed(f"📋 Salons autorisés ({len(allowed)})", "\n".join(lines)))
    channel, raw_id = await resolve_channel(ctx, channel_input)
    if not channel:
        return await ctx.send(embed=error_embed("❌ Salon introuvable", "Mention #salon ou ID."))
    if is_channel_allowed(ctx.guild.id, channel.id):
        return await ctx.send(embed=error_embed("Déjà autorisé", f"{channel.mention} est déjà autorisé."))
    add_allowed_channel(ctx.guild.id, channel.id, ctx.author.id)
    await ctx.send(embed=success_embed("✅ Salon autorisé", f"{channel.mention} est maintenant autorisé."))


@bot.command(name="unallow")
async def _unallow(ctx, *, channel_input: str = None):
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not channel_input:
        return await ctx.send(embed=error_embed("Argument manquant", f"Usage : `{get_prefix_cached()}unallow #salon`"))
    channel, raw_id = await resolve_channel(ctx, channel_input)
    if not channel:
        if raw_id is not None:
            if remove_allowed_channel(ctx.guild.id, raw_id):
                return await ctx.send(embed=success_embed("✅ Salon retiré", f"Salon `{raw_id}` retiré."))
            return await ctx.send(embed=error_embed("Pas dans la liste", f"Salon `{raw_id}` pas autorisé."))
        return await ctx.send(embed=error_embed("❌ Salon introuvable"))
    if not remove_allowed_channel(ctx.guild.id, channel.id):
        return await ctx.send(embed=error_embed("Pas dans la liste", f"{channel.mention} pas autorisé."))
    await ctx.send(embed=success_embed("✅ Salon retiré", f"{channel.mention} n'est plus autorisé."))


# ========================= SETLOG / LOGS =========================

@bot.command(name="setlog")
async def _setlog(ctx, log_type: str = None, channel: discord.TextChannel = None):
    """+setlog <type> #salon — type : moderation/messages/members/voice/roles"""
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not log_type or not channel:
        return await ctx.send(embed=error_embed(
            "Usage",
            f"`{get_prefix_cached()}setlog <type> #salon`\n"
            f"Types : `{'`, `'.join(LOG_TYPES)}`"
        ))
    log_type = log_type.lower()
    if log_type not in LOG_TYPES:
        return await ctx.send(embed=error_embed("❌", f"Types valides : `{'`, `'.join(LOG_TYPES)}`"))
    set_log_channel(ctx.guild.id, log_type, channel.id)
    await ctx.send(embed=success_embed("✅ Log configuré", f"**{log_type}** → {channel.mention}"))


@bot.command(name="dellog")
async def _dellog(ctx, log_type: str = None):
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "**Sys+** requis."))
    if not log_type:
        return await ctx.send(embed=error_embed(
            "Usage",
            f"`{get_prefix_cached()}dellog <type>`\n"
            f"Types : `{'`, `'.join(LOG_TYPES)}`"
        ))
    log_type = log_type.lower()
    if log_type not in LOG_TYPES:
        return await ctx.send(embed=error_embed("❌", f"Types valides : `{'`, `'.join(LOG_TYPES)}`"))
    delete_log_channel(ctx.guild.id, log_type)
    await ctx.send(embed=success_embed("✅ Log retiré", f"**{log_type}** : désactivé"))


@bot.command(name="logs")
async def _logs(ctx):
    if not has_min_rank(ctx.author.id, 2):
        return await ctx.send(embed=error_embed("❌", "**Sys+** requis."))
    all_logs = get_all_log_channels(ctx.guild.id)
    lines = []
    for t in LOG_TYPES:
        cid = all_logs.get(t)
        if cid:
            ch = ctx.guild.get_channel(int(cid))
            lines.append(f"• **{t}** → {ch.mention if ch else f'`{cid}` (introuvable)'}")
        else:
            lines.append(f"• **{t}** → *non défini*")
    await ctx.send(embed=info_embed(
        "📋 Configuration des logs",
        "\n".join(lines) +
        f"\n\nConfigurer : `{get_prefix_cached()}setlog <type> #salon`\n"
        f"Retirer : `{get_prefix_cached()}dellog <type>`"
    ))


# ========================= PREFIX =========================

@bot.command(name="prefix")
async def _prefix(ctx, new_prefix: str = None):
    if not has_min_rank(ctx.author.id, 3):
        return await ctx.send(embed=error_embed("❌ Permission refusée", "Seul un **Owner** peut changer le prefix."))
    if not new_prefix:
        return await ctx.send(embed=info_embed("Prefix actuel", f"`{get_prefix_cached()}`"))
    if len(new_prefix) > 5:
        return await ctx.send(embed=error_embed("❌", "Prefix max 5 caractères."))
    set_config("prefix", new_prefix)
    await ctx.send(embed=success_embed("✅ Prefix modifié", f"Nouveau prefix : `{new_prefix}`"))


# ========================= HELP DROPDOWN =========================

HELP_CATEGORIES = {
    "moderation": {
        "emoji": "⚖️", "label": "Modération", "title": "⚖️  Modération",
        "items": [
            ("ban @u [durée] [raison]",   "Bannir (perm ou temporaire)", 2),
            ("unban <id> [raison]",       "Débannir", 2),
            ("kick @u [raison]",          "Expulser", 2),
            ("mute @u <durée> [raison]",  "Timeout", 2),
            ("unmute @u",                 "Retirer le mute", 2),
            ("warn @u <raison>",          "Avertir", 2),
            ("warns [@u]",                "Voir les warns", 0),
            ("delwarn <id>",              "Supprimer un warn", 2),
            ("clear <n>",                 "Supprimer N messages", 2),
            ("lock / unlock [#salon]",    "Verrouiller un salon", 2),
            ("slowmode <sec>",            "Slowmode du salon", 2),
            ("history [@u]",              "Historique sanctions", 2),
        ],
    },
    "config": {
        "emoji": "⚙️", "label": "Configuration", "title": "⚙️  Configuration",
        "items": [
            ("setlog <type> #salon",  "Salon de log par type", 2),
            ("dellog <type>",         "Retirer un log", 2),
            ("logs",                  "Voir config logs", 2),
            ("allow #salon",          "Autoriser un salon", 2),
            ("unallow #salon",        "Retirer un salon", 2),
            ("allow",                 "Lister les salons autorisés", 2),
            ("prefix [new]",          "Changer le prefix", 3),
        ],
    },
    "perms": {
        "emoji": "👥", "label": "Permissions", "title": "👥  Permissions",
        "items": [
            ("wl @u / unwl @u",                "WL (whitelist)", 2),
            ("wl",                              "Lister les WL", 2),
            ("sys @u / unsys @u",              "Sys", 3),
            ("sys",                             "Lister les Sys", 2),
            ("addowner @u / removeowner @u",   "Owner", 3),
            ("owner",                           "Lister les Owners", 2),
            ("botban @u",                       "Empêcher d'utiliser Moh", 2),
            ("unbotban @u",                     "Annuler le bot-ban", 2),
        ],
    },
    "hierarchy": {
        "emoji": "📋", "label": "Hiérarchie", "title": "📋  Hiérarchie",
        "min_rank": 0, "items": [],
    },
}


def help_accessible_items(key, rank):
    cat = HELP_CATEGORIES.get(key, {})
    return [(s, d) for (s, d, mr) in cat.get("items", []) if rank >= mr]


def help_category_visible(key, rank):
    cat = HELP_CATEGORIES.get(key, {})
    if "min_rank" in cat:
        return rank >= cat["min_rank"]
    return len(help_accessible_items(key, rank)) > 0


def build_help_category_embed(key, rank):
    p = get_prefix_cached()
    cat = HELP_CATEGORIES[key]
    em = discord.Embed(title=cat["title"], color=embed_color())
    items = help_accessible_items(key, rank)
    if not items:
        em.description = "*Aucune commande accessible à ton rang.*"
    else:
        max_syntax = max(len(f"{p}{syntax}") for syntax, _ in items)
        lines = [f"{p}{syntax}".ljust(max_syntax + 2) + f"→ {desc}" for syntax, desc in items]
        em.description = "```\n" + "\n".join(lines) + "\n```"
    em.set_footer(text=FOOTER_TEXT)
    return em


def build_help_hierarchy_embed(rank):
    em = discord.Embed(title="📋  Hiérarchie des rangs", color=embed_color())
    lines = ["```\nOwner > Sys > WL > Tout le monde\n```\n"]
    levels = [
        (3, "👑 **Owner**", "Accès total. `prefix`, `addowner`, `sys`/`unsys`"),
        (2, "🔧 **Sys**",    "Toute la modération + `allow`, `setlog`, `botban`, `wl`/`unwl`"),
        (1, "✨ **WL**",     "Statut de confiance (whitelist). Peut utiliser les commandes ouvertes."),
        (0, "👤 **Tout le monde**", "`warns` et `help` uniquement."),
    ]
    for lvl, name, desc in levels:
        marker = " ← **toi**" if lvl == rank else ""
        lines.append(f"> {name} — {desc}{marker}")
    em.description = "\n".join(lines)
    em.set_footer(text=FOOTER_TEXT)
    return em


def build_help_home_embed(rank):
    p = get_prefix_cached()
    em = discord.Embed(color=embed_color())
    em.set_author(name="Moh ─ Panel d'aide")
    rank_label = rank_name(rank)
    intro = (
        f"```\n🕐  {get_french_time()}\n```\n"
        f"Bot de gestion : modération + logs.\n\n"
        f"**Prefix :** `{p}` ・ **Ton rang :** {rank_label}\n\n"
    )
    descs = {
        "moderation": "Ban, kick, mute, warn, clear, lock...",
        "config":     "Allow, setlog, prefix",
        "perms":      "Gérer Owner / Sys / WL, botban",
        "hierarchy":  "Qui peut faire quoi",
    }
    visible = []
    for key, lbl in descs.items():
        if help_category_visible(key, rank):
            cat = HELP_CATEGORIES[key]
            visible.append(f"> {cat['emoji']} **{cat['label']}** — {lbl}")
    em.description = intro + ("\n".join(visible) if visible else "")
    em.set_footer(text=FOOTER_TEXT)
    return em


def build_help_embed_for(key, rank):
    if key == "home":
        return build_help_home_embed(rank)
    if key == "hierarchy":
        return build_help_hierarchy_embed(rank)
    return build_help_category_embed(key, rank)


class HelpDropdown(discord.ui.Select):
    def __init__(self, user_rank):
        self.user_rank = user_rank
        options = [discord.SelectOption(label="Accueil", emoji="🏠", value="home")]
        for key, cat in HELP_CATEGORIES.items():
            if help_category_visible(key, user_rank):
                options.append(discord.SelectOption(label=cat["label"], emoji=cat["emoji"], value=key))
        super().__init__(placeholder="📂 Choisis une catégorie...",
                         min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        if key != "home" and not help_category_visible(key, self.user_rank):
            return await interaction.response.send_message(
                "Tu n'as pas accès à cette catégorie.", ephemeral=True
            )
        await interaction.response.edit_message(
            embed=build_help_embed_for(key, self.user_rank), view=self.view
        )


class HelpView(discord.ui.View):
    def __init__(self, author_id, user_rank):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.user_rank = user_rank
        self.add_item(HelpDropdown(user_rank))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"Ce menu n'est pas à toi. Fais `{get_prefix_cached()}help` pour le tien.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.command(name="help", aliases=["aide", "h"])
async def _help(ctx):
    rank = get_rank_db(ctx.author.id)
    view = HelpView(ctx.author.id, rank)
    await ctx.send(embed=build_help_home_embed(rank), view=view)


# ========================= RUN =========================

if __name__ == "__main__":
    try:
        log.info("Démarrage de Moh...")
        bot.run(BOT_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        log.info("Arrêt demandé.")
    except Exception as e:
        log.error(f"Erreur fatale : {e}", exc_info=True)
        sys.exit(1)

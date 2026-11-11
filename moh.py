"""
╔══════════════════════════════════════════════════════════════════════════╗
║                        MOH — Bot de gestion                              ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import discord
from discord.ext import commands, tasks
import os
import sys
import sqlite3
import re
import asyncio
import logging
import traceback
import aiohttp
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ========================= CONFIG =========================
BOT_TOKEN = os.environ.get("TOKEN_MOH") or os.environ.get("TOKEN")
if not BOT_TOKEN:
    print("[ERREUR CRITIQUE] Aucune variable TOKEN_MOH ni TOKEN trouvée.")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────
#  BUYER : hardcodé, niveau max, ne peut PAS être set/retiré en commande.
# ─────────────────────────────────────────────────────────────────────────
BUYER_IDS = [
    142365250803466240,
    1546586066407133308,
]

PARIS_TZ = ZoneInfo("Europe/Paris")
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
YOUNG_ACCOUNT_DAYS = 7          # alerte dans les logs si compte plus jeune
MAX_EMOJI_BYTES = 256 * 1024    # limite Discord

# Couleurs
COLOR_DEFAULT = 0x2b2d31
COLOR_SUCCESS = 0x43b581
COLOR_ERROR = 0xf04747
COLOR_WARNING = 0xfaa61a
COLOR_INFO = 0x3498db
COLOR_MOD = 0xeb459e

# ========================= NIVEAUX DE PERMISSION =========================
#   0 = Public · 1..9 = perm1..perm9 · 10 = WL · 11 = Owner · 12 = Buyer
LEVEL_PUBLIC = 0
MAX_TIER = 9
LEVEL_WL = 10
LEVEL_OWNER = 11
LEVEL_BUYER = 12

# Commandes que SEUL un Buyer peut reconfigurer via +setperm
BUYER_RETIER_CMDS = {"owner", "unowner"}

# Permissions Discord qu'un non-Owner ne peut pas distribuer via +role
DANGEROUS_PERMS = ("administrator", "manage_guild", "manage_roles", "ban_members",
                   "kick_members", "manage_channels", "manage_webhooks", "mention_everyone",
                   "moderate_members")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
)
log = logging.getLogger("moh")

_prefix_cache = {"value": None}


# ========================= CATALOGUE DES COMMANDES =========================
COMMANDS = {
    # ---- Aide / perms ----
    "help":      {"args": "",                       "desc": "Affiche ce menu d'aide",            "cat": "perms", "level": 0,  "locked": True},
    "perms":     {"args": "",                       "desc": "Qui a quel niveau de perm",         "cat": "perms", "level": 0,  "locked": True},
    "helpall":   {"args": "",                       "desc": "Quelles commandes par niveau",      "cat": "perms", "level": 0,  "locked": True},

    # ---- Gestion des rangs ----
    "owner":     {"args": "[@u]",                   "desc": "Ajouter / lister les Owners",       "cat": "perms", "level": 12, "locked": False},
    "unowner":   {"args": "@u",                     "desc": "Retirer un Owner",                  "cat": "perms", "level": 12, "locked": False},
    "wl":        {"args": "[@u]",                   "desc": "Ajouter / lister les WL",           "cat": "perms", "level": 11, "locked": True},
    "unwl":      {"args": "@u",                     "desc": "Retirer un WL",                     "cat": "perms", "level": 11, "locked": True},
    "setperm":   {"args": "<cmd|@rôle|@u> <niv>",   "desc": "Définir un niveau",                 "cat": "perms", "level": 11, "locked": True},
    "resetperm": {"args": "<cmd|@rôle|@u>",         "desc": "Réinitialiser un niveau",           "cat": "perms", "level": 11, "locked": True},
    "botban":    {"args": "@u",                     "desc": "Bloquer l'accès au bot",            "cat": "perms", "level": 11, "locked": True},
    "unbotban":  {"args": "@u",                     "desc": "Débloquer l'accès au bot",          "cat": "perms", "level": 11, "locked": True},

    # ---- Configuration ----
    "prefix":    {"args": "[nouveau]",              "desc": "Changer le prefix",                 "cat": "config", "level": 12, "locked": True},
    "allow":     {"args": "[#salon]",               "desc": "Autoriser les commandes publiques", "cat": "config", "level": 11, "locked": True},
    "unallow":   {"args": "#salon",                 "desc": "Bloquer les commandes publiques",   "cat": "config", "level": 11, "locked": True},
    "setlog":    {"args": "<type> #salon",          "desc": "Définir un salon de logs",          "cat": "config", "level": 11, "locked": True},
    "dellog":    {"args": "<type>",                 "desc": "Retirer un salon de logs",          "cat": "config", "level": 11, "locked": True},
    "logs":      {"args": "",                       "desc": "Voir la config des logs",           "cat": "config", "level": 11, "locked": True},
    "renew":     {"args": "",                       "desc": "Recréer le salon (nuke)",           "cat": "config", "level": 12, "locked": True},

    # ---- Infos ----
    "userinfo":  {"args": "[@u]",                   "desc": "Infos d'un membre",                 "cat": "infos", "level": 0,  "locked": False},
    "serverinfo":{"args": "",                       "desc": "Infos du serveur",                  "cat": "infos", "level": 0,  "locked": False},
    "avatar":    {"args": "[@u]",                   "desc": "Avatar d'un membre",                "cat": "infos", "level": 0,  "locked": False},
    "roleinfo":  {"args": "@rôle",                  "desc": "Infos d'un rôle",                   "cat": "infos", "level": 0,  "locked": False},
    "ping":      {"args": "",                       "desc": "Latence du bot",                    "cat": "infos", "level": 0,  "locked": False},

    # ---- Modération ----
    "ban":       {"args": "@u [durée] [raison]",    "desc": "Bannir (perm/temporaire)",          "cat": "moderation", "level": 11, "locked": False},
    "unban":     {"args": "<id> [raison]",          "desc": "Débannir",                          "cat": "moderation", "level": 11, "locked": False},
    "kick":      {"args": "@u [raison]",            "desc": "Expulser",                          "cat": "moderation", "level": 11, "locked": False},
    "mute":      {"args": "@u <durée> [raison]",    "desc": "Timeout",                           "cat": "moderation", "level": 11, "locked": False},
    "unmute":    {"args": "@u",                     "desc": "Retirer le mute",                   "cat": "moderation", "level": 11, "locked": False},
    "warn":      {"args": "@u <raison>",            "desc": "Avertir",                           "cat": "moderation", "level": 11, "locked": False},
    "warns":     {"args": "[@u]",                   "desc": "Voir les warns",                    "cat": "moderation", "level": 11, "locked": False},
    "delwarn":   {"args": "<id>",                   "desc": "Supprimer un warn (alias unwarn)",  "cat": "moderation", "level": 11, "locked": False},
    "clearwarns":{"args": "@u",                     "desc": "Effacer tous les warns",            "cat": "moderation", "level": 11, "locked": False},
    "softban":   {"args": "@u [raison]",            "desc": "Ban + unban (clean messages)",      "cat": "moderation", "level": 11, "locked": False},
    "clear":     {"args": "[@u] <n>",               "desc": "Supprimer N messages",              "cat": "moderation", "level": 11, "locked": False},
    "lock":      {"args": "[#salon]",               "desc": "Verrouiller un salon",              "cat": "moderation", "level": 11, "locked": False},
    "unlock":    {"args": "[#salon]",               "desc": "Déverrouiller un salon",            "cat": "moderation", "level": 11, "locked": False},
    "slowmode":  {"args": "<sec>",                  "desc": "Slowmode du salon",                 "cat": "moderation", "level": 11, "locked": False},
    "banlist":   {"args": "",                       "desc": "Liste des bannis",                  "cat": "moderation", "level": 11, "locked": False},
    "snipe":     {"args": "",                       "desc": "Dernier message supprimé",          "cat": "moderation", "level": 11, "locked": False},
    "history":   {"args": "[@u]",                   "desc": "Historique des sanctions",          "cat": "moderation", "level": 11, "locked": False},
    "case":      {"args": "<id>",                   "desc": "Détail d'une sanction",             "cat": "moderation", "level": 11, "locked": False},
    "reason":    {"args": "<id> <raison>",          "desc": "Modifier la raison d'une sanction", "cat": "moderation", "level": 11, "locked": False},

    # ---- Outils ----
    "role":      {"args": "@u @rôle",               "desc": "Ajouter/retirer un rôle",           "cat": "outils", "level": 11, "locked": False},
    "nick":      {"args": "@u [pseudo]",            "desc": "Changer le pseudo",                 "cat": "outils", "level": 11, "locked": False},
    "derank":    {"args": "@u",                     "desc": "Retirer tous les rôles",            "cat": "outils", "level": 11, "locked": False},
    "say":       {"args": "<message>",              "desc": "Faire parler le bot",               "cat": "outils", "level": 11, "locked": False},
    "dm":        {"args": "@u <message>",           "desc": "Envoyer un MP",                     "cat": "outils", "level": 11, "locked": False},
    "embed":     {"args": "<message>",              "desc": "Envoyer un embed",                  "cat": "outils", "level": 11, "locked": False},
    "addemoji":  {"args": "<nom> <url/emoji>",      "desc": "Ajouter un émoji",                  "cat": "outils", "level": 11, "locked": False},
}

CATEGORY_META = {
    "moderation": {"emoji": "⚖️", "label": "Modération", "color": 0xed4245, "sub": "Sanctionne et garde le serveur propre."},
    "infos":      {"emoji": "ℹ️", "label": "Infos",      "color": 0x5865f2, "sub": "Infos sur les membres, rôles et le serveur."},
    "outils":     {"emoji": "🧰", "label": "Outils",     "color": 0x1abc9c, "sub": "Gestion des rôles et utilitaires du staff."},
    "config":     {"emoji": "⚙️", "label": "Configuration", "color": 0xfaa61a, "sub": "Configure le bot pour ton serveur."},
    "perms":      {"emoji": "👥", "label": "Permissions", "color": 0x9b59b6, "sub": "Gère qui a accès à quoi."},
}

LOG_TYPES = ["moderation", "messages", "members", "voice", "roles", "commands"]


# ========================= DATABASE =========================
# Une seule connexion partagée (le bot tourne sur un seul thread asyncio),
# en autocommit : plus d'ouverture/fermeture à chaque requête.

_DB = None


def get_db():
    global _DB
    if _DB is None:
        _DB = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
        _DB.row_factory = sqlite3.Row
        _DB.execute("PRAGMA journal_mode=WAL")
        _DB.execute("PRAGMA foreign_keys=ON")
    return _DB


def db_exec(sql, params=()):
    return get_db().execute(sql, params)


def db_one(sql, params=()):
    return db_exec(sql, params).fetchone()


def db_all(sql, params=()):
    return db_exec(sql, params).fetchall()


def init_db():
    get_db().executescript("""
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS ranks (user_id TEXT PRIMARY KEY, rank INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS role_perms (
            guild_id TEXT NOT NULL, role_id TEXT NOT NULL, level INTEGER NOT NULL,
            PRIMARY KEY (guild_id, role_id));
        CREATE TABLE IF NOT EXISTS member_perms (
            guild_id TEXT NOT NULL, user_id TEXT NOT NULL, level INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id));
        CREATE TABLE IF NOT EXISTS cmd_perms (
            guild_id TEXT NOT NULL, command TEXT NOT NULL, level INTEGER NOT NULL,
            PRIMARY KEY (guild_id, command));
        CREATE TABLE IF NOT EXISTS blocked_channels (
            guild_id TEXT NOT NULL, channel_id TEXT NOT NULL,
            PRIMARY KEY (guild_id, channel_id));
        CREATE TABLE IF NOT EXISTS bot_bans (user_id TEXT PRIMARY KEY, banned_by TEXT, banned_at TEXT);
        CREATE TABLE IF NOT EXISTS log_channels (
            guild_id TEXT NOT NULL, log_type TEXT NOT NULL, channel_id TEXT NOT NULL,
            PRIMARY KEY (guild_id, log_type));
        CREATE TABLE IF NOT EXISTS sanctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL, user_id TEXT NOT NULL, moderator_id TEXT NOT NULL,
            type TEXT NOT NULL, reason TEXT, duration INTEGER,
            timestamp TEXT NOT NULL, active INTEGER DEFAULT 1);
        CREATE INDEX IF NOT EXISTS idx_sanctions_user ON sanctions(guild_id, user_id);
        CREATE INDEX IF NOT EXISTS idx_sanctions_active ON sanctions(active, type);
    """)
    db_exec("INSERT OR IGNORE INTO config VALUES ('prefix', ?)", (DEFAULT_PREFIX,))


# ---- Config ----

def get_config(key):
    row = db_one("SELECT value FROM config WHERE key = ?", (key,))
    return row["value"] if row else None


def set_config(key, value):
    db_exec("INSERT OR REPLACE INTO config VALUES (?, ?)", (key, str(value)))
    if key == "prefix":
        _prefix_cache["value"] = str(value)


def get_prefix_cached():
    if _prefix_cache["value"] is None:
        _prefix_cache["value"] = get_config("prefix") or DEFAULT_PREFIX
    return _prefix_cache["value"]


# ---- Rangs de base (WL / Owner) ----

def get_base_rank(user_id):
    if int(user_id) in BUYER_IDS:
        return LEVEL_BUYER
    row = db_one("SELECT rank FROM ranks WHERE user_id = ?", (str(user_id),))
    return row["rank"] if row else 0


def set_base_rank(user_id, rank):
    if rank == 0:
        db_exec("DELETE FROM ranks WHERE user_id = ?", (str(user_id),))
    else:
        db_exec("INSERT OR REPLACE INTO ranks VALUES (?, ?)", (str(user_id), rank))


def get_users_by_rank(rank):
    return [r["user_id"] for r in db_all("SELECT user_id FROM ranks WHERE rank = ?", (rank,))]


# ---- Perms rôle (avec cache mémoire par serveur) ----

_role_perm_cache = {}


def _guild_role_perms(guild_id):
    gid = int(guild_id)
    if gid not in _role_perm_cache:
        rows = db_all("SELECT role_id, level FROM role_perms WHERE guild_id = ?", (str(gid),))
        _role_perm_cache[gid] = {int(r["role_id"]): r["level"] for r in rows}
    return _role_perm_cache[gid]


def set_role_perm(guild_id, role_id, level):
    if level == 0:
        db_exec("DELETE FROM role_perms WHERE guild_id = ? AND role_id = ?", (str(guild_id), str(role_id)))
    else:
        db_exec("INSERT OR REPLACE INTO role_perms VALUES (?, ?, ?)", (str(guild_id), str(role_id), level))
    _role_perm_cache.pop(int(guild_id), None)


def get_role_perm(guild_id, role_id):
    return _guild_role_perms(guild_id).get(int(role_id))


def del_role_perm(guild_id, role_id):
    n = db_exec("DELETE FROM role_perms WHERE guild_id = ? AND role_id = ?",
                (str(guild_id), str(role_id))).rowcount
    _role_perm_cache.pop(int(guild_id), None)
    return n > 0


# ---- Perms membre ----

def set_member_perm(guild_id, user_id, level):
    if level == 0:
        db_exec("DELETE FROM member_perms WHERE guild_id = ? AND user_id = ?", (str(guild_id), str(user_id)))
    else:
        db_exec("INSERT OR REPLACE INTO member_perms VALUES (?, ?, ?)", (str(guild_id), str(user_id), level))


def get_member_perm(guild_id, user_id):
    row = db_one("SELECT level FROM member_perms WHERE guild_id = ? AND user_id = ?",
                 (str(guild_id), str(user_id)))
    return row["level"] if row else None


def del_member_perm(guild_id, user_id):
    return db_exec("DELETE FROM member_perms WHERE guild_id = ? AND user_id = ?",
                   (str(guild_id), str(user_id))).rowcount > 0


def get_perms_at_level(guild_id, level):
    roles = [str(rid) for rid, lvl in _guild_role_perms(guild_id).items() if lvl == level]
    users = [r["user_id"] for r in db_all(
        "SELECT user_id FROM member_perms WHERE guild_id = ? AND level = ?", (str(guild_id), level))]
    return roles, users


# ---- Override niveau commande ----

def set_cmd_level(guild_id, command, level):
    db_exec("INSERT OR REPLACE INTO cmd_perms VALUES (?, ?, ?)", (str(guild_id), command, level))


def get_cmd_override(guild_id, command):
    row = db_one("SELECT level FROM cmd_perms WHERE guild_id = ? AND command = ?", (str(guild_id), command))
    return row["level"] if row else None


def del_cmd_override(guild_id, command):
    return db_exec("DELETE FROM cmd_perms WHERE guild_id = ? AND command = ?",
                   (str(guild_id), command)).rowcount > 0


# ---- Bot bans ----

def is_bot_banned(user_id):
    if int(user_id) in BUYER_IDS:
        return False
    return db_one("SELECT 1 FROM bot_bans WHERE user_id = ?", (str(user_id),)) is not None


def add_bot_ban(user_id, banned_by):
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %Hh%M")
    db_exec("INSERT OR REPLACE INTO bot_bans VALUES (?, ?, ?)", (str(user_id), str(banned_by), now))


def remove_bot_ban(user_id):
    db_exec("DELETE FROM bot_bans WHERE user_id = ?", (str(user_id),))


# ---- Blocked channels ----

def block_channel(guild_id, channel_id):
    db_exec("INSERT OR IGNORE INTO blocked_channels VALUES (?, ?)", (str(guild_id), str(channel_id)))


def unblock_channel(guild_id, channel_id):
    return db_exec("DELETE FROM blocked_channels WHERE guild_id = ? AND channel_id = ?",
                   (str(guild_id), str(channel_id))).rowcount > 0


def is_channel_blocked(guild_id, channel_id):
    return db_one("SELECT 1 FROM blocked_channels WHERE guild_id = ? AND channel_id = ? LIMIT 1",
                  (str(guild_id), str(channel_id))) is not None


def get_blocked_channels(guild_id):
    return [r["channel_id"] for r in db_all(
        "SELECT channel_id FROM blocked_channels WHERE guild_id = ?", (str(guild_id),))]


# ---- Log channels ----

def get_log_channel(guild_id, log_type):
    row = db_one("SELECT channel_id FROM log_channels WHERE guild_id = ? AND log_type = ?",
                 (str(guild_id), log_type))
    return row["channel_id"] if row else None


def set_log_channel(guild_id, log_type, channel_id):
    db_exec("INSERT OR REPLACE INTO log_channels VALUES (?, ?, ?)", (str(guild_id), log_type, str(channel_id)))


def delete_log_channel(guild_id, log_type):
    db_exec("DELETE FROM log_channels WHERE guild_id = ? AND log_type = ?", (str(guild_id), log_type))


def get_all_log_channels(guild_id):
    return {r["log_type"]: r["channel_id"] for r in db_all(
        "SELECT log_type, channel_id FROM log_channels WHERE guild_id = ?", (str(guild_id),))}


# ---- Sanctions ----

def add_sanction(guild_id, user_id, moderator_id, type_, reason=None, duration=None):
    now = datetime.now(PARIS_TZ).isoformat()
    cur = db_exec(
        "INSERT INTO sanctions (guild_id, user_id, moderator_id, type, reason, duration, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(guild_id), str(user_id), str(moderator_id), type_, reason, duration, now))
    return cur.lastrowid


def get_sanction(guild_id, sanction_id):
    row = db_one("SELECT * FROM sanctions WHERE id = ? AND guild_id = ?", (sanction_id, str(guild_id)))
    return dict(row) if row else None


def update_sanction_reason(guild_id, sanction_id, reason):
    return db_exec("UPDATE sanctions SET reason = ? WHERE id = ? AND guild_id = ?",
                   (reason, sanction_id, str(guild_id))).rowcount > 0


def deactivate_user_sanctions(guild_id, user_id, types):
    marks = ",".join("?" * len(types))
    return db_exec(
        f"UPDATE sanctions SET active = 0 WHERE guild_id = ? AND user_id = ? AND type IN ({marks}) AND active = 1",
        (str(guild_id), str(user_id), *types)).rowcount


def get_warns(guild_id, user_id):
    rows = db_all(
        "SELECT id, moderator_id, reason, timestamp FROM sanctions "
        "WHERE guild_id = ? AND user_id = ? AND type = 'warn' AND active = 1 "
        "ORDER BY id DESC LIMIT 25", (str(guild_id), str(user_id)))
    return [dict(r) for r in rows]


def delete_warn(guild_id, warn_id):
    return db_exec(
        "UPDATE sanctions SET active = 0 WHERE id = ? AND guild_id = ? AND type = 'warn' AND active = 1",
        (warn_id, str(guild_id))).rowcount > 0


def get_active_tempbans():
    return [dict(r) for r in db_all("SELECT * FROM sanctions WHERE type = 'tempban' AND active = 1")]


def deactivate_sanction(sanction_id):
    db_exec("UPDATE sanctions SET active = 0 WHERE id = ?", (sanction_id,))


def get_history(guild_id, user_id, limit=20):
    return [dict(r) for r in db_all(
        "SELECT * FROM sanctions WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
        (str(guild_id), str(user_id), limit))]


# ========================= LOGIQUE DE PERMISSION =========================

def level_name(lvl):
    if lvl >= LEVEL_BUYER:
        return "Buyer"
    if lvl == LEVEL_OWNER:
        return "Owner"
    if lvl == LEVEL_WL:
        return "WL"
    if 1 <= lvl <= MAX_TIER:
        return f"perm{lvl}"
    return "Public"


def level_emoji(lvl):
    if lvl >= LEVEL_BUYER:
        return "💎"
    if lvl == LEVEL_OWNER:
        return "👑"
    if lvl == LEVEL_WL:
        return "✨"
    if 1 <= lvl <= MAX_TIER:
        return "🔹"
    return "👤"


def parse_level(s, max_level=MAX_TIER):
    """Accepte 'public', '0'..'9', 'perm1'..'perm9' (et 'wl'/'owner' si autorisé)."""
    if s is None:
        return None
    s = str(s).strip().lower()
    if s in ("public", "membre", "membres", "pub"):
        return 0
    if s == "wl":
        return LEVEL_WL if max_level >= LEVEL_WL else None
    if s == "owner":
        return LEVEL_OWNER if max_level >= LEVEL_OWNER else None
    if s == "buyer":
        return None
    m = re.match(r"^perm\s*(\d)$", s)
    if m:
        n = int(m.group(1))
        return n if 0 <= n <= min(max_level, MAX_TIER) else None
    if s.isdigit():
        n = int(s)
        return n if 0 <= n <= max_level else None
    return None


def get_user_level(member):
    """Niveau effectif = max(rang de base, perm membre, perms des rôles)."""
    if member.id in BUYER_IDS:
        return LEVEL_BUYER
    lvl = get_base_rank(member.id)
    guild = getattr(member, "guild", None)
    if guild is None:
        return lvl
    mp = get_member_perm(guild.id, member.id)
    if mp is not None:
        lvl = max(lvl, mp)
    rp = _guild_role_perms(guild.id)
    for r in getattr(member, "roles", []):
        lvl = max(lvl, rp.get(r.id, 0))
    return lvl


def get_command_level(guild_id, command_name):
    meta = COMMANDS.get(command_name)
    if not meta:
        return LEVEL_OWNER
    if meta["locked"]:
        return meta["level"]
    ov = get_cmd_override(guild_id, command_name)
    return ov if ov is not None else meta["level"]


def is_owner_plus(member):
    return get_user_level(member) >= LEVEL_OWNER


def check_target(ctx, target, uid=None):
    """Vérifie qu'on peut agir sur la cible. Renvoie un message d'erreur, ou None si OK.
    Règles : pas soi-même, pas le bot, pas un Buyer, pas un niveau Moh >= au sien,
    et respect de la hiérarchie des rôles Discord (sauf Owner+)."""
    uid = target.id if target is not None else uid
    if uid == ctx.author.id:
        return "Tu ne peux pas faire ça sur toi-même."
    if bot.user and uid == bot.user.id:
        return "Je ne vais pas faire ça sur moi-même."
    if uid in BUYER_IDS:
        return "Impossible sur un **Buyer**."
    author_lvl = get_user_level(ctx.author)
    target_lvl = get_user_level(target) if isinstance(target, discord.Member) else get_base_rank(uid)
    if target_lvl >= author_lvl:
        return f"La cible a un niveau égal ou supérieur au tien (**{level_name(target_lvl)}**)."
    if isinstance(target, discord.Member):
        if target.id == ctx.guild.owner_id:
            return "Impossible sur le propriétaire du serveur."
        if target.top_role >= ctx.guild.me.top_role:
            return "Ce membre a un rôle au-dessus ou égal au mien."
        if (target.top_role >= ctx.author.top_role and author_lvl < LEVEL_OWNER
                and ctx.author.id != ctx.guild.owner_id):
            return "Ce membre a un rôle égal ou supérieur au tien."
    return None


# ========================= HELPERS =========================

def trunc(text, limit):
    text = str(text) if text is not None else ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def join_lines(items, limit=1024, empty="*aucun*"):
    """Joint des lignes sans dépasser la limite d'un champ d'embed."""
    out, size = [], 0
    for i, it in enumerate(items):
        if size + len(it) + 1 > limit - 20:
            out.append(f"… +{len(items) - i}")
            break
        out.append(it)
        size += len(it) + 1
    return "\n".join(out) if out else empty


def _embed(title, desc, color):
    em = discord.Embed(title=title, description=trunc(desc, 4000), color=color)
    em.set_footer(text=FOOTER_TEXT)
    return em


def embed_color():
    return COLOR_DEFAULT


def success_embed(title, desc=""):
    return _embed(title, desc, COLOR_SUCCESS)


def error_embed(title, desc=""):
    return _embed(title, desc, COLOR_ERROR)


def info_embed(title, desc=""):
    return _embed(title, desc, COLOR_INFO)


def warning_embed(title, desc=""):
    return _embed(title, desc, COLOR_WARNING)


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
    em.add_field(name="Raison", value=trunc(reason, 1024) if reason else "*Non spécifiée*", inline=False)
    em.set_footer(text=f"{FOOTER_TEXT} ・ Sanction #{case_id}" if case_id is not None else FOOTER_TEXT)
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
    units = [(86400*365, "an", "ans"), (86400, "jour", "jours"), (3600, "heure", "heures"),
             (60, "minute", "minutes"), (1, "seconde", "secondes")]
    parts = []
    rem = int(seconds)
    for sec, sing, plur in units:
        n, rem = divmod(rem, sec)
        if n > 0:
            parts.append(f"{n} {sing if n == 1 else plur}")
        if len(parts) >= 2:
            break
    return ", ".join(parts) if parts else "0 sec"


DURATION_RE = re.compile(r"(\d+)\s*([smhdjw])", re.IGNORECASE)
DURATION_FULL_RE = re.compile(r"^(\d+[smhdjw])+$", re.IGNORECASE)


def parse_duration(s):
    """'30m', '2h', '1d12h', '1w' → secondes. Un nombre seul = minutes. Sinon None."""
    if not s:
        return None
    s = s.strip().lower().replace(" ", "")
    if s.isdigit():
        return int(s) * 60
    if not DURATION_FULL_RE.match(s):
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "j": 86400, "w": 604800}
    return sum(int(m.group(1)) * units[m.group(2)] for m in DURATION_RE.finditer(s))


def parse_id(raw):
    try:
        return int(str(raw).strip().strip("<@!&#>"))
    except (ValueError, TypeError):
        return None


async def resolve_member(ctx, user_input):
    if not user_input:
        return None
    mid = parse_id(user_input)
    if mid is not None:
        m = ctx.guild.get_member(mid)
        if m:
            return m
    try:
        return await commands.MemberConverter().convert(ctx, str(user_input))
    except commands.CommandError:
        return None


async def resolve_user_or_id(ctx, user_input):
    if not user_input:
        return None, None
    raw = str(user_input).strip()
    uid = parse_id(raw)
    if uid is None:
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
        member = ctx.guild.get_member(uid)
        if member:
            return member, uid
    try:
        return await bot.fetch_user(uid), uid
    except discord.NotFound:
        return None, None   # ID qui ne correspond à aucun compte
    except discord.HTTPException:
        return None, uid


async def resolve_channel(ctx, channel_input):
    cid = parse_id(channel_input)
    if cid is not None:
        ch = ctx.guild.get_channel(cid)
        if ch:
            return ch, cid
    try:
        ch = await commands.TextChannelConverter().convert(ctx, str(channel_input))
        return ch, ch.id
    except commands.CommandError:
        return None, cid


def format_user_display(display_obj, user_id):
    if display_obj is not None:
        return f"{display_obj.mention} (`{display_obj.id}`)"
    return f"<@{user_id}> (`{user_id}`) *(hors serveur)*"


async def safe_dm(user, embed):
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        return False


# ========================= BOT SETUP =========================

init_db()
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True


def get_prefix(bot_, message):
    return commands.when_mentioned_or(get_prefix_cached())(bot_, message)


bot = commands.Bot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
)


# ========================= UI : vues réutilisables =========================

class OwnedView(discord.ui.View):
    """Vue utilisable seulement par l'auteur, désactivée proprement au timeout."""

    def __init__(self, author_id, timeout=120):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"Ce menu n'est pas à toi. Fais `{get_prefix_cached()}help`.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Paginator(OwnedView):
    def __init__(self, author_id, pages):
        super().__init__(author_id, timeout=120)
        self.pages = pages
        self.index = 0
        self._sync()

    def _sync(self):
        self.prev_b.disabled = self.index <= 0
        self.next_b.disabled = self.index >= len(self.pages) - 1

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary)
    async def prev_b(self, interaction, button):
        self.index = max(0, self.index - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary)
    async def next_b(self, interaction, button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)


class ConfirmView(OwnedView):
    def __init__(self, author_id, timeout=30):
        super().__init__(author_id, timeout=timeout)
        self.value = None

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.danger)
    async def confirm_b(self, interaction, button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel_b(self, interaction, button):
        self.value = False
        await interaction.response.defer()
        self.stop()


async def send_paginated(ctx, pages):
    if len(pages) == 1:
        return await ctx.send(embed=pages[0])
    view = Paginator(ctx.author.id, pages)
    view.message = await ctx.send(embed=pages[0], view=view)
    return view.message


# ========================= GLOBAL CHECK =========================

class BotBannedError(commands.CheckFailure):
    pass


class ChannelBlockedError(commands.CheckFailure):
    pass


class PermLevelError(commands.CheckFailure):
    def __init__(self, required):
        super().__init__()
        self.required = required


@bot.check
async def global_check(ctx):
    if ctx.command is None:
        return True
    if is_bot_banned(ctx.author.id):
        raise BotBannedError()
    if ctx.guild is None:
        raise commands.NoPrivateMessage()

    level = get_user_level(ctx.author)
    required = get_command_level(ctx.guild.id, ctx.command.name)

    # Staff (WL+) : bypass blocage de salon
    if level < LEVEL_WL and is_channel_blocked(ctx.guild.id, ctx.channel.id):
        raise ChannelBlockedError()

    if level < required:
        raise PermLevelError(required)
    return True


# ========================= EVENTS =========================

@bot.event
async def on_ready():
    log.info(f"Moh connecté : {bot.user} ({bot.user.id}) sur {len(bot.guilds)} serveur(s)")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.playing, name="au stake fils de hechek")
    )
    if not tempban_loop.is_running():
        tempban_loop.start()


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        error = error.original
    if isinstance(error, BotBannedError):
        try:
            await ctx.send(embed=error_embed("⛔ Accès refusé", "Tu es **banni du bot Moh**."))
        except discord.HTTPException:
            pass
        return
    if isinstance(error, (ChannelBlockedError, PermLevelError)):
        try:
            await ctx.message.add_reaction("🔇" if isinstance(error, ChannelBlockedError) else "❌")
        except discord.HTTPException:
            pass
        return
    if isinstance(error, (commands.CommandNotFound, commands.NoPrivateMessage)):
        return
    try:
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
        elif isinstance(error, discord.Forbidden):
            await ctx.send(embed=error_embed("❌ Permissions Discord insuffisantes",
                                             "Le bot n'a pas les permissions Discord nécessaires."))
        else:
            log.error(f"Erreur '{ctx.command}' par {ctx.author} : {error}\n"
                      + "".join(traceback.format_exception(type(error), error, error.__traceback__)))
            await ctx.send(embed=error_embed("❌ Erreur interne", "Une erreur inattendue est survenue."))
    except discord.HTTPException:
        pass


@bot.event
async def on_command_completion(ctx):
    if ctx.guild is None or ctx.command is None:
        return
    await send_log(ctx.guild, "commands", f"⌨️ {get_prefix_cached()}{ctx.command.qualified_name}",
                   author=ctx.author,
                   desc=f"**Salon :** {ctx.channel.mention}\n**Message :** {trunc(ctx.message.content, 1000)}",
                   color=COLOR_DEFAULT)


# ========================= SEND LOG =========================

async def send_log(guild, log_type, title, *, author=None, target=None, desc=None, color=None, fields=None):
    channel_id = get_log_channel(guild.id, log_type)
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    em = discord.Embed(title=trunc(title, 256), color=color if color is not None else COLOR_DEFAULT,
                       timestamp=datetime.now(timezone.utc))
    if author:
        em.add_field(name="Auteur", value=f"{author.mention} (`{author.id}`)", inline=True)
    if target:
        em.add_field(name="Cible", value=f"{target.mention} (`{target.id}`)", inline=True)
    if desc:
        em.description = trunc(desc, 4000)
    if fields:
        for name, value, inline in fields:
            em.add_field(name=name, value=trunc(value, 1024), inline=inline)
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
            if start + timedelta(seconds=int(s["duration"])) > now:
                continue
            guild = bot.get_guild(int(s["guild_id"]))
            if not guild:
                continue
            try:
                await guild.unban(discord.Object(id=int(s["user_id"])), reason="Tempban expiré")
                deactivate_sanction(s["id"])
                await send_log(guild, "moderation", "⏰ Tempban expiré",
                               desc=f"**Cible :** <@{s['user_id']}>\n**Sanction :** `#{s['id']}`",
                               color=COLOR_INFO)
            except discord.NotFound:
                deactivate_sanction(s["id"])
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning(f"tempban unban {s['id']}: {e}")
    except Exception as e:
        log.error(f"tempban_loop: {e}", exc_info=True)


@tempban_loop.before_loop
async def _bef_tempban():
    await bot.wait_until_ready()


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  MODÉRATION                                              ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.command(name="ban")
async def _ban(ctx, user_input: str = None, *, args: str = None):
    if not user_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}ban @user [durée] [raison]`"))
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
            reason = rest.strip() or "Aucune raison fournie"
    member = ctx.guild.get_member(uid)
    err = check_target(ctx, member, uid)
    if err:
        return await ctx.send(embed=error_embed("❌", err))
    if member:
        await safe_dm(member, error_embed(
            f"🔨 Tu as été banni de {ctx.guild.name}",
            f"**Modérateur :** {ctx.author}\n**Raison :** {reason}\n**Durée :** "
            + (format_duration_fr(duration_seconds) if duration_seconds else "Permanent")))
    try:
        await ctx.guild.ban(discord.Object(id=uid), reason=trunc(f"[{ctx.author}] {reason}", 512),
                            delete_message_seconds=0)
    except discord.NotFound:
        return await ctx.send(embed=error_embed("❌", "Utilisateur introuvable sur Discord."))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission de bannir."))
    # Un nouveau ban remplace un éventuel tempban encore actif
    deactivate_user_sanctions(ctx.guild.id, uid, ("tempban",))
    sid = add_sanction(ctx.guild.id, uid, ctx.author.id, "tempban" if duration_seconds else "ban",
                       reason, duration_seconds)
    duration_str = format_duration_fr(duration_seconds) if duration_seconds else "Permanent"
    await ctx.send(embed=mod_embed("Ban", display, uid, ctx.author, reason, duration_str, sid))
    await send_log(ctx.guild, "moderation", "🔨 Ban", author=ctx.author,
                   desc=f"**Cible :** <@{uid}> (`{uid}`)\n**Durée :** {duration_str}\n**Raison :** {reason}\n**Sanction :** `#{sid}`",
                   color=COLOR_ERROR)


@bot.command(name="unban")
async def _unban(ctx, user_input: str = None, *, reason: str = None):
    user_id = parse_id(user_input)
    if not user_id:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}unban <id> [raison]`"))
    try:
        await ctx.guild.unban(discord.Object(id=user_id), reason=trunc(f"[{ctx.author}] {reason or ''}", 512))
    except discord.NotFound:
        return await ctx.send(embed=error_embed("❌", "Aucun ban trouvé pour cet ID."))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))
    deactivate_user_sanctions(ctx.guild.id, user_id, ("ban", "tempban"))
    await ctx.send(embed=success_embed("✅ Débanni", f"<@{user_id}> a été débanni."))
    await send_log(ctx.guild, "moderation", "✅ Unban", author=ctx.author,
                   desc=f"**Cible :** <@{user_id}> (`{user_id}`)\n**Raison :** {reason or '*Aucune*'}", color=COLOR_SUCCESS)


@bot.command(name="kick")
async def _kick(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not member:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}kick @user [raison]`"))
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("❌", err))
    await safe_dm(member, error_embed(f"👢 Tu as été expulsé de {ctx.guild.name}",
                                      f"**Modérateur :** {ctx.author}\n**Raison :** {reason}"))
    try:
        await member.kick(reason=trunc(f"[{ctx.author}] {reason}", 512))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
    await ctx.send(embed=mod_embed("Kick", member, member.id, ctx.author, reason, case_id=sid))
    await send_log(ctx.guild, "moderation", "👢 Kick", author=ctx.author, target=member,
                   desc=f"**Raison :** {reason}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="mute", aliases=["timeout"])
async def _mute(ctx, member: discord.Member = None, duration: str = None, *, reason: str = "Aucune raison fournie"):
    if not member or not duration:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}mute @user <durée> [raison]`\nExemples : `30m`, `2h`, `1d`"))
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("❌", err))
    if member.guild_permissions.administrator:
        return await ctx.send(embed=error_embed("❌", "Impossible de mute un administrateur (limite Discord)."))
    seconds = parse_duration(duration)
    if seconds is None or seconds <= 0:
        return await ctx.send(embed=error_embed("❌ Durée invalide", "Exemples : `30m`, `2h`, `1d`"))
    if seconds > 60*60*24*28:
        return await ctx.send(embed=error_embed("❌ Trop long", "Maximum 28 jours (limite Discord)."))
    try:
        await member.timeout(timedelta(seconds=seconds), reason=trunc(f"[{ctx.author}] {reason}", 512))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))
    except discord.HTTPException as e:
        return await ctx.send(embed=error_embed("❌ Erreur Discord", str(e)))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "mute", reason, seconds)
    duration_str = format_duration_fr(seconds)
    await safe_dm(member, warning_embed(f"🔇 Tu as été mute sur {ctx.guild.name}",
                                        f"**Modérateur :** {ctx.author}\n**Durée :** {duration_str}\n**Raison :** {reason}"))
    await ctx.send(embed=mod_embed("Mute", member, member.id, ctx.author, reason, duration_str, sid))
    await send_log(ctx.guild, "moderation", "🔇 Mute", author=ctx.author, target=member,
                   desc=f"**Durée :** {duration_str}\n**Raison :** {reason}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="unmute", aliases=["untimeout"])
async def _unmute(ctx, member: discord.Member = None, *, reason: str = None):
    if not member:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}unmute @user [raison]`"))
    if not member.is_timed_out():
        return await ctx.send(embed=info_embed("Pas mute", f"{member.mention} n'est pas mute."))
    try:
        await member.timeout(None, reason=trunc(f"[{ctx.author}] {reason or ''}", 512))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission."))
    deactivate_user_sanctions(ctx.guild.id, member.id, ("mute",))
    await ctx.send(embed=success_embed("🔊 Mute retiré", f"{member.mention} a été démuté."))
    await send_log(ctx.guild, "moderation", "🔊 Unmute", author=ctx.author, target=member,
                   desc=f"**Raison :** {reason or '*Aucune*'}", color=COLOR_SUCCESS)


@bot.command(name="warn")
async def _warn(ctx, member: discord.Member = None, *, reason: str = None):
    if not member or not reason:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}warn @user <raison>`"))
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("❌", err))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
    warn_count = len(get_warns(ctx.guild.id, member.id))
    await safe_dm(member, warning_embed(f"⚠️ Avertissement sur {ctx.guild.name}",
                                        f"**Modérateur :** {ctx.author}\n**Raison :** {reason}\n**Total :** {warn_count} warn(s)"))
    em = mod_embed("Warn", member, member.id, ctx.author, reason, case_id=sid)
    em.add_field(name="Total warns", value=f"{warn_count}", inline=True)
    await ctx.send(embed=em)
    await send_log(ctx.guild, "moderation", "⚠️ Warn", author=ctx.author, target=member,
                   desc=f"**Raison :** {reason}\n**Total :** {warn_count}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="warns", aliases=["warnings"])
async def _warns(ctx, *, user_input: str = None):
    if user_input:
        display, uid = await resolve_user_or_id(ctx, user_input)
        if uid is None:
            return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    else:
        display, uid = ctx.author, ctx.author.id
    warns = get_warns(ctx.guild.id, uid)
    if not warns:
        return await ctx.send(embed=info_embed("⚠️ Avertissements", f"{format_user_display(display, uid)} n'a aucun warn actif."))
    lines = []
    for w in warns[:15]:
        mod = ctx.guild.get_member(int(w["moderator_id"]))
        mod_name = mod.display_name if mod else f"<@{w['moderator_id']}>"
        try:
            ts = datetime.fromisoformat(w["timestamp"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            ts = "?"
        lines.append(f"`#{w['id']}` — {ts} — par **{mod_name}**\n→ *{trunc(w['reason'], 200)}*")
    name = getattr(display, "display_name", str(uid))
    em = info_embed(f"⚠️ Avertissements de {name} ({len(warns)})", "\n\n".join(lines))
    if display is not None:
        em.set_thumbnail(url=display.display_avatar.url)
    await ctx.send(embed=em)


@bot.command(name="delwarn", aliases=["unwarn"])
async def _delwarn(ctx, warn_id: int = None):
    if not warn_id:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}delwarn <id>`"))
    if delete_warn(ctx.guild.id, warn_id):
        await ctx.send(embed=success_embed("✅ Warn supprimé", f"Warn `#{warn_id}` retiré."))
        await send_log(ctx.guild, "moderation", "🗑️ Warn supprimé", author=ctx.author,
                       desc=f"Warn `#{warn_id}` supprimé.", color=COLOR_SUCCESS)
    else:
        await ctx.send(embed=error_embed("❌", f"Warn `#{warn_id}` introuvable ou déjà supprimé."))


@bot.command(name="clearwarns")
async def _clearwarns(ctx, *, user_input: str = None):
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}clearwarns @user`"))
    count = deactivate_user_sanctions(ctx.guild.id, uid, ("warn",))
    if count:
        await ctx.send(embed=success_embed("✅ Warns effacés", f"**{count}** warn(s) retiré(s) à {format_user_display(display, uid)}."))
        await send_log(ctx.guild, "moderation", "🗑️ Warns effacés", author=ctx.author,
                       desc=f"**Cible :** <@{uid}>\n**{count}** warns retirés", color=COLOR_SUCCESS)
    else:
        await ctx.send(embed=error_embed("❌", f"{format_user_display(display, uid)} n'a aucun warn actif."))


@bot.command(name="softban")
async def _softban(ctx, member: discord.Member = None, *, reason: str = "Aucune raison fournie"):
    if not member:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}softban @user [raison]`"))
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("❌", err))
    await safe_dm(member, warning_embed(f"🧹 Softban sur {ctx.guild.name}",
                                        f"**Modérateur :** {ctx.author}\n**Raison :** {reason}\n*Tu peux revenir, tes messages récents ont été supprimés.*"))
    try:
        await ctx.guild.ban(member, reason=trunc(f"[{ctx.author}] softban: {reason}", 512),
                            delete_message_seconds=86400)
        await ctx.guild.unban(discord.Object(id=member.id), reason="softban (auto-unban)")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "softban", reason)
    await ctx.send(embed=mod_embed("Softban", member, member.id, ctx.author, reason, case_id=sid))
    await send_log(ctx.guild, "moderation", "🧹 Softban", author=ctx.author, target=member,
                   desc=f"**Raison :** {reason}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="clear", aliases=["purge"])
async def _clear(ctx, *args):
    """+clear <n>  ou  +clear @user <n>  (supprime n messages de ce membre)"""
    member, amount = None, None
    for a in args:
        if a.isdigit() and len(a) <= 4:
            amount = int(a)
        else:
            member = await resolve_member(ctx, a)
            if member is None:
                return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if not amount:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}clear [@user] <nombre>` (max {MAX_CLEAR})"))
    if amount < 1 or amount > MAX_CLEAR:
        return await ctx.send(embed=error_embed("❌", f"Entre 1 et {MAX_CLEAR}."))
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    try:
        if member:
            remaining = [amount]

            def check(m):
                if m.author.id != member.id or remaining[0] <= 0:
                    return False
                remaining[0] -= 1
                return True
            deleted = await ctx.channel.purge(limit=min(amount * 10, 1000), check=check)
        else:
            deleted = await ctx.channel.purge(limit=amount)
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Je n'ai pas la permission de supprimer."))
    who = f" de {member.mention}" if member else ""
    await ctx.send(f"🧹 **{len(deleted)}** message(s){who} supprimé(s).", delete_after=5)
    await send_log(ctx.guild, "moderation", "🧹 Clear", author=ctx.author,
                   desc=f"**Salon :** {ctx.channel.mention}\n**Messages :** {len(deleted)}{who}", color=COLOR_INFO)


@bot.command(name="lock")
async def _lock(ctx, channel: discord.TextChannel = None, *, reason: str = "Pas de raison"):
    channel = channel or ctx.channel
    try:
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = False
        ow.add_reactions = False
        ow.create_public_threads = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=trunc(f"[{ctx.author}] {reason}", 512))
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))
    await ctx.send(embed=warning_embed("🔒 Salon verrouillé", f"{channel.mention} est en lecture seule."))
    await send_log(ctx.guild, "moderation", "🔒 Lock", author=ctx.author,
                   desc=f"**Salon :** {channel.mention}\n**Raison :** {reason}", color=COLOR_WARNING)


@bot.command(name="unlock")
async def _unlock(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    try:
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = None
        ow.add_reactions = None
        ow.create_public_threads = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=None if ow.is_empty() else ow,
                                      reason=f"[{ctx.author}] unlock")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))
    await ctx.send(embed=success_embed("🔓 Salon déverrouillé", f"{channel.mention} est à nouveau ouvert."))
    await send_log(ctx.guild, "moderation", "🔓 Unlock", author=ctx.author,
                   desc=f"**Salon :** {channel.mention}", color=COLOR_SUCCESS)


@bot.command(name="slowmode", aliases=["slow"])
async def _slowmode(ctx, duration: str = None):
    if duration is None:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}slowmode <secondes|30s|5m|1h>` (0 = off, max 6h)"))
    seconds = int(duration) if duration.isdigit() else parse_duration(duration)
    if seconds is None or seconds < 0 or seconds > 21600:
        return await ctx.send(embed=error_embed("❌", "Entre 0 et 21600 secondes (6h)."))
    try:
        await ctx.channel.edit(slowmode_delay=seconds, reason=f"[{ctx.author}] slowmode")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))
    if seconds == 0:
        await ctx.send(embed=success_embed("✅ Slowmode désactivé", f"{ctx.channel.mention}"))
    else:
        await ctx.send(embed=success_embed("🐌 Slowmode activé", f"{ctx.channel.mention} → **{format_duration_fr(seconds)}**"))


@bot.command(name="banlist", aliases=["bans"])
async def _banlist(ctx):
    try:
        entries = [e async for e in ctx.guild.bans(limit=1000)]
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission **Bannir des membres** requise."))
    if not entries:
        return await ctx.send(embed=info_embed("🔨 Bannis", "Aucun membre banni."))
    lines = [f"• **{e.user}** (`{e.user.id}`) — *{trunc(e.reason or 'sans raison', 60)}*" for e in entries]
    chunks = [lines[i:i + 15] for i in range(0, len(lines), 15)]
    pages = []
    for i, chunk in enumerate(chunks):
        em = info_embed(f"🔨 Bannis ({len(entries)})", "\n".join(chunk))
        em.set_footer(text=f"{FOOTER_TEXT} ・ {i+1}/{len(chunks)}")
        pages.append(em)
    await send_paginated(ctx, pages)


@bot.command(name="snipe")
async def _snipe(ctx):
    data = _snipe_cache.get(ctx.channel.id)
    if not data:
        return await ctx.send(embed=info_embed("🔍 Snipe", "Aucun message supprimé récemment ici."))
    ts = int(data["time"].timestamp())
    em = discord.Embed(description=trunc(data["content"], 4000) or "*pas de texte*", color=COLOR_DEFAULT)
    em.set_author(name=data["author"], icon_url=data["avatar"])
    em.add_field(name="Supprimé", value=f"<t:{ts}:R>", inline=True)
    if data.get("image"):
        em.set_image(url=data["image"])
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


SANCTION_EMOJI = {"ban": "🔨", "tempban": "⏰", "kick": "👢", "mute": "🔇", "warn": "⚠️", "softban": "🧹"}


@bot.command(name="history", aliases=["sanctions"])
async def _history(ctx, *, user_input: str = None):
    if user_input:
        display, uid = await resolve_user_or_id(ctx, user_input)
        if uid is None:
            return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    else:
        display, uid = ctx.author, ctx.author.id
    rows = get_history(ctx.guild.id, uid, limit=50)
    if not rows:
        return await ctx.send(embed=info_embed("📜 Historique", f"{format_user_display(display, uid)} n'a aucune sanction."))
    lines = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            ts = "?"
        mod = ctx.guild.get_member(int(r["moderator_id"]))
        mod_name = mod.display_name if mod else f"<@{r['moderator_id']}>"
        marker = "" if r["active"] else " *(retiré)*"
        dur = f" · {format_duration_fr(r['duration'])}" if r["duration"] else ""
        lines.append(f"{SANCTION_EMOJI.get(r['type'], '•')} `#{r['id']}` · {ts} · **{r['type']}**{dur}{marker}\n"
                     f"→ par {mod_name} : *{trunc(r['reason'] or 'sans raison', 150)}*")
    name = getattr(display, "display_name", str(uid))
    chunks = [lines[i:i + 8] for i in range(0, len(lines), 8)]
    pages = []
    for i, chunk in enumerate(chunks):
        em = discord.Embed(title=f"📜 Historique de {name} ({len(rows)})",
                           description=trunc("\n\n".join(chunk), 4000), color=embed_color())
        if display is not None:
            em.set_thumbnail(url=display.display_avatar.url)
        em.set_footer(text=f"{FOOTER_TEXT} ・ {i+1}/{len(chunks)}")
        pages.append(em)
    await send_paginated(ctx, pages)


@bot.command(name="case")
async def _case(ctx, case_id: int = None):
    if not case_id:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}case <id>`"))
    s = get_sanction(ctx.guild.id, case_id)
    if not s:
        return await ctx.send(embed=error_embed("❌", f"Sanction `#{case_id}` introuvable."))
    try:
        ts = int(datetime.fromisoformat(s["timestamp"]).timestamp())
        when = f"<t:{ts}:F> (<t:{ts}:R>)"
    except (ValueError, TypeError):
        when = "?"
    em = discord.Embed(title=f"{SANCTION_EMOJI.get(s['type'], '•')} Sanction #{s['id']} — {s['type']}", color=COLOR_MOD)
    em.add_field(name="Cible", value=f"<@{s['user_id']}> (`{s['user_id']}`)", inline=True)
    em.add_field(name="Modérateur", value=f"<@{s['moderator_id']}>", inline=True)
    em.add_field(name="Statut", value="🟢 Active" if s["active"] else "⚪ Retirée", inline=True)
    if s["duration"]:
        em.add_field(name="Durée", value=format_duration_fr(s["duration"]), inline=True)
    em.add_field(name="Date", value=when, inline=False)
    em.add_field(name="Raison", value=trunc(s["reason"] or "*Non spécifiée*", 1024), inline=False)
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


@bot.command(name="reason")
async def _reason(ctx, case_id: int = None, *, new_reason: str = None):
    if not case_id or not new_reason:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}reason <id> <nouvelle raison>`"))
    s = get_sanction(ctx.guild.id, case_id)
    if not s:
        return await ctx.send(embed=error_embed("❌", f"Sanction `#{case_id}` introuvable."))
    # Seul l'auteur de la sanction ou un Owner+ peut modifier la raison
    if int(s["moderator_id"]) != ctx.author.id and not is_owner_plus(ctx.author):
        return await ctx.send(embed=error_embed("❌", "Seul le modérateur de la sanction ou un **Owner** peut la modifier."))
    update_sanction_reason(ctx.guild.id, case_id, new_reason)
    await ctx.send(embed=success_embed("✏️ Raison modifiée", f"Sanction `#{case_id}` → *{trunc(new_reason, 500)}*"))
    await send_log(ctx.guild, "moderation", "✏️ Raison modifiée", author=ctx.author,
                   desc=f"**Sanction :** `#{case_id}`\n**Avant :** {s['reason'] or '*aucune*'}\n**Après :** {new_reason}",
                   color=COLOR_INFO)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  INFOS + OUTILS                                          ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.command(name="userinfo", aliases=["ui", "whois"])
async def _userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    created = int(member.created_at.timestamp())
    joined = int(member.joined_at.timestamp()) if member.joined_at else None
    roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
    lvl = get_user_level(member)
    em = discord.Embed(title=f"👤 {member}", color=member.color if member.color.value else COLOR_DEFAULT)
    em.set_thumbnail(url=member.display_avatar.url)
    em.add_field(name="ID", value=f"`{member.id}`", inline=True)
    em.add_field(name="Surnom", value=member.nick or "*aucun*", inline=True)
    em.add_field(name="Bot ?", value="Oui" if member.bot else "Non", inline=True)
    em.add_field(name="Compte créé", value=f"<t:{created}:R>", inline=True)
    if joined:
        em.add_field(name="A rejoint", value=f"<t:{joined}:R>", inline=True)
    em.add_field(name="Niveau Moh", value=f"{level_emoji(lvl)} {level_name(lvl)}", inline=True)
    if member.is_timed_out() and member.timed_out_until:
        em.add_field(name="🔇 Mute jusqu'à", value=f"<t:{int(member.timed_out_until.timestamp())}:R>", inline=True)
    warns = len(get_warns(ctx.guild.id, member.id))
    if warns:
        em.add_field(name="⚠️ Warns actifs", value=str(warns), inline=True)
    em.add_field(name=f"Rôles ({len(roles)})", value=trunc(", ".join(roles[:30]), 1024) if roles else "*aucun*", inline=False)
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


@bot.command(name="serverinfo", aliases=["si"])
async def _serverinfo(ctx):
    g = ctx.guild
    created = int(g.created_at.timestamp())
    bots = sum(1 for m in g.members if m.bot)
    humans = (g.member_count or 0) - bots
    em = discord.Embed(title=f"🏠 {g.name}", color=COLOR_DEFAULT)
    if g.icon:
        em.set_thumbnail(url=g.icon.url)
    em.add_field(name="ID", value=f"`{g.id}`", inline=True)
    em.add_field(name="Propriétaire", value=f"<@{g.owner_id}>", inline=True)
    em.add_field(name="Créé", value=f"<t:{created}:R>", inline=True)
    em.add_field(name="Membres", value=f"{g.member_count} ({humans} 👤 / {bots} 🤖)", inline=True)
    em.add_field(name="Salons", value=f"{len(g.text_channels)} 💬 / {len(g.voice_channels)} 🔊", inline=True)
    em.add_field(name="Rôles", value=str(len(g.roles)), inline=True)
    em.add_field(name="Boosts", value=f"{g.premium_subscription_count} (niveau {g.premium_tier})", inline=True)
    em.add_field(name="Émojis", value=str(len(g.emojis)), inline=True)
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


@bot.command(name="avatar", aliases=["av", "pdp"])
async def _avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    em = discord.Embed(title=f"🖼️ Avatar de {member.display_name}", color=COLOR_DEFAULT)
    em.set_image(url=member.display_avatar.url)
    em.description = f"[Lien direct]({member.display_avatar.url})"
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


@bot.command(name="ping")
async def _ping(ctx):
    await ctx.send(embed=success_embed("🏓 Pong !", f"Latence WebSocket : **{round(bot.latency*1000)} ms**"))


@bot.command(name="roleinfo", aliases=["ri"])
async def _roleinfo(ctx, *, role_input: str = None):
    if not role_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}roleinfo @rôle`"))
    try:
        role = await commands.RoleConverter().convert(ctx, role_input)
    except commands.CommandError:
        return await ctx.send(embed=error_embed("❌", "Rôle introuvable."))
    created = int(role.created_at.timestamp())
    em = discord.Embed(title=f"🎭 {role.name}", color=role.color if role.color.value else COLOR_DEFAULT)
    em.add_field(name="ID", value=f"`{role.id}`", inline=True)
    em.add_field(name="Membres", value=str(len(role.members)), inline=True)
    em.add_field(name="Couleur", value=str(role.color), inline=True)
    em.add_field(name="Position", value=str(role.position), inline=True)
    em.add_field(name="Mentionnable", value="Oui" if role.mentionable else "Non", inline=True)
    em.add_field(name="Affiché à part", value="Oui" if role.hoist else "Non", inline=True)
    rp = get_role_perm(ctx.guild.id, role.id)
    if rp:
        em.add_field(name="Niveau Moh", value=f"🔹 perm{rp}", inline=True)
    em.add_field(name="Créé", value=f"<t:{created}:R>", inline=False)
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


@bot.command(name="role")
async def _role(ctx, member: discord.Member = None, *, role_input: str = None):
    if not member or not role_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}role @user @rôle`"))
    try:
        role = await commands.RoleConverter().convert(ctx, role_input)
    except commands.CommandError:
        return await ctx.send(embed=error_embed("❌", "Rôle introuvable."))
    owner_plus = is_owner_plus(ctx.author)
    if role >= ctx.guild.me.top_role:
        return await ctx.send(embed=error_embed("❌", "Ce rôle est au-dessus du mien."))
    if role >= ctx.author.top_role and not owner_plus:
        return await ctx.send(embed=error_embed("❌", "Ce rôle est au-dessus ou égal au tien."))
    if role.is_default() or role.is_premium_subscriber() or role.is_bot_managed() or role.managed:
        return await ctx.send(embed=error_embed("❌", "Ce rôle ne peut pas être attribué manuellement."))
    if not owner_plus and any(getattr(role.permissions, p) for p in DANGEROUS_PERMS):
        return await ctx.send(embed=error_embed("❌ Rôle sensible",
                                                "Ce rôle a des permissions de modération/admin : réservé aux **Owners**."))
    try:
        if role in member.roles:
            if member.id != ctx.author.id:
                err = check_target(ctx, member)
                if err:
                    return await ctx.send(embed=error_embed("❌", err))
            await member.remove_roles(role, reason=f"[{ctx.author}] role")
            await ctx.send(embed=success_embed("➖ Rôle retiré", f"{role.mention} retiré à {member.mention}."))
        else:
            await member.add_roles(role, reason=f"[{ctx.author}] role")
            await ctx.send(embed=success_embed("➕ Rôle ajouté", f"{role.mention} ajouté à {member.mention}."))
    except discord.Forbidden:
        await ctx.send(embed=error_embed("❌", "Permission insuffisante."))


@bot.command(name="nick", aliases=["setnick"])
async def _nick(ctx, member: discord.Member = None, *, nickname: str = None):
    if not member:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}nick @user [pseudo]` (vide = reset)"))
    if member.id != ctx.author.id:
        err = check_target(ctx, member)
        if err:
            return await ctx.send(embed=error_embed("❌", err))
    elif member.top_role >= ctx.guild.me.top_role or member.id == ctx.guild.owner_id:
        return await ctx.send(embed=error_embed("❌", "Je ne peux pas modifier ton pseudo (rôle trop haut)."))
    if nickname and len(nickname) > 32:
        return await ctx.send(embed=error_embed("❌", "Pseudo max 32 caractères."))
    try:
        await member.edit(nick=nickname, reason=f"[{ctx.author}] nick")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))
    if nickname:
        await ctx.send(embed=success_embed("✏️ Pseudo changé", f"{member.mention} → **{nickname}**"))
    else:
        await ctx.send(embed=success_embed("✏️ Pseudo réinitialisé", f"{member.mention}"))


@bot.command(name="derank")
async def _derank(ctx, member: discord.Member = None):
    if not member:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}derank @user`"))
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("❌", err))
    to_remove = [r for r in member.roles
                 if r != ctx.guild.default_role and r < ctx.guild.me.top_role and not r.managed]
    if not to_remove:
        return await ctx.send(embed=error_embed("❌", "Aucun rôle gérable à retirer."))
    try:
        await member.remove_roles(*to_remove, reason=f"[{ctx.author}] derank")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission insuffisante."))
    await ctx.send(embed=success_embed("🧹 Derank", f"**{len(to_remove)}** rôle(s) retiré(s) à {member.mention}."))
    await send_log(ctx.guild, "roles", "🧹 Derank", author=ctx.author, target=member,
                   desc=f"**{len(to_remove)}** rôles retirés : {trunc(', '.join(r.mention for r in to_remove), 3500)}",
                   color=COLOR_WARNING)


@bot.command(name="say")
async def _say(ctx, *, message: str = None):
    if not message:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}say <message>`"))
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    await ctx.send(trunc(message, 2000))


@bot.command(name="dm")
async def _dm(ctx, member: discord.Member = None, *, message: str = None):
    if not member or not message:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}dm @user <message>`"))
    em = discord.Embed(title=f"📨 Message de {ctx.guild.name}", description=trunc(message, 4000), color=COLOR_INFO)
    em.set_footer(text=FOOTER_TEXT)
    if await safe_dm(member, em):
        await ctx.send(embed=success_embed("✅ MP envoyé", f"À {member.mention}."))
    else:
        await ctx.send(embed=error_embed("❌", "Impossible d'envoyer le MP (DM fermés ?)."))


@bot.command(name="embed")
async def _embed_cmd(ctx, *, message: str = None):
    if not message:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}embed <message>` ou `titre | message`"))
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    title, sep, body = message.partition("|")
    if sep:
        em = discord.Embed(title=trunc(title.strip(), 256), description=trunc(body.strip(), 4000), color=COLOR_DEFAULT)
    else:
        em = discord.Embed(description=trunc(message, 4000), color=COLOR_DEFAULT)
    em.set_footer(text=FOOTER_TEXT)
    await ctx.send(embed=em)


@bot.command(name="addemoji", aliases=["addemote"])
async def _addemoji(ctx, name: str = None, *, source: str = None):
    if not name:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}addemoji <nom> <url|emoji>` ou joins une image."))
    name = name.strip(":")
    if not re.fullmatch(r"[A-Za-z0-9_]{2,32}", name):
        return await ctx.send(embed=error_embed("❌", "Nom : 2 à 32 caractères, lettres/chiffres/_ uniquement."))
    image_bytes = None
    if ctx.message.attachments:
        att = ctx.message.attachments[0]
        if att.size > MAX_EMOJI_BYTES:
            return await ctx.send(embed=error_embed("❌", "Image trop lourde (max 256 Ko)."))
        try:
            image_bytes = await att.read()
        except discord.HTTPException:
            pass
    if image_bytes is None and source:
        src = source.strip()
        custom = re.match(r"<(a?):\w+:(\d+)>", src)
        url = None
        if custom:
            url = f"https://cdn.discordapp.com/emojis/{custom.group(2)}.{'gif' if custom.group(1) else 'png'}"
        elif src.startswith("https://"):
            url = src
        if url:
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.content.read(MAX_EMOJI_BYTES + 1)
                            if len(data) > MAX_EMOJI_BYTES:
                                return await ctx.send(embed=error_embed("❌", "Image trop lourde (max 256 Ko)."))
                            image_bytes = data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning(f"addemoji fetch: {e}")
    if image_bytes is None:
        return await ctx.send(embed=error_embed("❌", "Fournis une image (pièce jointe, URL https, ou emoji custom)."))
    try:
        emoji = await ctx.guild.create_custom_emoji(name=name, image=image_bytes, reason=f"[{ctx.author}] addemoji")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission **Gérer les émojis** requise."))
    except discord.HTTPException as e:
        return await ctx.send(embed=error_embed("❌ Erreur", f"{e}"))
    await ctx.send(embed=success_embed("✅ Émoji ajouté", f"{emoji} `:{name}:`"))


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  LOG LISTENERS + SNIPE                                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝

_snipe_cache = {}


@bot.event
async def on_message_delete(message):
    if not message.guild or message.author.bot:
        return
    image = next((a.url for a in message.attachments
                  if a.content_type and a.content_type.startswith("image/")), None)
    if message.content or image:
        _snipe_cache[message.channel.id] = {
            "content": message.content, "author": str(message.author),
            "author_id": message.author.id, "avatar": message.author.display_avatar.url,
            "time": datetime.now(timezone.utc), "image": image,
        }
    desc = f"**Auteur :** {message.author.mention} (`{message.author.id}`)\n**Salon :** {message.channel.mention}\n"
    if message.content:
        desc += f"**Contenu :**\n{trunc(message.content, 1500)}"
    if message.attachments:
        desc += f"\n**Pièces jointes :** {len(message.attachments)}"
    await send_log(message.guild, "messages", "🗑️ Message supprimé", desc=desc, color=COLOR_ERROR)


@bot.event
async def on_bulk_message_delete(messages):
    if not messages or not messages[0].guild:
        return
    ch = messages[0].channel
    await send_log(messages[0].guild, "messages", "🧹 Suppression en masse",
                   desc=f"**Salon :** {ch.mention}\n**Messages :** {len(messages)}", color=COLOR_ERROR)


@bot.event
async def on_message_edit(before, after):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    desc = (f"**Auteur :** {before.author.mention}\n**Salon :** {before.channel.mention}\n"
            f"**Avant :** {trunc(before.content, 1500) or '*vide*'}\n**Après :** {trunc(after.content, 1500) or '*vide*'}\n"
            f"[→ Jump]({after.jump_url})")
    await send_log(before.guild, "messages", "✏️ Message édité", desc=desc, color=COLOR_INFO)


@bot.event
async def on_member_join(member):
    age = (datetime.now(timezone.utc) - member.created_at).days
    young = age < YOUNG_ACCOUNT_DAYS
    await send_log(member.guild, "members", "⚠️ Nouveau membre (compte récent)" if young else "➡️ Nouveau membre",
                   target=member,
                   desc=f"**Compte créé :** <t:{int(member.created_at.timestamp())}:R> ({age} jours)\n"
                        f"**Membres :** {member.guild.member_count}",
                   color=COLOR_WARNING if young else COLOR_SUCCESS)


@bot.event
async def on_member_remove(member):
    roles = ", ".join(r.mention for r in member.roles if r != member.guild.default_role) or "*aucun*"
    await send_log(member.guild, "members", "⬅️ Membre parti", target=member,
                   desc=f"**Rôles :** {trunc(roles, 3500)}", color=COLOR_WARNING)


@bot.event
async def on_member_update(before, after):
    if before.nick != after.nick:
        await send_log(after.guild, "members", "✏️ Pseudo changé", target=after,
                       desc=f"**Avant :** {before.nick or before.name}\n**Après :** {after.nick or after.name}", color=COLOR_INFO)
    if set(before.roles) != set(after.roles):
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        parts = []
        if added:
            parts.append("**Ajoutés :** " + ", ".join(r.mention for r in added))
        if removed:
            parts.append("**Retirés :** " + ", ".join(r.mention for r in removed))
        if parts:
            await send_log(after.guild, "roles", "🎭 Rôles modifiés", target=after, desc="\n".join(parts), color=COLOR_INFO)


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel == after.channel:
        return
    if before.channel is None and after.channel:
        action, desc, color = "🎧 Rejoint un vocal", f"**Salon :** {after.channel.mention}", COLOR_SUCCESS
    elif after.channel is None and before.channel:
        action, desc, color = "🚪 Quitte un vocal", f"**Salon :** {before.channel.mention}", COLOR_WARNING
    else:
        action, desc, color = "🔄 Change de vocal", f"**De :** {before.channel.mention}\n**Vers :** {after.channel.mention}", COLOR_INFO
    await send_log(member.guild, "voice", action, target=member, desc=desc, color=color)


@bot.event
async def on_guild_role_create(role):
    await send_log(role.guild, "roles", "🎭 Rôle créé", desc=f"**Rôle :** {role.mention} (`{role.id}`)", color=COLOR_SUCCESS)


@bot.event
async def on_guild_role_delete(role):
    # Nettoie la perm Moh attachée à ce rôle
    if get_role_perm(role.guild.id, role.id) is not None:
        del_role_perm(role.guild.id, role.id)
    await send_log(role.guild, "roles", "🗑️ Rôle supprimé", desc=f"**Rôle :** `{role.name}` (`{role.id}`)", color=COLOR_ERROR)


@bot.event
async def on_member_ban(guild, user):
    await send_log(guild, "moderation", "🔨 Membre banni (audit)", desc=f"**Cible :** {user.mention} (`{user.id}`)", color=COLOR_ERROR)


@bot.event
async def on_member_unban(guild, user):
    await send_log(guild, "moderation", "✅ Membre débanni (audit)", desc=f"**Cible :** {user.mention} (`{user.id}`)", color=COLOR_SUCCESS)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  RANGS : OWNER / WL                                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.command(name="owner")
async def _owner(ctx, *, user_input: str = None):
    if user_input is None:
        ids = get_users_by_rank(LEVEL_OWNER)
        buyers = join_lines([f"💎 <@{i}>" for i in BUYER_IDS])
        owners = join_lines([f"👑 <@{i}>" for i in ids])
        return await ctx.send(embed=info_embed(
            "📋 Owners & Buyers", f"**Buyers (hardcodé) :**\n{buyers}\n\n**Owners :**\n{owners}"))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if uid in BUYER_IDS:
        return await ctx.send(embed=error_embed("❌", "C'est déjà un Buyer."))
    if getattr(display, "bot", False):
        return await ctx.send(embed=error_embed("❌", "Impossible de donner un rang à un bot."))
    if get_base_rank(uid) == LEVEL_OWNER:
        return await ctx.send(embed=error_embed("Déjà Owner", f"{format_user_display(display, uid)} est déjà Owner."))
    set_base_rank(uid, LEVEL_OWNER)
    await ctx.send(embed=success_embed("✅ Owner ajouté", f"{format_user_display(display, uid)} est maintenant **Owner**."))


@bot.command(name="unowner")
async def _unowner(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}unowner @user`"))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if uid == ctx.author.id:
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas te retirer toi-même le rang **Owner**."))
    if get_base_rank(uid) != LEVEL_OWNER:
        return await ctx.send(embed=error_embed("Pas Owner", f"{format_user_display(display, uid)} n'est pas Owner."))
    set_base_rank(uid, 0)
    await ctx.send(embed=success_embed("✅ Owner retiré", f"{format_user_display(display, uid)} n'est plus Owner."))


@bot.command(name="wl")
async def _wl(ctx, *, user_input: str = None):
    if user_input is None:
        ids = get_users_by_rank(LEVEL_WL)
        if not ids:
            return await ctx.send(embed=info_embed("📋 Liste WL", "Aucun WL."))
        return await ctx.send(embed=info_embed(f"📋 Liste WL ({len(ids)})", join_lines([f"✨ <@{i}>" for i in ids], 4000)))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if getattr(display, "bot", False):
        return await ctx.send(embed=error_embed("❌", "Impossible de donner un rang à un bot."))
    if get_base_rank(uid) >= LEVEL_WL:
        return await ctx.send(embed=error_embed("❌", f"{format_user_display(display, uid)} a déjà un rang égal ou supérieur."))
    set_base_rank(uid, LEVEL_WL)
    await ctx.send(embed=success_embed("✅ WL ajouté", f"{format_user_display(display, uid)} est maintenant **WL**."))


@bot.command(name="unwl")
async def _unwl(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}unwl @user`"))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if uid == ctx.author.id:
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas te retirer toi-même la **WL**."))
    if get_base_rank(uid) != LEVEL_WL:
        return await ctx.send(embed=error_embed("Pas WL", f"{format_user_display(display, uid)} n'est pas WL."))
    set_base_rank(uid, 0)
    await ctx.send(embed=success_embed("✅ WL retiré", f"{format_user_display(display, uid)} n'est plus WL."))


# ========================= BOTBAN =========================

@bot.command(name="botban")
async def _botban(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}botban @user`"))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    member = ctx.guild.get_member(uid)
    target_lvl = get_user_level(member) if member else get_base_rank(uid)
    if uid in BUYER_IDS or uid == ctx.author.id or target_lvl >= get_user_level(ctx.author):
        return await ctx.send(embed=error_embed("❌", "Tu ne peux pas bot-ban un rang égal ou supérieur."))
    if is_bot_banned(uid):
        return await ctx.send(embed=error_embed("Déjà banni", f"{format_user_display(display, uid)} est déjà bot-banni."))
    add_bot_ban(uid, ctx.author.id)
    await ctx.send(embed=success_embed("⛔ Bot-banni", f"{format_user_display(display, uid)} ne peut plus utiliser **Moh**."))


@bot.command(name="unbotban")
async def _unbotban(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}unbotban @user`"))
    display, uid = await resolve_user_or_id(ctx, user_input)
    if uid is None:
        return await ctx.send(embed=error_embed("❌ Utilisateur introuvable"))
    if not is_bot_banned(uid):
        return await ctx.send(embed=error_embed("Pas banni", f"{format_user_display(display, uid)} n'est pas bot-banni."))
    remove_bot_ban(uid)
    await ctx.send(embed=success_embed("✅ Bot-débanni", f"{format_user_display(display, uid)} peut à nouveau utiliser **Moh**."))


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  SYSTÈME DE PERMS                                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.command(name="setperm")
async def _setperm(ctx, target: str = None, level_str: str = None):
    """+setperm <commande|@rôle|@membre> <niveau>"""
    p = get_prefix_cached()
    if not target or level_str is None:
        return await ctx.send(embed=error_embed(
            "Usage",
            f"`{p}setperm <commande|@rôle|@membre> <niveau>`\n"
            "Niveau : `public`, `0` à `9` (ou `perm3`).\n"
            "Exemples :\n"
            f"• `{p}setperm ban 4` → la commande ban demande perm4\n"
            f"• `{p}setperm @Modo 4` → le rôle Modo a perm4\n"
            f"• `{p}setperm @membre 2` → ce membre a perm2"
        ))

    # 1) Commande ?
    cmd = bot.get_command(target.lower())
    if cmd is not None and cmd.name in COMMANDS:
        meta = COMMANDS[cmd.name]
        if meta["locked"]:
            return await ctx.send(embed=error_embed("❌ Commande verrouillée", f"`{cmd.name}` ne peut pas être reconfigurée."))
        if cmd.name in BUYER_RETIER_CMDS and ctx.author.id not in BUYER_IDS:
            return await ctx.send(embed=error_embed("❌ Réservé au Buyer",
                                                    f"Seul un **Buyer** peut changer le niveau de `{cmd.name}`."))
        cmd_lvl = parse_level(level_str, max_level=LEVEL_OWNER)
        if cmd_lvl is None:
            return await ctx.send(embed=error_embed("❌ Niveau invalide", "Niveau : `public`, `0`-`9`, `wl` ou `owner`."))
        set_cmd_level(ctx.guild.id, cmd.name, cmd_lvl)
        return await ctx.send(embed=success_embed("✅ Commande configurée",
                                                  f"`{p}{cmd.name}` demande maintenant **{level_name(cmd_lvl)}**."))

    lvl = parse_level(level_str)
    if lvl is None:
        return await ctx.send(embed=error_embed("❌ Niveau invalide", "Niveau : `public`, `0` à `9` (ex: `perm4`)."))

    # 2) Rôle ?
    try:
        role = await commands.RoleConverter().convert(ctx, target)
    except commands.CommandError:
        role = None
    if role is not None:
        if role.is_default():
            return await ctx.send(embed=error_embed("❌", "Utilise `+setperm <commande> public` plutôt que @everyone."))
        set_role_perm(ctx.guild.id, role.id, lvl)
        if lvl == 0:
            return await ctx.send(embed=success_embed("✅ Perm retirée", f"{role.mention} repasse **Public**."))
        return await ctx.send(embed=success_embed("✅ Rôle configuré", f"{role.mention} a maintenant **{level_name(lvl)}**."))

    # 3) Membre ?
    display, uid = await resolve_user_or_id(ctx, target)
    if uid is not None:
        set_member_perm(ctx.guild.id, uid, lvl)
        if lvl == 0:
            return await ctx.send(embed=success_embed("✅ Perm retirée", f"{format_user_display(display, uid)} repasse **Public**."))
        return await ctx.send(embed=success_embed("✅ Membre configuré", f"{format_user_display(display, uid)} a maintenant **{level_name(lvl)}**."))

    await ctx.send(embed=error_embed("❌ Cible introuvable", "Donne une commande, un @rôle ou un @membre."))


@bot.command(name="resetperm", aliases=["delperm"])
async def _resetperm(ctx, *, target: str = None):
    if not target:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}resetperm <commande|@rôle|@membre>`"))
    cmd = bot.get_command(target.lower())
    if cmd is not None and cmd.name in COMMANDS:
        if COMMANDS[cmd.name]["locked"]:
            return await ctx.send(embed=error_embed("❌", "Commande verrouillée."))
        if cmd.name in BUYER_RETIER_CMDS and ctx.author.id not in BUYER_IDS:
            return await ctx.send(embed=error_embed("❌ Réservé au Buyer", f"Seul un **Buyer** peut réinitialiser `{cmd.name}`."))
        del_cmd_override(ctx.guild.id, cmd.name)
        return await ctx.send(embed=success_embed("✅ Commande réinitialisée",
                                                  f"`{cmd.name}` revient à son niveau par défaut (**{level_name(COMMANDS[cmd.name]['level'])}**)."))
    try:
        role = await commands.RoleConverter().convert(ctx, target)
    except commands.CommandError:
        role = None
    if role is not None:
        if del_role_perm(ctx.guild.id, role.id):
            return await ctx.send(embed=success_embed("✅ Perm retirée", f"{role.mention} repasse **Public**."))
        return await ctx.send(embed=error_embed("❌", "Ce rôle n'avait pas de perm."))
    display, uid = await resolve_user_or_id(ctx, target)
    if uid is not None:
        if del_member_perm(ctx.guild.id, uid):
            return await ctx.send(embed=success_embed("✅ Perm retirée", f"{format_user_display(display, uid)} repasse **Public**."))
        return await ctx.send(embed=error_embed("❌", "Ce membre n'avait pas de perm."))
    await ctx.send(embed=error_embed("❌ Cible introuvable"))


@bot.command(name="perms")
async def _perms(ctx):
    viewer = get_user_level(ctx.author)
    em = discord.Embed(color=0x9b59b6)
    em.set_author(name="👥  QUI A QUEL NIVEAU")
    em.description = f"*Tu vois jusqu'à ton niveau (**{level_name(viewer)}**).*"

    if viewer >= LEVEL_BUYER:
        em.add_field(name="💎 Buyer", value=join_lines([f"<@{i}>" for i in BUYER_IDS]), inline=False)
    if viewer >= LEVEL_OWNER:
        em.add_field(name="👑 Owner", value=join_lines([f"<@{i}>" for i in get_users_by_rank(LEVEL_OWNER)]), inline=False)
    if viewer >= LEVEL_WL:
        em.add_field(name="✨ WL", value=join_lines([f"<@{i}>" for i in get_users_by_rank(LEVEL_WL)]), inline=False)

    for lvl in range(MAX_TIER, 0, -1):
        if lvl > viewer:
            continue
        roles, users = get_perms_at_level(ctx.guild.id, lvl)
        if not roles and not users:
            continue
        val = ""
        if roles:
            val += "Rôles : " + ", ".join(f"<@&{r}>" for r in roles) + "\n"
        if users:
            val += "Membres : " + ", ".join(f"<@{u}>" for u in users)
        em.add_field(name=f"🔹 perm{lvl}", value=trunc(val, 1024) or "*vide*", inline=False)

    em.set_footer(text=f"{FOOTER_TEXT} ・ {get_prefix_cached()}helpall pour les commandes par niveau")
    await ctx.send(embed=em)


@bot.command(name="helpall")
async def _helpall(ctx):
    viewer = get_user_level(ctx.author)
    by_level = {}
    for name in COMMANDS:
        by_level.setdefault(get_command_level(ctx.guild.id, name), []).append(name)

    order = [LEVEL_BUYER, LEVEL_OWNER, LEVEL_WL] + list(range(MAX_TIER, -1, -1))
    levels = [lvl for lvl in order if lvl <= viewer and by_level.get(lvl)]
    if not levels:
        return await ctx.send(embed=info_embed("📚 Commandes", "Aucune commande accessible."))

    p = get_prefix_cached()
    pages = []
    for i, lvl in enumerate(levels):
        em = discord.Embed(color=0x9b59b6)
        em.set_author(name=f"{level_emoji(lvl)}  {level_name(lvl).upper()}")
        em.description = " ".join(f"`{p}{c}`" for c in sorted(by_level[lvl]))
        em.set_footer(text=f"{FOOTER_TEXT} ・ {i+1}/{len(levels)}")
        pages.append(em)
    await send_paginated(ctx, pages)


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  ALLOW / SETLOG / PREFIX / RENEW                         ║
# ╚══════════════════════════════════════════════════════════════════════════╝

@bot.command(name="allow")
async def _allow(ctx, *, channel_input: str = None):
    if channel_input is None:
        blocked = get_blocked_channels(ctx.guild.id)
        if not blocked:
            return await ctx.send(embed=info_embed(
                "📋 Salons bloqués",
                "Aucun. Les membres peuvent utiliser les commandes **publiques partout**.\n"
                f"Utilise `{get_prefix_cached()}unallow #salon` pour bloquer un salon."))
        lines = []
        for cid in blocked:
            ch = ctx.guild.get_channel(int(cid))
            lines.append(f"• {ch.mention} (`{cid}`)" if ch else f"• `{cid}`")
        return await ctx.send(embed=info_embed(f"📋 Salons bloqués ({len(blocked)})", join_lines(lines, 4000)))
    channel, raw_id = await resolve_channel(ctx, channel_input)
    if not channel:
        if raw_id is not None and unblock_channel(ctx.guild.id, raw_id):
            return await ctx.send(embed=success_embed("✅ Salon réautorisé", f"`{raw_id}`"))
        return await ctx.send(embed=error_embed("❌ Salon introuvable"))
    if unblock_channel(ctx.guild.id, channel.id):
        await ctx.send(embed=success_embed("✅ Salon réautorisé", f"Les commandes publiques marchent à nouveau dans {channel.mention}."))
    else:
        await ctx.send(embed=info_embed("Déjà autorisé", f"{channel.mention} n'était pas bloqué."))


@bot.command(name="unallow")
async def _unallow(ctx, *, channel_input: str = None):
    if not channel_input:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}unallow #salon`"))
    channel, raw_id = await resolve_channel(ctx, channel_input)
    if not channel:
        return await ctx.send(embed=error_embed("❌ Salon introuvable"))
    block_channel(ctx.guild.id, channel.id)
    await ctx.send(embed=success_embed("🔇 Salon bloqué",
                                       f"Les membres ne peuvent plus utiliser les commandes publiques dans {channel.mention}.\n*(le staff WL+ passe quand même)*"))


@bot.command(name="setlog")
async def _setlog(ctx, log_type: str = None, channel: discord.TextChannel = None):
    types = "`, `".join(LOG_TYPES)
    if not log_type or not channel:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}setlog <type|all> #salon`\nTypes : `{types}`"))
    log_type = log_type.lower()
    if log_type == "all":
        for t in LOG_TYPES:
            set_log_channel(ctx.guild.id, t, channel.id)
        return await ctx.send(embed=success_embed("✅ Logs configurés", f"**Tous les logs** → {channel.mention}"))
    if log_type not in LOG_TYPES:
        return await ctx.send(embed=error_embed("❌", f"Types : `{types}` (ou `all`)"))
    perms = channel.permissions_for(ctx.guild.me)
    if not (perms.send_messages and perms.embed_links):
        return await ctx.send(embed=error_embed("❌", f"Je ne peux pas envoyer d'embeds dans {channel.mention}."))
    set_log_channel(ctx.guild.id, log_type, channel.id)
    await ctx.send(embed=success_embed("✅ Log configuré", f"**{log_type}** → {channel.mention}"))


@bot.command(name="dellog")
async def _dellog(ctx, log_type: str = None):
    types = "`, `".join(LOG_TYPES)
    if not log_type:
        return await ctx.send(embed=error_embed("Usage", f"`{get_prefix_cached()}dellog <type|all>`\nTypes : `{types}`"))
    log_type = log_type.lower()
    if log_type == "all":
        for t in LOG_TYPES:
            delete_log_channel(ctx.guild.id, t)
        return await ctx.send(embed=success_embed("✅ Logs retirés", "Tous les logs sont désactivés."))
    if log_type not in LOG_TYPES:
        return await ctx.send(embed=error_embed("❌", f"Types : `{types}` (ou `all`)"))
    delete_log_channel(ctx.guild.id, log_type)
    await ctx.send(embed=success_embed("✅ Log retiré", f"**{log_type}** : désactivé"))


@bot.command(name="logs")
async def _logs(ctx):
    all_logs = get_all_log_channels(ctx.guild.id)
    lines = []
    for t in LOG_TYPES:
        cid = all_logs.get(t)
        if cid:
            ch = ctx.guild.get_channel(int(cid))
            lines.append(f"• **{t}** → {ch.mention if ch else f'`{cid}` *(salon supprimé)*'}")
        else:
            lines.append(f"• **{t}** → *non défini*")
    await ctx.send(embed=info_embed("📋 Configuration des logs",
                                    "\n".join(lines) + f"\n\n`{get_prefix_cached()}setlog <type|all> #salon`"))


@bot.command(name="prefix")
async def _prefix(ctx, new_prefix: str = None):
    if not new_prefix:
        return await ctx.send(embed=info_embed("Prefix actuel", f"`{get_prefix_cached()}`\n*Tu peux aussi me mentionner.*"))
    if len(new_prefix) > 5:
        return await ctx.send(embed=error_embed("❌", "Prefix max 5 caractères."))
    set_config("prefix", new_prefix)
    await ctx.send(embed=success_embed("✅ Prefix modifié", f"Nouveau prefix : `{new_prefix}`"))


@bot.command(name="renew", aliases=["nuke"])
async def _renew(ctx):
    """Recrée le salon à l'identique, après confirmation."""
    channel = ctx.channel
    view = ConfirmView(ctx.author.id)
    view.message = await ctx.send(embed=warning_embed(
        "♻️ Recréer ce salon ?",
        f"{channel.mention} sera **supprimé et recréé** à l'identique. Tous les messages seront perdus."), view=view)
    await view.wait()
    if not view.value:
        try:
            await view.message.edit(embed=info_embed("Annulé", "Le salon n'a pas été touché."), view=None)
        except discord.HTTPException:
            pass
        return
    try:
        new_ch = await channel.clone(reason=f"[{ctx.author}] renew")
        try:
            await new_ch.edit(position=channel.position)
        except discord.HTTPException:
            pass
        await channel.delete(reason=f"[{ctx.author}] renew")
    except discord.Forbidden:
        return await ctx.send(embed=error_embed("❌", "Permission **Gérer les salons** requise."))
    except discord.HTTPException as e:
        return await ctx.send(embed=error_embed("❌ Erreur", str(e)))
    # Garde les configs liées à l'ancien salon (salon bloqué, logs)
    if is_channel_blocked(ctx.guild.id, channel.id):
        unblock_channel(ctx.guild.id, channel.id)
        block_channel(ctx.guild.id, new_ch.id)
    for t, cid in get_all_log_channels(ctx.guild.id).items():
        if int(cid) == channel.id:
            set_log_channel(ctx.guild.id, t, new_ch.id)
    try:
        await new_ch.send(f"♻️ Salon recréé par {ctx.author.mention}")
    except discord.HTTPException:
        pass


# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                  HELP ADAPTÉ AU NIVEAU (dropdown)                        ║
# ╚══════════════════════════════════════════════════════════════════════════╝

def _bot_avatar():
    try:
        return bot.user.display_avatar.url
    except Exception:
        return None


def cat_visible_commands(guild_id, cat, viewer_level):
    out = []
    for name, meta in COMMANDS.items():
        if meta["cat"] != cat:
            continue
        lvl = get_command_level(guild_id, name)
        if viewer_level >= lvl:
            out.append((name, meta, lvl))
    out.sort(key=lambda x: (x[2], x[0]))
    return out


def category_visible(guild_id, cat, viewer_level):
    return len(cat_visible_commands(guild_id, cat, viewer_level)) > 0


def build_help_home_embed(guild_id, viewer_level):
    p = get_prefix_cached()
    em = discord.Embed(color=COLOR_DEFAULT)
    em.set_author(name="MOH ─ AIDE")
    av = _bot_avatar()
    if av:
        em.set_thumbnail(url=av)
    em.description = (
        f"Choisis une **catégorie** dans le menu ci-dessous.\n\n"
        f"🔧 Prefix `{p}`　•　🎖️ Niveau : {level_emoji(viewer_level)} **{level_name(viewer_level)}**"
    )
    for cat, meta in CATEGORY_META.items():
        n = len(cat_visible_commands(guild_id, cat, viewer_level))
        if n:
            em.add_field(name=f"{meta['emoji']} {meta['label']}", value=f"{meta['sub']}\n`{n}` commande(s)", inline=True)
    em.set_footer(text=FOOTER_TEXT)
    return em


def build_category_pages(guild_id, cat, viewer_level):
    p = get_prefix_cached()
    meta = CATEGORY_META[cat]
    items = cat_visible_commands(guild_id, cat, viewer_level)
    if not items:
        em = discord.Embed(color=meta["color"], description="🔒 *Aucune commande accessible ici.*")
        em.set_author(name=f"{meta['emoji']}  {meta['label'].upper()}")
        em.set_footer(text=FOOTER_TEXT)
        return [em]

    chunks = [items[i:i + 6] for i in range(0, len(items), 6)]
    pages = []
    for idx, chunk in enumerate(chunks):
        blocks = []
        for name, cmeta, lvl in chunk:
            line = f"`{p}{name}`"
            if cmeta["args"]:
                line += f" `{cmeta['args']}`"
            line += f"　`{level_name(lvl)}`"
            blocks.append(f"{line}\n╰─➤ {cmeta['desc']}")
        em = discord.Embed(color=meta["color"], description="\n\n".join(blocks))
        em.set_author(name=f"{meta['emoji']}  {meta['label'].upper()}")
        em.set_footer(text=f"{FOOTER_TEXT} ・ Page {idx+1}/{len(chunks)}")
        pages.append(em)
    return pages


class HelpSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        options = [discord.SelectOption(label="Accueil", emoji="🏠", value="home")]
        for cat, meta in CATEGORY_META.items():
            if category_visible(parent.guild_id, cat, parent.viewer_level):
                options.append(discord.SelectOption(label=meta["label"], emoji=meta["emoji"],
                                                    value=cat, description=meta["sub"][:100]))
        super().__init__(placeholder="📂 Choisis une catégorie...", min_values=1, max_values=1,
                         options=options[:25], row=0)

    async def callback(self, interaction):
        v = self.parent_view
        val = self.values[0]
        v.page = 0
        if val == "home":
            v.current_cat, v.pages = None, []
        else:
            v.current_cat = val
            v.pages = build_category_pages(v.guild_id, val, v.viewer_level)
        v.sync()
        await interaction.response.edit_message(embed=v.current_embed(), view=v)


class HelpView(OwnedView):
    def __init__(self, author_id, guild_id, viewer_level):
        super().__init__(author_id, timeout=180)
        self.guild_id = guild_id
        self.viewer_level = viewer_level
        self.current_cat = None
        self.page = 0
        self.pages = []
        self.add_item(HelpSelect(self))
        self.sync()

    def current_embed(self):
        if self.current_cat is None or not self.pages:
            return build_help_home_embed(self.guild_id, self.viewer_level)
        self.page = max(0, min(self.page, len(self.pages) - 1))
        return self.pages[self.page]

    def sync(self):
        multi = self.current_cat is not None and len(self.pages) > 1
        self.prev_b.disabled = (not multi) or self.page <= 0
        self.next_b.disabled = (not multi) or self.page >= len(self.pages) - 1

    @discord.ui.button(emoji="◀", style=discord.ButtonStyle.secondary, row=1)
    async def prev_b(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.sync()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)

    @discord.ui.button(emoji="▶", style=discord.ButtonStyle.secondary, row=1)
    async def next_b(self, interaction, button):
        self.page = min(len(self.pages) - 1, self.page + 1)
        self.sync()
        await interaction.response.edit_message(embed=self.current_embed(), view=self)


@bot.command(name="help", aliases=["aide", "h"])
async def _help(ctx):
    level = get_user_level(ctx.author)
    view = HelpView(ctx.author.id, ctx.guild.id, level)
    view.message = await ctx.send(embed=build_help_home_embed(ctx.guild.id, level), view=view)


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
    finally:
        if _DB is not None:
            _DB.close()

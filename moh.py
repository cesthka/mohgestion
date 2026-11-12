"""
================================================================================
  MOH · Bot de gestion
================================================================================
Hiérarchie : Buyer (12) > Owner (11) > Whitelist (10) > perm1..perm9 > Public (0)

  - Modération : ban (perm/temp), kick, mute, warn, clear, lock, slowmode...
  - Permissions par commande, par rôle ou par membre, avec un panneau interactif
  - Logs par catégorie, snipe, historique des sanctions
  - Clone et suppression d'emojis en masse

Partout où le bot attend un membre ou un rôle, tu peux donner :
  une mention, un ID, ou juste le nom (même partiel, sans majuscules ni accents).
  Si plusieurs résultats collent, le bot te propose de choisir.

Variables d'environnement : TOKEN_MOH (ou TOKEN), DATA_DIR
Conseillé pour la vitesse : pip install "discord.py[speed]" uvloop
================================================================================
"""
import discord
from discord.ext import commands, tasks
import os
import sys
import re
import math
import time
import asyncio
import sqlite3
import logging
import traceback
import unicodedata
import io
import aiohttp
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from PIL import Image  # optionnel : sert à réduire les images trop lourdes
except ImportError:
    Image = None

# ========================= CONFIG =========================
BOT_TOKEN = os.environ.get("TOKEN_MOH") or os.environ.get("TOKEN")
if not BOT_TOKEN:
    print("[ERREUR CRITIQUE] Aucune variable TOKEN_MOH ni TOKEN trouvée.")
    sys.exit(1)

# Buyer : hardcodé, niveau max, non modifiable en commande.
BUYER_IDS = {
    142365250803466240,
    1546586066407133308,
}

PARIS_TZ = ZoneInfo("Europe/Paris")
DEFAULT_PREFIX = "+"

DATA_DIR = os.environ.get("DATA_DIR")
if not DATA_DIR:
    print("[ERREUR CRITIQUE] DATA_DIR non défini. Configure DATA_DIR=/data dans Railway.")
    sys.exit(1)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "moh.db")

MAX_CLEAR = 1000              # messages max par clear
DEFAULT_CLEAR = 100           # clear sans nombre ni membre
MEMBER_SCAN = 1000            # messages parcourus pour un clear de membre sans limite
YOUNG_ACCOUNT_DAYS = 7
MAX_EMOJI_BYTES = 256 * 1024
MAX_CREATE = 50               # emojis clonés max par commande

COLOR_DEFAULT = 0x2b2d31
COLOR_SUCCESS = 0x43b581
COLOR_ERROR = 0xf04747
COLOR_WARNING = 0xfaa61a
COLOR_INFO = 0x3498db

# ========================= NIVEAUX =========================
LEVEL_PUBLIC = 0
MAX_TIER = 9
LEVEL_WL = 10
LEVEL_OWNER = 11
LEVEL_BUYER = 12

BUYER_RETIER_CMDS = {"owner", "unowner"}
DANGEROUS_PERMS = ("administrator", "manage_guild", "manage_roles", "ban_members",
                   "kick_members", "manage_channels", "manage_webhooks", "mention_everyone",
                   "moderate_members")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%d/%m/%Y %H:%M:%S")
log = logging.getLogger("moh")


# ========================= CATALOGUE =========================
COMMANDS = {
    # Permissions
    "help":      {"args": "",                        "desc": "Le menu d'aide",                       "cat": "perms", "level": 0,  "locked": True},
    "perms":     {"args": "[niveau]",                "desc": "Le panneau des permissions",           "cat": "perms", "level": 0,  "locked": True},
    "helpall":   {"args": "",                        "desc": "Les commandes rangées par niveau",     "cat": "perms", "level": 0,  "locked": True},
    "owner":     {"args": "[membre]",                "desc": "Ajouter ou lister les owners",         "cat": "perms", "level": 12, "locked": False},
    "unowner":   {"args": "membre",                  "desc": "Retirer un owner",                     "cat": "perms", "level": 12, "locked": False},
    "wl":        {"args": "[membre]",                "desc": "Ajouter ou lister la whitelist",       "cat": "perms", "level": 11, "locked": True},
    "unwl":      {"args": "membre",                  "desc": "Retirer de la whitelist",              "cat": "perms", "level": 11, "locked": True},
    "setperm":   {"args": "<cible> <niveau>",        "desc": "Mettre une commande, un rôle ou un membre à un niveau", "cat": "perms", "level": 11, "locked": True},
    "resetperm": {"args": "<cible>",                 "desc": "Remettre par défaut",                  "cat": "perms", "level": 11, "locked": True},
    "botban":    {"args": "membre",                  "desc": "Bloquer l'accès au bot",               "cat": "perms", "level": 11, "locked": True},
    "unbotban":  {"args": "membre",                  "desc": "Rendre l'accès au bot",                "cat": "perms", "level": 11, "locked": True},

    # Configuration
    "prefix":    {"args": "[nouveau]",               "desc": "Changer le prefix",                    "cat": "config", "level": 12, "locked": True},
    "allow":     {"args": "[salon]",                 "desc": "Réautoriser les commandes publiques",  "cat": "config", "level": 11, "locked": True},
    "unallow":   {"args": "salon",                   "desc": "Bloquer les commandes publiques",      "cat": "config", "level": 11, "locked": True},
    "setlog":    {"args": "<type|all> [salon]",      "desc": "Définir un salon de logs",             "cat": "config", "level": 11, "locked": True},
    "dellog":    {"args": "<type|all>",              "desc": "Retirer un salon de logs",             "cat": "config", "level": 11, "locked": True},
    "logs":      {"args": "",                        "desc": "Voir la config des logs",              "cat": "config", "level": 11, "locked": True},
    "renew":     {"args": "",                        "desc": "Recréer le salon à neuf",              "cat": "config", "level": 12, "locked": True},

    # Infos
    "userinfo":  {"args": "[membre]",                "desc": "Les infos d'un membre",                "cat": "infos", "level": 0,  "locked": False},
    "serverinfo":{"args": "",                        "desc": "Les infos du serveur",                 "cat": "infos", "level": 0,  "locked": False},
    "avatar":    {"args": "[membre]",                "desc": "L'avatar d'un membre",                 "cat": "infos", "level": 0,  "locked": False},
    "roleinfo":  {"args": "rôle",                    "desc": "Les infos d'un rôle",                  "cat": "infos", "level": 0,  "locked": False},
    "ping":      {"args": "",                        "desc": "La latence du bot",                    "cat": "infos", "level": 0,  "locked": False},

    # Modération
    "ban":       {"args": "membre [durée] [raison]", "desc": "Bannir, en définitif ou temporaire",   "cat": "moderation", "level": 11, "locked": False},
    "unban":     {"args": "membre [raison]",         "desc": "Débannir",                             "cat": "moderation", "level": 11, "locked": False},
    "kick":      {"args": "membre [raison]",         "desc": "Expulser",                             "cat": "moderation", "level": 11, "locked": False},
    "mute":      {"args": "membre [durée] [raison]", "desc": "Timeout (1h par défaut)",              "cat": "moderation", "level": 11, "locked": False},
    "unmute":    {"args": "membre",                  "desc": "Retirer le timeout",                   "cat": "moderation", "level": 11, "locked": False},
    "warn":      {"args": "membre <raison>",         "desc": "Avertir",                              "cat": "moderation", "level": 11, "locked": False},
    "warns":     {"args": "[membre]",                "desc": "Voir les avertissements",              "cat": "moderation", "level": 11, "locked": False},
    "delwarn":   {"args": "<id>",                    "desc": "Supprimer un avertissement",           "cat": "moderation", "level": 11, "locked": False},
    "clearwarns":{"args": "membre",                  "desc": "Effacer tous les avertissements",      "cat": "moderation", "level": 11, "locked": False},
    "softban":   {"args": "membre [raison]",         "desc": "Ban puis unban pour nettoyer",         "cat": "moderation", "level": 11, "locked": False},
    "clear":     {"args": "[membre] [nombre]",       "desc": "Supprimer des messages",               "cat": "moderation", "level": 11, "locked": False},
    "lock":      {"args": "[salon]",                 "desc": "Verrouiller un salon",                 "cat": "moderation", "level": 11, "locked": False},
    "unlock":    {"args": "[salon]",                 "desc": "Déverrouiller un salon",               "cat": "moderation", "level": 11, "locked": False},
    "slowmode":  {"args": "<durée> [salon]",         "desc": "Régler le slowmode",                   "cat": "moderation", "level": 11, "locked": False},
    "banlist":   {"args": "",                        "desc": "Lister les bannis",                    "cat": "moderation", "level": 11, "locked": False},
    "snipe":     {"args": "",                        "desc": "Le dernier message supprimé",          "cat": "moderation", "level": 11, "locked": False},
    "history":   {"args": "[membre]",                "desc": "L'historique des sanctions",           "cat": "moderation", "level": 11, "locked": False},
    "case":      {"args": "<id>",                    "desc": "Le détail d'une sanction",             "cat": "moderation", "level": 11, "locked": False},
    "reason":    {"args": "<id> <raison>",           "desc": "Modifier la raison d'une sanction",    "cat": "moderation", "level": 11, "locked": False},

    # Outils
    "role":      {"args": "membre rôle",             "desc": "Ajouter ou retirer un rôle",           "cat": "outils", "level": 11, "locked": False},
    "addrole":   {"args": "rôle membres...",         "desc": "Donner un rôle à un ou plusieurs membres", "cat": "outils", "level": 11, "locked": False},
    "delrole":   {"args": "rôle membres...",         "desc": "Retirer un rôle à un ou plusieurs membres", "cat": "outils", "level": 11, "locked": False},
    "roleall":   {"args": "rôle",                    "desc": "Donner un rôle à tout le monde",       "cat": "outils", "level": 11, "locked": False},
    "nick":      {"args": "membre [pseudo]",         "desc": "Changer le pseudo",                    "cat": "outils", "level": 11, "locked": False},
    "derank":    {"args": "membre",                  "desc": "Retirer tous les rôles",               "cat": "outils", "level": 11, "locked": False},
    "say":       {"args": "<message>",               "desc": "Faire parler le bot",                  "cat": "outils", "level": 11, "locked": False},
    "dm":        {"args": "membre <message>",        "desc": "Envoyer un message privé",             "cat": "outils", "level": 11, "locked": False},
    "embed":     {"args": "<titre | message>",       "desc": "Envoyer un embed",                     "cat": "outils", "level": 11, "locked": False},
    "create":    {"args": "<emojis...>",             "desc": "Cloner des emojis sur le serveur",     "cat": "outils", "level": 11, "locked": False},
    "delemoji":  {"args": "<emojis...>",             "desc": "Supprimer un ou plusieurs emojis",     "cat": "outils", "level": 11, "locked": False},
}

CATEGORY_META = {
    "moderation": {"emoji": "🛡️", "label": "Modération",  "sub": "Sanctions et ménage du serveur"},
    "outils":     {"emoji": "🛠️", "label": "Outils",      "sub": "Rôles, emojis et messages"},
    "infos":      {"emoji": "🔎", "label": "Infos",       "sub": "Membres, rôles et serveur"},
    "config":     {"emoji": "⚙️", "label": "Config",      "sub": "Les réglages du bot"},
    "perms":      {"emoji": "🔐", "label": "Permissions", "sub": "Qui a accès à quoi"},
}

LOG_TYPES = ["moderation", "messages", "members", "voice", "roles", "commands"]


# ========================= DATABASE + CACHES =========================
# Toutes les données lues à chaque commande sont gardées en mémoire.
# SQLite ne sert qu'à les sauvegarder : zéro requête disque dans le check global.

_DB = None

RANKS = {}          # user_id -> rang (10 / 11)
MEMBER_PERMS = {}   # (guild_id, user_id) -> niveau
ROLE_PERMS = {}     # guild_id -> {role_id: niveau}
CMD_PERMS = {}      # (guild_id, commande) -> niveau
BLOCKED = set()     # (guild_id, channel_id)
BOT_BANS = set()    # user_id
LOG_CH = {}         # guild_id -> {type: channel_id}
CONFIG = {}


def get_db():
    global _DB
    if _DB is None:
        _DB = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
        _DB.row_factory = sqlite3.Row
        _DB.execute("PRAGMA journal_mode=WAL")
        _DB.execute("PRAGMA synchronous=NORMAL")
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


def load_caches():
    for r in db_all("SELECT key, value FROM config"):
        CONFIG[r["key"]] = r["value"]
    for r in db_all("SELECT user_id, rank FROM ranks"):
        RANKS[int(r["user_id"])] = r["rank"]
    for r in db_all("SELECT guild_id, user_id, level FROM member_perms"):
        MEMBER_PERMS[(int(r["guild_id"]), int(r["user_id"]))] = r["level"]
    for r in db_all("SELECT guild_id, role_id, level FROM role_perms"):
        ROLE_PERMS.setdefault(int(r["guild_id"]), {})[int(r["role_id"])] = r["level"]
    for r in db_all("SELECT guild_id, command, level FROM cmd_perms"):
        CMD_PERMS[(int(r["guild_id"]), r["command"])] = r["level"]
    for r in db_all("SELECT guild_id, channel_id FROM blocked_channels"):
        BLOCKED.add((int(r["guild_id"]), int(r["channel_id"])))
    for r in db_all("SELECT user_id FROM bot_bans"):
        BOT_BANS.add(int(r["user_id"]))
    for r in db_all("SELECT guild_id, log_type, channel_id FROM log_channels"):
        LOG_CH.setdefault(int(r["guild_id"]), {})[r["log_type"]] = int(r["channel_id"])


# ---- Config ----

def get_prefix_cached():
    return CONFIG.get("prefix") or DEFAULT_PREFIX


def set_config(key, value):
    CONFIG[key] = str(value)
    db_exec("INSERT OR REPLACE INTO config VALUES (?, ?)", (key, str(value)))


# ---- Rangs (WL / Owner) ----

def get_base_rank(user_id):
    uid = int(user_id)
    if uid in BUYER_IDS:
        return LEVEL_BUYER
    return RANKS.get(uid, 0)


def set_base_rank(user_id, rank):
    uid = int(user_id)
    if rank == 0:
        RANKS.pop(uid, None)
        db_exec("DELETE FROM ranks WHERE user_id = ?", (str(uid),))
    else:
        RANKS[uid] = rank
        db_exec("INSERT OR REPLACE INTO ranks VALUES (?, ?)", (str(uid), rank))


def get_users_by_rank(rank):
    return [uid for uid, r in RANKS.items() if r == rank]


# ---- Perms rôle ----

def set_role_perm(guild_id, role_id, level):
    gid, rid = int(guild_id), int(role_id)
    if level == 0:
        ROLE_PERMS.get(gid, {}).pop(rid, None)
        db_exec("DELETE FROM role_perms WHERE guild_id = ? AND role_id = ?", (str(gid), str(rid)))
    else:
        ROLE_PERMS.setdefault(gid, {})[rid] = level
        db_exec("INSERT OR REPLACE INTO role_perms VALUES (?, ?, ?)", (str(gid), str(rid), level))


def get_role_perm(guild_id, role_id):
    return ROLE_PERMS.get(int(guild_id), {}).get(int(role_id))


def del_role_perm(guild_id, role_id):
    existed = get_role_perm(guild_id, role_id) is not None
    set_role_perm(guild_id, role_id, 0)
    return existed


# ---- Perms membre ----

def set_member_perm(guild_id, user_id, level):
    key = (int(guild_id), int(user_id))
    if level == 0:
        MEMBER_PERMS.pop(key, None)
        db_exec("DELETE FROM member_perms WHERE guild_id = ? AND user_id = ?", (str(key[0]), str(key[1])))
    else:
        MEMBER_PERMS[key] = level
        db_exec("INSERT OR REPLACE INTO member_perms VALUES (?, ?, ?)", (str(key[0]), str(key[1]), level))


def get_member_perm(guild_id, user_id):
    return MEMBER_PERMS.get((int(guild_id), int(user_id)))


def del_member_perm(guild_id, user_id):
    existed = get_member_perm(guild_id, user_id) is not None
    set_member_perm(guild_id, user_id, 0)
    return existed


def get_perms_at_level(guild_id, level):
    gid = int(guild_id)
    roles = [rid for rid, lvl in ROLE_PERMS.get(gid, {}).items() if lvl == level]
    users = [uid for (g, uid), lvl in MEMBER_PERMS.items() if g == gid and lvl == level]
    return roles, users


# ---- Override niveau commande ----

def set_cmd_level(guild_id, command, level):
    CMD_PERMS[(int(guild_id), command)] = level
    db_exec("INSERT OR REPLACE INTO cmd_perms VALUES (?, ?, ?)", (str(guild_id), command, level))


def del_cmd_override(guild_id, command):
    existed = CMD_PERMS.pop((int(guild_id), command), None) is not None
    db_exec("DELETE FROM cmd_perms WHERE guild_id = ? AND command = ?", (str(guild_id), command))
    return existed


# ---- Bot bans ----

def is_bot_banned(user_id):
    uid = int(user_id)
    return uid not in BUYER_IDS and uid in BOT_BANS


def add_bot_ban(user_id, banned_by):
    BOT_BANS.add(int(user_id))
    now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %Hh%M")
    db_exec("INSERT OR REPLACE INTO bot_bans VALUES (?, ?, ?)", (str(user_id), str(banned_by), now))


def remove_bot_ban(user_id):
    BOT_BANS.discard(int(user_id))
    db_exec("DELETE FROM bot_bans WHERE user_id = ?", (str(user_id),))


# ---- Salons bloqués ----

def block_channel(guild_id, channel_id):
    BLOCKED.add((int(guild_id), int(channel_id)))
    db_exec("INSERT OR IGNORE INTO blocked_channels VALUES (?, ?)", (str(guild_id), str(channel_id)))


def unblock_channel(guild_id, channel_id):
    key = (int(guild_id), int(channel_id))
    existed = key in BLOCKED
    BLOCKED.discard(key)
    db_exec("DELETE FROM blocked_channels WHERE guild_id = ? AND channel_id = ?", (str(guild_id), str(channel_id)))
    return existed


def is_channel_blocked(guild_id, channel_id):
    return (int(guild_id), int(channel_id)) in BLOCKED


def get_blocked_channels(guild_id):
    gid = int(guild_id)
    return [cid for g, cid in BLOCKED if g == gid]


# ---- Salons de logs ----

def get_log_channel(guild_id, log_type):
    return LOG_CH.get(int(guild_id), {}).get(log_type)


def set_log_channel(guild_id, log_type, channel_id):
    LOG_CH.setdefault(int(guild_id), {})[log_type] = int(channel_id)
    db_exec("INSERT OR REPLACE INTO log_channels VALUES (?, ?, ?)", (str(guild_id), log_type, str(channel_id)))


def delete_log_channel(guild_id, log_type):
    LOG_CH.get(int(guild_id), {}).pop(log_type, None)
    db_exec("DELETE FROM log_channels WHERE guild_id = ? AND log_type = ?", (str(guild_id), log_type))


def get_all_log_channels(guild_id):
    return dict(LOG_CH.get(int(guild_id), {}))


# ---- Sanctions (pas sur le chemin critique, restent en base) ----

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


def count_warns(guild_id, user_id):
    return db_one("SELECT COUNT(*) AS n FROM sanctions WHERE guild_id = ? AND user_id = ? "
                  "AND type = 'warn' AND active = 1", (str(guild_id), str(user_id)))["n"]


def delete_warn(guild_id, warn_id):
    return db_exec(
        "UPDATE sanctions SET active = 0 WHERE id = ? AND guild_id = ? AND type = 'warn' AND active = 1",
        (warn_id, str(guild_id))).rowcount > 0


def get_active_tempbans():
    return [dict(r) for r in db_all("SELECT * FROM sanctions WHERE type = 'tempban' AND active = 1")]


def deactivate_sanction(sanction_id):
    db_exec("UPDATE sanctions SET active = 0 WHERE id = ?", (sanction_id,))


def get_history(guild_id, user_id, limit=50):
    return [dict(r) for r in db_all(
        "SELECT * FROM sanctions WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
        (str(guild_id), str(user_id), limit))]


# ========================= NIVEAUX : HELPERS =========================

def level_name(lvl):
    if lvl >= LEVEL_BUYER:
        return "Buyer"
    if lvl == LEVEL_OWNER:
        return "Owner"
    if lvl == LEVEL_WL:
        return "Whitelist"
    if 1 <= lvl <= MAX_TIER:
        return f"perm{lvl}"
    return "Public"


def parse_level(s, max_level=MAX_TIER):
    """'perm7', 'p7', '7', 'public', 'wl', 'owner' -> niveau (ou None)."""
    if s is None:
        return None
    s = str(s).strip().lower().replace(" ", "")
    if s in ("public", "membre", "membres", "pub", "everyone", "tous"):
        return 0
    if s in ("wl", "whitelist"):
        return LEVEL_WL if max_level >= LEVEL_WL else None
    if s == "owner":
        return LEVEL_OWNER if max_level >= LEVEL_OWNER else None
    m = re.match(r"^(?:perm|p)?(\d{1,2})$", s)
    if m:
        n = int(m.group(1))
        if n <= MAX_TIER or (n in (LEVEL_WL, LEVEL_OWNER) and n <= max_level):
            return n if n <= max_level else None
    return None


def get_user_level(member):
    if member.id in BUYER_IDS:
        return LEVEL_BUYER
    lvl = RANKS.get(member.id, 0)
    guild = getattr(member, "guild", None)
    if guild is None:
        return lvl
    lvl = max(lvl, MEMBER_PERMS.get((guild.id, member.id), 0))
    rp = ROLE_PERMS.get(guild.id)
    if rp and isinstance(member, discord.Member):
        for rid, rl in rp.items():
            if rl > lvl and member.get_role(rid) is not None:
                lvl = rl
    return lvl


def get_command_level(guild_id, command_name):
    meta = COMMANDS.get(command_name)
    if not meta:
        return LEVEL_OWNER
    if meta["locked"]:
        return meta["level"]
    return CMD_PERMS.get((int(guild_id), command_name), meta["level"])


def is_owner_plus(member):
    return get_user_level(member) >= LEVEL_OWNER


def check_target(ctx, target, uid=None):
    """Renvoie un message d'erreur, ou None si l'action est autorisée."""
    uid = target.id if target is not None else uid
    if uid == ctx.author.id:
        return "Tu peux pas faire ça sur toi-même."
    if bot.user and uid == bot.user.id:
        return "Je vais pas me faire ça à moi-même."
    if uid in BUYER_IDS:
        return "Impossible sur un Buyer."
    author_lvl = get_user_level(ctx.author)
    target_lvl = get_user_level(target) if isinstance(target, discord.Member) else get_base_rank(uid)
    if target_lvl >= author_lvl:
        return f"Cette personne a un niveau égal ou au-dessus du tien ({level_name(target_lvl)})."
    if isinstance(target, discord.Member):
        if target.id == ctx.guild.owner_id:
            return "Impossible sur le propriétaire du serveur."
        if target.top_role >= ctx.guild.me.top_role:
            return "Son rôle est au-dessus du mien ou au même niveau."
        if (target.top_role >= ctx.author.top_role and author_lvl < LEVEL_OWNER
                and ctx.author.id != ctx.guild.owner_id):
            return "Son rôle est au-dessus du tien ou au même niveau."
    return None


# ========================= EMBEDS =========================

def trunc(text, limit):
    text = str(text) if text is not None else ""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def apply_thumbnail(em, guild):
    if guild and getattr(guild, "icon", None):
        em.set_thumbnail(url=guild.icon.url)


def info_embed(title, desc=""):
    return discord.Embed(title=title, description=trunc(desc, 4000), color=COLOR_DEFAULT)


def success_embed(title, desc=""):
    return discord.Embed(title=title, description=trunc(desc, 4000), color=COLOR_SUCCESS)


def error_embed(title, desc=""):
    return discord.Embed(title=title, description=trunc(desc, 4000), color=COLOR_ERROR)


def warning_embed(title, desc=""):
    return discord.Embed(title=title, description=trunc(desc, 4000), color=COLOR_WARNING)


def mod_embed(action, target_obj, target_id, moderator, reason=None, duration=None, case_id=None, color=COLOR_ERROR):
    em = discord.Embed(title=action, color=color, timestamp=datetime.now(timezone.utc))
    if target_obj is not None:
        em.add_field(name="Membre", value=f"{target_obj.mention} (`{target_obj.id}`)", inline=True)
        em.set_thumbnail(url=target_obj.display_avatar.url)
    else:
        em.add_field(name="Membre", value=f"<@{target_id}> (`{target_id}`)", inline=True)
    em.add_field(name="Par", value=moderator.mention, inline=True)
    if duration:
        em.add_field(name="Durée", value=duration, inline=True)
    em.add_field(name="Raison", value=trunc(reason, 1024) if reason else "Pas de raison", inline=False)
    if case_id is not None:
        em.set_footer(text=f"Sanction #{case_id}")
    return em


def usage(cmd, extra=""):
    meta = COMMANDS.get(cmd, {})
    txt = f"`{get_prefix_cached()}{cmd} {meta.get('args', '')}`".replace(" `", "`")
    return error_embed("Comment l'utiliser", txt + (f"\n{extra}" if extra else ""))


# ========================= DURÉES =========================

def format_duration_fr(seconds):
    if not seconds or seconds <= 0:
        return "0 sec"
    if seconds < 60:
        return f"{int(seconds)} sec"
    units = [(86400 * 365, "an", "ans"), (86400, "jour", "jours"), (3600, "heure", "heures"),
             (60, "minute", "minutes"), (1, "seconde", "secondes")]
    parts, rem = [], int(seconds)
    for sec, sing, plur in units:
        n, rem = divmod(rem, sec)
        if n > 0:
            parts.append(f"{n} {sing if n == 1 else plur}")
        if len(parts) >= 2:
            break
    return " et ".join(parts) if parts else "0 sec"


DURATION_RE = re.compile(r"(\d+)\s*([smhdjw])", re.IGNORECASE)
DURATION_FULL_RE = re.compile(r"^(\d+[smhdjw])+$", re.IGNORECASE)


def parse_duration(s):
    if not s:
        return None
    s = s.strip().lower().replace(" ", "")
    if s.isdigit() and len(s) <= 6:
        return int(s) * 60
    if not DURATION_FULL_RE.match(s):
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "j": 86400, "w": 604800}
    return sum(int(m.group(1)) * units[m.group(2)] for m in DURATION_RE.finditer(s))


def is_unit_duration(s):
    """Durée écrite avec une unité (10m, 2h, 1d). Sert à repérer la durée quel que soit l'ordre."""
    return bool(s) and bool(DURATION_FULL_RE.match(s.strip()))


# ========================= RÉSOLUTION (membres, rôles, salons) =========================

USER_MENTION_RE = re.compile(r"^<@!?(\d{15,21})>$")
ROLE_MENTION_RE = re.compile(r"^<@&(\d{15,21})>$")
CHANNEL_MENTION_RE = re.compile(r"^<#(\d{15,21})>$")
ID_RE = re.compile(r"^\d{15,21}$")
TOKEN_RE = re.compile(r'"([^"]+)"|(\S+)')
EMOJI_RE = re.compile(r"<(a?):(\w{2,32}):(\d{15,21})>")


class Missing(commands.CommandError):
    """Erreur lisible renvoyée à l'utilisateur sous forme d'embed."""
    def __init__(self, title, desc=""):
        super().__init__(title)
        self.title, self.desc = title, desc


class Abort(commands.CommandError):
    """Action annulée par l'utilisateur, on ne dit rien."""


def tokenize(s):
    return [a or b for a, b in TOKEN_RE.findall(s or "")]


def norm(s):
    """Minuscules, sans accents, sans emojis ni symboles, polices fantaisie ramenées en normal."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower()).replace("_", " ")
    return " ".join(s.split())


def rank_matches(query, items, names_fn):
    """Exact > commence par > contient. Renvoie le meilleur groupe de résultats."""
    q = norm(query)
    raw = str(query).strip().lower()
    exact, starts, contains = [], [], []
    for it in items:
        raw_names = [n for n in names_fn(it) if n]
        if any(n.strip().lower() == raw for n in raw_names):
            exact.append(it)
            continue
        if not q:
            continue
        names = [norm(n) for n in raw_names]
        if any(n == q for n in names):
            exact.append(it)
        elif any(n.startswith(q) for n in names):
            starts.append(it)
        elif any(q in n for n in names):
            contains.append(it)
    return exact or starts or contains


def user_id_from(tok):
    tok = str(tok).strip()
    m = USER_MENTION_RE.match(tok)
    if m:
        return int(m.group(1))
    if ID_RE.match(tok):
        return int(tok)
    return None


def find_roles(guild, query):
    query = str(query).strip()
    m = ROLE_MENTION_RE.match(query)
    if m or ID_RE.match(query):
        r = guild.get_role(int(m.group(1) if m else query))
        return [r] if r else []
    roles = [r for r in guild.roles if not r.is_default()]
    res = rank_matches(query, roles, lambda r: [r.name])
    return sorted(res, key=lambda r: r.position, reverse=True)


def find_members(guild, query):
    uid = user_id_from(query)
    if uid is not None:
        m = guild.get_member(uid)
        return [m] if m else []
    return rank_matches(query, guild.members,
                        lambda m: [m.name, m.global_name, m.nick, m.display_name])


def find_channels(guild, query):
    query = str(query).strip()
    m = CHANNEL_MENTION_RE.match(query)
    if m or ID_RE.match(query):
        ch = guild.get_channel(int(m.group(1) if m else query))
        return [ch] if ch else []
    chans = [c for c in guild.channels if not isinstance(c, discord.CategoryChannel)]
    return rank_matches(query.lstrip("#"), chans, lambda c: [c.name])


class PickView(discord.ui.View):
    def __init__(self, author_id, items, label_fn, desc_fn):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.items = items[:25]
        self.result = None
        options = []
        for i, it in enumerate(self.items):
            desc = desc_fn(it) if desc_fn else None
            options.append(discord.SelectOption(label=trunc(label_fn(it), 100) or "?", value=str(i),
                                                description=trunc(desc, 100) if desc else None))
        self.select = discord.ui.Select(placeholder="Choisis le bon...", options=options)
        self.select.callback = self._picked
        self.add_item(self.select)
        cancel = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Ce choix n'est pas pour toi.", ephemeral=True)
            return False
        return True

    async def _picked(self, interaction):
        self.result = self.items[int(self.select.values[0])]
        await interaction.response.defer()
        self.stop()

    async def _cancel(self, interaction):
        await interaction.response.defer()
        self.stop()


async def pick(ctx, items, kind, label_fn, desc_fn=None):
    """Un seul résultat : direct. Plusieurs : menu pour choisir."""
    if len(items) == 1:
        return items[0]
    view = PickView(ctx.author.id, items, label_fn, desc_fn)
    more = f"\nJe t'en montre 25 sur {len(items)}, précise un peu si besoin." if len(items) > 25 else ""
    msg = await ctx.send(embed=info_embed(f"Plusieurs {kind} trouvés", f"Lequel tu veux ?{more}"), view=view)
    await view.wait()
    try:
        await msg.delete()
    except discord.HTTPException:
        pass
    if view.result is None:
        raise Abort()
    return view.result


def _role_label(r):
    return r.name


def _role_desc(r):
    return f"{len(r.members)} membres · ID {r.id}"


def _member_label(m):
    return m.display_name


def _member_desc(m):
    return f"@{m.name} · ID {m.id}"


async def get_member_by_id(guild, uid):
    m = guild.get_member(uid)
    if m:
        return m
    try:
        return await guild.fetch_member(uid)
    except discord.HTTPException:
        return None


async def get_role(ctx, query):
    cands = find_roles(ctx.guild, query)
    if not cands:
        raise Missing("Rôle introuvable", f"Aucun rôle ne ressemble à `{trunc(query, 60)}`.")
    return await pick(ctx, cands, "rôles", _role_label, _role_desc)


async def get_member(ctx, query):
    uid = user_id_from(query)
    if uid is not None:
        m = await get_member_by_id(ctx.guild, uid)
        if m:
            return m
        raise Missing("Membre introuvable", "Cette personne n'est pas sur le serveur.")
    cands = find_members(ctx.guild, query)
    if not cands:
        raise Missing("Membre introuvable", f"Personne ne ressemble à `{trunc(query, 60)}` ici.")
    return await pick(ctx, cands, "membres", _member_label, _member_desc)


async def get_user(ctx, query):
    """Membre du serveur ou n'importe quel compte via ID. Renvoie (objet ou None, id)."""
    uid = user_id_from(query)
    if uid is not None:
        m = ctx.guild.get_member(uid)
        if m:
            return m, uid
        u = bot.get_user(uid)
        if u:
            return u, uid
        try:
            return await bot.fetch_user(uid), uid
        except discord.NotFound:
            raise Missing("Utilisateur introuvable", "Cet ID ne correspond à aucun compte Discord.")
        except discord.HTTPException:
            return None, uid
    m = await get_member(ctx, query)
    return m, m.id


async def get_channel(ctx, query):
    cands = find_channels(ctx.guild, query)
    if not cands:
        raise Missing("Salon introuvable", f"Aucun salon ne ressemble à `{trunc(query, 60)}`.")
    return await pick(ctx, cands, "salons", lambda c: f"#{c.name}",
                      lambda c: c.category.name if c.category else None)


def split_role_and_members(guild, words):
    """Trouve quelle partie des mots est le rôle et quels mots sont des membres, peu importe l'ordre."""
    n = len(words)
    for length in range(n, 0, -1):
        for start in range(n - length + 1):
            cands = find_roles(guild, " ".join(words[start:start + length]))
            if not cands:
                continue
            others = words[:start] + words[start + length:]
            mems = []
            for o in others:
                mc = find_members(guild, o)
                if len(mc) != 1:
                    break
                mems.append(mc[0])
            else:
                return cands, mems
    return [], []


async def parse_role_members(ctx, text):
    """Rôle + membres dans n'importe quel ordre (mentions, IDs ou noms)."""
    roles_found, members, words = [], [], []
    for t in tokenize(text):
        m = USER_MENTION_RE.match(t)
        if m:
            mem = await get_member_by_id(ctx.guild, int(m.group(1)))
            if not mem:
                raise Missing("Membre introuvable", f"{t} n'est pas sur le serveur.")
            members.append(mem)
            continue
        m = ROLE_MENTION_RE.match(t)
        if m:
            r = ctx.guild.get_role(int(m.group(1)))
            if not r:
                raise Missing("Rôle introuvable", f"{t} n'existe plus.")
            roles_found.append(r)
            continue
        if ID_RE.match(t):
            i = int(t)
            r = ctx.guild.get_role(i)
            if r:
                roles_found.append(r)
                continue
            mem = await get_member_by_id(ctx.guild, i)
            if mem:
                members.append(mem)
                continue
            raise Missing("Introuvable", f"`{t}` ne correspond à aucun rôle ni membre ici.")
        words.append(t)

    if roles_found:
        role = roles_found[0]
        for w in words:
            members.append(await get_member(ctx, w))
    else:
        if not words:
            raise Missing("Rôle manquant", "Donne un rôle : mention, ID ou juste son nom.")
        cands, extra = split_role_and_members(ctx.guild, words)
        if not cands:
            raise Missing("Rôle introuvable", f"Aucun rôle ne ressemble à `{trunc(' '.join(words), 60)}`.")
        role = await pick(ctx, cands, "rôles", _role_label, _role_desc)
        members += extra

    seen, uniq = set(), []
    for m in members:
        if m.id not in seen:
            seen.add(m.id)
            uniq.append(m)
    return role, uniq


def split_target(text):
    """Premier mot = la cible, le reste = le texte (raison, pseudo...)."""
    text = (text or "").strip()
    if not text:
        return None, ""
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
    first, _, rest = text.partition(" ")
    return first, rest.strip()


def format_user_display(display_obj, user_id):
    if display_obj is not None:
        return f"{display_obj.mention} (`{display_obj.id}`)"
    return f"<@{user_id}> (`{user_id}`), hors serveur"


async def safe_dm(user, embed):
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        return False


# ========================= BOT SETUP =========================

init_db()
load_caches()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True


def get_prefix(bot_, message):
    return commands.when_mentioned_or(get_prefix_cached())(bot_, message)


class MohBot(commands.Bot):
    session: aiohttp.ClientSession = None

    async def setup_hook(self):
        # Une seule session HTTP réutilisée (connexions gardées ouvertes = plus rapide)
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        if not tempban_loop.is_running():
            tempban_loop.start()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        await super().close()


bot = MohBot(
    command_prefix=get_prefix,
    intents=intents,
    help_command=None,
    case_insensitive=True,
    activity=discord.Activity(type=discord.ActivityType.playing, name="au stake fils de hechek"),
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False),
)


# ========================= VUES =========================

class OwnedView(discord.ui.View):
    def __init__(self, author_id, guild=None, timeout=120):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.guild = guild
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"Ce menu n'est pas à toi. Fais `{get_prefix_cached()}help` pour avoir le tien.", ephemeral=True)
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


class PagerView(OwnedView):
    def __init__(self, author_id, guild, title, lines, per_page=10, footer=None, color=COLOR_DEFAULT):
        super().__init__(author_id, guild, timeout=180)
        self.title, self.lines, self.per_page, self.footer, self.color = title, lines, per_page, footer, color
        self.page = 0
        self.pages = max(1, math.ceil(len(lines) / per_page))
        self._sync()

    def _sync(self):
        self.prev_b.disabled = self.page <= 0
        self.next_b.disabled = self.page >= self.pages - 1

    def embed(self):
        chunk = self.lines[self.page * self.per_page:(self.page + 1) * self.per_page]
        em = discord.Embed(title=self.title, description=trunc("\n".join(chunk), 4000) or "Rien à afficher.",
                           color=self.color)
        em.set_footer(text=f"Page {self.page + 1}/{self.pages}" + (f" · {self.footer}" if self.footer else ""))
        apply_thumbnail(em, self.guild)
        return em

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_b(self, interaction, button):
        self.page = max(0, self.page - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_b(self, interaction, button):
        self.page = min(self.pages - 1, self.page + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.embed(), view=self)


async def send_pager(ctx, title, lines, per_page=10, footer=None, color=COLOR_DEFAULT):
    view = PagerView(ctx.author.id, ctx.guild, title, lines, per_page, footer, color)
    view.message = await ctx.send(embed=view.embed(), view=view if view.pages > 1 else None)
    return view.message


class ConfirmView(OwnedView):
    def __init__(self, author_id, timeout=30):
        super().__init__(author_id, timeout=timeout)
        self.value = None

    @discord.ui.button(label="Go", style=discord.ButtonStyle.danger)
    async def confirm_b(self, interaction, button):
        self.value = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel_b(self, interaction, button):
        self.value = False
        await interaction.response.defer()
        self.stop()


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
    # 100% mémoire : aucune requête disque ni réseau ici
    if ctx.command is None:
        return True
    if is_bot_banned(ctx.author.id):
        raise BotBannedError()
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    level = get_user_level(ctx.author)
    if level < LEVEL_WL and is_channel_blocked(ctx.guild.id, ctx.channel.id):
        raise ChannelBlockedError()
    if level < get_command_level(ctx.guild.id, ctx.command.name):
        raise PermLevelError(get_command_level(ctx.guild.id, ctx.command.name))
    return True


# ========================= EVENTS =========================

@bot.event
async def on_ready():
    log.info(f"Moh connecté : {bot.user} ({bot.user.id}) sur {len(bot.guilds)} serveur(s)")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandInvokeError):
        error = error.original
    if isinstance(error, Abort):
        return
    if isinstance(error, Missing):
        try:
            await ctx.send(embed=error_embed(error.title, error.desc))
        except discord.HTTPException:
            pass
        return
    if isinstance(error, BotBannedError):
        try:
            await ctx.send(embed=error_embed("Accès refusé", "Tu es banni du bot."))
        except discord.HTTPException:
            pass
        return
    if isinstance(error, (ChannelBlockedError, PermLevelError)):
        try:
            await ctx.message.add_reaction("🔇" if isinstance(error, ChannelBlockedError) else "⛔")
        except discord.HTTPException:
            pass
        return
    if isinstance(error, (commands.CommandNotFound, commands.NoPrivateMessage)):
        return
    try:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=usage(ctx.command.name) if ctx.command else error_embed("Il manque quelque chose"))
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=error_embed("Argument invalide", str(error)[:300]))
        elif isinstance(error, discord.Forbidden):
            await ctx.send(embed=error_embed("Il me manque une permission",
                                             "Discord me bloque pour ça. Vérifie mes permissions et la position de mon rôle."))
        else:
            log.error(f"Erreur '{ctx.command}' par {ctx.author} : {error}\n"
                      + "".join(traceback.format_exception(type(error), error, error.__traceback__)))
            await ctx.send(embed=error_embed("Oups", "Un truc a planté de mon côté. Réessaie dans un instant."))
    except discord.HTTPException:
        pass


@bot.event
async def on_command_completion(ctx):
    if ctx.guild is None or ctx.command is None or not get_log_channel(ctx.guild.id, "commands"):
        return
    await send_log(ctx.guild, "commands", f"Commande {get_prefix_cached()}{ctx.command.qualified_name}",
                   author=ctx.author,
                   desc=f"**Salon :** {ctx.channel.mention}\n**Message :** {trunc(ctx.message.content, 1000)}",
                   color=COLOR_DEFAULT)


# ========================= LOGS =========================

async def send_log(guild, log_type, title, *, author=None, target=None, desc=None, color=None, fields=None):
    channel_id = get_log_channel(guild.id, log_type)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if not channel:
        return
    em = discord.Embed(title=trunc(title, 256), color=color if color is not None else COLOR_DEFAULT,
                       timestamp=datetime.now(timezone.utc))
    if author:
        em.add_field(name="Par", value=f"{author.mention} (`{author.id}`)", inline=True)
    if target:
        em.add_field(name="Membre", value=f"{target.mention} (`{target.id}`)", inline=True)
    if desc:
        em.description = trunc(desc, 4000)
    for name, value, inline in fields or ():
        em.add_field(name=name, value=trunc(value, 1024), inline=inline)
    try:
        await channel.send(embed=em, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as e:
        log.warning(f"send_log: {e}")


_BG_TASKS = set()


def background(coro):
    """Lance une tâche sans attendre (on garde une référence pour qu'elle ne soit pas perdue)."""
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


def fire_log(*args, **kwargs):
    """Envoie le log en arrière-plan pour ne pas ralentir la réponse."""
    background(send_log(*args, **kwargs))


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
                await guild.unban(discord.Object(id=int(s["user_id"])), reason="Tempban terminé")
                deactivate_sanction(s["id"])
                await send_log(guild, "moderation", "Fin de tempban",
                               desc=f"**Membre :** <@{s['user_id']}>\n**Sanction :** `#{s['id']}`",
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


# ========================= MODÉRATION =========================

def _split_user_duration(text):
    """Accepte 'membre 1d raison' comme '1d membre raison'. Renvoie (cible, durée en s, raison)."""
    toks = (text or "").split()
    if not toks:
        return None, None, ""
    if len(toks) >= 2 and is_unit_duration(toks[0]):
        return toks[1], parse_duration(toks[0]), " ".join(toks[2:])
    target, rest = toks[0], toks[1:]
    if rest:
        d = parse_duration(rest[0])
        if d:
            return target, d, " ".join(rest[1:])
    return target, None, " ".join(rest)


@bot.command(name="ban")
async def _ban(ctx, *, text: str = None):
    target, duration_seconds, reason = _split_user_duration(text)
    if not target:
        return await ctx.send(embed=usage("ban", "Exemples : `+ban @moh 1d spam`, `+ban 1234567890 pub`"))
    reason = reason or "Pas de raison"
    display, uid = await get_user(ctx, target)
    member = ctx.guild.get_member(uid)
    err = check_target(ctx, member, uid)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    duration_str = format_duration_fr(duration_seconds) if duration_seconds else "Définitif"
    if member:
        await safe_dm(member, error_embed(f"Tu as été banni de {ctx.guild.name}",
                                          f"**Par :** {ctx.author}\n**Raison :** {reason}\n**Durée :** {duration_str}"))
    try:
        await ctx.guild.ban(discord.Object(id=uid), reason=trunc(f"[{ctx.author}] {reason}", 512),
                            delete_message_seconds=0)
    except discord.NotFound:
        return await ctx.send(embed=error_embed("Introuvable", "Ce compte n'existe pas sur Discord."))
    deactivate_user_sanctions(ctx.guild.id, uid, ("tempban",))
    sid = add_sanction(ctx.guild.id, uid, ctx.author.id, "tempban" if duration_seconds else "ban",
                       reason, duration_seconds)
    await ctx.send(embed=mod_embed("Membre banni", display, uid, ctx.author, reason, duration_str, sid))
    fire_log(ctx.guild, "moderation", "Ban", author=ctx.author,
             desc=f"**Membre :** <@{uid}> (`{uid}`)\n**Durée :** {duration_str}\n**Raison :** {reason}\n**Sanction :** `#{sid}`",
             color=COLOR_ERROR)


@bot.command(name="unban")
async def _unban(ctx, *, text: str = None):
    target, reason = split_target(text)
    if not target:
        return await ctx.send(embed=usage("unban", "Avec l'ID, la mention ou le nom du banni."))
    uid = user_id_from(target)
    if uid is None:
        bans = [e async for e in ctx.guild.bans(limit=None)]
        cands = rank_matches(target, bans, lambda e: [e.user.name, e.user.global_name])
        if not cands:
            raise Missing("Aucun ban", f"Aucun banni ne ressemble à `{trunc(target, 60)}`.")
        entry = await pick(ctx, cands, "bannis", lambda e: str(e.user), lambda e: f"ID {e.user.id}")
        uid = entry.user.id
    try:
        await ctx.guild.unban(discord.Object(id=uid), reason=trunc(f"[{ctx.author}] {reason}", 512))
    except discord.NotFound:
        raise Missing("Aucun ban", "Cette personne n'est pas bannie.")
    deactivate_user_sanctions(ctx.guild.id, uid, ("ban", "tempban"))
    await ctx.send(embed=success_embed("Membre débanni", f"<@{uid}> peut revenir."))
    fire_log(ctx.guild, "moderation", "Unban", author=ctx.author,
             desc=f"**Membre :** <@{uid}> (`{uid}`)\n**Raison :** {reason or 'aucune'}", color=COLOR_SUCCESS)


@bot.command(name="kick")
async def _kick(ctx, *, text: str = None):
    target, reason = split_target(text)
    if not target:
        return await ctx.send(embed=usage("kick"))
    member = await get_member(ctx, target)
    reason = reason or "Pas de raison"
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    await safe_dm(member, error_embed(f"Tu as été expulsé de {ctx.guild.name}",
                                      f"**Par :** {ctx.author}\n**Raison :** {reason}"))
    await member.kick(reason=trunc(f"[{ctx.author}] {reason}", 512))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "kick", reason)
    await ctx.send(embed=mod_embed("Membre expulsé", member, member.id, ctx.author, reason, case_id=sid, color=COLOR_WARNING))
    fire_log(ctx.guild, "moderation", "Kick", author=ctx.author, target=member,
             desc=f"**Raison :** {reason}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="mute", aliases=["timeout", "tempmute"])
async def _mute(ctx, *, text: str = None):
    target, seconds, reason = _split_user_duration(text)
    if not target:
        return await ctx.send(embed=usage("mute", "Exemples : `+mute @moh 30m`, `+mute 2h moh spam`"))
    member = await get_member(ctx, target)
    seconds = seconds or 3600
    reason = reason or "Pas de raison"
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    if member.guild_permissions.administrator:
        return await ctx.send(embed=error_embed("Pas possible", "Discord ne laisse pas timeout un administrateur."))
    if seconds > 60 * 60 * 24 * 28:
        return await ctx.send(embed=error_embed("Trop long", "28 jours maximum, c'est la limite de Discord."))
    await member.timeout(timedelta(seconds=seconds), reason=trunc(f"[{ctx.author}] {reason}", 512))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "mute", reason, seconds)
    duration_str = format_duration_fr(seconds)
    await ctx.send(embed=mod_embed("Membre mute", member, member.id, ctx.author, reason, duration_str, sid, color=COLOR_WARNING))
    background(safe_dm(member, warning_embed(f"Tu as été mute sur {ctx.guild.name}",
                                                      f"**Par :** {ctx.author}\n**Durée :** {duration_str}\n**Raison :** {reason}")))
    fire_log(ctx.guild, "moderation", "Mute", author=ctx.author, target=member,
             desc=f"**Durée :** {duration_str}\n**Raison :** {reason}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="unmute", aliases=["untimeout"])
async def _unmute(ctx, *, text: str = None):
    target, reason = split_target(text)
    if not target:
        return await ctx.send(embed=usage("unmute"))
    member = await get_member(ctx, target)
    if not member.is_timed_out():
        return await ctx.send(embed=info_embed("Pas mute", f"{member.mention} n'est pas mute."))
    await member.timeout(None, reason=trunc(f"[{ctx.author}] {reason}", 512))
    deactivate_user_sanctions(ctx.guild.id, member.id, ("mute",))
    await ctx.send(embed=success_embed("Mute retiré", f"{member.mention} peut de nouveau parler."))
    fire_log(ctx.guild, "moderation", "Unmute", author=ctx.author, target=member,
             desc=f"**Raison :** {reason or 'aucune'}", color=COLOR_SUCCESS)


@bot.command(name="warn")
async def _warn(ctx, *, text: str = None):
    target, reason = split_target(text)
    if not target or not reason:
        return await ctx.send(embed=usage("warn"))
    member = await get_member(ctx, target)
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "warn", reason)
    warn_count = count_warns(ctx.guild.id, member.id)
    em = mod_embed("Membre averti", member, member.id, ctx.author, reason, case_id=sid, color=COLOR_WARNING)
    em.add_field(name="Total", value=f"{warn_count} avertissement(s)", inline=True)
    await ctx.send(embed=em)
    background(safe_dm(member, warning_embed(f"Avertissement sur {ctx.guild.name}",
                                                      f"**Par :** {ctx.author}\n**Raison :** {reason}\n**Total :** {warn_count}")))
    fire_log(ctx.guild, "moderation", "Warn", author=ctx.author, target=member,
             desc=f"**Raison :** {reason}\n**Total :** {warn_count}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="warns", aliases=["warnings"])
async def _warns(ctx, *, user_input: str = None):
    display, uid = await get_user(ctx, user_input) if user_input else (ctx.author, ctx.author.id)
    warns = get_warns(ctx.guild.id, uid)
    if not warns:
        return await ctx.send(embed=info_embed("Avertissements", f"{format_user_display(display, uid)} n'a aucun avertissement."))
    lines = []
    for w in warns[:15]:
        mod = ctx.guild.get_member(int(w["moderator_id"]))
        mod_name = mod.display_name if mod else f"<@{w['moderator_id']}>"
        try:
            ts = datetime.fromisoformat(w["timestamp"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            ts = "?"
        lines.append(f"`#{w['id']}` · {ts} · par **{mod_name}**\n{trunc(w['reason'], 200)}")
    name = getattr(display, "display_name", str(uid))
    em = info_embed(f"Avertissements de {name} ({len(warns)})", "\n\n".join(lines))
    if display is not None:
        em.set_thumbnail(url=display.display_avatar.url)
    await ctx.send(embed=em)


@bot.command(name="delwarn", aliases=["unwarn"])
async def _delwarn(ctx, warn_id: str = None):
    if not warn_id or not warn_id.lstrip("#").isdigit():
        return await ctx.send(embed=usage("delwarn"))
    wid = int(warn_id.lstrip("#"))
    if delete_warn(ctx.guild.id, wid):
        await ctx.send(embed=success_embed("Avertissement retiré", f"Le `#{wid}` est supprimé."))
        fire_log(ctx.guild, "moderation", "Avertissement supprimé", author=ctx.author,
                 desc=f"Avertissement `#{wid}` supprimé.", color=COLOR_SUCCESS)
    else:
        await ctx.send(embed=error_embed("Introuvable", f"Le `#{wid}` n'existe pas ou est déjà retiré."))


@bot.command(name="clearwarns")
async def _clearwarns(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=usage("clearwarns"))
    display, uid = await get_user(ctx, user_input)
    count = deactivate_user_sanctions(ctx.guild.id, uid, ("warn",))
    if count:
        await ctx.send(embed=success_embed("Avertissements effacés", f"**{count}** retiré(s) à {format_user_display(display, uid)}."))
        fire_log(ctx.guild, "moderation", "Avertissements effacés", author=ctx.author,
                 desc=f"**Membre :** <@{uid}>\n**{count}** avertissements retirés", color=COLOR_SUCCESS)
    else:
        await ctx.send(embed=info_embed("Rien à faire", f"{format_user_display(display, uid)} n'a aucun avertissement."))


@bot.command(name="softban")
async def _softban(ctx, *, text: str = None):
    target, reason = split_target(text)
    if not target:
        return await ctx.send(embed=usage("softban"))
    member = await get_member(ctx, target)
    reason = reason or "Pas de raison"
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    await safe_dm(member, warning_embed(f"Softban sur {ctx.guild.name}",
                                        f"**Par :** {ctx.author}\n**Raison :** {reason}\nTu peux revenir, tes messages récents ont été supprimés."))
    await ctx.guild.ban(member, reason=trunc(f"[{ctx.author}] softban: {reason}", 512), delete_message_seconds=86400)
    await ctx.guild.unban(discord.Object(id=member.id), reason="softban (unban auto)")
    sid = add_sanction(ctx.guild.id, member.id, ctx.author.id, "softban", reason)
    await ctx.send(embed=mod_embed("Membre softban", member, member.id, ctx.author, reason, case_id=sid, color=COLOR_WARNING))
    fire_log(ctx.guild, "moderation", "Softban", author=ctx.author, target=member,
             desc=f"**Raison :** {reason}\n**Sanction :** `#{sid}`", color=COLOR_WARNING)


@bot.command(name="clear", aliases=["purge", "c"])
async def _clear(ctx, *, text: str = None):
    """+clear · +clear 50 · +clear @moh · +clear moh 20 · +clear 20 1234567890..."""
    amount, words = None, []
    for t in tokenize(text):
        if t.isdigit() and len(t) <= 4:
            amount = int(t)
        else:
            words.append(t)
    uid, who_obj = None, None
    if words:
        q = " ".join(words)
        uid = user_id_from(q)          # un ID marche même si la personne a quitté le serveur
        if uid is None:
            who_obj = await get_member(ctx, q)
            uid = who_obj.id
    if amount is not None and not 1 <= amount <= MAX_CLEAR:
        return await ctx.send(embed=error_embed("Nombre invalide", f"Entre 1 et {MAX_CLEAR}."))

    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass

    if uid is None:
        deleted = await ctx.channel.purge(limit=amount or DEFAULT_CLEAR)
    else:
        remaining = [amount if amount else float("inf")]

        def check(m):
            if m.author.id != uid or remaining[0] <= 0:
                return False
            remaining[0] -= 1
            return True
        scan = min(max(amount * 10, 200), 5000) if amount else MEMBER_SCAN
        deleted = await ctx.channel.purge(limit=scan, check=check, oldest_first=False)

    who = f" de <@{uid}>" if uid else ""
    await ctx.send(embed=success_embed(f"🧹 {len(deleted)} message(s) supprimé(s)", who.strip() and f"Messages{who}"),
                   delete_after=4)
    fire_log(ctx.guild, "moderation", "Clear", author=ctx.author,
             desc=f"**Salon :** {ctx.channel.mention}\n**Messages :** {len(deleted)}{who}", color=COLOR_INFO)


async def _channel_and_rest(ctx, text):
    """Salon optionnel en premier (mention, ID ou nom), sinon le salon actuel."""
    target, rest = split_target(text)
    if target:
        cands = find_channels(ctx.guild, target)
        if cands:
            ch = await get_channel(ctx, target)
            return ch, rest
    return ctx.channel, (text or "").strip()


@bot.command(name="lock")
async def _lock(ctx, *, text: str = None):
    channel, reason = await _channel_and_rest(ctx, text)
    reason = reason or "Pas de raison"
    ow = channel.overwrites_for(ctx.guild.default_role)
    ow.send_messages = False
    ow.add_reactions = False
    ow.create_public_threads = False
    await channel.set_permissions(ctx.guild.default_role, overwrite=ow, reason=trunc(f"[{ctx.author}] {reason}", 512))
    await ctx.send(embed=warning_embed("🔒 Salon verrouillé", f"{channel.mention} est en lecture seule."))
    fire_log(ctx.guild, "moderation", "Lock", author=ctx.author,
             desc=f"**Salon :** {channel.mention}\n**Raison :** {reason}", color=COLOR_WARNING)


@bot.command(name="unlock")
async def _unlock(ctx, *, text: str = None):
    channel, _ = await _channel_and_rest(ctx, text)
    ow = channel.overwrites_for(ctx.guild.default_role)
    ow.send_messages = None
    ow.add_reactions = None
    ow.create_public_threads = None
    await channel.set_permissions(ctx.guild.default_role, overwrite=None if ow.is_empty() else ow,
                                  reason=f"[{ctx.author}] unlock")
    await ctx.send(embed=success_embed("🔓 Salon déverrouillé", f"{channel.mention} est de nouveau ouvert."))
    fire_log(ctx.guild, "moderation", "Unlock", author=ctx.author,
             desc=f"**Salon :** {channel.mention}", color=COLOR_SUCCESS)


@bot.command(name="slowmode", aliases=["slow"])
async def _slowmode(ctx, *, text: str = None):
    toks = tokenize(text)
    dur_tok = next((t for t in toks if t.isdigit() or is_unit_duration(t)), None)
    if dur_tok is None:
        return await ctx.send(embed=usage("slowmode", "`0` pour couper. 6h max."))
    others = [t for t in toks if t is not dur_tok]
    channel = await get_channel(ctx, " ".join(others)) if others else ctx.channel
    seconds = int(dur_tok) if dur_tok.isdigit() else parse_duration(dur_tok)
    if seconds is None or not 0 <= seconds <= 21600:
        return await ctx.send(embed=error_embed("Durée invalide", "Entre 0 et 6h."))
    await channel.edit(slowmode_delay=seconds, reason=f"[{ctx.author}] slowmode")
    if seconds == 0:
        await ctx.send(embed=success_embed("Slowmode coupé", channel.mention))
    else:
        await ctx.send(embed=success_embed("Slowmode activé", f"{channel.mention} : un message toutes les {format_duration_fr(seconds)}"))


@bot.command(name="banlist", aliases=["bans"])
async def _banlist(ctx):
    entries = [e async for e in ctx.guild.bans(limit=None)]
    if not entries:
        return await ctx.send(embed=info_embed("Bannis", "Personne n'est banni."))
    lines = [f"**{e.user}** (`{e.user.id}`) · {trunc(e.reason or 'sans raison', 60)}" for e in entries]
    await send_pager(ctx, f"Bannis ({len(entries)})", lines, per_page=15)


_snipe_cache = {}


@bot.command(name="snipe")
async def _snipe(ctx):
    data = _snipe_cache.get(ctx.channel.id)
    if not data:
        return await ctx.send(embed=info_embed("Snipe", "Rien de supprimé récemment ici."))
    ts = int(data["time"].timestamp())
    em = discord.Embed(description=trunc(data["content"], 4000) or "*pas de texte*", color=COLOR_DEFAULT)
    em.set_author(name=data["author"], icon_url=data["avatar"])
    em.add_field(name="Supprimé", value=f"<t:{ts}:R>", inline=True)
    if data.get("image"):
        em.set_image(url=data["image"])
    await ctx.send(embed=em)


SANCTION_LABEL = {"ban": "Ban", "tempban": "Ban temporaire", "kick": "Kick", "mute": "Mute",
                  "warn": "Avertissement", "softban": "Softban"}


@bot.command(name="history", aliases=["sanctions"])
async def _history(ctx, *, user_input: str = None):
    display, uid = await get_user(ctx, user_input) if user_input else (ctx.author, ctx.author.id)
    rows = get_history(ctx.guild.id, uid, limit=50)
    if not rows:
        return await ctx.send(embed=info_embed("Historique", f"{format_user_display(display, uid)} n'a aucune sanction."))
    lines = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(r["timestamp"]).strftime("%d/%m/%Y")
        except (ValueError, TypeError):
            ts = "?"
        mod = ctx.guild.get_member(int(r["moderator_id"]))
        mod_name = mod.display_name if mod else f"<@{r['moderator_id']}>"
        marker = "" if r["active"] else " *(retirée)*"
        dur = f" · {format_duration_fr(r['duration'])}" if r["duration"] else ""
        lines.append(f"`#{r['id']}` · {ts} · **{SANCTION_LABEL.get(r['type'], r['type'])}**{dur}{marker}\n"
                     f"par {mod_name} : {trunc(r['reason'] or 'sans raison', 150)}")
    name = getattr(display, "display_name", str(uid))
    await send_pager(ctx, f"Historique de {name} ({len(rows)})", lines, per_page=8,
                     footer=f"{get_prefix_cached()}case <id> pour le détail")


@bot.command(name="case")
async def _case(ctx, case_id: str = None):
    if not case_id or not case_id.lstrip("#").isdigit():
        return await ctx.send(embed=usage("case"))
    cid = int(case_id.lstrip("#"))
    s = get_sanction(ctx.guild.id, cid)
    if not s:
        return await ctx.send(embed=error_embed("Introuvable", f"La sanction `#{cid}` n'existe pas."))
    try:
        ts = int(datetime.fromisoformat(s["timestamp"]).timestamp())
        when = f"<t:{ts}:F> (<t:{ts}:R>)"
    except (ValueError, TypeError):
        when = "?"
    em = discord.Embed(title=f"Sanction #{s['id']} · {SANCTION_LABEL.get(s['type'], s['type'])}", color=COLOR_DEFAULT)
    em.add_field(name="Membre", value=f"<@{s['user_id']}> (`{s['user_id']}`)", inline=True)
    em.add_field(name="Par", value=f"<@{s['moderator_id']}>", inline=True)
    em.add_field(name="Statut", value="Active" if s["active"] else "Retirée", inline=True)
    if s["duration"]:
        em.add_field(name="Durée", value=format_duration_fr(s["duration"]), inline=True)
    em.add_field(name="Date", value=when, inline=False)
    em.add_field(name="Raison", value=trunc(s["reason"] or "Pas de raison", 1024), inline=False)
    await ctx.send(embed=em)


@bot.command(name="reason")
async def _reason(ctx, case_id: str = None, *, new_reason: str = None):
    if not case_id or not case_id.lstrip("#").isdigit() or not new_reason:
        return await ctx.send(embed=usage("reason"))
    cid = int(case_id.lstrip("#"))
    s = get_sanction(ctx.guild.id, cid)
    if not s:
        return await ctx.send(embed=error_embed("Introuvable", f"La sanction `#{cid}` n'existe pas."))
    if int(s["moderator_id"]) != ctx.author.id and not is_owner_plus(ctx.author):
        return await ctx.send(embed=error_embed("Pas possible", "Seul l'auteur de la sanction ou un Owner peut la modifier."))
    update_sanction_reason(ctx.guild.id, cid, new_reason)
    await ctx.send(embed=success_embed("Raison modifiée", f"Sanction `#{cid}` : {trunc(new_reason, 500)}"))
    fire_log(ctx.guild, "moderation", "Raison modifiée", author=ctx.author,
             desc=f"**Sanction :** `#{cid}`\n**Avant :** {s['reason'] or 'aucune'}\n**Après :** {new_reason}",
             color=COLOR_INFO)


# ========================= INFOS =========================

@bot.command(name="userinfo", aliases=["ui", "whois"])
async def _userinfo(ctx, *, user_input: str = None):
    display, uid = await get_user(ctx, user_input) if user_input else (ctx.author, ctx.author.id)
    if display is None:
        return await ctx.send(embed=error_embed("Introuvable", "Impossible de récupérer ce compte."))
    created = int(display.created_at.timestamp())
    em = discord.Embed(title=str(display), color=display.color if getattr(display, "color", None) and display.color.value else COLOR_DEFAULT)
    em.set_thumbnail(url=display.display_avatar.url)
    em.add_field(name="ID", value=f"`{display.id}`", inline=True)
    em.add_field(name="Bot", value="oui" if display.bot else "non", inline=True)
    em.add_field(name="Compte créé", value=f"<t:{created}:R>", inline=True)
    if isinstance(display, discord.Member):
        member = display
        if member.nick:
            em.add_field(name="Surnom", value=member.nick, inline=True)
        if member.joined_at:
            em.add_field(name="A rejoint", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
        em.add_field(name="Niveau", value=level_name(get_user_level(member)), inline=True)
        if member.is_timed_out() and member.timed_out_until:
            em.add_field(name="Mute jusqu'à", value=f"<t:{int(member.timed_out_until.timestamp())}:R>", inline=True)
        roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        warns = count_warns(ctx.guild.id, member.id)
        if warns:
            em.add_field(name="Avertissements", value=str(warns), inline=True)
        em.add_field(name=f"Rôles ({len(roles)})", value=trunc(", ".join(roles[:30]), 1024) if roles else "aucun", inline=False)
    else:
        em.set_footer(text="Pas sur ce serveur")
    await ctx.send(embed=em)


@bot.command(name="serverinfo", aliases=["si"])
async def _serverinfo(ctx):
    g = ctx.guild
    bots = sum(1 for m in g.members if m.bot)
    humans = (g.member_count or 0) - bots
    em = discord.Embed(title=g.name, color=COLOR_DEFAULT)
    apply_thumbnail(em, g)
    em.add_field(name="ID", value=f"`{g.id}`", inline=True)
    em.add_field(name="Propriétaire", value=f"<@{g.owner_id}>", inline=True)
    em.add_field(name="Créé", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    em.add_field(name="Membres", value=f"{g.member_count} ({humans} humains, {bots} bots)", inline=True)
    em.add_field(name="Salons", value=f"{len(g.text_channels)} écrits, {len(g.voice_channels)} vocaux", inline=True)
    em.add_field(name="Rôles", value=str(len(g.roles)), inline=True)
    em.add_field(name="Boosts", value=f"{g.premium_subscription_count} (niveau {g.premium_tier})", inline=True)
    em.add_field(name="Emojis", value=f"{len(g.emojis)}/{g.emoji_limit * 2}", inline=True)
    await ctx.send(embed=em)


@bot.command(name="avatar", aliases=["av", "pdp"])
async def _avatar(ctx, *, user_input: str = None):
    display, uid = await get_user(ctx, user_input) if user_input else (ctx.author, ctx.author.id)
    if display is None:
        return await ctx.send(embed=error_embed("Introuvable", "Impossible de récupérer ce compte."))
    em = discord.Embed(title=f"Avatar de {display.display_name}", color=COLOR_DEFAULT,
                       description=f"[Lien direct]({display.display_avatar.url})")
    em.set_image(url=display.display_avatar.with_size(1024).url)
    await ctx.send(embed=em)


@bot.command(name="ping")
async def _ping(ctx):
    ws = round(bot.latency * 1000)
    t = time.perf_counter()
    msg = await ctx.send(embed=info_embed("🏓 Pong", f"WebSocket : **{ws} ms**"))
    api = round((time.perf_counter() - t) * 1000)
    await msg.edit(embed=success_embed("🏓 Pong", f"WebSocket : **{ws} ms**\nAPI : **{api} ms**"))


@bot.command(name="roleinfo", aliases=["ri"])
async def _roleinfo(ctx, *, role_input: str = None):
    if not role_input:
        return await ctx.send(embed=usage("roleinfo"))
    role = await get_role(ctx, role_input)
    em = discord.Embed(title=role.name, color=role.color if role.color.value else COLOR_DEFAULT)
    em.add_field(name="ID", value=f"`{role.id}`", inline=True)
    em.add_field(name="Membres", value=str(len(role.members)), inline=True)
    em.add_field(name="Couleur", value=str(role.color), inline=True)
    em.add_field(name="Position", value=str(role.position), inline=True)
    em.add_field(name="Mentionnable", value="oui" if role.mentionable else "non", inline=True)
    em.add_field(name="Affiché à part", value="oui" if role.hoist else "non", inline=True)
    rp = get_role_perm(ctx.guild.id, role.id)
    if rp:
        em.add_field(name="Niveau", value=level_name(rp), inline=True)
    em.add_field(name="Créé", value=f"<t:{int(role.created_at.timestamp())}:R>", inline=False)
    await ctx.send(embed=em)


# ========================= OUTILS : RÔLES =========================

def _role_guard(ctx, role):
    """Renvoie un message d'erreur si le rôle ne peut pas être manipulé, sinon None."""
    if role.is_default() or role.is_premium_subscriber() or role.managed:
        return "Ce rôle ne peut pas être donné à la main."
    if role >= ctx.guild.me.top_role:
        return "Ce rôle est au-dessus du mien, monte mon rôle dans les paramètres."
    if role >= ctx.author.top_role and not is_owner_plus(ctx.author):
        return "Ce rôle est au-dessus du tien ou au même niveau."
    if not is_owner_plus(ctx.author) and any(getattr(role.permissions, p) for p in DANGEROUS_PERMS):
        return "Ce rôle a des permissions sensibles, c'est réservé aux Owners."
    return None


@bot.command(name="role", aliases=["r"])
async def _role(ctx, *, text: str = None):
    """+role @moh image · +role image moh · +role 123 456..."""
    if not text:
        return await ctx.send(embed=usage("role", "L'ordre n'a pas d'importance."))
    role, members = await parse_role_members(ctx, text)
    if not members:
        return await ctx.send(embed=error_embed("Membre manquant", "Dis-moi à qui donner ou retirer le rôle."))
    err = _role_guard(ctx, role)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    added, removed = [], []
    for member in members:
        if member.get_role(role.id):
            if member.id != ctx.author.id and check_target(ctx, member):
                continue
            await member.remove_roles(role, reason=f"[{ctx.author}] role")
            removed.append(member.mention)
        else:
            await member.add_roles(role, reason=f"[{ctx.author}] role")
            added.append(member.mention)
    lines = []
    if added:
        lines.append(f"Ajouté à {', '.join(added)}")
    if removed:
        lines.append(f"Retiré à {', '.join(removed)}")
    if not lines:
        return await ctx.send(embed=error_embed("Pas possible", "Tu ne peux pas retirer ce rôle à cette personne."))
    await ctx.send(embed=success_embed(role.name, "\n".join(lines)))


async def _mass_role(ctx, text, add):
    cmd = "addrole" if add else "delrole"
    if not text:
        return await ctx.send(embed=usage(cmd, "L'ordre n'a pas d'importance : rôle puis membres ou l'inverse."))
    role, members = await parse_role_members(ctx, text)
    if not members:
        return await ctx.send(embed=error_embed("Membre manquant", "Dis-moi à qui appliquer le rôle."))
    err = _role_guard(ctx, role)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    if add:
        targets = [m for m in members if not m.get_role(role.id)]
    else:
        targets = [m for m in members if m.get_role(role.id)
                   and (m.id == ctx.author.id or not check_target(ctx, m))]
    if not targets:
        return await ctx.send(embed=info_embed("Rien à faire",
                                               "Ils ont déjà ce rôle." if add else "Personne à qui le retirer."))

    async def apply(m):
        try:
            if add:
                await m.add_roles(role, reason=f"[{ctx.author}] {cmd}")
            else:
                await m.remove_roles(role, reason=f"[{ctx.author}] {cmd}")
            return m
        except discord.HTTPException:
            return None

    done = [m for m in await asyncio.gather(*(apply(m) for m in targets)) if m]
    verb = "ajouté à" if add else "retiré à"
    names = ", ".join(m.mention for m in done[:20]) + (f" et {len(done) - 20} autres" if len(done) > 20 else "")
    await ctx.send(embed=success_embed(role.name, f"{role.mention} {verb} {names or 'personne'}"))
    fire_log(ctx.guild, "roles", "Rôle en masse", author=ctx.author,
             desc=f"{role.mention} {verb} {len(done)} membre(s)", color=COLOR_INFO)


@bot.command(name="addrole", aliases=["ar", "giverole"])
async def _addrole(ctx, *, text: str = None):
    await _mass_role(ctx, text, True)


@bot.command(name="delrole", aliases=["dr", "removerole"])
async def _delrole(ctx, *, text: str = None):
    await _mass_role(ctx, text, False)


@bot.command(name="roleall")
async def _roleall(ctx, *, role_input: str = None):
    if not role_input:
        return await ctx.send(embed=usage("roleall"))
    role = await get_role(ctx, role_input)
    err = _role_guard(ctx, role)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    targets = [m for m in ctx.guild.members if not m.bot and not m.get_role(role.id)]
    if not targets:
        return await ctx.send(embed=info_embed("Rien à faire", "Tout le monde a déjà ce rôle."))
    view = ConfirmView(ctx.author.id)
    view.message = await ctx.send(embed=warning_embed(
        "Donner le rôle à tout le monde ?",
        f"{role.mention} va être donné à **{len(targets)}** membre(s)."), view=view)
    await view.wait()
    if not view.value:
        try:
            await view.message.edit(embed=info_embed("Annulé", "Aucun rôle donné."), view=None)
        except discord.HTTPException:
            pass
        return
    await view.message.edit(embed=info_embed("En cours", f"Attribution à {len(targets)} membre(s)..."), view=None)
    sem = asyncio.Semaphore(8)

    async def apply(m):
        async with sem:
            try:
                await m.add_roles(role, reason=f"[{ctx.author}] roleall")
                return True
            except discord.HTTPException:
                return False

    done = sum(await asyncio.gather(*(apply(m) for m in targets)))
    await view.message.edit(embed=success_embed("C'est fait", f"{role.mention} donné à **{done}** membre(s)."))
    fire_log(ctx.guild, "roles", "Roleall", author=ctx.author,
             desc=f"{role.mention} donné à {done} membre(s)", color=COLOR_INFO)


@bot.command(name="nick", aliases=["setnick"])
async def _nick(ctx, *, text: str = None):
    target, nickname = split_target(text)
    if not target:
        return await ctx.send(embed=usage("nick", "Sans pseudo, ça remet le pseudo d'origine."))
    member = await get_member(ctx, target)
    if member.id != ctx.author.id:
        err = check_target(ctx, member)
        if err:
            return await ctx.send(embed=error_embed("Pas possible", err))
    elif member.top_role >= ctx.guild.me.top_role or member.id == ctx.guild.owner_id:
        return await ctx.send(embed=error_embed("Pas possible", "Ton rôle est trop haut pour que je touche à ton pseudo."))
    if nickname and len(nickname) > 32:
        return await ctx.send(embed=error_embed("Trop long", "32 caractères max."))
    await member.edit(nick=nickname or None, reason=f"[{ctx.author}] nick")
    if nickname:
        await ctx.send(embed=success_embed("Pseudo changé", f"{member.mention} s'appelle maintenant **{nickname}**"))
    else:
        await ctx.send(embed=success_embed("Pseudo remis à zéro", member.mention))


@bot.command(name="derank")
async def _derank(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=usage("derank"))
    member = await get_member(ctx, user_input)
    err = check_target(ctx, member)
    if err:
        return await ctx.send(embed=error_embed("Pas possible", err))
    to_remove = [r for r in member.roles
                 if r != ctx.guild.default_role and r < ctx.guild.me.top_role and not r.managed]
    if not to_remove:
        return await ctx.send(embed=info_embed("Rien à faire", "Aucun rôle que je peux retirer."))
    await member.remove_roles(*to_remove, reason=f"[{ctx.author}] derank")
    await ctx.send(embed=success_embed("Derank", f"**{len(to_remove)}** rôle(s) retiré(s) à {member.mention}."))
    fire_log(ctx.guild, "roles", "Derank", author=ctx.author, target=member,
             desc=f"**{len(to_remove)}** rôles retirés : {trunc(', '.join(r.mention for r in to_remove), 3500)}",
             color=COLOR_WARNING)


@bot.command(name="say")
async def _say(ctx, *, message: str = None):
    if not message:
        return await ctx.send(embed=usage("say"))
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    await ctx.send(trunc(message, 2000))


@bot.command(name="dm")
async def _dm(ctx, *, text: str = None):
    target, message = split_target(text)
    if not target or not message:
        return await ctx.send(embed=usage("dm"))
    member = await get_member(ctx, target)
    em = discord.Embed(title=f"Message de {ctx.guild.name}", description=trunc(message, 4000), color=COLOR_INFO)
    if await safe_dm(member, em):
        await ctx.send(embed=success_embed("Message envoyé", f"À {member.mention}."))
    else:
        await ctx.send(embed=error_embed("Pas envoyé", "Ses MP sont sûrement fermés."))


@bot.command(name="embed")
async def _embed_cmd(ctx, *, message: str = None):
    if not message:
        return await ctx.send(embed=usage("embed"))
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass
    title, sep, body = message.partition("|")
    if sep:
        em = discord.Embed(title=trunc(title.strip(), 256), description=trunc(body.strip(), 4000), color=COLOR_DEFAULT)
    else:
        em = discord.Embed(description=trunc(message, 4000), color=COLOR_DEFAULT)
    await ctx.send(embed=em)


# ========================= OUTILS : EMOJIS =========================

def clean_emoji_name(name):
    name = re.sub(r"[^A-Za-z0-9_]", "", (name or "").strip(":"))[:32]
    if len(name) < 2:
        name = (name + "_emoji")[:32]
    return name


def image_complete(data):
    """Vérifie que l'image n'est pas coupée en plein milieu (cause des emojis à moitié chargés)."""
    if not data or len(data) < 16:
        return False
    if data.startswith(b"\x89PNG"):
        return b"IEND" in data[-32:]
    if data.startswith(b"GIF8"):
        return data.rstrip(b"\x00").endswith(b";")
    if data.startswith(b"\xff\xd8"):
        return b"\xff\xd9" in data[-32:]
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return len(data) >= int.from_bytes(data[4:8], "little") + 8
    return True


async def http_get(url, attempts=3):
    """Télécharge le fichier en entier, vérifie qu'il est complet, réessaie sinon."""
    for i in range(attempts):
        try:
            async with bot.session.get(url) as resp:
                if resp.status == 404:
                    return None
                if resp.status == 200:
                    data = await resp.read()   # lit TOUT le corps (l'ancien read(n) pouvait s'arrêter avant)
                    expected = resp.content_length if not resp.headers.get("Content-Encoding") else None
                    if (not expected or len(data) == expected) and image_complete(data):
                        return data
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(0.3 * (i + 1))
    return None


def shrink_image(data):
    """Réduit une image fixe trop lourde (si Pillow est installé)."""
    if Image is None:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "is_animated", False):
            return None
        img = img.convert("RGBA")
        for size in (128, 96, 64):
            im = img.copy()
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            out = buf.getvalue()
            if len(out) <= MAX_EMOJI_BYTES:
                return out
    except Exception:
        return None
    return None


async def fetch_emoji_data(job):
    """Renvoie (bytes, erreur)."""
    if "eid" in job:
        ext = "gif" if job["animated"] else "png"
        base = f"https://cdn.discordapp.com/emojis/{job['eid']}.{ext}"
        # Le CDN redimensionne lui-même : on descend la taille tant que c'est trop lourd
        for size in (None, 128, 96, 64, 48):
            data = await http_get(base + (f"?size={size}" if size else ""))
            if data is None:
                return None, "téléchargement raté"
            if len(data) <= MAX_EMOJI_BYTES:
                return data, None
        return None, "trop lourd"
    if "att" in job:
        try:
            data = await job["att"].read()
        except discord.HTTPException:
            data = await http_get(job["att"].url)
    else:
        data = await http_get(job["url"])
    if not data:
        return None, "téléchargement raté"
    if len(data) > MAX_EMOJI_BYTES:
        data = shrink_image(data)
        if data is None:
            return None, "trop lourd (256 Ko max)"
    return data, None


@bot.command(name="create", aliases=["addemoji", "addemote", "steal", "clone"])
async def _create(ctx, *, source: str = None):
    """Colle des emojis d'autres serveurs, ou joins des images (avec des noms), ou 'nom url'."""
    perms = ctx.guild.me.guild_permissions
    if not (getattr(perms, "manage_expressions", False) or getattr(perms, "manage_emojis", False)):
        raise Missing("Il me manque une permission", "Il me faut « Gérer les expressions ».")

    jobs, seen = [], set()
    for anim, name, eid in EMOJI_RE.findall(source or ""):
        if eid not in seen:
            seen.add(eid)
            jobs.append({"name": clean_emoji_name(name), "eid": int(eid), "animated": bool(anim)})

    words = EMOJI_RE.sub(" ", source or "").split()
    urls = [w for w in words if w.startswith(("http://", "https://"))]
    names = [w for w in words if not w.startswith(("http://", "https://"))]
    for i, att in enumerate(ctx.message.attachments):
        nm = names.pop(0) if names else os.path.splitext(att.filename)[0]
        jobs.append({"name": clean_emoji_name(nm), "att": att})
    for i, u in enumerate(urls):
        nm = names.pop(0) if names else f"emoji{i + 1}"
        jobs.append({"name": clean_emoji_name(nm), "url": u})

    if not jobs:
        return await ctx.send(embed=usage("create", "Colle des emojis d'autres serveurs, ou joins une image avec un nom."))
    jobs = jobs[:MAX_CREATE]

    status = None
    if len(jobs) > 3:
        status = await ctx.send(embed=info_embed("En cours", f"J'ajoute {len(jobs)} emojis..."))

    # 1) Tous les téléchargements en parallèle
    downloads = await asyncio.gather(*(fetch_emoji_data(j) for j in jobs))

    # 2) Créations en parallèle (discord.py gère les limites de Discord tout seul)
    sem = asyncio.Semaphore(5)

    async def create_one(job, data):
        async with sem:
            try:
                emoji = await ctx.guild.create_custom_emoji(name=job["name"], image=data,
                                                            reason=f"[{ctx.author}] create")
                return str(emoji), None
            except discord.Forbidden:
                return None, "permission refusée"
            except discord.HTTPException as e:
                if e.code == 30008:
                    return None, "plus de place sur le serveur"
                if e.code == 50045:
                    return None, "fichier trop lourd"
                return None, "refusé par Discord"

    tasks_ = []
    failed = []
    for job, (data, err) in zip(jobs, downloads):
        if err:
            failed.append(f"`{job['name']}` ({err})")
        else:
            tasks_.append((job, create_one(job, data)))
    results = await asyncio.gather(*(t for _, t in tasks_))
    created = []
    for (job, _), (emoji, err) in zip(tasks_, results):
        if emoji:
            created.append(emoji)
        else:
            failed.append(f"`{job['name']}` ({err})")

    if created:
        em = success_embed(f"{len(created)} emoji(s) ajouté(s)", " ".join(created))
        if failed:
            em.add_field(name="Pas passés", value=trunc(", ".join(failed), 1024), inline=False)
    else:
        em = error_embed("Aucun emoji ajouté", trunc(", ".join(failed), 4000))
    if status:
        await status.edit(embed=em)
    else:
        await ctx.send(embed=em)
    if created:
        fire_log(ctx.guild, "roles", "Emojis ajoutés", author=ctx.author,
                 desc=f"**{len(created)}** emoji(s) : {trunc(' '.join(created), 3500)}", color=COLOR_SUCCESS)


@bot.command(name="delemoji", aliases=["delemote", "removeemoji", "delemojis"])
async def _delemoji(ctx, *, emoji_input: str = None):
    """Supprime tous les emojis donnés d'un coup (emoji collé, nom ou ID)."""
    if not emoji_input:
        return await ctx.send(embed=usage("delemoji", "Tu peux en mettre autant que tu veux d'un coup."))
    targets, missing = {}, []
    for tok in re.findall(r"<a?:\w+:\d+>|\S+", emoji_input):
        m = EMOJI_RE.match(tok)
        if m or ID_RE.match(tok):
            e = ctx.guild.get_emoji(int(m.group(3) if m else tok))
            if e:
                targets[e.id] = e
            else:
                missing.append(tok if not m else f":{m.group(2)}:")
            continue
        name = tok.strip(":").lower()
        found = [e for e in ctx.guild.emojis if e.name.lower() == name]
        if found:
            for e in found:
                targets[e.id] = e
        else:
            missing.append(f"`{tok}`")
    if not targets:
        return await ctx.send(embed=error_embed("Introuvable", "Aucun de ces emojis n'est sur ce serveur."))

    async def delete_one(e):
        try:
            await e.delete(reason=f"[{ctx.author}] delemoji")
            return e.name
        except discord.HTTPException:
            return None

    results = await asyncio.gather(*(delete_one(e) for e in targets.values()))
    done = [n for n in results if n]
    failed = len(results) - len(done)
    em = success_embed(f"{len(done)} emoji(s) supprimé(s)", trunc(", ".join(f"`:{n}:`" for n in done), 4000))
    if missing:
        em.add_field(name="Pas trouvés", value=trunc(", ".join(missing), 1024), inline=False)
    if failed:
        em.add_field(name="Ratés", value=f"{failed} emoji(s), vérifie mes permissions", inline=False)
    await ctx.send(embed=em)
    if done:
        fire_log(ctx.guild, "roles", "Emojis supprimés", author=ctx.author,
                 desc=f"**{len(done)}** emoji(s) : {trunc(', '.join(done), 3500)}", color=COLOR_ERROR)


# ========================= LOG LISTENERS + SNIPE =========================

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
    if not get_log_channel(message.guild.id, "messages"):
        return
    desc = f"**Auteur :** {message.author.mention} (`{message.author.id}`)\n**Salon :** {message.channel.mention}\n"
    if message.content:
        desc += f"**Contenu :**\n{trunc(message.content, 1500)}"
    if message.attachments:
        desc += f"\n**Pièces jointes :** {len(message.attachments)}"
    await send_log(message.guild, "messages", "Message supprimé", desc=desc, color=COLOR_ERROR)


@bot.event
async def on_bulk_message_delete(messages):
    if not messages or not messages[0].guild:
        return
    ch = messages[0].channel
    await send_log(messages[0].guild, "messages", "Suppression en masse",
                   desc=f"**Salon :** {ch.mention}\n**Messages :** {len(messages)}", color=COLOR_ERROR)


@bot.event
async def on_message_edit(before, after):
    if not before.guild or before.author.bot or before.content == after.content:
        return
    desc = (f"**Auteur :** {before.author.mention}\n**Salon :** {before.channel.mention}\n"
            f"**Avant :** {trunc(before.content, 1500) or 'vide'}\n**Après :** {trunc(after.content, 1500) or 'vide'}\n"
            f"[Aller au message]({after.jump_url})")
    await send_log(before.guild, "messages", "Message modifié", desc=desc, color=COLOR_INFO)


@bot.event
async def on_member_join(member):
    age = (datetime.now(timezone.utc) - member.created_at).days
    young = age < YOUNG_ACCOUNT_DAYS
    await send_log(member.guild, "members", "Nouveau membre, compte récent" if young else "Nouveau membre",
                   target=member,
                   desc=f"**Compte créé :** <t:{int(member.created_at.timestamp())}:R> ({age} jours)\n"
                        f"**Membres :** {member.guild.member_count}",
                   color=COLOR_WARNING if young else COLOR_SUCCESS)


@bot.event
async def on_member_remove(member):
    roles = ", ".join(r.mention for r in member.roles if r != member.guild.default_role) or "aucun"
    await send_log(member.guild, "members", "Membre parti", target=member,
                   desc=f"**Rôles :** {trunc(roles, 3500)}", color=COLOR_WARNING)


@bot.event
async def on_member_update(before, after):
    if before.nick != after.nick:
        await send_log(after.guild, "members", "Pseudo changé", target=after,
                       desc=f"**Avant :** {before.nick or before.name}\n**Après :** {after.nick or after.name}", color=COLOR_INFO)
    if before._roles != after._roles:
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        parts = []
        if added:
            parts.append("**Ajoutés :** " + ", ".join(r.mention for r in added))
        if removed:
            parts.append("**Retirés :** " + ", ".join(r.mention for r in removed))
        if parts:
            await send_log(after.guild, "roles", "Rôles modifiés", target=after, desc="\n".join(parts), color=COLOR_INFO)


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel == after.channel:
        return
    if before.channel is None and after.channel:
        action, desc, color = "Rejoint un vocal", f"**Salon :** {after.channel.mention}", COLOR_SUCCESS
    elif after.channel is None and before.channel:
        action, desc, color = "Quitte un vocal", f"**Salon :** {before.channel.mention}", COLOR_WARNING
    else:
        action, desc, color = "Change de vocal", f"**De :** {before.channel.mention}\n**Vers :** {after.channel.mention}", COLOR_INFO
    await send_log(member.guild, "voice", action, target=member, desc=desc, color=color)


@bot.event
async def on_guild_role_create(role):
    await send_log(role.guild, "roles", "Rôle créé", desc=f"**Rôle :** {role.mention} (`{role.id}`)", color=COLOR_SUCCESS)


@bot.event
async def on_guild_role_delete(role):
    if get_role_perm(role.guild.id, role.id) is not None:
        del_role_perm(role.guild.id, role.id)
    await send_log(role.guild, "roles", "Rôle supprimé", desc=f"**Rôle :** `{role.name}` (`{role.id}`)", color=COLOR_ERROR)


@bot.event
async def on_member_ban(guild, user):
    await send_log(guild, "moderation", "Membre banni", desc=f"**Membre :** {user.mention} (`{user.id}`)", color=COLOR_ERROR)


@bot.event
async def on_member_unban(guild, user):
    await send_log(guild, "moderation", "Membre débanni", desc=f"**Membre :** {user.mention} (`{user.id}`)", color=COLOR_SUCCESS)


# ========================= RANGS : OWNER / WL =========================

@bot.command(name="owner")
async def _owner(ctx, *, user_input: str = None):
    if user_input is None:
        buyers = "\n".join(f"<@{i}>" for i in BUYER_IDS) or "aucun"
        owners = "\n".join(f"<@{i}>" for i in get_users_by_rank(LEVEL_OWNER)) or "aucun"
        return await ctx.send(embed=info_embed("Owners et Buyers", f"**Buyers**\n{buyers}\n\n**Owners**\n{owners}"))
    display, uid = await get_user(ctx, user_input)
    if uid in BUYER_IDS:
        return await ctx.send(embed=info_embed("Déjà Buyer", "C'est déjà un Buyer."))
    if getattr(display, "bot", False):
        return await ctx.send(embed=error_embed("Pas possible", "On ne donne pas de rang à un bot."))
    if get_base_rank(uid) == LEVEL_OWNER:
        return await ctx.send(embed=info_embed("Déjà Owner", f"{format_user_display(display, uid)} est déjà Owner."))
    set_base_rank(uid, LEVEL_OWNER)
    await ctx.send(embed=success_embed("Owner ajouté", f"{format_user_display(display, uid)} est maintenant Owner."))


@bot.command(name="unowner")
async def _unowner(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=usage("unowner"))
    display, uid = await get_user(ctx, user_input)
    if uid == ctx.author.id:
        return await ctx.send(embed=error_embed("Pas possible", "Tu ne peux pas te retirer Owner toi-même."))
    if get_base_rank(uid) != LEVEL_OWNER:
        return await ctx.send(embed=info_embed("Pas Owner", f"{format_user_display(display, uid)} n'est pas Owner."))
    set_base_rank(uid, 0)
    await ctx.send(embed=success_embed("Owner retiré", f"{format_user_display(display, uid)} n'est plus Owner."))


@bot.command(name="wl")
async def _wl(ctx, *, user_input: str = None):
    if user_input is None:
        ids = get_users_by_rank(LEVEL_WL)
        if not ids:
            return await ctx.send(embed=info_embed("Whitelist", "Personne dans la whitelist."))
        return await send_pager(ctx, f"Whitelist ({len(ids)})", [f"<@{i}> (`{i}`)" for i in ids], per_page=15)
    display, uid = await get_user(ctx, user_input)
    if getattr(display, "bot", False):
        return await ctx.send(embed=error_embed("Pas possible", "On ne donne pas de rang à un bot."))
    if get_base_rank(uid) >= LEVEL_WL:
        return await ctx.send(embed=info_embed("Déjà fait", f"{format_user_display(display, uid)} a déjà un rang égal ou plus haut."))
    set_base_rank(uid, LEVEL_WL)
    await ctx.send(embed=success_embed("Ajouté à la whitelist", f"{format_user_display(display, uid)} est maintenant whitelist."))


@bot.command(name="unwl")
async def _unwl(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=usage("unwl"))
    display, uid = await get_user(ctx, user_input)
    if uid == ctx.author.id:
        return await ctx.send(embed=error_embed("Pas possible", "Tu ne peux pas te retirer de la whitelist toi-même."))
    if get_base_rank(uid) != LEVEL_WL:
        return await ctx.send(embed=info_embed("Pas whitelist", f"{format_user_display(display, uid)} n'est pas whitelist."))
    set_base_rank(uid, 0)
    await ctx.send(embed=success_embed("Retiré de la whitelist", f"{format_user_display(display, uid)} n'est plus whitelist."))


# ========================= BOTBAN =========================

@bot.command(name="botban")
async def _botban(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=usage("botban"))
    display, uid = await get_user(ctx, user_input)
    member = ctx.guild.get_member(uid)
    target_lvl = get_user_level(member) if member else get_base_rank(uid)
    if uid in BUYER_IDS or uid == ctx.author.id or target_lvl >= get_user_level(ctx.author):
        return await ctx.send(embed=error_embed("Pas possible", "Tu ne peux pas bot-ban un rang égal ou plus haut que toi."))
    if is_bot_banned(uid):
        return await ctx.send(embed=info_embed("Déjà fait", f"{format_user_display(display, uid)} est déjà bot-banni."))
    add_bot_ban(uid, ctx.author.id)
    await ctx.send(embed=success_embed("Bot-banni", f"{format_user_display(display, uid)} ne peut plus utiliser le bot."))


@bot.command(name="unbotban")
async def _unbotban(ctx, *, user_input: str = None):
    if not user_input:
        return await ctx.send(embed=usage("unbotban"))
    display, uid = await get_user(ctx, user_input)
    if not is_bot_banned(uid):
        return await ctx.send(embed=info_embed("Pas banni", f"{format_user_display(display, uid)} n'est pas bot-banni."))
    remove_bot_ban(uid)
    await ctx.send(embed=success_embed("Accès rendu", f"{format_user_display(display, uid)} peut de nouveau utiliser le bot."))


# ========================= PANNEAU DES PERMISSIONS =========================
# Les niveaux perm1..perm9 n'ont pas besoin d'être créés : dès que tu mets
# quelque chose dans perm7, la perm7 existe.

PANEL_ORDER = [LEVEL_OWNER, LEVEL_WL] + list(range(MAX_TIER, 0, -1)) + [LEVEL_PUBLIC]


def level_entries(guild, lvl):
    """(ids rôles, ids membres) à ce niveau."""
    if lvl in (LEVEL_OWNER, LEVEL_WL):
        return [], get_users_by_rank(lvl)
    if 1 <= lvl <= MAX_TIER:
        return get_perms_at_level(guild.id, lvl)
    return [], []


def commands_at_level(guild_id, lvl):
    return sorted(n for n in COMMANDS if get_command_level(guild_id, n) == lvl)


def editable_command(name, viewer_id):
    meta = COMMANDS[name]
    if meta["locked"]:
        return False
    if name in BUYER_RETIER_CMDS and viewer_id not in BUYER_IDS:
        return False
    return True


def _summary(guild, lvl):
    roles, users = level_entries(guild, lvl)
    cmds = commands_at_level(guild.id, lvl)
    parts = []
    if roles:
        parts.append(f"{len(roles)} rôle{'s' if len(roles) > 1 else ''}")
    if users:
        parts.append(f"{len(users)} membre{'s' if len(users) > 1 else ''}")
    if cmds:
        parts.append(f"{len(cmds)} commande{'s' if len(cmds) > 1 else ''}")
    return " · ".join(parts)


class PermPanel(OwnedView):
    def __init__(self, ctx, level=None):
        super().__init__(ctx.author.id, ctx.guild, timeout=600)
        self.author = ctx.author
        self.viewer = get_user_level(ctx.author)
        self.level = level if level is None or level <= self.viewer else None
        self.mode = "main"
        self.cat = None
        self.note = None
        self.build()

    @property
    def can_edit(self):
        return self.viewer >= LEVEL_OWNER

    # ---------- embeds ----------
    def embed(self):
        em = self._level_embed() if self.level is not None else self._home_embed()
        apply_thumbnail(em, self.guild)
        if self.note:
            em.set_footer(text=self.note)
        elif self.can_edit:
            em.set_footer(text="Tout est appliqué direct, pas besoin de valider.")
        return em

    def _home_embed(self):
        lines = []
        for lvl in PANEL_ORDER:
            if lvl > self.viewer:
                continue
            s = _summary(self.guild, lvl)
            if s:
                lines.append(f"**{level_name(lvl)}** · {s}")
        desc = "Choisis un niveau dans le menu pour voir et modifier qui y a accès et quelles commandes il ouvre.\n"
        desc += "Un niveau vide se crée tout seul dès que tu y mets un rôle, un membre ou une commande.\n\n"
        desc += "\n".join(lines) if lines else "*Rien de configuré pour l'instant.*"
        return discord.Embed(title="🔐 Permissions", description=desc, color=COLOR_DEFAULT)

    def _level_embed(self):
        lvl = self.level
        p = get_prefix_cached()
        roles, users = level_entries(self.guild, lvl)
        cmds = commands_at_level(self.guild.id, lvl)
        em = discord.Embed(title=f"🔐 {level_name(lvl)}", color=COLOR_DEFAULT)
        if self.mode == "cmds":
            em.description = ("Choisis une catégorie puis coche les commandes qui doivent demander ce niveau.\n"
                              "Décocher une commande la remet à son niveau par défaut.")
        if 1 <= lvl <= MAX_TIER:
            em.add_field(name="Rôles", value=trunc(" ".join(f"<@&{r}>" for r in roles), 1024) or "aucun", inline=False)
        if lvl != LEVEL_PUBLIC:
            em.add_field(name="Membres", value=trunc(" ".join(f"<@{u}>" for u in users), 1024) or "aucun", inline=False)
        em.add_field(name=f"Commandes ({len(cmds)})",
                     value=trunc(" ".join(f"`{p}{c}`" for c in cmds), 1024) or "aucune", inline=False)
        return em

    # ---------- composants ----------
    def build(self):
        self.clear_items()
        self.add_item(_LevelSelect(self))
        lvl = self.level
        if lvl is None:
            return
        if not self.can_edit:
            self.add_item(_button("Accueil", discord.ButtonStyle.secondary, 4, self._go_home))
            return
        if self.mode == "cmds":
            self.add_item(_CatSelect(self))
            if self.cat:
                sel = _CmdSelect.make(self)
                if sel:
                    self.add_item(sel)
            self.add_item(_button("Terminé", discord.ButtonStyle.success, 4, self._go_main))
            return
        if 1 <= lvl <= MAX_TIER:
            self.add_item(_AddRoles(self))
            self.add_item(_AddUsers(self, row=2))
        elif lvl == LEVEL_WL or (lvl == LEVEL_OWNER and self.viewer >= get_command_level(self.guild.id, "owner")):
            self.add_item(_AddUsers(self, row=1))
        rem = _RemoveSelect.make(self)
        if rem:
            self.add_item(rem)
        self.add_item(_button("Commandes", discord.ButtonStyle.primary, 4, self._go_cmds))
        self.add_item(_button("Accueil", discord.ButtonStyle.secondary, 4, self._go_home))

    async def refresh(self, interaction, note=None):
        self.note = note
        self.build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _go_home(self, interaction):
        self.level, self.mode, self.cat = None, "main", None
        await self.refresh(interaction)

    async def _go_main(self, interaction):
        self.mode, self.cat = "main", None
        await self.refresh(interaction)

    async def _go_cmds(self, interaction):
        self.mode, self.cat = "cmds", None
        await self.refresh(interaction)


def _button(label, style, row, cb):
    b = discord.ui.Button(label=label, style=style, row=row)
    b.callback = cb
    return b


class _LevelSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        opts = [discord.SelectOption(label="Vue d'ensemble", value="home", emoji="🏠", default=panel.level is None)]
        for lvl in PANEL_ORDER:
            if lvl > panel.viewer:
                continue
            opts.append(discord.SelectOption(label=level_name(lvl), value=str(lvl),
                                             description=_summary(panel.guild, lvl)[:100] or "vide",
                                             default=panel.level == lvl))
        super().__init__(placeholder="Choisis un niveau", options=opts, row=0)

    async def callback(self, interaction):
        p = self.panel
        v = self.values[0]
        p.level = None if v == "home" else int(v)
        p.mode, p.cat = "main", None
        await p.refresh(interaction)


class _AddRoles(discord.ui.RoleSelect):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(placeholder=f"Ajouter des rôles à {level_name(panel.level)}",
                         min_values=1, max_values=25, row=1)

    async def callback(self, interaction):
        p = self.panel
        n = 0
        for r in self.values:
            if r.is_default() or r.managed:
                continue
            set_role_perm(p.guild.id, r.id, p.level)
            n += 1
        await p.refresh(interaction, f"{n} rôle(s) mis en {level_name(p.level)}")


class _AddUsers(discord.ui.UserSelect):
    def __init__(self, panel, row):
        self.panel = panel
        super().__init__(placeholder=f"Ajouter des membres à {level_name(panel.level)}",
                         min_values=1, max_values=25, row=row)

    async def callback(self, interaction):
        p = self.panel
        n = 0
        for u in self.values:
            if u.bot or u.id in BUYER_IDS:
                continue
            if 1 <= p.level <= MAX_TIER:
                set_member_perm(p.guild.id, u.id, p.level)
            elif get_base_rank(u.id) < p.level:
                set_base_rank(u.id, p.level)
            else:
                continue
            n += 1
        await p.refresh(interaction, f"{n} membre(s) mis en {level_name(p.level)}")


class _RemoveSelect(discord.ui.Select):
    @classmethod
    def make(cls, panel):
        roles, users = level_entries(panel.guild, panel.level)
        opts = []
        for rid in roles:
            r = panel.guild.get_role(rid)
            opts.append(discord.SelectOption(label=trunc(f"@{r.name}" if r else f"Rôle supprimé {rid}", 100),
                                             value=f"r{rid}", description="Rôle"))
        for uid in users:
            if uid == panel.author.id and panel.level in (LEVEL_OWNER, LEVEL_WL):
                continue
            m = panel.guild.get_member(uid)
            opts.append(discord.SelectOption(label=trunc(m.display_name if m else f"Hors serveur {uid}", 100),
                                             value=f"u{uid}", description="Membre"))
        if not opts:
            return None
        return cls(panel, opts[:25])

    def __init__(self, panel, opts):
        self.panel = panel
        super().__init__(placeholder="Retirer de ce niveau...", min_values=1, max_values=len(opts),
                         options=opts, row=3)

    async def callback(self, interaction):
        p = self.panel
        for v in self.values:
            kind, xid = v[0], int(v[1:])
            if kind == "r":
                set_role_perm(p.guild.id, xid, 0)
            elif 1 <= p.level <= MAX_TIER:
                set_member_perm(p.guild.id, xid, 0)
            elif get_base_rank(xid) == p.level:
                set_base_rank(xid, 0)
        await p.refresh(interaction, f"{len(self.values)} retiré(s) de {level_name(p.level)}")


class _CatSelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        opts = []
        for cat, meta in CATEGORY_META.items():
            if any(m["cat"] == cat and editable_command(n, panel.author.id) for n, m in COMMANDS.items()):
                opts.append(discord.SelectOption(label=meta["label"], value=cat, emoji=meta["emoji"],
                                                 default=panel.cat == cat))
        super().__init__(placeholder="Choisis une catégorie de commandes", options=opts, row=1)

    async def callback(self, interaction):
        self.panel.cat = self.values[0]
        await self.panel.refresh(interaction)


class _CmdSelect(discord.ui.Select):
    @classmethod
    def make(cls, panel):
        names = [n for n, m in COMMANDS.items()
                 if m["cat"] == panel.cat and editable_command(n, panel.author.id)]
        if not names:
            return None
        return cls(panel, names[:25])

    def __init__(self, panel, names):
        self.panel = panel
        self.names = names
        p = get_prefix_cached()
        opts = []
        for n in names:
            cur = get_command_level(panel.guild.id, n)
            opts.append(discord.SelectOption(label=f"{p}{n}", value=n, default=cur == panel.level,
                                             description=f"Actuellement {level_name(cur)}"))
        super().__init__(placeholder=f"Commandes en {level_name(panel.level)}", min_values=0,
                         max_values=len(opts), options=opts, row=2)

    async def callback(self, interaction):
        p = self.panel
        gid, lvl = p.guild.id, p.level
        chosen = set(self.values)
        moved = 0
        for n in self.names:
            cur = get_command_level(gid, n)
            if n in chosen and cur != lvl:
                if COMMANDS[n]["level"] == lvl:
                    del_cmd_override(gid, n)
                else:
                    set_cmd_level(gid, n, lvl)
                moved += 1
            elif n not in chosen and cur == lvl and COMMANDS[n]["level"] != lvl:
                del_cmd_override(gid, n)
                moved += 1
        await p.refresh(interaction, f"{moved} commande(s) déplacée(s)")


async def open_perm_panel(ctx, level=None):
    view = PermPanel(ctx, level)
    view.message = await ctx.send(embed=view.embed(), view=view)


# ========================= PERMS : COMMANDES TEXTE =========================

LEVEL_WORD_RE = re.compile(r"^(perm\d{1,2}|p\d{1,2}|wl|whitelist|owner|public|pub)$", re.IGNORECASE)


def _extract_level(text):
    """Trouve le niveau n'importe où dans le texte. Renvoie (niveau brut, autres mots)."""
    text = re.sub(r"\bperm\s+(\d)", r"perm\1", text or "", flags=re.IGNORECASE)
    toks = tokenize(text)
    idx = next((i for i, t in enumerate(toks) if LEVEL_WORD_RE.match(t)), None)
    if idx is None:
        idx = next((i for i in range(len(toks) - 1, -1, -1) if toks[i].isdigit() and len(toks[i]) <= 2), None)
    if idx is None:
        return None, toks
    lvl_tok = toks.pop(idx)
    return lvl_tok, toks


def _as_commands(words):
    out = []
    for w in words:
        c = bot.get_command(w.lower().lstrip(get_prefix_cached()))
        if c is None or c.name not in COMMANDS:
            return None
        out.append(c.name)
    return out or None


async def _resolve_perm_target(ctx, words):
    """Rôle ou membre, peu importe le format. Renvoie ('role', Role) ou ('user', (obj, id))."""
    q = " ".join(words)
    if ROLE_MENTION_RE.match(q):
        return "role", await get_role(ctx, q)
    if user_id_from(q) is not None:
        r = ctx.guild.get_role(user_id_from(q)) if ID_RE.match(q) else None
        if r:
            return "role", r
        return "user", await get_user(ctx, q)
    roles = find_roles(ctx.guild, q)
    if roles:
        return "role", await pick(ctx, roles, "rôles", _role_label, _role_desc)
    members = find_members(ctx.guild, q)
    if members:
        m = await pick(ctx, members, "membres", _member_label, _member_desc)
        return "user", (m, m.id)
    raise Missing("Cible introuvable", f"`{trunc(q, 60)}` n'est ni une commande, ni un rôle, ni un membre.")


@bot.command(name="setperm", aliases=["addperm"])
async def _setperm(ctx, *, text: str = None):
    """+setperm ban perm7 · +setperm perm7 ban kick · +setperm image 4 · +setperm @moh perm2"""
    if not text:
        return await open_perm_panel(ctx)
    p = get_prefix_cached()
    lvl_tok, words = _extract_level(text)
    if lvl_tok is None or not words:
        return await ctx.send(embed=error_embed(
            "Comment l'utiliser",
            f"`{p}setperm <cible> <niveau>`, dans l'ordre que tu veux.\n"
            "Cible : une ou plusieurs commandes, un rôle ou un membre (mention, ID ou nom).\n"
            "Niveau : `perm1` à `perm9`, `public`, et pour les commandes `wl` ou `owner`.\n\n"
            f"`{p}setperm ban perm7`\n`{p}setperm perm4 kick mute warn`\n`{p}setperm Modo 4`\n\n"
            f"Ou juste `{p}perms` pour le panneau."))

    cmds = _as_commands(words)
    if cmds:
        lvl = parse_level(lvl_tok, max_level=LEVEL_OWNER)
        if lvl is None:
            raise Missing("Niveau invalide", "Pour une commande : `public`, `perm1` à `perm9`, `wl` ou `owner`.")
        done, refused = [], []
        for name in cmds:
            if not editable_command(name, ctx.author.id):
                refused.append(name)
                continue
            if COMMANDS[name]["level"] == lvl:
                del_cmd_override(ctx.guild.id, name)
            else:
                set_cmd_level(ctx.guild.id, name, lvl)
            done.append(name)
        desc = ""
        if done:
            desc += " ".join(f"`{p}{n}`" for n in done) + f" demande maintenant **{level_name(lvl)}**."
        if refused:
            desc += "\nPas modifiable : " + " ".join(f"`{n}`" for n in refused)
        return await ctx.send(embed=(success_embed if done else error_embed)("Commandes configurées" if done else "Pas possible", desc))

    lvl = parse_level(lvl_tok)
    if lvl is None:
        raise Missing("Niveau invalide",
                      f"Pour un rôle ou un membre : `public` ou `perm1` à `perm9`.\nPour la whitelist, utilise `{p}wl`.")
    kind, target = await _resolve_perm_target(ctx, words)
    if kind == "role":
        if target.is_default():
            raise Missing("Pas possible", f"Pour @everyone, mets plutôt la commande en public : `{p}setperm <commande> public`.")
        set_role_perm(ctx.guild.id, target.id, lvl)
        if lvl == 0:
            return await ctx.send(embed=success_embed("Perm retirée", f"{target.mention} repasse public."))
        return await ctx.send(embed=success_embed("Rôle configuré", f"{target.mention} est maintenant **{level_name(lvl)}**."))
    display, uid = target
    set_member_perm(ctx.guild.id, uid, lvl)
    if lvl == 0:
        return await ctx.send(embed=success_embed("Perm retirée", f"{format_user_display(display, uid)} repasse public."))
    await ctx.send(embed=success_embed("Membre configuré", f"{format_user_display(display, uid)} est maintenant **{level_name(lvl)}**."))


@bot.command(name="resetperm", aliases=["delperm"])
async def _resetperm(ctx, *, text: str = None):
    if not text:
        return await ctx.send(embed=usage("resetperm", "Une ou plusieurs commandes, un rôle ou un membre."))
    words = tokenize(text)
    cmds = _as_commands(words)
    if cmds:
        done = [n for n in cmds if editable_command(n, ctx.author.id)]
        for n in done:
            del_cmd_override(ctx.guild.id, n)
        if not done:
            raise Missing("Pas possible", "Ces commandes ne sont pas modifiables.")
        return await ctx.send(embed=success_embed("Remis par défaut", "\n".join(
            f"`{n}` : {level_name(COMMANDS[n]['level'])}" for n in done)))
    kind, target = await _resolve_perm_target(ctx, words)
    if kind == "role":
        if del_role_perm(ctx.guild.id, target.id):
            return await ctx.send(embed=success_embed("Perm retirée", f"{target.mention} repasse public."))
        return await ctx.send(embed=info_embed("Rien à faire", "Ce rôle n'avait pas de perm."))
    display, uid = target
    if del_member_perm(ctx.guild.id, uid):
        return await ctx.send(embed=success_embed("Perm retirée", f"{format_user_display(display, uid)} repasse public."))
    await ctx.send(embed=info_embed("Rien à faire", "Ce membre n'avait pas de perm."))


@bot.command(name="perms", aliases=["perm", "permissions", "panel"])
async def _perms(ctx, *, text: str = None):
    level = parse_level(text, max_level=LEVEL_OWNER) if text else None
    await open_perm_panel(ctx, level)


@bot.command(name="helpall")
async def _helpall(ctx):
    viewer = get_user_level(ctx.author)
    by_level = {}
    for name in COMMANDS:
        by_level.setdefault(get_command_level(ctx.guild.id, name), []).append(name)
    order = [LEVEL_BUYER, LEVEL_OWNER, LEVEL_WL] + list(range(MAX_TIER, -1, -1))
    levels = [lvl for lvl in order if lvl <= viewer and by_level.get(lvl)]
    if not levels:
        return await ctx.send(embed=info_embed("Commandes", "Aucune commande accessible."))
    p = get_prefix_cached()
    em = discord.Embed(title="Commandes par niveau", color=COLOR_DEFAULT)
    apply_thumbnail(em, ctx.guild)
    for lvl in levels:
        em.add_field(name=level_name(lvl), value=trunc(" ".join(f"`{p}{c}`" for c in sorted(by_level[lvl])), 1024), inline=False)
    await ctx.send(embed=em)


# ========================= ALLOW / SETLOG / PREFIX / RENEW =========================

@bot.command(name="allow")
async def _allow(ctx, *, channel_input: str = None):
    if channel_input is None:
        blocked = get_blocked_channels(ctx.guild.id)
        if not blocked:
            return await ctx.send(embed=info_embed(
                "Salons bloqués",
                "Aucun, les commandes publiques marchent partout.\n"
                f"`{get_prefix_cached()}unallow #salon` pour en bloquer un."))
        lines = []
        for cid in blocked:
            ch = ctx.guild.get_channel(cid)
            lines.append(f"{ch.mention} (`{cid}`)" if ch else f"`{cid}`")
        return await ctx.send(embed=info_embed(f"Salons bloqués ({len(blocked)})", "\n".join(lines)))
    raw = channel_input.strip()
    if ID_RE.match(raw) and not ctx.guild.get_channel(int(raw)):
        if unblock_channel(ctx.guild.id, int(raw)):
            return await ctx.send(embed=success_embed("Salon réautorisé", f"`{raw}`"))
    channel = await get_channel(ctx, raw)
    if unblock_channel(ctx.guild.id, channel.id):
        await ctx.send(embed=success_embed("Salon réautorisé", f"Les commandes publiques remarchent dans {channel.mention}."))
    else:
        await ctx.send(embed=info_embed("Déjà autorisé", f"{channel.mention} n'était pas bloqué."))


@bot.command(name="unallow")
async def _unallow(ctx, *, channel_input: str = None):
    channel = await get_channel(ctx, channel_input) if channel_input else ctx.channel
    block_channel(ctx.guild.id, channel.id)
    await ctx.send(embed=success_embed("Salon bloqué",
                                       f"Les membres ne peuvent plus utiliser les commandes publiques dans {channel.mention}.\nLe staff passe toujours."))


@bot.command(name="setlog")
async def _setlog(ctx, *, text: str = None):
    types = "`, `".join(LOG_TYPES)
    toks = tokenize(text)
    type_tok = next((t for t in toks if t.lower() in LOG_TYPES or t.lower() == "all"), None)
    if not type_tok:
        return await ctx.send(embed=usage("setlog", f"Types : `{types}` ou `all`. Sans salon, je prends celui-ci."))
    others = [t for t in toks if t is not type_tok]
    channel = await get_channel(ctx, " ".join(others)) if others else ctx.channel
    perms = channel.permissions_for(ctx.guild.me)
    if not (perms.send_messages and perms.embed_links):
        raise Missing("Pas possible", f"Je ne peux pas envoyer d'embeds dans {channel.mention}.")
    log_type = type_tok.lower()
    for t in (LOG_TYPES if log_type == "all" else [log_type]):
        set_log_channel(ctx.guild.id, t, channel.id)
    label = "Tous les logs" if log_type == "all" else f"Les logs **{log_type}**"
    await ctx.send(embed=success_embed("Logs configurés", f"{label} arrivent dans {channel.mention}"))


@bot.command(name="dellog")
async def _dellog(ctx, log_type: str = None):
    types = "`, `".join(LOG_TYPES)
    if not log_type or (log_type.lower() not in LOG_TYPES and log_type.lower() != "all"):
        return await ctx.send(embed=usage("dellog", f"Types : `{types}` ou `all`"))
    log_type = log_type.lower()
    for t in (LOG_TYPES if log_type == "all" else [log_type]):
        delete_log_channel(ctx.guild.id, t)
    await ctx.send(embed=success_embed("Logs coupés", "Tous les logs sont coupés." if log_type == "all" else f"**{log_type}** coupé."))


@bot.command(name="logs")
async def _logs(ctx):
    all_logs = get_all_log_channels(ctx.guild.id)
    lines = []
    for t in LOG_TYPES:
        cid = all_logs.get(t)
        if cid:
            ch = ctx.guild.get_channel(cid)
            lines.append(f"**{t}** : {ch.mention if ch else f'`{cid}` (salon supprimé)'}")
        else:
            lines.append(f"**{t}** : pas défini")
    await ctx.send(embed=info_embed("📜 Logs", "\n".join(lines) + f"\n\n`{get_prefix_cached()}setlog <type|all> [salon]`"))


@bot.command(name="prefix")
async def _prefix(ctx, new_prefix: str = None):
    if not new_prefix:
        return await ctx.send(embed=info_embed("Prefix", f"Le prefix est `{get_prefix_cached()}`. Tu peux aussi me mentionner."))
    if len(new_prefix) > 5:
        return await ctx.send(embed=error_embed("Trop long", "5 caractères max."))
    set_config("prefix", new_prefix)
    await ctx.send(embed=success_embed("Prefix changé", f"Maintenant c'est `{new_prefix}`"))


@bot.command(name="renew", aliases=["nuke"])
async def _renew(ctx):
    """Recrée le salon direct, sans confirmation."""
    channel = ctx.channel
    new_ch = await channel.clone(reason=f"[{ctx.author}] renew")
    try:
        await new_ch.edit(position=channel.position)
    except discord.HTTPException:
        pass
    await channel.delete(reason=f"[{ctx.author}] renew")
    if is_channel_blocked(ctx.guild.id, channel.id):
        unblock_channel(ctx.guild.id, channel.id)
        block_channel(ctx.guild.id, new_ch.id)
    for t, cid in get_all_log_channels(ctx.guild.id).items():
        if cid == channel.id:
            set_log_channel(ctx.guild.id, t, new_ch.id)
    try:
        await new_ch.send(f"Salon recréé par {ctx.author.mention}", delete_after=10)
    except discord.HTTPException:
        pass


# ========================= HELP =========================

def cat_visible_commands(guild_id, cat, viewer_level):
    out = [(name, meta, get_command_level(guild_id, name)) for name, meta in COMMANDS.items() if meta["cat"] == cat]
    out = [x for x in out if viewer_level >= x[2]]
    out.sort(key=lambda x: (x[2], x[0]))
    return out


def build_help_home_embed(guild, viewer_level):
    p = get_prefix_cached()
    em = discord.Embed(
        title="Moh",
        description=f"Prefix `{p}` · Ton niveau : **{level_name(viewer_level)}**\n\n"
                    "Choisis une catégorie dans le menu.\n"
                    "Pour un membre ou un rôle, mets une mention, un ID ou juste le nom.",
        color=COLOR_DEFAULT)
    if bot.user:
        em.set_thumbnail(url=bot.user.display_avatar.url)
    lines = []
    total = 0
    for k, meta in CATEGORY_META.items():
        n = len(cat_visible_commands(guild.id, k, viewer_level))
        total += n
        if n:
            lines.append(f"{meta['emoji']} **{meta['label']}** · {meta['sub']}")
    if lines:
        em.add_field(name="Catégories", value="\n".join(lines), inline=False)
    em.set_footer(text=f"{total} commandes dispo pour toi")
    return em


def build_category_embed(guild, cat, viewer_level):
    p = get_prefix_cached()
    meta = CATEGORY_META[cat]
    items = cat_visible_commands(guild.id, cat, viewer_level)
    em = discord.Embed(title=f"{meta['emoji']} {meta['label']}", color=COLOR_DEFAULT)
    apply_thumbnail(em, guild)
    if not items:
        em.description = "Tu n'as pas le niveau pour cette catégorie."
        return em
    lines = []
    for name, cmeta, lvl in items:
        line = f"`{p}{name}" + (f" {cmeta['args']}" if cmeta["args"] else "") + f"`\n{cmeta['desc']}"
        if lvl > 0:
            line += f" · *{level_name(lvl)}*"
        lines.append(line)
    em.description = trunc("\n".join(lines), 4000)
    return em


class HelpSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent_view = parent
        options = [discord.SelectOption(label="Accueil", emoji="🏠", value="home")]
        for cat, meta in CATEGORY_META.items():
            if cat_visible_commands(parent.guild.id, cat, parent.viewer_level):
                options.append(discord.SelectOption(label=meta["label"], emoji=meta["emoji"],
                                                    value=cat, description=meta["sub"][:100]))
        super().__init__(placeholder="Choisis une catégorie", min_values=1, max_values=1, options=options[:25])

    async def callback(self, interaction):
        v = self.parent_view
        val = self.values[0]
        em = (build_help_home_embed(v.guild, v.viewer_level) if val == "home"
              else build_category_embed(v.guild, val, v.viewer_level))
        await interaction.response.edit_message(embed=em, view=v)


class HelpView(OwnedView):
    def __init__(self, author_id, guild, viewer_level):
        super().__init__(author_id, guild, timeout=180)
        self.viewer_level = viewer_level
        self.add_item(HelpSelect(self))


@bot.command(name="help", aliases=["aide", "h"])
async def _help(ctx):
    level = get_user_level(ctx.author)
    view = HelpView(ctx.author.id, ctx.guild, level)
    view.message = await ctx.send(embed=build_help_home_embed(ctx.guild, level), view=view)


# ========================= RUN =========================

if __name__ == "__main__":
    try:
        import uvloop  # boucle d'événements bien plus rapide (Linux / Railway)
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        log.info("uvloop activé")
    except ImportError:
        pass
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

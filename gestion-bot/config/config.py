"""Global configuration constants for the bot."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# === Environment ===
TOKEN: Final[str] = os.getenv("DISCORD_TOKEN", "")
OWNER_ID: Final[int] = int(os.getenv("OWNER_ID", "0") or "0")
DEFAULT_PREFIX: Final[str] = os.getenv("DEFAULT_PREFIX", "+")
DB_PATH: Final[str] = os.getenv("DB_PATH", "./data/bot.db")

# Ensure parent directory of DB exists (only if not the root)
_db_parent = Path(DB_PATH).parent
if str(_db_parent) and str(_db_parent) != ".":
    try:
        _db_parent.mkdir(parents=True, exist_ok=True)
    except (PermissionError, OSError):
        pass

# === Bot metadata ===
BOT_NAME: Final[str] = "Gestion Bot"
BOT_VERSION: Final[str] = "1.0.0"
BOT_AUTHOR: Final[str] = "Inspired by CrowBots Gestion V2"

# === Default colors (hex int) ===
COLOR_DEFAULT: Final[int] = 0x2B2D31  # Discord dark embed
COLOR_SUCCESS: Final[int] = 0x57F287  # green
COLOR_ERROR: Final[int] = 0xED4245    # red
COLOR_WARNING: Final[int] = 0xFEE75C  # yellow
COLOR_INFO: Final[int] = 0x5865F2     # blurple
COLOR_MOD: Final[int] = 0xEB459E      # pink for mod actions

# === Permission system ===
MAX_PERM_LEVEL: Final[int] = 9
MIN_PERM_LEVEL: Final[int] = 1

# === Limits ===
MAX_CLEAR_MESSAGES: Final[int] = 100
MAX_WARNS_DISPLAY: Final[int] = 25
LEVEL_XP_COOLDOWN: Final[int] = 60  # seconds between XP gains
LEVEL_XP_MIN: Final[int] = 15
LEVEL_XP_MAX: Final[int] = 25

# === Anti-raid defaults ===
ANTIRAID_THRESHOLD: Final[int] = 8     # joins
ANTIRAID_WINDOW: Final[int] = 10       # seconds
ANTIRAID_MIN_ACCOUNT_AGE_DAYS: Final[int] = 7

# === Default required perm level per command name ===
# Commands not listed here default to level 0 (everyone) unless decorated
DEFAULT_COMMAND_PERMS: dict[str, int] = {
    # Moderation
    "ban": 3, "unban": 3, "kick": 3, "mute": 3, "unmute": 3,
    "warn": 3, "warns": 1, "delwarn": 5, "clear": 3,
    "lock": 5, "unlock": 5, "slowmode": 5,
    # Automod
    "antispam": 5, "antilink": 5, "antibadword": 5,
    "antimassmention": 5, "anticaps": 5, "whitelist": 5,
    # Antiraid
    "antiraid": 7, "lockdown": 7, "unlockdown": 7,
    # Tickets
    "ticket": 5,
    # Logs
    "setlog": 7, "dellog": 7, "logs": 7,
    # Welcome
    "welcome": 5, "goodbye": 5, "autorole": 5,
    # Levels
    "rank": 1, "leaderboard": 1, "setlevel": 7, "levelroles": 5, "xpchannel": 5,
    # Reminders
    "remind": 1, "reminders": 1, "delreminder": 1,
    # Custom commands
    "addcmd": 5, "delcmd": 5, "listcmd": 1, "editcmd": 5,
    # Messages editor
    "setmsg": 7, "resetmsg": 7, "listmsg": 7,
    # Config
    "setprefix": 9, "setcolor": 9, "setstatus": 9, "config": 5,
    # Perm system
    "set": 9, "del": 9, "changeall": 9, "perms": 1, "helpall": 1,
}

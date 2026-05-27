-- ============================================================
-- Gestion Bot - SQLite schema
-- All tables use guild_id to support multi-server deployment.
-- ============================================================

-- Guild-level settings (prefix, color, etc.)
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id      INTEGER PRIMARY KEY,
    prefix        TEXT    NOT NULL DEFAULT '+',
    embed_color   INTEGER NOT NULL DEFAULT 2829361,
    welcome_channel    INTEGER,
    welcome_message    TEXT,
    welcome_dm_enabled INTEGER NOT NULL DEFAULT 0,
    welcome_dm_message TEXT,
    goodbye_channel    INTEGER,
    goodbye_message    TEXT,
    antispam_enabled       INTEGER NOT NULL DEFAULT 0,
    antispam_messages      INTEGER NOT NULL DEFAULT 5,
    antispam_seconds       INTEGER NOT NULL DEFAULT 5,
    antilink_enabled       INTEGER NOT NULL DEFAULT 0,
    antilink_mode          TEXT    NOT NULL DEFAULT 'all',
    antibadword_enabled    INTEGER NOT NULL DEFAULT 0,
    antimassmention_enabled INTEGER NOT NULL DEFAULT 0,
    antimassmention_count   INTEGER NOT NULL DEFAULT 5,
    anticaps_enabled       INTEGER NOT NULL DEFAULT 0,
    antiraid_enabled       INTEGER NOT NULL DEFAULT 0,
    lockdown_active        INTEGER NOT NULL DEFAULT 0
);

-- Permission system: assigns a level to a role or user
CREATE TABLE IF NOT EXISTS permissions (
    guild_id    INTEGER NOT NULL,
    level       INTEGER NOT NULL,
    target_id   INTEGER NOT NULL,
    target_type TEXT    NOT NULL, -- 'role' or 'user'
    PRIMARY KEY (guild_id, target_id, target_type)
);

-- Per-command overrides
CREATE TABLE IF NOT EXISTS command_perms (
    guild_id     INTEGER NOT NULL,
    command_name TEXT    NOT NULL,
    level        INTEGER NOT NULL,
    PRIMARY KEY (guild_id, command_name)
);

-- Per-command custom user/role grants (overrides default)
CREATE TABLE IF NOT EXISTS command_grants (
    guild_id     INTEGER NOT NULL,
    command_name TEXT    NOT NULL,
    target_id    INTEGER NOT NULL,
    target_type  TEXT    NOT NULL,
    PRIMARY KEY (guild_id, command_name, target_id, target_type)
);

-- Moderation sanctions history
CREATE TABLE IF NOT EXISTS sanctions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    moderator_id  INTEGER NOT NULL,
    type          TEXT    NOT NULL,
    reason        TEXT,
    duration      INTEGER,
    timestamp     INTEGER NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sanctions_guild_user ON sanctions(guild_id, user_id);
CREATE INDEX IF NOT EXISTS idx_sanctions_active ON sanctions(active, type);

-- Tickets
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_config (
    guild_id        INTEGER PRIMARY KEY,
    category_id     INTEGER,
    support_role_id INTEGER,
    log_channel_id  INTEGER,
    panel_channel_id INTEGER,
    panel_message_id INTEGER
);

-- Logs configuration
CREATE TABLE IF NOT EXISTS logs_config (
    guild_id    INTEGER NOT NULL,
    log_type    TEXT    NOT NULL,
    channel_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, log_type)
);

-- Autorole on join
CREATE TABLE IF NOT EXISTS autoroles (
    guild_id INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

-- Levels / XP
CREATE TABLE IF NOT EXISTS levels (
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    xp           INTEGER NOT NULL DEFAULT 0,
    level        INTEGER NOT NULL DEFAULT 0,
    last_message INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_levels_xp ON levels(guild_id, xp DESC);

CREATE TABLE IF NOT EXISTS level_roles (
    guild_id INTEGER NOT NULL,
    level    INTEGER NOT NULL,
    role_id  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);

CREATE TABLE IF NOT EXISTS xp_disabled_channels (
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

-- Reminders
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER,
    user_id     INTEGER NOT NULL,
    channel_id  INTEGER NOT NULL,
    message     TEXT    NOT NULL,
    remind_at   INTEGER NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(remind_at);

-- Custom commands
CREATE TABLE IF NOT EXISTS custom_commands (
    guild_id INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    response TEXT    NOT NULL,
    created_by INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (guild_id, name)
);

-- Editable messages
CREATE TABLE IF NOT EXISTS custom_messages (
    guild_id INTEGER NOT NULL,
    key      TEXT    NOT NULL,
    value    TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);

-- Automod: bad words
CREATE TABLE IF NOT EXISTS badwords (
    guild_id INTEGER NOT NULL,
    word     TEXT    NOT NULL,
    PRIMARY KEY (guild_id, word)
);

-- Automod: whitelist (immune to automod)
CREATE TABLE IF NOT EXISTS automod_whitelist (
    guild_id    INTEGER NOT NULL,
    target_id   INTEGER NOT NULL,
    target_type TEXT    NOT NULL,
    PRIMARY KEY (guild_id, target_id, target_type)
);

-- Automod infraction counters
CREATE TABLE IF NOT EXISTS automod_infractions (
    guild_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    last_at   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

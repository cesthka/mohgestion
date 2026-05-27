"""Default editable messages used by the bot (FR)."""
from __future__ import annotations

DEFAULT_MESSAGES: dict[str, str] = {
    "welcome": "👋 Bienvenue {user} sur **{server}** ! Tu es notre **{count}ème** membre.",
    "welcome_dm": "Bienvenue sur **{server}** ! Lis bien le règlement avant de commencer.",
    "goodbye": "👋 **{user}** a quitté le serveur. Nous sommes maintenant {count} membres.",
    "ban": "🔨 **{user}** a été banni par **{moderator}**.\n**Raison :** {reason}",
    "kick": "👢 **{user}** a été expulsé par **{moderator}**.\n**Raison :** {reason}",
    "mute": "🔇 **{user}** a été rendu muet pour **{duration}** par **{moderator}**.\n**Raison :** {reason}",
    "unmute": "🔊 **{user}** a été démuté par **{moderator}**.",
    "warn": "⚠️ **{user}** a été averti par **{moderator}**.\n**Raison :** {reason}",
    "unban": "✅ **{user}** a été débanni par **{moderator}**.",
    "antiraid_triggered": "🚨 **Alerte anti-raid activée !** Le serveur est temporairement verrouillé.",
    "lockdown_on": "🔒 Le serveur a été placé en mode lockdown.",
    "lockdown_off": "🔓 Le mode lockdown a été désactivé.",
    "ticket_created": "Ton ticket a été créé : {channel}",
    "ticket_closed": "🔒 Ce ticket a été fermé.",
}

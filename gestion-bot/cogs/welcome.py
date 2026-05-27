"""Welcome / goodbye messages and autoroles."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands

from config.default_messages import DEFAULT_MESSAGES
from utils.checks import perm

log = logging.getLogger(__name__)


def format_message(template: str, member: discord.Member) -> str:
    """Replace {user}, {server}, {count} placeholders."""
    return (
        template.replace("{user}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count or 0))
    )


class Welcome(commands.Cog, name="Bienvenue"):
    """Welcome / goodbye messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]

    # --- Listeners -----------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await self.db.get_config(member.guild.id)
        if not config:
            return

        # Welcome message
        if config["welcome_channel"]:
            chan = member.guild.get_channel(int(config["welcome_channel"]))
            if isinstance(chan, discord.TextChannel):
                template = config["welcome_message"] or DEFAULT_MESSAGES["welcome"]
                try:
                    await chan.send(format_message(template, member))
                except discord.HTTPException:
                    pass

        # DM welcome
        if config["welcome_dm_enabled"]:
            template = config["welcome_dm_message"] or DEFAULT_MESSAGES["welcome_dm"]
            try:
                await member.send(format_message(template, member))
            except discord.HTTPException:
                pass

        # Autoroles
        rows = await self.db.fetchall(
            "SELECT role_id FROM autoroles WHERE guild_id = ?",
            (member.guild.id,),
        )
        for r in rows:
            role = member.guild.get_role(int(r["role_id"]))
            if role and role < member.guild.me.top_role:
                try:
                    await member.add_roles(role, reason="Autorole")
                except discord.Forbidden:
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return
        config = await self.db.get_config(member.guild.id)
        if not config or not config["goodbye_channel"]:
            return
        chan = member.guild.get_channel(int(config["goodbye_channel"]))
        if not isinstance(chan, discord.TextChannel):
            return
        template = config["goodbye_message"] or DEFAULT_MESSAGES["goodbye"]
        msg = (
            template.replace("{user}", str(member))
            .replace("{server}", member.guild.name)
            .replace("{count}", str(member.guild.member_count or 0))
        )
        try:
            await chan.send(msg)
        except discord.HTTPException:
            pass

    # --- Welcome commands ----------------------------------------------

    @commands.group(name="welcome", invoke_without_command=True)
    @perm(5)
    async def welcome(self, ctx: commands.Context) -> None:
        """Sous-commandes : `channel`, `message`, `dm`, `test`."""
        await ctx.send(embed=self.embed.info(
            "Sous-commandes : `welcome channel <#salon>`, `welcome message <texte>`, "
            "`welcome dm on|off`, `welcome dm message <texte>`, `welcome test`."
        ))

    @welcome.command(name="channel")
    @perm(5)
    async def welcome_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.db.update_config(ctx.guild.id, welcome_channel=channel.id)
        await ctx.send(embed=self.embed.success(f"Salon de bienvenue : {channel.mention}"))

    @welcome.command(name="message")
    @perm(5)
    async def welcome_message(self, ctx: commands.Context, *, message: str) -> None:
        await self.db.update_config(ctx.guild.id, welcome_message=message)
        await ctx.send(embed=self.embed.success(
            "Message de bienvenue mis à jour. Variables : `{user}`, `{server}`, `{count}`."
        ))

    @welcome.group(name="dm", invoke_without_command=True)
    @perm(5)
    async def welcome_dm(self, ctx: commands.Context, mode: str) -> None:
        m = mode.lower()
        if m == "on":
            await self.db.update_config(ctx.guild.id, welcome_dm_enabled=1)
            await ctx.send(embed=self.embed.success("DM de bienvenue activé."))
        elif m == "off":
            await self.db.update_config(ctx.guild.id, welcome_dm_enabled=0)
            await ctx.send(embed=self.embed.success("DM de bienvenue désactivé."))
        else:
            await ctx.send(embed=self.embed.error("Usage : `welcome dm on|off`."))

    @welcome_dm.command(name="message")
    @perm(5)
    async def welcome_dm_message(self, ctx: commands.Context, *, message: str) -> None:
        await self.db.update_config(ctx.guild.id, welcome_dm_message=message)
        await ctx.send(embed=self.embed.success("Message DM mis à jour."))

    @welcome.command(name="test")
    @perm(5)
    async def welcome_test(self, ctx: commands.Context) -> None:
        """Simule l'arrivée d'un membre pour tester."""
        await self.on_member_join(ctx.author)  # type: ignore[arg-type]
        await ctx.send(embed=self.embed.success("Test envoyé."))

    # --- Goodbye -------------------------------------------------------

    @commands.group(name="goodbye", invoke_without_command=True)
    @perm(5)
    async def goodbye(self, ctx: commands.Context) -> None:
        await ctx.send(embed=self.embed.info(
            "Sous-commandes : `goodbye channel <#salon>`, `goodbye message <texte>`."
        ))

    @goodbye.command(name="channel")
    @perm(5)
    async def goodbye_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        await self.db.update_config(ctx.guild.id, goodbye_channel=channel.id)
        await ctx.send(embed=self.embed.success(f"Salon d'au-revoir : {channel.mention}"))

    @goodbye.command(name="message")
    @perm(5)
    async def goodbye_message(self, ctx: commands.Context, *, message: str) -> None:
        await self.db.update_config(ctx.guild.id, goodbye_message=message)
        await ctx.send(embed=self.embed.success(
            "Message de départ mis à jour. Variables : `{user}`, `{server}`, `{count}`."
        ))

    # --- Autoroles -----------------------------------------------------

    @commands.group(name="autorole", invoke_without_command=True)
    @perm(5)
    async def autorole(self, ctx: commands.Context) -> None:
        rows = await self.db.fetchall(
            "SELECT role_id FROM autoroles WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        if not rows:
            await ctx.send(embed=self.embed.info("Aucun autorole défini."))
            return
        roles = []
        for r in rows:
            role = ctx.guild.get_role(int(r["role_id"]))
            roles.append(role.mention if role else f"<@&{r['role_id']}> (introuvable)")
        await ctx.send(embed=self.embed.custom(
            description="\n".join(roles), title="🎭 Autoroles"
        ))

    @autorole.command(name="add")
    @perm(5)
    async def autorole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        if role >= ctx.guild.me.top_role:
            await ctx.send(embed=self.embed.error(
                "Je ne peux pas attribuer ce rôle (il est plus haut que le mien)."
            ))
            return
        await self.db.execute(
            "INSERT OR IGNORE INTO autoroles (guild_id, role_id) VALUES (?, ?)",
            (ctx.guild.id, role.id),
        )
        await ctx.send(embed=self.embed.success(f"{role.mention} ajouté aux autoroles."))

    @autorole.command(name="del")
    @perm(5)
    async def autorole_del(self, ctx: commands.Context, role: discord.Role) -> None:
        await self.db.execute(
            "DELETE FROM autoroles WHERE guild_id = ? AND role_id = ?",
            (ctx.guild.id, role.id),
        )
        await ctx.send(embed=self.embed.success(f"{role.mention} retiré des autoroles."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))

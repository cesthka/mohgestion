"""Ticket system with button-based panel and HTML transcripts."""
from __future__ import annotations

import html
import io
import logging
import time
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands
from discord import ui

from utils.checks import perm

log = logging.getLogger(__name__)


class TicketPanelView(ui.View):
    """Persistent view with the 'Open a ticket' button."""

    def __init__(self, cog: "Tickets") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(
        label="Ouvrir un ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="gestionbot:ticket:open",
    )
    async def open_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self.cog.open_ticket(interaction)


class TicketControlView(ui.View):
    """View shown inside a ticket: close button."""

    def __init__(self, cog: "Tickets") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(
        label="Fermer",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="gestionbot:ticket:close",
    )
    async def close_button(self, interaction: discord.Interaction, button: ui.Button) -> None:
        await self.cog.close_ticket_interaction(interaction)


class Tickets(commands.Cog, name="Tickets"):
    """Ticket system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        # Register persistent views so buttons survive restarts
        self.bot.add_view(TicketPanelView(self))
        self.bot.add_view(TicketControlView(self))

    # --- Setup wizard --------------------------------------------------

    @commands.group(name="ticket", invoke_without_command=True)
    @perm(5)
    async def ticket(self, ctx: commands.Context) -> None:
        """Voir les sous-commandes : `setup`, `close`, `add`, `remove`."""
        await ctx.send(embed=self.embed.info(
            "Sous-commandes : `ticket setup`, `ticket close`, "
            "`ticket add <@user>`, `ticket remove <@user>`."
        ))

    @ticket.command(name="setup")
    @perm(5)
    @commands.bot_has_permissions(manage_channels=True)
    async def ticket_setup(
        self,
        ctx: commands.Context,
        category: Optional[discord.CategoryChannel] = None,
        support_role: Optional[discord.Role] = None,
    ) -> None:
        """Crée le panel de tickets dans le salon courant.

        `category` : catégorie où créer les tickets (optionnel).
        `support_role` : rôle qui peut voir/gérer les tickets (optionnel).
        """
        # Save config
        await self.db.execute(
            "INSERT OR REPLACE INTO ticket_config "
            "(guild_id, category_id, support_role_id, panel_channel_id, panel_message_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                ctx.guild.id,
                category.id if category else None,
                support_role.id if support_role else None,
                ctx.channel.id,
                None,
            ),
        )

        panel_embed = self.embed.custom(
            title="🎫 Support",
            description=(
                "Besoin d'aide ? Clique sur le bouton ci-dessous pour ouvrir un ticket "
                "privé avec l'équipe.\n\n"
                "Merci de décrire ton problème clairement une fois le ticket ouvert."
            ),
        )
        view = TicketPanelView(self)
        panel = await ctx.channel.send(embed=panel_embed, view=view)

        await self.db.execute(
            "UPDATE ticket_config SET panel_message_id = ? WHERE guild_id = ?",
            (panel.id, ctx.guild.id),
        )
        await ctx.send(embed=self.embed.success("Panel de tickets installé."))

    # --- Open / close logic --------------------------------------------

    async def open_ticket(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        user = interaction.user

        # Check existing open ticket for this user
        existing = await self.db.fetchone(
            "SELECT channel_id FROM tickets "
            "WHERE guild_id = ? AND user_id = ? AND status = 'open'",
            (guild.id, user.id),
        )
        if existing:
            ch = guild.get_channel(int(existing["channel_id"]))
            if ch:
                await interaction.response.send_message(
                    embed=self.embed.warning(f"Tu as déjà un ticket ouvert : {ch.mention}"),
                    ephemeral=True,
                )
                return

        config = await self.db.fetchone(
            "SELECT category_id, support_role_id FROM ticket_config WHERE guild_id = ?",
            (guild.id,),
        )
        category = None
        support_role = None
        if config:
            if config["category_id"]:
                category = guild.get_channel(int(config["category_id"]))
            if config["support_role_id"]:
                support_role = guild.get_role(int(config["support_role_id"]))

        # Permissions
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
                embed_links=True, read_message_history=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True,
                manage_messages=True, attach_files=True, embed_links=True,
                read_message_history=True,
            ),
        }
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
                embed_links=True, read_message_history=True, manage_messages=True,
            )

        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name}".lower()[:90],
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                topic=f"Ticket de {user} ({user.id})",
                reason=f"Ticket ouvert par {user}",
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=self.embed.error("Je n'ai pas la permission de créer un salon."),
                ephemeral=True,
            )
            return

        await self.db.execute(
            "INSERT INTO tickets (guild_id, channel_id, user_id, status, created_at) "
            "VALUES (?, ?, ?, 'open', ?)",
            (guild.id, channel.id, user.id, int(time.time())),
        )

        opener_embed = self.embed.custom(
            title="🎫 Nouveau ticket",
            description=(
                f"Bonjour {user.mention}, l'équipe va te répondre rapidement.\n"
                "Décris ton problème en détail.\n\n"
                "Clique sur **Fermer** quand le sujet est résolu."
            ),
        )
        view = TicketControlView(self)
        mention_line = f"{user.mention}"
        if support_role:
            mention_line += f" {support_role.mention}"
        await channel.send(content=mention_line, embed=opener_embed, view=view)

        await interaction.response.send_message(
            embed=self.embed.success(f"Ton ticket a été créé : {channel.mention}"),
            ephemeral=True,
        )

    async def close_ticket_interaction(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=self.embed.error("Cette action ne peut être faite ici."),
                ephemeral=True,
            )
            return

        ticket = await self.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'",
            (interaction.channel.id,),
        )
        if not ticket:
            await interaction.response.send_message(
                embed=self.embed.error("Ce salon n'est pas un ticket ouvert."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=self.embed.warning("Fermeture du ticket dans 5 secondes...")
        )
        await self._close_and_transcript(interaction.channel, ticket, interaction.user)

    @ticket.command(name="close")
    @perm(1)
    async def ticket_close(self, ctx: commands.Context) -> None:
        ticket = await self.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'",
            (ctx.channel.id,),
        )
        if not ticket:
            await ctx.send(embed=self.embed.error("Ce salon n'est pas un ticket ouvert."))
            return
        await ctx.send(embed=self.embed.warning("Fermeture du ticket dans 5 secondes..."))
        await self._close_and_transcript(ctx.channel, ticket, ctx.author)

    async def _close_and_transcript(
        self,
        channel: discord.TextChannel,
        ticket_row,
        closer: discord.User | discord.Member,
    ) -> None:
        import asyncio
        await asyncio.sleep(5)

        # Build transcript
        transcript_text = await self._build_transcript_html(channel)
        file = discord.File(
            io.BytesIO(transcript_text.encode("utf-8")),
            filename=f"transcript-{channel.name}.html",
        )

        # Send transcript to opener
        opener = self.bot.get_user(int(ticket_row["user_id"]))
        if opener:
            try:
                await opener.send(
                    embed=self.embed.info(
                        f"Voici le transcript de ton ticket sur **{channel.guild.name}**."
                    ),
                    file=file,
                )
            except discord.HTTPException:
                pass

        # Send to ticket log channel
        log_chan_id = await self.db.fetchval(
            "SELECT log_channel_id FROM ticket_config WHERE guild_id = ?",
            (channel.guild.id,),
        )
        if log_chan_id:
            log_chan = channel.guild.get_channel(int(log_chan_id))
            if log_chan:
                # Need a fresh file since the first was consumed
                file2 = discord.File(
                    io.BytesIO(transcript_text.encode("utf-8")),
                    filename=f"transcript-{channel.name}.html",
                )
                try:
                    await log_chan.send(
                        embed=self.embed.info(
                            f"Ticket fermé : `{channel.name}`\n"
                            f"Ouvert par : <@{ticket_row['user_id']}>\n"
                            f"Fermé par : {closer.mention}"
                        ),
                        file=file2,
                    )
                except discord.HTTPException:
                    pass

        await self.db.execute(
            "UPDATE tickets SET status = 'closed' WHERE id = ?",
            (int(ticket_row["id"]),),
        )

        try:
            await channel.delete(reason=f"Ticket fermé par {closer}")
        except discord.HTTPException:
            pass

    async def _build_transcript_html(self, channel: discord.TextChannel) -> str:
        """Build an HTML transcript of the channel."""
        messages: list[discord.Message] = []
        async for m in channel.history(limit=1000, oldest_first=True):
            messages.append(m)

        rows = []
        for m in messages:
            content = html.escape(m.content or "")
            timestamp = m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
            author = html.escape(str(m.author))
            avatar = m.author.display_avatar.url
            attachments = ""
            if m.attachments:
                links = "<br>".join(
                    f'<a href="{html.escape(a.url)}">{html.escape(a.filename)}</a>'
                    for a in m.attachments
                )
                attachments = f'<div class="attachments">{links}</div>'
            embeds = "<br>".join(
                f'<div class="embed">[Embed: {html.escape(e.title or "sans titre")}]</div>'
                for e in m.embeds
            )
            rows.append(f"""
            <div class="msg">
              <img class="avatar" src="{html.escape(avatar)}" />
              <div class="body">
                <div class="meta"><span class="author">{author}</span>
                  <span class="ts">{timestamp}</span></div>
                <div class="content">{content}</div>
                {attachments}
                {embeds}
              </div>
            </div>
            """)

        guild_name = html.escape(channel.guild.name)
        chan_name = html.escape(channel.name)
        generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Transcript - {chan_name}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #2b2d31; color: #dcddde; margin: 0; padding: 20px; }}
  .header {{ background: #1e1f22; padding: 20px; border-radius: 8px;
            margin-bottom: 20px; }}
  .header h1 {{ margin: 0 0 8px 0; }}
  .header p {{ margin: 4px 0; opacity: 0.7; font-size: 14px; }}
  .msg {{ display: flex; margin: 12px 0; }}
  .avatar {{ width: 40px; height: 40px; border-radius: 50%; margin-right: 12px; }}
  .body {{ flex: 1; }}
  .meta {{ font-size: 14px; }}
  .author {{ font-weight: bold; color: #fff; }}
  .ts {{ margin-left: 8px; opacity: 0.5; font-size: 12px; }}
  .content {{ margin-top: 4px; white-space: pre-wrap; word-break: break-word; }}
  .attachments {{ margin-top: 6px; font-size: 13px; }}
  .embed {{ background: #1e1f22; padding: 6px 10px; margin-top: 6px;
            border-left: 4px solid #5865f2; border-radius: 4px; font-size: 13px; }}
  a {{ color: #00aff4; text-decoration: none; }}
</style>
</head>
<body>
  <div class="header">
    <h1>Transcript - #{chan_name}</h1>
    <p>Serveur : {guild_name}</p>
    <p>Généré le : {generated}</p>
    <p>{len(messages)} message(s)</p>
  </div>
  {''.join(rows)}
</body>
</html>"""

    # --- Add / remove members in a ticket ------------------------------

    @ticket.command(name="add")
    @perm(1)
    async def ticket_add(self, ctx: commands.Context, member: discord.Member) -> None:
        ticket = await self.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'",
            (ctx.channel.id,),
        )
        if not ticket:
            await ctx.send(embed=self.embed.error("Ce salon n'est pas un ticket ouvert."))
            return
        try:
            await ctx.channel.set_permissions(
                member, view_channel=True, send_messages=True, read_message_history=True,
            )
            await ctx.send(embed=self.embed.success(f"{member.mention} ajouté au ticket."))
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))

    @ticket.command(name="remove")
    @perm(1)
    async def ticket_remove(self, ctx: commands.Context, member: discord.Member) -> None:
        ticket = await self.db.fetchone(
            "SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'",
            (ctx.channel.id,),
        )
        if not ticket:
            await ctx.send(embed=self.embed.error("Ce salon n'est pas un ticket ouvert."))
            return
        if int(ticket["user_id"]) == member.id:
            await ctx.send(embed=self.embed.error(
                "Tu ne peux pas retirer le créateur du ticket."
            ))
            return
        try:
            await ctx.channel.set_permissions(member, overwrite=None)
            await ctx.send(embed=self.embed.success(f"{member.mention} retiré du ticket."))
        except discord.Forbidden:
            await ctx.send(embed=self.embed.error("Je n'ai pas la permission."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))

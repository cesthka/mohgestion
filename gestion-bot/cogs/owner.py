"""Owner-only utility commands (reload, sync, shutdown, eval, stats)."""
from __future__ import annotations

import asyncio
import io
import logging
import platform
import sys
import textwrap
import time
import traceback
from contextlib import redirect_stdout
from typing import Optional

import discord
import psutil
from discord.ext import commands

from config.config import BOT_NAME, BOT_VERSION
from utils.checks import owner_only

log = logging.getLogger(__name__)


class Owner(commands.Cog):
    """Owner-only utilities."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.embed = bot.embed
        self.start_time: float = time.time()
        self._last_eval_result: object = None

    # ------------------------------------------------------------------
    @commands.command(name="reload")
    @owner_only()
    async def reload(self, ctx: commands.Context, cog: str) -> None:
        """Reload a cog at runtime."""
        ext = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self.bot.reload_extension(ext)
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(ext)
            except Exception as e:
                await ctx.send(embed=self.embed.error(f"Échec du chargement : `{e}`"))
                return
        except Exception as e:
            await ctx.send(embed=self.embed.error(f"Échec du reload : `{e}`"))
            return
        await ctx.send(embed=self.embed.success(f"Cog `{ext}` rechargé."))

    @commands.command(name="sync")
    @owner_only()
    async def sync(self, ctx: commands.Context, scope: Optional[str] = None) -> None:
        """Sync slash commands. Use 'guild' to sync to current guild only."""
        try:
            if scope == "guild" and ctx.guild is not None:
                self.bot.tree.copy_global_to(guild=ctx.guild)
                synced = await self.bot.tree.sync(guild=ctx.guild)
                await ctx.send(
                    embed=self.embed.success(
                        f"{len(synced)} commande(s) slash synchronisée(s) sur ce serveur."
                    )
                )
            else:
                synced = await self.bot.tree.sync()
                await ctx.send(
                    embed=self.embed.success(
                        f"{len(synced)} commande(s) slash synchronisée(s) globalement."
                    )
                )
        except Exception as e:
            await ctx.send(embed=self.embed.error(f"Erreur de sync : `{e}`"))

    @commands.command(name="shutdown")
    @owner_only()
    async def shutdown(self, ctx: commands.Context) -> None:
        """Gracefully stop the bot."""
        await ctx.send(embed=self.embed.warning("Arrêt du bot..."))
        await self.bot.close()

    # ------------------------------------------------------------------
    @commands.command(name="eval")
    @owner_only()
    async def _eval(self, ctx: commands.Context, *, body: str) -> None:
        """Evaluate Python code (owner only). Use with care."""
        # Strip code-fences if present
        if body.startswith("```") and body.endswith("```"):
            body = "\n".join(body.split("\n")[1:-1])
        body = body.strip("` \n")

        env: dict = {
            "bot": self.bot,
            "ctx": ctx,
            "channel": ctx.channel,
            "author": ctx.author,
            "guild": ctx.guild,
            "message": ctx.message,
            "db": self.db,
            "discord": discord,
            "commands": commands,
            "_": self._last_eval_result,
        }
        env.update(globals())

        stdout = io.StringIO()
        to_compile = f"async def __eval_fn():\n{textwrap.indent(body, '  ')}"

        try:
            exec(to_compile, env)
        except Exception as e:
            await ctx.send(
                embed=self.embed.error(
                    f"```py\n{type(e).__name__}: {e}\n```"
                )
            )
            return

        func = env["__eval_fn"]
        try:
            with redirect_stdout(stdout):
                ret = await func()
        except Exception:
            value = stdout.getvalue()
            tb = traceback.format_exc()
            content = f"```py\n{value}{tb}\n```"
            if len(content) > 1990:
                content = content[:1990] + "..."
            await ctx.send(content)
            return

        value = stdout.getvalue()
        self._last_eval_result = ret

        if ret is None:
            if value:
                content = f"```py\n{value}\n```"
                if len(content) > 1990:
                    content = content[:1990] + "..."
                await ctx.send(content)
            else:
                try:
                    await ctx.message.add_reaction("✅")
                except discord.HTTPException:
                    pass
        else:
            content = f"```py\n{value}{ret!r}\n```"
            if len(content) > 1990:
                content = content[:1990] + "..."
            await ctx.send(content)

    # ------------------------------------------------------------------
    @commands.hybrid_command(
        name="stats",
        description="Affiche les statistiques techniques du bot.",
    )
    async def stats(self, ctx: commands.Context) -> None:
        """Show technical stats: uptime, memory, guilds, latency."""
        uptime_seconds = int(time.time() - self.start_time)
        days, rem = divmod(uptime_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        parts: list[str] = []
        if days:
            parts.append(f"{days}j")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        parts.append(f"{seconds}s")
        uptime_str = " ".join(parts)

        try:
            process = psutil.Process()
            mem_mb = process.memory_info().rss / 1024 / 1024
            cpu_percent = process.cpu_percent(interval=None)
        except Exception:
            mem_mb = 0.0
            cpu_percent = 0.0

        total_users = sum(g.member_count or 0 for g in self.bot.guilds)
        latency_ms = round(self.bot.latency * 1000)

        embed = self.embed.info(
            description=(
                f"**Version :** `{BOT_VERSION}`\n"
                f"**Uptime :** {uptime_str}\n"
                f"**Latence :** {latency_ms} ms\n"
                f"**Serveurs :** {len(self.bot.guilds)}\n"
                f"**Utilisateurs :** {total_users}\n"
                f"**RAM :** {mem_mb:.1f} MB\n"
                f"**CPU :** {cpu_percent:.1f} %\n"
                f"**Python :** {platform.python_version()}\n"
                f"**discord.py :** {discord.__version__}"
            ),
            title=f"📊 Stats · {BOT_NAME}",
        )
        if self.bot.user and self.bot.user.display_avatar:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Owner(bot))

"""Hierarchical permission system - the core of the bot.

9 cumulative permission levels. Level N includes access to all commands
requiring level <= N. Commands can be overridden on a per-guild basis,
and specific users/roles can be granted access to a single command.
"""
from __future__ import annotations

import logging
from typing import Optional, Union

import discord
from discord.ext import commands

from config.config import (
    DEFAULT_COMMAND_PERMS,
    MAX_PERM_LEVEL,
    MIN_PERM_LEVEL,
)
from utils.checks import get_user_perm_level, perm

log = logging.getLogger(__name__)

# Type alias: target can be either a role or a member
PermTarget = Union[discord.Role, discord.Member]


class PermissionsSystem(commands.Cog, name="Permissions"):
    """Hierarchical permission system commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db  # type: ignore[attr-defined]
        self.embed = bot.embed  # type: ignore[attr-defined]

    # --- Helpers --------------------------------------------------------

    @staticmethod
    def _target_type(target: PermTarget) -> str:
        return "role" if isinstance(target, discord.Role) else "user"

    @staticmethod
    def _target_mention(target: PermTarget) -> str:
        return target.mention

    async def _resolve_command_or_level(
        self, ctx: commands.Context, value: str
    ) -> Optional[Union[int, str]]:
        """Try to parse `value` as a level int, else as a command name."""
        try:
            n = int(value)
            if MIN_PERM_LEVEL <= n <= MAX_PERM_LEVEL:
                return n
            return None
        except ValueError:
            pass
        # Treat as command name (only if a real command exists)
        cmd = self.bot.get_command(value)
        if cmd is not None:
            return cmd.qualified_name
        # Or known by default mapping
        if value.lower() in DEFAULT_COMMAND_PERMS:
            return value.lower()
        return None

    # --- set/del command groups ----------------------------------------

    @commands.group(name="set", invoke_without_command=True)
    @perm(9)
    async def set_group(self, ctx: commands.Context) -> None:
        """Groupe de commandes de configuration. Voir `+helpall`."""
        await ctx.send(embed=self.embed.info(
            "Sous-commandes : `set perm <niveau|commande> <@cible>`."
        ))

    @set_group.command(name="perm")
    @perm(9)
    async def set_perm(
        self,
        ctx: commands.Context,
        level_or_command: str,
        target: PermTarget,
    ) -> None:
        """Assigne une perm (1-9) ou un override de commande à un rôle/membre.

        Exemples :
        `+set perm 7 @Modos`
        `+set perm ban @user`
        """
        resolved = await self._resolve_command_or_level(ctx, level_or_command)
        if resolved is None:
            await ctx.send(embed=self.embed.error(
                f"`{level_or_command}` n'est ni un niveau (1-9) ni une commande connue."
            ))
            return

        ttype = self._target_type(target)

        if isinstance(resolved, int):
            # Assign perm level
            await self.db.execute(
                "INSERT OR REPLACE INTO permissions "
                "(guild_id, level, target_id, target_type) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, resolved, target.id, ttype),
            )
            await ctx.send(embed=self.embed.success(
                f"Permission niveau **{resolved}** assignée à {target.mention}."
            ))
        else:
            # Per-command grant
            await self.db.execute(
                "INSERT OR REPLACE INTO command_grants "
                "(guild_id, command_name, target_id, target_type) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, resolved, target.id, ttype),
            )
            await ctx.send(embed=self.embed.success(
                f"Accès à la commande `{resolved}` accordé à {target.mention}."
            ))

    @commands.group(name="del", invoke_without_command=True)
    @perm(9)
    async def del_group(self, ctx: commands.Context) -> None:
        """Groupe de commandes de suppression."""
        await ctx.send(embed=self.embed.info(
            "Sous-commandes : `del perm <niveau|commande> <@cible>`."
        ))

    @del_group.command(name="perm")
    @perm(9)
    async def del_perm(
        self,
        ctx: commands.Context,
        level_or_command: str,
        target: PermTarget,
    ) -> None:
        """Retire une perm ou un override de commande."""
        resolved = await self._resolve_command_or_level(ctx, level_or_command)
        if resolved is None:
            await ctx.send(embed=self.embed.error(
                f"`{level_or_command}` n'est ni un niveau (1-9) ni une commande connue."
            ))
            return

        ttype = self._target_type(target)

        if isinstance(resolved, int):
            await self.db.execute(
                "DELETE FROM permissions "
                "WHERE guild_id = ? AND level = ? AND target_id = ? AND target_type = ?",
                (ctx.guild.id, resolved, target.id, ttype),
            )
            await ctx.send(embed=self.embed.success(
                f"Permission niveau **{resolved}** retirée à {target.mention}."
            ))
        else:
            await self.db.execute(
                "DELETE FROM command_grants "
                "WHERE guild_id = ? AND command_name = ? AND target_id = ? AND target_type = ?",
                (ctx.guild.id, resolved, target.id, ttype),
            )
            await ctx.send(embed=self.embed.success(
                f"Accès à `{resolved}` retiré à {target.mention}."
            ))

    # --- changeall -----------------------------------------------------

    @commands.command(name="changeall")
    @perm(9)
    async def changeall(
        self,
        ctx: commands.Context,
        old_level: int,
        new_level: int,
    ) -> None:
        """Migre toutes les commandes du niveau `old_level` vers `new_level`."""
        if not (MIN_PERM_LEVEL <= old_level <= MAX_PERM_LEVEL):
            await ctx.send(embed=self.embed.error("Ancien niveau invalide."))
            return
        if not (MIN_PERM_LEVEL <= new_level <= MAX_PERM_LEVEL):
            await ctx.send(embed=self.embed.error("Nouveau niveau invalide."))
            return

        # Update the explicit overrides
        await self.db.execute(
            "UPDATE command_perms SET level = ? WHERE guild_id = ? AND level = ?",
            (new_level, ctx.guild.id, old_level),
        )
        # And set default commands at this level to the new level explicitly
        for cmd_name, lvl in DEFAULT_COMMAND_PERMS.items():
            if lvl == old_level:
                await self.db.execute(
                    "INSERT OR REPLACE INTO command_perms "
                    "(guild_id, command_name, level) VALUES (?, ?, ?)",
                    (ctx.guild.id, cmd_name, new_level),
                )
        await ctx.send(embed=self.embed.success(
            f"Toutes les commandes de niveau **{old_level}** sont maintenant en **{new_level}**."
        ))

    # --- helpall -------------------------------------------------------

    @commands.command(name="helpall")
    @perm(1)
    async def helpall(self, ctx: commands.Context) -> None:
        """Affiche toutes les commandes triées par niveau de permission."""
        levels: dict[int, list[str]] = {i: [] for i in range(MAX_PERM_LEVEL + 1)}
        for cmd in self.bot.walk_commands():
            if cmd.hidden:
                continue
            name = cmd.qualified_name
            # Resolve effective level: DB override > default mapping > 0
            override = await self.db.fetchval(
                "SELECT level FROM command_perms WHERE guild_id = ? AND command_name = ?",
                (ctx.guild.id, name),
            )
            if override is not None:
                lvl = int(override)
            else:
                lvl = DEFAULT_COMMAND_PERMS.get(
                    name, DEFAULT_COMMAND_PERMS.get(cmd.name, 0)
                )
            levels.setdefault(lvl, []).append(name)

        prefix = ctx.prefix
        embed = self.embed.custom(
            description=f"Liste des commandes par niveau de permission. Préfixe : `{prefix}`",
            title="📜 Toutes les commandes",
        )
        for lvl in sorted(levels.keys()):
            cmds = sorted(set(levels[lvl]))
            if not cmds:
                continue
            label = "Public" if lvl == 0 else f"Perm {lvl}"
            value = ", ".join(f"`{c}`" for c in cmds)
            if len(value) > 1024:
                value = value[:1020] + "…"
            embed.add_field(name=f"⚙️ {label}", value=value, inline=False)
        await ctx.send(embed=embed)

    # --- perms ---------------------------------------------------------

    @commands.command(name="perms")
    @perm(1)
    async def perms(
        self,
        ctx: commands.Context,
        member: Optional[discord.Member] = None,
    ) -> None:
        """Affiche le niveau de permission d'un membre."""
        target = member or ctx.author
        level = await get_user_perm_level(self.bot, ctx.guild, target)

        # Detailed breakdown of sources
        user_row = await self.db.fetchone(
            "SELECT level FROM permissions "
            "WHERE guild_id = ? AND target_id = ? AND target_type = 'user'",
            (ctx.guild.id, target.id),
        )
        role_rows: list = []
        if isinstance(target, discord.Member) and target.roles:
            role_ids = [r.id for r in target.roles]
            placeholders = ",".join("?" * len(role_ids))
            role_rows = await self.db.fetchall(
                f"SELECT target_id, level FROM permissions "
                f"WHERE guild_id = ? AND target_type = 'role' "
                f"AND target_id IN ({placeholders})",
                (ctx.guild.id, *role_ids),
            )

        embed = self.embed.custom(
            description=f"Permissions de {target.mention}",
            title="🛡️ Permissions",
        )
        embed.add_field(name="Niveau effectif", value=f"**{level}** / {MAX_PERM_LEVEL}", inline=False)

        if user_row:
            embed.add_field(name="Perm utilisateur", value=f"Niveau {user_row['level']}", inline=True)

        if role_rows:
            lines = []
            for r in role_rows:
                role = ctx.guild.get_role(int(r["target_id"]))
                if role:
                    lines.append(f"• {role.mention} → niveau **{r['level']}**")
            if lines:
                embed.add_field(name="Via rôles", value="\n".join(lines), inline=False)

        if target.id == self.bot.owner_id:
            embed.add_field(name="Spécial", value="🔑 Owner du bot", inline=False)
        elif ctx.guild.owner_id == target.id:
            embed.add_field(name="Spécial", value="👑 Owner du serveur", inline=False)
        elif isinstance(target, discord.Member) and target.guild_permissions.administrator:
            embed.add_field(name="Spécial", value="🛠️ Administrateur Discord", inline=False)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PermissionsSystem(bot))

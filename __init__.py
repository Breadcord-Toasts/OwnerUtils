import discord.abc
from discord.ext import commands

import breadcord


class OwnerUtils(breadcord.module.ModuleCog):
    @commands.command()
    @commands.is_owner()
    async def stop(self, ctx: commands.Context) -> None:
        """Stops the bot and the running python process."""
        self.logger.info("Stopping bot")
        await ctx.reply("Stopping bot")
        await self.bot.close()

    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def sync(
        self,
        ctx: commands.Context,
        guilds: commands.Greedy[discord.Guild] = commands.parameter(
            description="IDs of the guilds to sync commands to."
        ),
        scope: str | None = commands.parameter(
            description="\n".join((
                'The scope in which to sync commands. ',
                'Can be "global" or "local" (or an allowed synonym). ',
                'If guilds are provided, only a local scope is allowed, and will be appended to the guilds to sync to.',
            )),
            default=None
        ),
        mode: str | None = commands.parameter(
            description="\n".join((
                'The mode of the command. Can be "clear", "copy", or "sync" (or an allowed synonym). ',
                'Defaults to syncing.'
            )),
            default=None,
        ),
        clear_command_type: str | None = commands.parameter(
            description="\n".join((
                'If the mode is set to clear, this is the type of app command to clear. ',
                'Can be "slash", "user" or "message" specifying if slash commands or context menus should be cleared. ',
                'Defaults to clearing all app command types.',
            )),
            default=None
        )
    ) -> None:
        """Syncs application commands."""
        global_scopes = ["all", "global", "globally"]
        local_scopes = ["local", "locally", "here"]
        valid_scopes = global_scopes + local_scopes
        valid_modes = ["clear", "copy", "sync"]
        valid_clear_command_types = {
            "chat_input": "chat_input",
            "slash": "chat_input",
            "user": "user",
            "ctx_user": "user",
            "message": "message",
            "ctx_message": "message",
        }

        scope = scope.lower() if scope else None
        mode = mode.lower() if mode else None

        if guilds and scope in global_scopes:
            raise commands.BadArgument(f"Can not use a global scope when guilds are provided")

        if mode is None and scope is not None and scope not in valid_scopes:
            mode = scope
            scope = None

        if scope is not None and scope not in valid_scopes:
            raise commands.BadArgument(f"Invalid scope: {scope}")
        mode = mode or "sync"
        if mode is not None and mode not in valid_modes:
            raise commands.BadArgument(f"Invalid mode: {mode}")
        if clear_command_type is not None:
            if mode != "clear":
                raise commands.BadArgument(f"Can not clear command types when not in clear mode")
            if clear_command_type not in valid_clear_command_types:
                raise commands.BadArgument(f"Invalid clear command type: {clear_command_type}")
            clear_command_type = valid_clear_command_types[clear_command_type]

        if scope in local_scopes and ctx.guild is not None:
            guilds.append(ctx.guild)

        targets = list(set(guilds)) or None
        target_message = "all guilds"
        if targets:
            target_message = f"{len(targets)} guilds" if len(targets) > 1 else "1 guild"

        del scope, guilds

        if mode == "sync":
            response = await ctx.reply(f"Syncing commands in {target_message}..")
            if not targets:
                await ctx.bot.tree.sync()
            else:
                for guild in targets:
                    await ctx.bot.tree.sync(guild=guild)
            await response.edit(content=f"Synced commands in {target_message}")
            return

        if targets is None:
            raise commands.BadArgument("Clearing or copying to all guilds is not allowed.")
        elif not targets:
            raise commands.BadArgument("No guilds to target.")

        if mode == "clear":
            response = await ctx.reply(f"Clearing commands in {target_message}..")
            for guild in targets:
                ctx.bot.tree.clear_commands(guild=guild, type=clear_command_type)
                await ctx.bot.tree.sync(guild=guild)
            await response.edit(content=f"Cleared commands in {target_message}")
            return

        if mode == "copy":
            response = await ctx.reply(f"Copying global commands to {target_message}..")
            for guild in targets:
                ctx.bot.tree.copy_global_to(guild=guild)
                await ctx.bot.tree.sync(guild=guild)
            await response.edit(content=f"Copied global commands to {target_message}")
            return

        raise commands.BadArgument()

    @sync.error
    async def sync_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.reply(str(error))
            return
        raise error


async def setup(bot: breadcord.Bot, module: breadcord.module.Module) -> None:
    await bot.add_cog(OwnerUtils(module.id))

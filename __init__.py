import asyncio
import inspect
import io
import json
import os
import re
import sys
import textwrap
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from pprint import pprint, pp
from typing import Literal, Any

import aiohttp
import discord.abc
from discord.ext import commands

import breadcord

DEFAULT_GLOBALS = dict(
    __builtins__=__builtins__,
    discord=discord,
    commands=commands,
    re=re,
    json=json,
    os=os,
    sys=sys,
    pprint=pprint,
    pp=pp,
    Path=Path,
    io=io,
    breadcord=breadcord
)


# noinspection PyProtectedMember
class _UndefinedVar(discord.utils._MissingSentinel):
    pass


UNDEFINED: Any = _UndefinedVar()


def prepare_for_codeblock(string: str, /) -> str:
    string = re.sub("```", "``\u200d`", string)  # \u200d is a zero width joiner

    # I'll be honest, this was writen by ChatGPT and cleaned up by me lmao
    # It should remove escape codes (I hope)
    string = re.sub(r"[\x07\x1b].*?[a-zA-Z]", "", string)

    string = re.sub(r"^\s*\n|\n\s*$", "", string)  # Removes empty lines at the beginning and end of the output
    return string


async def format_output_as_kwargs(
    return_value: Any | _UndefinedVar,
    exception: Exception | _UndefinedVar,
    stdout: str | None,
    stderr: str | None,
) -> dict[str, Any]:
    def output_segment(*, value: Any, title: str) -> str:
        return (
            f"**{discord.utils.escape_markdown(title)}**```\n"
            f"{prepare_for_codeblock(str(value))}\n"
            "```"
        )

    output = (
        (output_segment(value=return_value, title="Return value") if return_value is not UNDEFINED else "")
        + (output_segment(value=exception, title="Exception") if exception is not UNDEFINED else "")
        + (output_segment(value=stdout, title="Output stream") if stdout else "")
        + (output_segment(value=stderr, title="Error stream") if stderr else "")
    )

    if not output:
        return dict(content="No output")

    if len(output) <= 2000:
        return dict(content=output)
    else:
        return dict(
            content="Output too big, uploading as file(s).",
            attachments=[
                discord.File(io.BytesIO(str(content).encode()), filename=filename)
                for content, filename in (
                    (return_value, "return.txt"),
                    (exception, "exception.txt"),
                    (stdout or UNDEFINED, "stdout.txt"),
                    (stderr or UNDEFINED, "stderr.txt")
                )
                if content is not UNDEFINED
            ]
        )


def strip_codeblock(
    string: str,
    *,
    language_regex: str = "",
    optional_lang: bool = True,
    strip_inline: bool = True
) -> str:
    if language_regex and not language_regex.endswith("\n"):
        language_regex = f"{language_regex}\n"
    regex = re.compile(
        # This is technically not accurate since
        # ```lang
        #
        # ```
        # won't match "lang".
        rf"```(?P<language>{language_regex}){'?' if optional_lang else ''}.+```",
        flags=re.DOTALL | re.IGNORECASE
    )

    if match := regex.match(string):
        start_strip = 3 + (len(match["language"]) if match[1] else 0)
        string = string[start_strip:-3]

    lines = string.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    string = "\n".join(lines)

    if strip_inline and re.match(r"^\s*`(?!`)", string) and re.match(r"(?<!`)`\s*$", string):
        string = string[1:-1]

    return textwrap.dedent(string)


class ShellInputModal(discord.ui.Modal, title="Shell input"):
    shell_input = discord.ui.TextInput(
        label="Input", placeholder="Input to send to the running shell", style=discord.TextStyle.long
    )

    def __init__(self, process: asyncio.subprocess.Process):
        super().__init__()
        self.process = process

    async def on_submit(self, interaction: discord.Interaction):
        self.process.stdin.write(self.shell_input.value.encode())
        await interaction.response.defer()


class ShellView(discord.ui.View):
    def __init__(self, process: asyncio.subprocess.Process, *, user_id: int) -> None:
        super().__init__(timeout=None)
        self.process = process
        self.user_id = user_id

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, *_) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f'Only <@{self.user_id}> can perform this action!',
                ephemeral=True
            )
            return
        self.process.terminate()
        self.stop()

    @discord.ui.button(label='Send input', style=discord.ButtonStyle.gray)
    async def send_input(self, interaction: discord.Interaction, _) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f'Only <@{self.user_id}> can perform this action!',
                ephemeral=True
            )
            return
        input_modal = ShellInputModal(self.process)
        await interaction.response.send_modal(input_modal)


class OwnerUtils(breadcord.module.ModuleCog):
    def __init__(self, module_id) -> None:
        super().__init__(module_id)

        @self.settings.rce_commands_enabled.observe
        def on_rce_commands_changed(_, new: bool) -> None:
            self.logger.debug(f"RCE commands {'enabled' if new else 'disabled'}")
            self.shell.enabled = new
            self.evaluate.enabled = new
            self.execute.enabled = new

        on_rce_commands_changed(None, self.settings.rce_commands_enabled.value)

    async def cog_load(self) -> None:
        DEFAULT_GLOBALS["session"] = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        await DEFAULT_GLOBALS["session"].close()
        del DEFAULT_GLOBALS["session"]

    @commands.command()
    @commands.is_owner()
    async def stop(self, ctx: commands.Context) -> None:
        """Stops the bot and the running python process."""
        self.logger.info("Stopping bot")
        await ctx.reply("Stopping bot")
        await self.bot.close()

    # The docstring is only meant to be used by the help command
    # noinspection PyIncorrectDocstring
    @commands.command()
    @commands.guild_only()
    @commands.is_owner()
    async def sync(
        self,
        ctx: commands.Context,
        guilds: commands.Greedy[discord.Guild],
        scope: str | None = None,
        mode: str | None = None
    ) -> None:
        """Syncs application commands

        Parameters
        -----------
        guilds:
            The IDs of guilds to sync to.
        scope:
            Alternative to specifying guild IDs.
            `all` or `global` syncs in all guilds
            `local` or `here` syncs in the current guild
        mode:
            Alternative sync modes.
            `clear` clears the commands and then syncs.
            `copy` copies the global commands into the guild as guild-specific commands.
        """

        guilds: list[discord.Guild] | Literal["all"] = guilds
        scope = scope.lower() if scope else None
        mode: Literal["clear", "copy"] | None = mode.lower() if mode else None

        if guilds:
            if mode:
                raise commands.TooManyArguments(f"Too many arguments passed to {ctx.command.qualified_name}")
            mode = scope
        elif scope in ["all", "global", "globally"]:
            guilds = "all"
        elif scope in ["local", "locally", "here"]:
            guilds = [ctx.guild]

        if mode is None:
            if guilds:
                response = await ctx.reply(f"Syncing commands in {len(guilds)} guild(s)..")
                for guild in guilds:
                    await ctx.bot.tree.sync(guild=guild)
                await response.edit(content=f"Synced commands in {len(guilds)} guild(s)")
            else:
                response = await ctx.reply("Syncing commands in all guilds..")
                await ctx.bot.tree.sync()
                await response.edit(content="Synced commands in all guilds")
            return
        elif mode == "clear":
            if guilds == "all":
                response = await ctx.reply("Clearing commands in all guilds..")
                ctx.bot.tree.clear_commands()
                await ctx.bot.tree.sync()
                await response.edit(content="Cleared commands in all guilds")
            else:
                response = await ctx.reply(f"Clearing commands in {len(guilds)} guild(s)..")
                for guild in guilds:
                    ctx.bot.tree.clear_commands(guild=guild)
                    await ctx.bot.tree.sync(guild=guild)
                await response.edit(content=f"Cleared commands in {len(guilds)} guild(s)")
            return
        elif mode == "copy" and guilds != "all":
            response = await ctx.reply(f"Copying global commands to {len(guilds)} guild(s)..")
            for guild in guilds:
                ctx.bot.tree.copy_global_to(guild=guild)
                await ctx.bot.tree.sync(guild=guild)
            await response.edit(content=f"Copied global commands to {len(guilds)} guild(s)")
            return

        raise commands.BadArgument()

    @sync.error
    async def sync_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.BadArgument):
            await ctx.reply("Invalid arguments were passed")
            return
        raise error

    @commands.command()
    @commands.is_owner()
    async def shell(self, ctx: commands.Context, *, command: str) -> None:
        """Runs an arbitrary shell command."""
        response = await ctx.reply("Running...")
        process = await asyncio.create_subprocess_shell(
            # The shell could be just about anything, so a proper regex isn't worth the effort
            # language=regexp
            strip_codeblock(command, language_regex="[a-z]+"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        shell_view = ShellView(process, user_id=ctx.author.id)

        async def update_output(new_out: str, /, *, extra_text: str = "", **edit_kwargs) -> None:
            new_out = prepare_for_codeblock(new_out)

            if not new_out.strip():
                if edit_kwargs:
                    await response.edit(**edit_kwargs)
            # There's a newline before the output so that it doesn't accidentally add syntax highlighting
            elif len(codeblock := f"```\n{new_out}\n```") <= 2000:
                await response.edit(content=codeblock + extra_text, **edit_kwargs)
            else:
                await response.edit(
                    content=f"Output too long, uploading as file.{extra_text}",
                    attachments=[discord.File(io.BytesIO(new_out.encode()), filename="output.txt")],
                    **edit_kwargs
                )

        await asyncio.sleep(update_interval := self.settings.shell_update_interval_seconds.value)
        out = ""
        while process.returncode is None:
            out += (await process.stdout.read(1024)).decode()
            if out.strip():
                await update_output(out, view=shell_view)
            await asyncio.sleep(update_interval)
        out += (await process.communicate())[0].decode()
        await update_output(out)

        response = await response.channel.fetch_message(response.id)  # Gets the message with its current content
        await response.edit(content=f"{response.content}\nProcess exited with code {process.returncode}", view=None)

    @commands.command(aliases=["eval"])
    @commands.is_owner()
    async def evaluate(self, ctx: commands.Context, *, code: str) -> None:
        """Evaluates python code (blocking)"""
        # language=regexp
        code = strip_codeblock(code, language_regex=r"py(thon)?")
        spoofed_globals: dict = DEFAULT_GLOBALS | dict(
            self=self,
            ctx=ctx,
            bot=self.bot,
        )
        if ctx.message.reference:
            spoofed_globals["reference"] = ctx.message.reference.cached_message

        response = await ctx.reply("Evaluating...")

        return_value = UNDEFINED
        exception = UNDEFINED
        with redirect_stdout(io.StringIO()) as stdout:
            with redirect_stderr(io.StringIO()) as stderr:
                try:
                    return_value = eval(code, spoofed_globals, {})
                    if inspect.isawaitable(return_value):
                        return_value = await return_value
                except Exception as error:
                    exception = error

        await response.edit(**await format_output_as_kwargs(
            return_value,
            exception,
            stdout.getvalue(),
            stderr.getvalue()
        ))

    @commands.command(aliases=["exec"])
    @commands.is_owner()
    async def execute(self, ctx: commands.Context, *, code: str) -> None:
        """Executes python code (blocking)"""
        # language=regexp
        code = strip_codeblock(code, language_regex=r"py(thon)?")
        to_execute = "async def _execute():\n" + "\n".join(
            f"    {line}" for line in code.splitlines()
        )
        spoofed_globals: dict = DEFAULT_GLOBALS | dict(
            self=self,
            ctx=ctx,
            bot=self.bot,
        )
        if ctx.message.reference:
            spoofed_globals["reference"] = ctx.message.reference.cached_message

        spoofed_locals = {}

        response = await ctx.reply("Executing...")

        return_value = UNDEFINED
        exception = UNDEFINED
        with redirect_stdout(io.StringIO()) as stdout:
            with redirect_stderr(io.StringIO()) as stderr:
                try:
                    exec(to_execute, spoofed_globals, spoofed_locals)
                    return_value = await spoofed_locals["_execute"]() or UNDEFINED
                except Exception as error:
                    exception = error

        await response.edit(**await format_output_as_kwargs(
            return_value,
            exception,
            stdout.getvalue(),
            stderr.getvalue()
        ))


async def setup(bot: breadcord.Bot):
    await bot.add_cog(OwnerUtils("owner_utils"))

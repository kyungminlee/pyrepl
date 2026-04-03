import shlex
import typer
import sys
import os
import asyncio
from pathlib import Path
from typing import List, Callable, Optional, Any, Dict, Type, Union, Coroutine
from rich import print as rprint
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

class REPLCompleter(Completer):
    def __init__(self, app: typer.Typer):
        self.app = app

    def get_completions(self, document: Document, complete_event):
        text = document.text_before_cursor
        
        # 1. Complete dot-command names if at the beginning
        if text.startswith('.') and ' ' not in text:
            prefix = text
            for command in self.app.registered_commands:
                name = command.name or f".{command.callback.__name__.replace('_', '-')}"
                if name.startswith(prefix):
                    yield Completion(name, start_position=-len(prefix))
            return

        # 2. Complete arguments for dot-commands
        if text.startswith('.'):
            try:
                parts = shlex.split(text)
            except ValueError:
                parts = text.split()

            if not parts:
                return
            
            cmd_name = parts[0]
            target_cmd = None
            for command in self.app.registered_commands:
                actual_name = command.name or f".{command.callback.__name__.replace('_', '-')}"
                if actual_name == cmd_name:
                    target_cmd = command
                    break
            
            if target_cmd:
                import click
                import typer.models
                
                click_cmd = getattr(target_cmd.callback, "click_command", None)
                if not click_cmd:
                    from typer.main import get_command
                    root_click_cmd = get_command(self.app)
                    if isinstance(root_click_cmd, click.Group):
                        click_cmd = root_click_cmd.get_command(click.Context(root_click_cmd), cmd_name)
                    else:
                        click_cmd = root_click_cmd

                if click_cmd:
                    word_before_cursor = document.get_word_before_cursor()
                    
                    # Suggest options
                    for param in click_cmd.params:
                        if isinstance(param, click.Option):
                            for opt in param.opts:
                                if opt.startswith(word_before_cursor):
                                    yield Completion(opt, start_position=-len(word_before_cursor))

                    # Suggest paths if any parameter is a Path
                    if not word_before_cursor.startswith('-'):
                        has_path_param = any(
                            isinstance(param.type, (click.Path, typer.models.TyperPath))
                            for param in click_cmd.params
                        )
                        
                        if has_path_param:
                            # Manual path completion that handles absolute paths and ~
                            line = document.text_before_cursor
                            last_space = line.rfind(' ')
                            path_part = line[last_space+1:] if last_space != -1 else ""
                            
                            expanded_path = os.path.expanduser(path_part)
                            dirname = os.path.dirname(expanded_path)
                            basename = os.path.basename(expanded_path)
                            
                            # If path_part ends with / or is exactly /
                            if path_part.endswith('/') or path_part == '/':
                                search_dir = expanded_path
                                basename = ""
                            else:
                                search_dir = dirname if dirname else "."

                            if os.path.isdir(search_dir):
                                try:
                                    for name in os.listdir(search_dir):
                                        if name.startswith(basename):
                                            yield Completion(name, start_position=-len(basename))
                                except Exception:
                                    pass

class PyREPL:
    def __init__(self, prompt: str = "pyrepl> "):
        self.app = typer.Typer(add_completion=False)
        self.prompt = prompt
        self.fallback: Optional[Union[Callable[[List[str]], Any], Callable[[List[str]], Coroutine[Any, Any, Any]]]] = None
        self.session = PromptSession(completer=REPLCompleter(self.app))
        self.queue: asyncio.Queue = asyncio.Queue()
        
        @self.app.command(name=".exit", help="Exit the REPL.")
        def exit_repl():
            rprint("[bold blue]Goodbye![/bold blue]")
            sys.exit(0)

        @self.app.command(name=".help", help="List all available dot-commands or show detailed help for a specific command.")
        def show_help(command_name: Optional[str] = typer.Argument(None, help="The command to show help for (with or without the dot).", show_default=False)):
            import click
            
            if command_name:
                search_name = command_name if command_name.startswith('.') else f".{command_name}"
                target_cmd = None
                for cmd in self.app.registered_commands:
                    name = cmd.name or f".{cmd.callback.__name__.replace('_', '-')}"
                    if name == search_name:
                        target_cmd = cmd
                        break
                
                if target_cmd:
                    rprint(f"[bold cyan]Help for {search_name}:[/bold cyan]")
                    if target_cmd.help:
                        rprint(f"[bold green]Description:[/bold green] {target_cmd.help}")
                    
                    doc = target_cmd.callback.__doc__
                    if doc:
                        rprint(f"\n[bold green]Details:[/bold green]\n{doc.strip()}")
                    
                    click_cmd = getattr(target_cmd.callback, "click_command", None)
                    if not click_cmd:
                        from typer.main import get_command
                        root_click_cmd = get_command(self.app)
                        if isinstance(root_click_cmd, click.Group):
                            click_cmd = root_click_cmd.get_command(click.Context(root_click_cmd), search_name)
                    
                    if click_cmd:
                        params = []
                        for param in click_cmd.params:
                            if isinstance(param, click.Argument):
                                arg_name = param.name.upper()
                                if not param.required:
                                    params.append(f"[{arg_name}]")
                                else:
                                    params.append(arg_name)
                            elif isinstance(param, click.Option) and not param.hidden:
                                if "[OPTIONS]" not in params:
                                    params.append("[OPTIONS]")
                        usage = " ".join(params)
                        rprint(f"\n[bold green]Usage:[/bold green] {search_name} {usage}")
                        
                        if click_cmd.params:
                            rprint("\n[bold green]Arguments & Options:[/bold green]")
                            for param in click_cmd.params:
                                if isinstance(param, click.Option):
                                    opts = "/".join(param.opts)
                                    rprint(f"  {opts:20} {param.help or ''}")
                                else:
                                    rprint(f"  {param.name.upper():20} {param.help or ''}")
                else:
                    rprint(f"[bold red]Unknown command: {command_name}[/bold red]")
                return

            rprint("[bold cyan]Available dot-commands:[/bold cyan]")
            for command in self.app.registered_commands:
                name = command.name or f".{command.callback.__name__.replace('_', '-')}"
                click_cmd = getattr(command.callback, "click_command", None)
                if not click_cmd:
                    from typer.main import get_command
                    root_click_cmd = get_command(self.app)
                    if isinstance(root_click_cmd, click.Group):
                        click_cmd = root_click_cmd.get_command(click.Context(root_click_cmd), name)
                
                usage = ""
                if click_cmd:
                    params = []
                    for param in click_cmd.params:
                        if isinstance(param, click.Argument):
                            arg_name = param.name.upper()
                            if not param.required:
                                params.append(f"[{arg_name}]")
                            else:
                                params.append(arg_name)
                        elif isinstance(param, click.Option) and not param.hidden:
                            if "[OPTIONS]" not in params:
                                params.append("[OPTIONS]")
                    usage = " ".join(params)

                help_text = command.help or "No help message provided."
                rprint(f"  [bold green]{name:15}[/bold green] [yellow]{usage:25}[/yellow] - {help_text}")

    def command(self, name: Optional[str] = None, description: Optional[str] = None, **kwargs):
        def decorator(func: Callable):
            cmd_name = name if name else f".{func.__name__.replace('_', '-')}"
            help_text = description or func.__doc__
            self.app.command(name=cmd_name, help=help_text, **kwargs)(func)
            return func
        return decorator

    def on_fallback(self, func: Union[Callable[[List[str]], Any], Callable[[List[str]], Coroutine[Any, Any, Any]]]):
        self.fallback = func
        return func

    async def _worker(self):
        """Background worker to process fallback commands from the queue."""
        while True:
            tokens = await self.queue.get()
            try:
                if self.fallback:
                    if asyncio.iscoroutinefunction(self.fallback):
                        await self.fallback(tokens)
                    else:
                        # Run sync fallback in a thread to avoid blocking the event loop
                        await asyncio.to_thread(self.fallback, tokens)
            except Exception as e:
                rprint(f"[bold red]Process error:[/bold red] {e}")
            finally:
                self.queue.task_done()

    def _run_dot_command(self, tokens: List[str]):
        try:
            self.app(args=tokens, standalone_mode=False)
        except typer.Exit:
            pass
        except typer.Abort:
            rprint("[bold red]Aborted.[/bold red]")
        except Exception as e:
            rprint(f"[bold red]Error:[/bold red] {e}")

    async def run(self):
        # Start background worker
        worker_task = asyncio.create_task(self._worker())
        
        rprint("[bold green]REPL started. Type '.help' for commands.[/bold green]")
        while True:
            try:
                # Use prompt_async to allow other tasks (like the worker) to run
                line = await self.session.prompt_async(self.prompt)
                line = line.strip()
                if not line:
                    continue
                
                try:
                    tokens = shlex.split(line)
                except ValueError as e:
                    rprint(f"[bold red]Tokenization error:[/bold red] {e}")
                    continue

                if not tokens:
                    continue

                if tokens[0].startswith('.'):
                    # Run dot commands synchronously as requested
                    self._run_dot_command(tokens)
                else:
                    if self.fallback:
                        # Queue the fallback command
                        await self.queue.put(tokens)
                    else:
                        rprint(f"[yellow]No handler for input: {line}[/yellow]")
            except (EOFError, KeyboardInterrupt):
                rprint("\n[bold blue]Goodbye![/bold blue]")
                worker_task.cancel()
                break
            except Exception as e:
                rprint(f"[bold red]An unexpected error occurred:[/bold red] {e}")

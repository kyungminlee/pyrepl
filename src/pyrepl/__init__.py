import shlex
import typer
import sys
import os
import asyncio
import curses
import io
from pathlib import Path
from enum import Enum, auto
from typing import List, Callable, Optional, Any, Dict, Type, Union, Coroutine
from rich.console import Console
from rich.text import Text

class REPLMode(Enum):
    INPUT = auto()
    QUEUE = auto()
    EDIT_QUEUE = auto()
    COMPLETE = auto()

class REPLStream(io.TextIOBase):
    """A stream that redirects writes to the REPL's internal print method in real-time."""
    def __init__(self, repl, target: str):
        self.repl = repl
        self.target = target
        self.line_buffer = ""

    def write(self, s):
        self.line_buffer += s
        while "\n" in self.line_buffer:
            line, self.line_buffer = self.line_buffer.split("\n", 1)
            self.repl._print(line, target=self.target)
        return len(s)

    def flush(self):
        if self.line_buffer:
            self.repl._print(self.line_buffer, target=self.target)
            self.line_buffer = ""

class PyREPL:
    def __init__(self, prompt: str = "pyrepl> "):
        self.app = typer.Typer(add_completion=False)
        self.prompt = prompt
        self.fallback: Optional[Union[Callable[[List[str]], Any], Callable[[List[str]], Coroutine[Any, Any, Any]]]] = None
        self.pending_queue: List[List[str]] = []
        self.queue_event = asyncio.Event()
        self.console = Console(file=io.StringIO(), force_terminal=True, width=80)
        self.fallback_buffer: List[Text] = []
        self.repl_buffer: List[Text] = []
        self.scroll_pos_output = 0
        self.scroll_pos_repl = 0
        self.mode = REPLMode.INPUT
        self.sh = 0
        self.sw = 80

        @self.app.command(name=".exit", help="Exit the REPL.")
        def exit_repl():
            self.running = False

        @self.app.command(name=".help", help="List all available dot-commands or show detailed help.")
        def show_help(command_name: Optional[str] = typer.Argument(None, show_default=False)):
            import click
            if command_name:
                search_name = command_name if command_name.startswith('.') else f".{command_name}"
                target_cmd = next((c for c in self.app.registered_commands if (c.name or f".{c.callback.__name__.replace('_', '-')}") == search_name), None)
                if target_cmd:
                    self._print(f"[bold cyan]Help for {search_name}:[/bold cyan]")
                    if target_cmd.help: self._print(f"[bold green]Description:[/bold green] {target_cmd.help}")
                    doc = target_cmd.callback.__doc__
                    if doc: self._print(f"\n[bold green]Details:[/bold green]\n{doc.strip()}")
                else:
                    self._print(f"[bold red]Unknown command: {command_name}[/bold red]")
                return
            self._print("[bold cyan]Available dot-commands:[/bold cyan]")
            for command in self.app.registered_commands:
                name = command.name or f".{command.callback.__name__.replace('_', '-')}"
                self._print(f"  [bold green]{name:15}[/bold green] - {command.help or ''}")

    def _print(self, msg: Union[str, Text], target: str = "repl"):
        """Append a message to either the 'repl' or 'fallback' buffer with color support."""
        if isinstance(msg, str):
            # Rich markup to Text
            text = Text.from_markup(msg)
        else:
            text = msg
        
        buffer = self.fallback_buffer if target == "fallback" else self.repl_buffer
        # Wrap to current width
        for line in text.wrap(self.console, self.sw):
            buffer.append(line)

    def _draw_line(self, stdscr, y: int, x: int, text: Text):
        """Render a Rich Text object to curses with attributes."""
        current_x = x
        for segment in text.render(self.console):
            content = segment.text
            style = segment.style
            attr = curses.A_NORMAL
            if style:
                if style.bold: attr |= curses.A_BOLD
                if style.underline: attr |= curses.A_UNDERLINE
                if style.reverse: attr |= curses.A_REVERSE
                if curses.has_colors():
                    c = style.color.name if style.color else None
                    if c == "green": attr |= curses.color_pair(4)
                    elif c == "cyan": attr |= curses.color_pair(5)
                    elif c == "red": attr |= curses.color_pair(6)
                    elif c == "magenta": attr |= curses.color_pair(7)
                    elif c == "blue": attr |= curses.color_pair(8)
                    elif c == "yellow": attr |= curses.color_pair(9)
            try:
                if current_x < self.sw:
                    stdscr.addstr(y, current_x, content[:self.sw-current_x], attr)
                    current_x += len(content)
            except curses.error: break

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
        while True:
            await self.queue_event.wait()
            if not self.pending_queue:
                self.queue_event.clear()
                continue
            tokens = self.pending_queue.pop(0)
            if not self.pending_queue: self.queue_event.clear()
            try:
                if self.fallback:
                    old_stdout = sys.stdout
                    stream = REPLStream(self, target="fallback")
                    sys.stdout = stream
                    try:
                        if asyncio.iscoroutinefunction(self.fallback): await self.fallback(tokens)
                        else: await asyncio.to_thread(self.fallback, tokens)
                        stream.flush()
                    finally: sys.stdout = old_stdout
            except Exception as e: self._print(f"[bold red]Error in fallback: {e}[/bold red]", target="fallback")

    def _run_dot_command(self, tokens: List[str]):
        old_stdout = sys.stdout
        stream = REPLStream(self, target="repl")
        sys.stdout = stream
        try:
            self.app(args=tokens, standalone_mode=False)
            stream.flush()
        except Exception as e: self._print(f"[bold red]Error in command: {e}[/bold red]", target="repl")
        finally: sys.stdout = old_stdout

    def _get_completions(self, text: str) -> List[str]:
        if not text: return []
        if text.startswith('.') and ' ' not in text:
            completions = []
            for command in self.app.registered_commands:
                name = command.name or f".{command.callback.__name__.replace('_', '-')}"
                if name.startswith(text): completions.append(name)
            return completions
        if text.startswith('.'):
            try: parts = shlex.split(text)
            except ValueError: parts = text.split()
            if not parts: return []
            cmd_name = parts[0]
            target_cmd = next((c for c in self.app.registered_commands if (c.name or f".{c.callback.__name__.replace('_', '-')}") == cmd_name), None)
            if target_cmd:
                import click
                import typer.models
                click_cmd = getattr(target_cmd.callback, "click_command", None)
                if not click_cmd:
                    from typer.main import get_command
                    root_click_cmd = get_command(self.app)
                    if isinstance(root_click_cmd, click.Group):
                        click_cmd = root_click_cmd.get_command(click.Context(root_click_cmd), cmd_name)
                    else: click_cmd = root_click_cmd
                if click_cmd:
                    last_space = text.rfind(' ')
                    path_part = text[last_space+1:] if last_space != -1 else ""
                    if not path_part.startswith('-'):
                        has_path_param = any(isinstance(param.type, (click.Path, typer.models.TyperPath)) for param in click_cmd.params)
                        if has_path_param:
                            expanded_path = os.path.expanduser(path_part)
                            dirname = os.path.dirname(expanded_path)
                            basename = os.path.basename(expanded_path)
                            search_dir = dirname if dirname else "."
                            if os.path.isdir(search_dir):
                                try: return [os.path.join(dirname, n) if dirname else n for n in os.listdir(search_dir) if n.startswith(basename)]
                                except Exception: pass
        return []

    async def _main_loop(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(1)
        stdscr.nodelay(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN) # Selected
            curses.init_pair(2, curses.COLOR_YELLOW, -1) # Header
            curses.init_pair(3, 244 if curses.COLORS >= 256 else curses.COLOR_WHITE, -1) # Dim
            curses.init_pair(4, curses.COLOR_GREEN, -1)
            curses.init_pair(5, curses.COLOR_CYAN, -1)
            curses.init_pair(6, curses.COLOR_RED, -1)
            curses.init_pair(7, curses.COLOR_MAGENTA, -1)
            curses.init_pair(8, curses.COLOR_BLUE, -1)
            curses.init_pair(9, curses.COLOR_YELLOW, -1)
        self.sh, self.sw = stdscr.getmaxyx()
        self.console.width = self.sw
        self.running = True
        current_input = ""; input_pos = 0; queue_select_idx = 0; edit_buffer = ""
        completion_candidates = []; completion_select_idx = 0
        PROMPT_AREA_HEIGHT = 10
        SEPARATOR_ROW = self.sh - PROMPT_AREA_HEIGHT - 1
        OUTPUT_AREA_HEIGHT = SEPARATOR_ROW
        worker_task = asyncio.create_task(self._worker())
        self._print("[bold green]Welcome to PyREPL![/bold green]", target="repl")
        self._print("Type '.help' for commands.", target="repl")
        while self.running:
            stdscr.erase()
            for i in range(OUTPUT_AREA_HEIGHT):
                idx = len(self.fallback_buffer) - OUTPUT_AREA_HEIGHT + i - self.scroll_pos_output
                if 0 <= idx < len(self.fallback_buffer):
                    self._draw_line(stdscr, i, 0, self.fallback_buffer[idx])
            try:
                attr = curses.color_pair(3) if curses.has_colors() else curses.A_DIM
                stdscr.addstr(SEPARATOR_ROW, 0, "─" * self.sw, attr)
            except curses.error: pass
            q_height = min(len(self.pending_queue), 5) if self.mode in (REPLMode.QUEUE, REPLMode.EDIT_QUEUE) else 0
            c_height = min(len(completion_candidates), 5) if self.mode == REPLMode.COMPLETE else 0
            extra_h = max(q_height, c_height)
            repl_msg_height = PROMPT_AREA_HEIGHT - extra_h - (1 if extra_h > 0 else 0) - 1
            for i in range(repl_msg_height):
                idx = len(self.repl_buffer) - repl_msg_height + i - self.scroll_pos_repl
                if 0 <= idx < len(self.repl_buffer):
                    self._draw_line(stdscr, SEPARATOR_ROW + 1 + i, 0, self.repl_buffer[idx])
            popup_start = SEPARATOR_ROW + 1 + repl_msg_height
            if self.mode == REPLMode.COMPLETE:
                header_attr = curses.color_pair(2) if curses.has_colors() else curses.A_BOLD
                try: stdscr.addstr(popup_start, 0, "--- COMPLETIONS (Tab: next, Enter: select) ---"[:self.sw-1], header_attr)
                except curses.error: pass
                for i in range(c_height):
                    window_off = max(0, min(completion_select_idx - 2, len(completion_candidates) - c_height))
                    idx = window_off + i
                    if idx < len(completion_candidates):
                        attr = curses.color_pair(1) if idx == completion_select_idx and curses.has_colors() else (curses.A_REVERSE if idx == completion_select_idx else curses.A_NORMAL)
                        try: stdscr.addstr(popup_start + 1 + i, 0, f" > {completion_candidates[idx]}"[:self.sw-1], attr)
                        except curses.error: pass
            elif q_height > 0:
                header_attr = curses.color_pair(2) if curses.has_colors() else curses.A_BOLD
                try: stdscr.addstr(popup_start, 0, "--- PENDING QUEUE (x: delete, e: edit) ---"[:self.sw-1], header_attr)
                except curses.error: pass
                for i in range(q_height):
                    window_off = max(0, min(queue_select_idx - 2, len(self.pending_queue) - q_height))
                    idx = window_off + i
                    if idx < len(self.pending_queue):
                        cmd_str = " ".join(self.pending_queue[idx])
                        attr = curses.color_pair(1) if idx == queue_select_idx and curses.has_colors() else (curses.A_REVERSE if idx == queue_select_idx else curses.A_NORMAL)
                        try: stdscr.addstr(popup_start + 1 + i, 0, f" Q{idx}: {cmd_str}"[:self.sw-1], attr)
                        except curses.error: pass
            if self.mode == REPLMode.EDIT_QUEUE:
                prompt_line = f"EDITING #{queue_select_idx}: {edit_buffer}"
                stdscr.addstr(self.sh-1, 0, prompt_line[:self.sw-1], curses.A_BOLD | curses.A_REVERSE)
                stdscr.move(self.sh-1, min(self.sw-1, 12 + len(str(queue_select_idx)) + len(edit_buffer)))
            else:
                prompt_line = f"{self.prompt}{current_input}"
                if self.pending_queue and self.mode == REPLMode.INPUT: prompt_line += f"  ({len(self.pending_queue)} pending)"
                stdscr.addstr(self.sh-1, 0, prompt_line[:self.sw-1])
                stdscr.move(self.sh-1, min(self.sw-1, len(self.prompt) + input_pos))
            stdscr.refresh()
            ch = stdscr.getch()
            if ch == -1:
                await asyncio.sleep(0.02); continue
            if self.mode == REPLMode.INPUT:
                if ch == ord('\n'):
                    line = current_input.strip()
                    if line:
                        self._print(f"{self.prompt}{current_input}", target="repl")
                        try:
                            tokens = shlex.split(line)
                            if tokens[0].startswith('.'): self._run_dot_command(tokens)
                            else: self.pending_queue.append(tokens); self.queue_event.set()
                        except ValueError as e: self._print(f"[bold red]Tokenization error: {e}[/bold red]", target="repl")
                    current_input = ""; input_pos = 0
                elif ch == 9:
                    completion_candidates = self._get_completions(current_input)
                    if len(completion_candidates) == 1:
                        last_space = current_input.rfind(' ')
                        if last_space == -1: current_input = completion_candidates[0]
                        else: current_input = current_input[:last_space+1] + completion_candidates[0]
                        input_pos = len(current_input)
                    elif len(completion_candidates) > 1: self.mode = REPLMode.COMPLETE; completion_select_idx = 0
                elif ch == 4: self.running = False
                elif ch == curses.KEY_UP:
                    if not current_input and self.pending_queue: self.mode = REPLMode.QUEUE; queue_select_idx = 0
                    else: self.scroll_pos_repl += 1
                elif ch == curses.KEY_DOWN: self.scroll_pos_repl = max(0, self.scroll_pos_repl - 1)
                elif ch == curses.KEY_PPAGE: self.scroll_pos_output += 5
                elif ch == curses.KEY_NPAGE: self.scroll_pos_output = max(0, self.scroll_pos_output - 5)
                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    if input_pos > 0: current_input = current_input[:input_pos-1] + current_input[input_pos:]; input_pos -= 1
                elif ch == curses.KEY_LEFT: input_pos = max(0, input_pos - 1)
                elif ch == curses.KEY_RIGHT: input_pos = min(len(current_input), input_pos + 1)
                elif 32 <= ch <= 126: current_input = current_input[:input_pos] + chr(ch) + current_input[input_pos:]; input_pos += 1
            elif self.mode == REPLMode.COMPLETE:
                if ch == 9 or ch == curses.KEY_DOWN: completion_select_idx = (completion_select_idx + 1) % len(completion_candidates)
                elif ch == curses.KEY_UP: completion_select_idx = (completion_select_idx - 1) % len(completion_candidates)
                elif ch == ord('\n'):
                    sel = completion_candidates[completion_select_idx]; last_space = current_input.rfind(' ')
                    if last_space == -1: current_input = sel
                    else: current_input = current_input[:last_space+1] + sel
                    input_pos = len(current_input); self.mode = REPLMode.INPUT
                elif ch == 27: self.mode = REPLMode.INPUT
                elif 32 <= ch <= 126: self.mode = REPLMode.INPUT; current_input = current_input[:input_pos] + chr(ch) + current_input[input_pos:]; input_pos += 1
            elif self.mode == REPLMode.QUEUE:
                if ch == ord('x') and self.pending_queue:
                    self.pending_queue.pop(queue_select_idx); queue_select_idx = max(0, min(queue_select_idx, len(self.pending_queue)-1))
                    if not self.pending_queue: self.mode = REPLMode.INPUT
                elif ch == ord('e') and self.pending_queue: self.mode = REPLMode.EDIT_QUEUE; edit_buffer = " ".join(self.pending_queue[queue_select_idx])
                elif ch == curses.KEY_UP: queue_select_idx = max(0, queue_select_idx - 1)
                elif ch == curses.KEY_DOWN: queue_select_idx = min(len(self.pending_queue)-1, queue_select_idx + 1)
                elif ch in (27, ord('\n')): self.mode = REPLMode.INPUT
            elif self.mode == REPLMode.EDIT_QUEUE:
                if ch == ord('\n'):
                    try: self.pending_queue[queue_select_idx] = shlex.split(edit_buffer)
                    except ValueError as e: self._print(f"[bold red]Edit error: {e}[/bold red]", target="repl")
                    self.mode = REPLMode.QUEUE
                elif ch == 27: self.mode = REPLMode.QUEUE
                elif ch in (curses.KEY_BACKSPACE, 127, 8): edit_buffer = edit_buffer[:-1]
                elif 32 <= ch <= 126: edit_buffer += chr(ch)
        worker_task.cancel()

    def run(self):
        try: curses.wrapper(lambda stdscr: asyncio.run(self._main_loop(stdscr)))
        except (KeyboardInterrupt, EOFError): pass

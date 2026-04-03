import asyncio
from typing import List
from pyrepl import PyREPL
from rich import print as rprint
from pathlib import Path
import typer
import subprocess

# Initialize the REPL
repl = PyREPL(prompt="pyrepl> ")

@repl.command(description="Execute a system shell command.")
def shell(args: List[str] = typer.Argument(..., help="The shell command and its arguments.")):
    """
    Passes the provided arguments to the system shell for execution.
    Example: .shell ls -la
    """
    try:
        result = subprocess.run(args, capture_output=False, text=True)
        if result.returncode != 0:
            rprint(f"[bold red]Command exited with code {result.returncode}[/bold red]")
    except FileNotFoundError:
        rprint(f"[bold red]Command not found: {args[0]}[/bold red]")
    except Exception as e:
        rprint(f"[bold red]Error executing command:[/bold red] {e}")

@repl.command(description="Print the contents of a file.")
def cat(path: Path = typer.Argument(..., help="The path to the file to read.")):
    """
    Reads a file from the local file system and prints its contents to the console.
    Supports absolute paths and home-directory expansion (~).
    """
    if path.is_file():
        rprint(path.read_text())
    else:
        rprint(f"[bold red]File not found: {path}[/bold red]")

@repl.command(description="Echo a message.")
def echo(message: str = typer.Argument(..., help="The message to repeat.")):
    """
    Simply repeats the provided message. Useful for testing tokenization and connectivity.
    """
    rprint(f"Echoing: [bold cyan]{message}[/bold cyan]")

@repl.command(description="Greet a user.")
def greet(
    name: str = typer.Argument(..., help="The name of the person to greet."),
    uppercase: bool = typer.Option(False, "--uppercase", "-u", help="If true, prints the greeting in ALL CAPS.")
):
    """
    Prints a friendly greeting. 
    Demonstrates the use of positional arguments and optional flags.
    """
    msg = f"Hello, {name}!"
    if uppercase:
        msg = msg.upper()
    rprint(f"[bold green]{msg}[/bold green]")

@repl.command(name=".add", description="Add two numbers.")
def add_numbers(a: int, b: int):
    rprint(f"[bold yellow]{a} + {b} = {a + b}[/bold yellow]")

# Register an ASYNC fallback handler
@repl.on_fallback
async def process(tokens: List[str]):
    """
    Default handler for non-dot commands.
    Runs asynchronously in a background worker.
    """
    rprint(f"[bold blue]\[Worker][/bold blue] Starting process for: {tokens}")
    # Simulate a long-running task
    await asyncio.sleep(3)
    rprint(f"[bold blue]\[Worker][/bold blue] Finished process for: {tokens}")

if __name__ == "__main__":
    # The framework handles its own event loop within curses.wrapper
    repl.run()

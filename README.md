# PyREPL Framework

A split-screen, asynchronous REPL framework for Python, leveraging `curses` and `typer`.

## Features

- **Split-Screen Interface**: Uses `curses` to divide the terminal into an output area (for background tasks) and a prompt area (for REPL commands).
- **Asynchronous Execution**: Background tasks are processed in a queue, keeping the REPL responsive.
- **Interactive Queue Management**: Use the Up arrow (when the prompt is empty) to enter "Queue Mode" where you can delete or edit pending tasks.
- **Easy Dot-Commands**: Register new commands with a simple `@repl.command` decorator using `typer`.
- **Bash-like Tokenization**: Standard user input is tokenized (handling quotes and escapes) and passed to a customizable fallback function.
- **Real-Time Streaming**: Any output printed to `sys.stdout` is captured and streamed to the appropriate terminal area immediately.
- **Tab Completion**: Supports dot-command and file path autocompletion with an interactive selection menu.

## Installation

```bash
pip install .
```

## Quick Start

```python
from pyrepl import PyREPL
import asyncio

repl = PyREPL()

@repl.command(description="Say hello.")
def hello(name: str):
    print(f"Hello, {name}!")

@repl.on_fallback
async def process(tokens):
    print(f"Working on {tokens}...")
    await asyncio.sleep(2)
    print(f"Done with {tokens}!")

if __name__ == "__main__":
    repl.run()
```

## UI Controls

- **Up/Down Arrows**: Scroll the prompt area history.
- **Page Up/Page Down**: Scroll the output (background task) area.
- **Tab**: Trigger autocompletion for dot-commands and paths.
- **Ctrl-D (EOF)**: Exit the REPL.
- **Queue Mode** (Empty prompt + Up arrow):
    - `x`: Delete selected task.
    - `e`: Edit selected task.
    - `Esc/Enter`: Exit Queue Mode.

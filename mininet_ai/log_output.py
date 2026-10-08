"""Literal, attributed terminal and plain-file presentation of run diagnostics."""

import logging
import os
from datetime import UTC, datetime

from rich.console import Console
from rich.text import Text


def literal(value: str, *, multiline: bool = False) -> str:
    """Make terminal controls visible without interpreting message markup."""
    return "".join(
        char
        if (char == "\n" and multiline)
        or (ord(char) >= 32 and not 127 <= ord(char) < 160)
        else "\\n"
        if char == "\n"
        else f"\\x{ord(char):02x}"
        for char in value
    )


class RunLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        )
        return (
            f"{timestamp} {record.levelname} run_id={record.__dict__['run_identity']} "
            f"[{literal(record.__dict__['source'])}] {literal(record.getMessage())}"
        )


def print_event(console: Console, level: str, source: str, message: str) -> None:
    label = {"WARNING": "WARN", "ERROR": "ERR", "CRITICAL": "ERR"}.get(level, level)
    severity_style = (
        "red"
        if level in {"ERROR", "CRITICAL"}
        else "yellow"
        if level == "WARNING"
        else ""
    )
    source_style = (
        "cyan" if source == "Run" else "blue" if source == "Mininet" else "magenta"
    )
    colored = console.is_terminal and "NO_COLOR" not in os.environ
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    for line in literal(message, multiline=True).split("\n"):
        text = Text(f"{timestamp} ")
        text.append(f"{label:<4}", style=severity_style if colored else "")
        text.append(" ")
        text.append(f"[{literal(source)}]", style=source_style if colored else "")
        text.append(f" {line}")
        console.print(text, soft_wrap=True)

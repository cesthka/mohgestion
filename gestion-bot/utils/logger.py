"""Colored logging setup."""
from __future__ import annotations

import logging
import sys

import colorlog


def setup_logging(level: int = logging.INFO) -> None:
    """Configure colored console logging for the whole process."""
    handler = colorlog.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s[%(asctime)s] %(levelname)-8s%(reset)s "
            "%(cyan)s%(name)s%(reset)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "blue",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    # Remove existing handlers (avoid duplicate logs on reload)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)

    # Quiet some noisy libs
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)

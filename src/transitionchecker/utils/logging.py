from __future__ import annotations

import logging


def level_for_verbosity(verbosity: int) -> int:
    """Map -v count style verbosity to a logging level."""

    if verbosity >= 2:
        return logging.DEBUG
    if verbosity >= 1:
        return logging.INFO
    return logging.WARNING


def configure_logging(verbosity: int) -> None:
    """Configure root logging for CLI commands."""

    logging.basicConfig(
        level=level_for_verbosity(verbosity),
        format="%(levelname)s: %(message)s",
    )

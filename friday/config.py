from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from friday.models import MentionType

DEFAULT_PATH = Path("config.yaml")


class ConfigError(Exception):
    """Configuration is missing or cannot be understood."""


@dataclass(frozen=True, slots=True)
class IngestConfig:
    """Which conversations and which kinds of mention are watched."""

    watched_channels: frozenset[str]
    mention_types: frozenset[MentionType]


@dataclass(frozen=True, slots=True)
class Config:
    database_path: str
    ingest: IngestConfig


def load_config(path: Path | str = DEFAULT_PATH) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"No configuration file at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc

    ingest = raw.get("ingest") or {}
    return Config(
        database_path=raw.get("database_path", "./data/friday.db"),
        ingest=IngestConfig(
            watched_channels=frozenset(ingest.get("watched_channels") or ()),
            mention_types=frozenset(
                _mention_type(value) for value in ingest.get("mention_types") or ()
            ),
        ),
    )


def _mention_type(value: str) -> MentionType:
    try:
        return MentionType(value)
    except ValueError as exc:
        known = ", ".join(m.value for m in MentionType)
        raise ConfigError(
            f"Unknown mention type {value!r}. Known types: {known}"
        ) from exc

"""Ticket 01 — scoping is configured, not hardcoded."""

from __future__ import annotations

import pytest

from friday.config import ConfigError, load_config
from friday.domain.models import MentionType

SAMPLE = """
database_path: ./data/friday.db
ingest:
  watched_channels: ["ops", "bugs"]
  mention_types: ["direct", "dm"]
"""


def test_scoping_is_read_from_the_configuration_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE)

    config = load_config(path)

    assert config.database_path == "./data/friday.db"
    assert config.ingest.watched_channels == frozenset({"ops", "bugs"})
    assert config.ingest.mention_types == frozenset(
        {MentionType.DIRECT, MentionType.DM}
    )


def test_an_unknown_mention_type_is_rejected_by_name(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text('ingest:\n  mention_types: ["shouted"]\n')

    with pytest.raises(ConfigError, match="shouted"):
        load_config(path)


def test_a_missing_configuration_file_is_reported_by_path(tmp_path):
    with pytest.raises(ConfigError, match="nope.yaml"):
        load_config(tmp_path / "nope.yaml")


def test_an_unquoted_channel_id_still_matches(tmp_path):
    """YAML parses a bare id as an int; a silent no-match would watch nothing."""
    path = tmp_path / "config.yaml"
    path.write_text("ingest:\n  watched_channels: [1360170800153366600]\n")

    config = load_config(path)

    assert config.ingest.watched_channels == frozenset({"1360170800153366600"})

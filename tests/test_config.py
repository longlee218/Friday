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


# --- durations (board `work-that-has-gone-cold`, ticket 01) ------------------


import pytest


@pytest.mark.parametrize(
    "written,seconds",
    [("10s", 10), ("90s", 90), ("10m", 600), ("10h", 36000), ("24h", 86400)],
)
def test_a_duration_reads_as_what_it_says(written, seconds):
    """`24h` says what it is. `max_message_age_hours: 24` puts the unit in the
    key and the number somewhere else, and the reader has to hold both."""
    from friday.config import duration

    assert duration(written, key="max_message_age") == seconds


@pytest.mark.parametrize("bad", ["24", 24, "24 h", "24x", "h", "", "-1h", "1.5h"])
def test_a_duration_that_is_not_one_is_refused_at_load(bad):
    """Refused where it is written, not where it is used. An unparsed
    threshold reaching the triage runner means either a crash on the first
    message or — worse — a silent zero, which would mark every message
    outdated and read as the agent having stopped working."""
    from friday.config import ConfigError, duration

    with pytest.raises(ConfigError, match="max_message_age"):
        duration(bad, key="max_message_age")


def test_no_duration_at_all_is_a_real_answer():
    """Absent means no cutoff — the behaviour that exists today. That is the
    shipped default, for the reason `daily_token_budget` has none."""
    from friday.config import duration

    assert duration(None, key="max_message_age") is None

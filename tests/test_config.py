"""Ticket 01 — scoping is configured, not hardcoded."""

from __future__ import annotations

import pytest

from friday.kernel.config import ConfigError, load_config
from friday.kernel.domain.models import MentionType

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


# --- extraction_budget_tokens (ticket 08: the build respects a budget) -----


def test_an_unset_extraction_budget_means_no_compaction(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE)

    config = load_config(path)

    assert config.context.extraction_budget_tokens is None


def test_the_extraction_budget_is_read_from_configuration(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE + "\ncontext:\n  extraction_budget_tokens: 500\n")

    config = load_config(path)

    assert config.context.extraction_budget_tokens == 500


@pytest.mark.parametrize("bad", [0, -1, -500])
def test_a_budget_that_cannot_be_a_budget_is_refused_at_load(tmp_path, bad):
    """D6: "a budget clause that cannot be evaluated fails loudly; it is
    never dropped" — the failure shape that emptied five context mechanisms
    in this repo was a bad value tolerated at run time. Zero or negative
    reaches nobody's transcript before the operator is told at startup."""
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE + f"\ncontext:\n  extraction_budget_tokens: {bad}\n")

    with pytest.raises(ConfigError, match="extraction_budget_tokens"):
        load_config(path)


def test_the_pool_works_two_tasks_at_once_unless_told_otherwise(tmp_path):
    """Ticket 13: small by default — every graph shares one SQLite file and
    one provider's rate limit, so this is a knob an operator raises."""
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE)
    assert load_config(path).workflows.concurrency == 2

    path.write_text(SAMPLE + "\nworkflows:\n  concurrency: 5\n")
    assert load_config(path).workflows.concurrency == 5


@pytest.mark.parametrize("bad", [0, -1])
def test_a_pool_that_could_run_nothing_is_refused_at_load(tmp_path, bad):
    """Zero slots is a pool that takes every task and never acts on one."""
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE + f"\nworkflows:\n  concurrency: {bad}\n")

    with pytest.raises(ConfigError, match="concurrency"):
        load_config(path)


def test_declared_secrets_gathers_keys_env_and_tokens(tmp_path):
    """Value-based redaction (§12) needs the exact secrets this deployment
    holds: the tiers' API keys, an MCP server's declared env and auth secret,
    and the tokens the composition root read from the environment."""
    from friday.kernel.config import declared_secrets

    path = tmp_path / "config.yaml"
    path.write_text(
        SAMPLE
        + """
tiers:
  flash:
    api_key: sk-agent-key
    base_url: https://api.example.invalid/v1
    model: some-model
mcp_servers:
  loki:
    command: npx
    env:
      LOKI_TOKEN: loki-secret
    auth:
      client_secret: oauth-secret
"""
    )
    config = load_config(path)

    secrets = declared_secrets(config, "discord-user-token", None)

    assert secrets == {
        "sk-agent-key",
        "loki-secret",
        "oauth-secret",
        "discord-user-token",
    }


def test_the_backup_dir_has_a_default_and_is_read(tmp_path):
    """Two-file backup (§12.1): where backups go is an install fact; how many
    days are kept is `KEEP_BACKUPS`, a knob."""
    default_path = tmp_path / "default.yaml"
    default_path.write_text(SAMPLE)
    assert load_config(default_path).backup_dir == "data/backups"

    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE + "\nbackup_dir: /var/backups/friday\n")
    assert load_config(path).backup_dir == "/var/backups/friday"


@pytest.mark.parametrize(
    "extra",
    [
        "keep_backups: 3\n",
        "heartbeat_seconds: 60\n",
        "down_after_seconds: 300\n",
        "summary_at_hour: 9\n",
        "keep_model_calls_days: 14\n",
        "outbox:\n  max_attempts: 3\n",
        "context:\n  summary_max_chars: 6000\n",
    ],
)
def test_a_knob_left_in_the_file_is_refused_and_says_where_it_went(tmp_path, extra):
    """A knob the operator edits and nothing reads is a change that silently
    does nothing — so a moved one is refused, naming its constant."""
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE + extra)

    with pytest.raises(ConfigError, match="no longer read.*[A-Z_]{6,}"):
        load_config(path)


@pytest.mark.parametrize(
    "knob", ["turn_seconds: 12", "sweep_interval_seconds: 300", "context_messages: 20"]
)
def test_an_ingest_knob_left_in_the_file_is_refused(tmp_path, knob):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE + "  " + knob + "\n")

    with pytest.raises(ConfigError, match="ingest"):
        load_config(path)

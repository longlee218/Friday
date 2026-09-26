"""The devops plugin's own config block, validated against its own schema.

Moved out of `friday.kernel.config.ApiIssueConfig` in ticket 14: a plugin owns its
configuration and validates it, so the core config no longer names `api_issue`.
The composition root hands the plugin the raw `devops:` block from `config.yaml`
and this turns it into a `DevopsConfig` (or refuses it, naming the bad key).

Every field is empty or a default on purpose: a fresh install has no dev host
and no Loki server, and every node that needs one skips with a reason rather
than failing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

#: Where a service's source sits inside its image, stripped from a stack frame
#: before it is joined to the clone. The default image layout; an operator whose
#: images differ overrides `container_roots` in the `devops:` block. Data now
#: (ticket 14, box 4), not a constant buried in the code reader — a frame path
#: is the one thing here shaped by the deployment, so it is the one thing an
#: operator must be able to correct without a release.
DEFAULT_CONTAINER_ROOTS = ("/usr/src/app", "/app", "/srv/app")

#: Frames that are somebody else's code — dropped before a frame is opened.
#: The default vendored-path set; overridable the same way for a stack that
#: nests third-party code somewhere these do not name.
DEFAULT_NOT_OURS = ("node_modules", "/internal/", "node:internal")


class ConfigError(Exception):
    """The `devops:` block is missing a setting's meaning or names one that
    does not exist. Its own class rather than `friday.kernel.config.ConfigError`,
    because a plugin does not import the core config — the host catches this and
    reports it the same way it reports its own."""


@dataclass(frozen=True, slots=True)
class DevopsConfig:
    """What the `devops.api_issue` graph reaches outside this process with."""

    #: The SSH alias `kubectl` runs behind, from `~/.ssh/config`. Empty means
    #: dev logs are not read at all — measured 2026-09-18: there is no
    #: kubeconfig for dev on this machine, so this is the only way in.
    ssh_host: str = ""
    #: Which configured MCP server carries the Loki tools, and which of its
    #: tools answers a range query. A name, not code, because the session
    #: that measured the server read its catalogue without running a query
    #: through it — so this is the field most likely to be wrong, and it
    #: should be correctable without a release.
    loki_server: str = "devops-generic"
    loki_tool: str = "loki_query_range"
    #: Where a run's report is written. Beside the database, for the same
    #: reason: state this process produced, not source.
    reports_dir: str = "./data/reports"
    #: **The longest one task may hold a pool slot**, checked at boot against
    #: the sum of the graph's node ceilings. A budget rather than a runtime
    #: kill: cancelling a graph half way leaves a task that has read a log,
    #: queued an acknowledgement and written nothing. 420s because the graph's
    #: ceilings sum to 400; the lever, if seven minutes is too long, is
    #: `agents."devops.diagnose".timeout_seconds`, which is 150 of it.
    timeout_seconds: float = 420.0
    #: The container source roots and vendored-path markers the code reader maps
    #: a stack frame against (box 4): data, so an operator whose images lay
    #: their source out differently corrects it in `config.yaml` rather than in
    #: the reader. See `DEFAULT_CONTAINER_ROOTS` / `DEFAULT_NOT_OURS`.
    container_roots: tuple[str, ...] = DEFAULT_CONTAINER_ROOTS
    not_ours: tuple[str, ...] = DEFAULT_NOT_OURS


def load_devops_config(raw: object) -> DevopsConfig:
    """The `devops:` block, or its defaults. An unknown key is refused rather
    than ignored: a misspelled `ssh_host` that reads as absent is a graph that
    silently stops reading dev logs."""
    if raw is None:
        return DevopsConfig()
    if not isinstance(raw, dict):
        raise ConfigError("devops: expected a block of settings")
    known = {f.name for f in fields(DevopsConfig)}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError(
            f"devops: unknown setting(s) {unknown} (known: {sorted(known)})"
        )
    raw = dict(raw)
    clock = raw.pop("timeout_seconds", None)
    # The two path lists are the one place a setting is a list rather than a
    # scalar, so they are taken out before the `str(v)` coercion below (which
    # would render a list as its `repr`). A single string is accepted as a
    # one-element list, since one root is the common override.
    paths: dict[str, tuple[str, ...]] = {}
    for key in ("container_roots", "not_ours"):
        if key not in raw:
            continue
        value = raw.pop(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(p, str) for p in value
        ):
            raise ConfigError(
                f"devops: {key} must be a list of paths, not {value!r}"
            )
        paths[key] = tuple(value)
    # `ssh_host:` with nothing after it parses as `None`, and `str(None)` is
    # `"None"` — a truthy hostname that turns every dev task into a failed
    # `ssh None`. An empty setting means "not configured".
    settings: dict = {k: "" if v is None else str(v) for k, v in raw.items()}
    if clock is not None:
        try:
            settings["timeout_seconds"] = float(clock)
        except (TypeError, ValueError):
            raise ConfigError(
                f"devops: timeout_seconds must be a number, not {clock!r}"
            ) from None
        if settings["timeout_seconds"] <= 0:
            raise ConfigError(
                f"devops: timeout_seconds must be positive, not {clock!r}"
            )
    settings.update(paths)
    return DevopsConfig(**settings)

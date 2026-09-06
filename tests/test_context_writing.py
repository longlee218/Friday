"""Board `a-window-on-the-whole-path`, ticket 03 — the one thing the page writes.

This reverses `docs/SPEC.md`'s "any interaction on the web page", narrowly:
`overrides` is context, not a decision (D7). What the tests here defend is the
narrowness — that `derived` survives, that values reach a prompt unescaped, and
that one rule decides when an edit takes effect (D8).
"""

from __future__ import annotations

import pytest
import yaml

from friday.memory.channel_context import ContextStore


@pytest.fixture
def store(tmp_path) -> ContextStore:
    return ContextStore(tmp_path)


# --- the store ---------------------------------------------------------------


def test_writing_overrides_leaves_the_machines_own_section_alone(store):
    """`derived` is the summariser's, rebuilt from the room's transcript. An
    operator's edit that dropped it would throw away a model call."""
    store.init_channel("c1")
    store.rebuild_derived("c1", {"summary": "they deploy on fridays"})

    store.set_overrides("c1", {"escalate_to": "on-call"})

    on_disk = yaml.safe_load(store.path_for("c1").read_text())
    assert on_disk["derived"] == {"summary": "they deploy on fridays"}
    assert on_disk["overrides"] == {"escalate_to": "on-call"}


def test_writing_overrides_leaves_the_summary_bookmark_alone(store):
    """`state.summary_of` decides whether the room gets re-summarised. Losing
    it means paying for a summary call the next heartbeat, every heartbeat."""
    store.init_channel("c1")
    store.remember_summary_of("c1", "m-42")

    store.set_overrides("c1", {"escalate_to": "on-call"})

    on_disk = yaml.safe_load(store.path_for("c1").read_text())
    assert on_disk["state"] == {"summary_of": "m-42"}


def test_overrides_are_replaced_wholesale_not_merged(store):
    """A key the operator deleted on the page has to actually go. Merging
    would make removal impossible from the only UI that can write."""
    store.init_channel("c1", {"old": "gone", "kept": "yes"})

    store.set_overrides("c1", {"kept": "yes"})

    assert store.load("c1").overrides == {"kept": "yes"}


def test_a_write_does_not_take_effect_until_a_reload(store):
    """D8: one rule for when an edit takes effect. Immediate for a page edit
    and restart-only for a hand-edit is the two rules `_held`'s own comment
    refuses."""
    store.init_channel("c1")
    store.hold_all()

    store.set_overrides("c1", {"escalate_to": "on-call"})

    assert store.context("c1").overrides == {}
    store.reload()
    assert store.context("c1").overrides == {"escalate_to": "on-call"}


def test_a_reload_picks_up_a_file_edited_by_hand(store):
    """The same button, so hand-editing is better off than before rather than
    worse — it used to need a process restart."""
    store.init_channel("c1")
    store.hold_all()
    store.path_for("c1").write_text(
        yaml.safe_dump({"derived": {}, "overrides": {"by": "hand"}})
    )

    store.reload()

    assert store.context("c1").overrides == {"by": "hand"}


def test_a_reload_reports_a_file_it_could_not_parse(store):
    """A channel silently losing its context is the failure `validate_all`
    exists for; the reload is the moment somebody is watching."""
    store.path_for("broken").write_text("derived: [unclosed")

    problems = store.reload()

    assert any("broken" in p for p in problems)


def test_creating_a_channel_twice_refuses(store):
    store.init_channel("c1", {"a": "b"})

    with pytest.raises(FileExistsError):
        store.init_channel("c1")

    assert store.load("c1").overrides == {"a": "b"}


def test_values_are_stored_plain(store):
    """`ChannelContext`: "Every value here is plain text. Escaping happens
    once, on the way into a prompt." Escaping here shows the model
    `&amp;lt;b&amp;gt;`."""
    store.init_channel("c1")

    store.set_overrides("c1", {"note": "<b>prod</b> & staging"})

    assert store.load("c1").overrides["note"] == "<b>prod</b> & staging"


def test_an_override_shadows_the_machines_value_in_the_merge(store):
    """Allowed — that is what an override is — and the reason the page has to
    say so: the summariser goes on recomputing a value nobody will read."""
    store.init_channel("c1")
    store.rebuild_derived("c1", {"summary": "machine says this"})
    store.set_overrides("c1", {"summary": "no, this"})

    assert store.load("c1").merged()["summary"] == "no, this"


# --- over HTTP ---------------------------------------------------------------


@pytest.fixture
def client(db, store):
    from fastapi.testclient import TestClient

    from friday.ops.api import build_api

    return TestClient(
        build_api(
            db=db,
            provider_status=lambda: "connected",
            origins=["http://x"],
            context_store=store,
        )
    )


def test_the_write_routes_are_absent_when_no_store_is_wired(db, tmp_path, monkeypatch):
    """Composition, not configuration: an API built without a context store
    has no way to write, and does not describe one.

    `PAGE` is pointed at nothing on purpose. With a built `web/dist` present
    the SPA catch-all matches every unknown path for GET, so an unknown POST
    comes back 405 rather than 404 — a difference that would make this test
    depend on whether somebody had run `npm run build`.
    """
    from fastapi.testclient import TestClient

    from friday.ops import api as api_module
    from friday.ops.api import build_api

    monkeypatch.setattr(api_module, "PAGE", tmp_path / "absent")
    read_only = TestClient(build_api(db=db, provider_status=lambda: "x"))

    assert read_only.post("/api/context/reload").status_code == 404
    assert read_only.put(
        "/api/channels/c1/context/overrides", json={"overrides": {}}
    ).status_code == 404


def test_a_channel_list_works_before_any_channel_has_a_file(client):
    """`context/` is empty as shipped — nothing has ever written one."""
    assert client.get("/api/channels").json() == []


def test_creating_then_editing_a_channels_overrides(client, store):
    assert client.post("/api/channels/c1/context").status_code == 201

    saved = client.put(
        "/api/channels/c1/context/overrides",
        json={"overrides": {"escalate_to": "on-call"}},
    )

    assert saved.status_code == 200
    assert store.load("c1").overrides == {"escalate_to": "on-call"}


def test_creating_a_channel_that_has_one_is_a_conflict_not_a_crash(client):
    client.post("/api/channels/c1/context")

    second = client.post("/api/channels/c1/context")

    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


def test_writing_to_a_channel_with_no_file_says_so(client):
    answer = client.put(
        "/api/channels/ghost/context/overrides", json={"overrides": {"a": "b"}}
    )

    assert answer.status_code == 404


def test_a_value_that_is_not_text_is_refused(client):
    """Key/value pairs of text (D9). A dict here reaches a prompt rendered as
    a Python repr, which is not a thing anybody wrote on purpose."""
    client.post("/api/channels/c1/context")

    answer = client.put(
        "/api/channels/c1/context/overrides",
        json={"overrides": {"rules": {"nested": "no"}}},
    )

    assert answer.status_code == 422


def test_a_saved_edit_is_not_live_until_reloaded(client, store):
    """D8, over HTTP: `live` is what the agents are using, `merged` is what is
    on disk. They differ exactly between saving and reloading."""
    client.post("/api/channels/c1/context")
    store.hold_all()
    client.put(
        "/api/channels/c1/context/overrides", json={"overrides": {"tone": "terse"}}
    )

    before = client.get("/api/channels/c1/context").json()
    assert before["overrides"]["tone"] == "terse"
    assert "terse" in before["prompt"]
    assert "terse" not in (before["live"] or "")

    client.post("/api/context/reload")

    after = client.get("/api/channels/c1/context").json()
    assert "terse" in after["live"]


def test_the_page_is_told_which_keys_shadow_the_machines_own(client, store):
    client.post("/api/channels/c1/context")
    store.rebuild_derived("c1", {"summary": "machine wrote this"})
    client.put(
        "/api/channels/c1/context/overrides", json={"overrides": {"summary": "no"}}
    )

    assert client.get("/api/channels/c1/context").json()["also_in"] == ["summary"]


def test_a_written_override_reaches_a_prompt_after_a_reload(client, store):
    """The end of the whole ticket: a value typed on the page is what an agent
    is actually told about the room. Rendered through the real prompt seam —
    `channel_sections`, the one an agent is built from — and not asserted
    against the store, which would prove only that a file was written.

    Note which seam that is. `ChannelContext.merged()` exists and no prompt
    has ever used it: the three layers reach a model as three labelled
    sections, not as one merged dict."""
    from friday.agent.instruction_prompt import channel_sections

    client.post("/api/channels/c1/context")
    client.put(
        "/api/channels/c1/context/overrides",
        json={"overrides": {"escalate_to": "the on-call engineer"}},
    )
    client.post("/api/context/reload")

    assert "the on-call engineer" in channel_sections(store.context("c1"))


def test_a_value_reaches_the_prompt_escaped_exactly_once(client, store):
    """Stored plain, escaped at the seam. Escaping on the way in would show
    the model `&amp;lt;b&amp;gt;`."""
    from friday.agent.instruction_prompt import channel_sections

    client.post("/api/channels/c1/context")
    client.put(
        "/api/channels/c1/context/overrides",
        json={"overrides": {"note": "<b>prod</b>"}},
    )
    client.post("/api/context/reload")

    rendered = channel_sections(store.context("c1"))

    assert "&lt;b&gt;prod&lt;/b&gt;" in rendered
    assert "&amp;lt;" not in rendered


# --- the store owns its directory --------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    ["../escaped", "../../escaped", "a/b", "/etc/passwd", "..", ".", ""],
)
def test_a_channel_id_cannot_name_a_file_outside_the_context_directory(
    store, hostile
):
    """`path_for` is `self._dir / f"{channel_id}.yaml"` and `channel_id`
    arrives from an unauthenticated HTTP route.

    Starlette's routing happens to refuse the encoded-slash spellings today,
    because `{channel_id}` is one path segment — but that is the router
    defending the store, and the store is the thing that owns this directory.
    The sibling failure on the read side (`servable`) was exactly this shape
    and it *was* reachable, so this is the belt to that braces rather than a
    hypothetical.
    """
    with pytest.raises(ValueError):
        store.path_for(hostile)


def test_an_ordinary_channel_id_still_resolves(store):
    """Discord ids are digits; the tests here use words. Both are files
    directly inside the directory and must keep working."""
    assert store.path_for("1234567890").name == "1234567890.yaml"
    assert store.path_for("watched").parent == store.path_for("other").parent


def test_base_is_not_a_channel_and_cannot_be_written_as_one(store):
    """`BASE_NAME` is `base.yaml` and `path_for` built it from any
    `channel_id`. `known_channels()` hides it from listings, so it never
    appears as a tab — but `set_overrides("base", ...)` found it, because the
    only check was `path_for(channel_id).exists()` and it does.

    No slash needed, so neither the routing regex nor the containment check
    added for the read-side traversal helps. What it costs: `base.yaml` is
    the layer that reaches *every* channel, and it is the layer
    `channel_base` calls trusted and does not escape.
    """
    from friday.memory.channel_context import BASE_NAME

    store.path_for("real").parent.mkdir(parents=True, exist_ok=True)
    (store._dir / BASE_NAME).write_text("who: the operator's agent\n")

    with pytest.raises(ValueError):
        store.path_for("base")

    assert "who: the operator's agent" in (store._dir / BASE_NAME).read_text()


def test_the_base_layer_still_reaches_a_channel_that_has_one(store):
    """Refusing to *write* it must not stop it being *read* — `base` is
    where what is true everywhere lives."""
    from friday.memory.channel_context import BASE_NAME

    store._dir.mkdir(parents=True, exist_ok=True)
    (store._dir / BASE_NAME).write_text("who: the operator's agent\n")
    store.init_channel("c1")

    assert store.load("c1").base == {"who": "the operator's agent"}


def test_an_id_the_store_refuses_reads_as_a_bad_request_not_a_crash(client):
    """The store raises `ValueError` for an id that is not one. Unhandled,
    that reaches the page as a 500 — which reads as "the server is broken"
    rather than "that is not a channel", and is the same objection ticket 03
    already made about `FileExistsError` arriving as one.

    Only `base` is exercised here, and the omission is deliberate: `.` and
    `..` cannot reach a route at all, because an HTTP client normalises them
    out of the path before the request is sent. Parametrising them would add
    two cases that pass whether or not the handler exists — the vacuity this
    review has already caught twice. They are covered at the store, which is
    where they are decidable.
    """
    hostile = "base"
    created = client.post(f"/api/channels/{hostile}/context")
    written = client.put(
        f"/api/channels/{hostile}/context/overrides", json={"overrides": {"a": "b"}}
    )

    assert created.status_code == 400, created.text
    assert written.status_code == 400, written.text
    assert "not a channel id" in created.json()["detail"]


def test_a_parse_failure_does_not_hand_back_the_file_it_failed_on(client, store):
    """`validate_all` returns `f"{path}: {exc}"`, and a `yaml.YAMLError`
    quotes the offending source line — so a broken context file returns its
    own content over HTTP. That content is operator-written and can hold
    anything they pasted into it.

    `friday/ops/api.py`'s whole stated reason to exist is that it is "the last
    place a credential can be caught" and that "all of it is scrubbed on the
    way out". The routes added for this ticket were the exception."""
    store._dir.mkdir(parents=True, exist_ok=True)
    (store._dir / "broken.yaml").write_text(
        "overrides: [unclosed\ntoken: sk-abcdefghijklmnopqrstuvwx\n"
    )

    answer = client.post("/api/context/reload")

    assert answer.status_code == 200
    assert "sk-abcdefghijklmnopqrstuvwx" not in answer.text
    assert answer.json()["problems"], "the operator still has to be told"


def test_every_route_on_this_api_scrubs_what_it_returns():
    """A rule worth stating is worth a test, and this one is stated in the
    module's own docstring. It stopped being true the moment the context
    routes were added — four of them returned strings straight."""
    import ast
    import pathlib

    source = pathlib.Path("friday/ops/api.py").read_text()
    tree = ast.parse(source)
    unscrubbed = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr in {"get", "post", "put"}
            for d in node.decorator_list
        )
        if not decorated or node.name == "page":
            continue  # `page` streams a file; it has no strings of its own
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return) and n.value]
        for ret in returns:
            called = {
                n.func.id
                for n in ast.walk(ret)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            if "_clean" not in called:
                unscrubbed.append(f"{node.name}:{ret.lineno}")

    assert not unscrubbed, f"routes returning unscrubbed strings: {unscrubbed}"

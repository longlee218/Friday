"""Ticket 19 — `repo_path` is chosen, not spelled.

The last free-text field on the `project` form is a path on the operator's
own machine, and a typo in it reads exactly like a correct one: the row looks
right in the form and `ReadFailingCode` quietly reads nothing.

The board runs on loopback on that machine, so a route that lists directories
is possible here where it would not be on a server. **What makes it
defensible is the confinement**, which is the same guard
`friday/sources/code.py:repo_file` already applies to a stack frame: resolve
first, then refuse anything that lands outside the root. Never an unbounded
filesystem browser, and never a path from the client trusted as given.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from friday.ops.api import build_api


def board(db, root=None) -> TestClient:
    return TestClient(
        build_api(
            db=db, provider_status=lambda: "connected",
            repo_root=None if root is None else str(root),
        )
    )


def _repos(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name in ("BE-ReelMe-V2", "BE-Midas", ".git"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("not a directory")
    (tmp_path / "BE-ReelMe-V2" / "src").mkdir()
    return tmp_path


async def test_it_lists_the_directories_under_the_root(db, tmp_path):
    client = board(db, _repos(tmp_path))

    found = client.get("/api/directories").json()

    assert found["directories"] == ["BE-Midas", "BE-ReelMe-V2"]
    assert found["path"] == ""


async def test_it_says_nothing_about_what_is_in_them(db, tmp_path):
    """Directories only. The operator is choosing a repository, and a file
    listing is a view of their machine that nothing here needs."""
    client = board(db, _repos(tmp_path))

    found = client.get("/api/directories").json()

    assert "notes.txt" not in found["directories"]


async def test_a_dotted_directory_is_not_offered(db, tmp_path):
    """`.git`, `.codegraph`, `.venv` — never the answer to "which repository",
    and offering them is offering a wrong choice that looks like a choice."""
    client = board(db, _repos(tmp_path))

    assert ".git" not in client.get("/api/directories").json()["directories"]


async def test_it_descends(db, tmp_path):
    client = board(db, _repos(tmp_path))

    found = client.get("/api/directories?path=BE-ReelMe-V2").json()

    assert found["directories"] == ["src"]
    assert found["path"] == "BE-ReelMe-V2"
    assert found["absolute"].endswith("BE-ReelMe-V2")


async def test_it_refuses_a_path_that_climbs_out_of_the_root(db, tmp_path):
    """The same guard `repo_file` applies to a stack frame. A path from a
    client is a path from outside, whatever it looks like."""
    client = board(db, _repos(tmp_path / "root"))

    refused = client.get("/api/directories?path=../elsewhere")

    assert refused.status_code == 422
    assert "outside" in refused.json()["detail"]


async def test_it_refuses_an_absolute_path(db, tmp_path):
    client = board(db, _repos(tmp_path))

    refused = client.get("/api/directories?path=/etc")

    assert refused.status_code == 422


async def test_it_refuses_a_symlink_pointing_out_of_the_root(db, tmp_path):
    """Resolving first is what catches this: the name is inside the root and
    the thing it names is not."""
    root = _repos(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secrets").mkdir()
    (root / "escape").symlink_to(outside)

    refused = client_get(board(db, root), "escape")

    assert refused.status_code == 422
    assert "outside" in refused.json()["detail"]


def client_get(client, path):
    return client.get(f"/api/directories?path={path}")


async def test_with_no_root_configured_there_is_nothing_to_browse(db):
    """Off unless an operator says where. A board that browses `/` by
    default is one nobody meant to switch on."""
    refused = board(db).get("/api/directories")

    assert refused.status_code == 404
    assert "repo_root" in refused.json()["detail"]


async def test_a_directory_that_cannot_be_read_is_empty_rather_than_an_error(
    db, tmp_path
):
    client = board(db, _repos(tmp_path))

    found = client.get("/api/directories?path=BE-Midas").json()

    assert found["directories"] == []


async def test_the_form_says_which_field_is_picked_from_the_filesystem(db, tmp_path):
    """Declared on the field, the way `names` is — so the page needs no list
    of its own and cannot disagree with one."""
    client = board(db, _repos(tmp_path))

    kinds = client.get("/api/channels/c1/memory-kinds").json()
    (project,) = [k for k in kinds if k["kind"] == "project"]
    (repo_path,) = [f for f in project["fields"] if f["name"] == "repo_path"]

    assert repo_path["picks"] == "directory"
    assert all(
        f["picks"] == "" for f in project["fields"] if f["name"] != "repo_path"
    )

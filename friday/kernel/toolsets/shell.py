"""`core.shell`: one read command, here or over SSH on a declared host.

Board `domains-plug-in` ticket 03, amendment §3. **Read-only by an allowlist
enforced in code, not by prompt** (`docs/DESIGN.md` D6):

- the command is parsed with `shlex`, never a regex; `|` is allowed only when
  every segment is on `READ_COMMANDS`; any other operator (`;`, `&&`, `||`,
  `&`, `>`, `<`, parentheses) and any substitution (`$(`, a backtick) is
  refused. Operators are found with a non-POSIX lexer, which keeps quotes on
  a word, so a quoted `"|"` or `";"` stays an argument rather than becoming a
  pipe; each segment is then split POSIX-style into its argv;
- a known write flag (`find -delete`, `journalctl --vacuum-*`, …) is refused,
  and so is a `kubectl` flag that picks the credentials or the server
  (`--kubeconfig` names a file whose `exec` entry runs any binary, and the
  workspace can write that file);
- a secret is not read: `kubectl` refuses the `secret` resource (and
  `--raw`, `-f`, `-k`, which reach it another way), `ps` refuses printing
  environments, and every other command refuses an argument naming a
  credential file or directory (`SECRET_FILES`, `SECRET_DIRS`, matched
  case-insensitively — macOS is; checked for the commands that print content,
  grep's pattern included). A recursive `grep` would pass that check by
  naming only the directory above, so every `grep` runs with
  `--exclude`/`--exclude-dir` for the same names and may not `--include` or
  `-R`. **A guardrail by name, not a boundary**: `grep -r` on a symlink named
  otherwise still follows it, and a secret in a file named otherwise is read;
- the command is re-quoted segment by segment before a shell sees it, so an
  argument reaches the remote side as the literal it was parsed as;
- a refused command is **refused, not queued**: no approval, no pause. Each
  one is written to `audit_log` (task, toolset, host, command) so the operator
  widens the list by commit when it was harmless.

`sed` and `awk` are off the list on purpose: both have a script language that
writes files, so a flag list cannot make them read-only.

Over 200 lines because the allowlist, its parser and the one tool that runs
it are one guard: split, a reader would have to hold three files to see what
can reach a shell.

Output passes the secret redaction and enters the run's `Evidence` (numbered
lines a ref can cite), or with `save_to` goes into the task's workspace
instead of the context. `backend.logs`' `read_log` stays the way to read a
service's logs; this is for everything else.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import signal
from collections.abc import Sequence
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Any

from friday.kernel.harness.harness import tool
from friday.kernel.toolsets import workspace
from friday.sdk.redact import scrub
from friday.sdk.toolset import RunContext

__all__ = [
    "KUBECTL_VERBS",
    "LOCAL",
    "MAX_LINES",
    "READ_COMMANDS",
    "TIMEOUT_SECONDS",
    "REFUSED_FLAGS",
    "SECRET_DIRS",
    "SECRET_FILES",
    "refusal",
    "shell_tools",
]

log = logging.getLogger(__name__)

#: The toolset's name, as `audit_log` records it.
NAME = "core.shell"

#: The host name that means "this machine" rather than an SSH alias.
LOCAL = "local"

#: The read-command allowlist: the same for every plugin; a plugin only
#: chooses whether its contract grants `core.shell`.
READ_COMMANDS = frozenset({
    "cat", "df", "du", "find", "grep", "head", "journalctl", "kubectl", "ls",
    "ps", "tail", "wc",
})

#: `kubectl` reads only through these verbs, written first (`kubectl get …`).
KUBECTL_VERBS = frozenset({"get", "logs", "describe", "top"})

#: Flags refused per command: ones that write; `kubectl`'s ones that pick the
#: credentials, server or cache dir, or read around the `secret` check
#: (`--raw` takes an API path, `-f`/`-k` a manifest the workspace can write);
#: `grep`'s that defeat its excludes (`--include`, `-R` follows a symlink to
#: `.ssh`); `ps`'s `-E`, which prints environments. A flag matches itself or
#: `flag=value`; a one-letter flag also matches `-sVALUE`, and for `grep` and
#: `ps` (boolean clusters) anywhere in `-nR`.
REFUSED_FLAGS = {
    "find": frozenset({
        "-delete", "-exec", "-execdir", "-ok", "-okdir",
        "-fprint", "-fprint0", "-fprintf", "-fls",
    }),
    "journalctl": frozenset({
        "--vacuum-size", "--vacuum-time", "--vacuum-files", "--rotate",
        "--flush", "--sync", "--relinquish-var", "--smart-relinquish-var",
        "--setup-keys", "--update-catalog", "--cursor-file",
    }),
    "kubectl": frozenset({
        "--kubeconfig", "--server", "-s", "--token", "--cache-dir", "--as",
        "--as-group", "--as-uid", "--user", "--cluster", "--context",
        "--certificate-authority", "--client-certificate", "--client-key",
        "--username", "--password", "--insecure-skip-tls-verify",
        "--raw", "-f", "--filename", "-k", "--kustomize", "--template",
    }),
    "grep": frozenset({"--include", "-R", "--dereference-recursive"}),
    "ps": frozenset({"-E"}),
}

#: Commands whose one-letter flags cluster (`grep -nR`, `ps -ef`,
#: `kubectl -Af`). Matched letter by letter, so a value written into the
#: cluster (`-ojsonpath=…`) can be refused: write it as `-o jsonpath=…`.
_CLUSTERED = frozenset({"grep", "kubectl", "ps"})

#: The commands that print a file's content; only their arguments are
#: checked against the credential names (`ls` and `find` print names).
_CONTENT_READERS = frozenset({"cat", "grep", "head", "tail"})

#: File names whose content is a credential, as globs on the last path part.
#: Refused as an argument; excluded from every `grep`.
SECRET_FILES = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
    "id_ecdsa*", "id_ed25519*", "id_dsa*", "ssh_host_*", ".netrc", ".pgpass",
    ".git-credentials", ".npmrc", "*.tfstate", "*.tfstate.*", "environ",
    "admin.conf", "super-admin.conf", "kubelet.conf", "controller-manager.conf",
    "scheduler.conf", "token",
)

#: A name matching `SECRET_FILES` with one of these endings is a template.
_TEMPLATES = (".example", ".sample", ".template", ".dist")

#: Directories that hold credentials; any path through one is refused.
SECRET_DIRS = frozenset({
    ".ssh", ".aws", ".kube", ".gnupg", ".docker", ".azure", "gcloud",
    "secrets", "serviceaccount",
})

#: Lines shown to the model; the rest is counted and left for `save_to`.
MAX_LINES = 200

#: How long one command may run. `tail -f` or `kubectl logs -f` ends here.
TIMEOUT_SECONDS = 30


def _segments(command: str) -> list[list[str]] | str:
    """The pipeline's segments as argv lists, or why it is refused."""
    if "`" in command or "$(" in command:
        return "command substitution (`$(…)` or a backtick) is not allowed"
    lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError as error:
        return f"cannot parse the command: {error}"
    words: list[list[str]] = [[]]
    for token in tokens:
        if token == "|":
            words.append([])
        elif token and set(token) <= set(lexer.punctuation_chars):
            return f"{token!r} is not allowed — only `|` between read commands"
        else:
            words[-1].append(token)
    if not all(words):
        return "an empty command or an empty pipe segment"
    # The words still carry their quotes; POSIX-split each segment for argv.
    return [shlex.split(" ".join(segment)) for segment in words]


def _segment_refusal(argv: Sequence[str]) -> str | None:
    name = argv[0]
    if name not in READ_COMMANDS:
        return f"{name!r} is not on the read-command allowlist"
    if name == "kubectl" and (len(argv) < 2 or argv[1] not in KUBECTL_VERBS):
        return f"kubectl reads only with {sorted(KUBECTL_VERBS)}, written first"
    # Every argument of a content reader is checked, grep's pattern included:
    # telling the pattern from a path means parsing grep's options (`-ie.`,
    # `--regex=`, `-A 2`), and a wrong guess reads the file unchecked.
    for index, arg in enumerate(argv[1:], start=1):
        if name == "kubectl" and _names_secrets(arg):
            return "kubectl may not read the secret resource"
        if name == "kubectl" and ("-file=" in arg or arg.endswith("-file")):
            return "kubectl may not read a template file (`*-file` output)"
        if name == "ps" and index == 1 and _ps_environment(arg):
            return "ps may not print process environments"
        if name in _CONTENT_READERS and _secret_path(arg):
            return f"{arg!r} names a credential file or directory"
        for flag in REFUSED_FLAGS.get(name, ()):
            if _matches(name, flag, arg):
                return f"{name} {flag} is refused (it writes, changes credentials or reads around a guard)"
    return None


def _matches(name: str, flag: str, arg: str) -> bool:
    if arg == flag or arg.startswith(f"{flag}="):
        return True
    # GNU getopt takes any unambiguous prefix of a long option (`--inc=*`).
    option = arg.partition("=")[0]
    if arg.startswith("--") and len(option) > 3 and flag.startswith(option):
        return True
    if len(flag) != 2:
        return False
    cluster = name in _CLUSTERED and arg.startswith("-") and not arg.startswith("--")
    return arg.startswith(flag) or (cluster and flag[1] in arg[1:])


def _ps_environment(arg: str) -> bool:
    """BSD-style `ps e`/`ps eww`: the first argument, an option word without
    a dash, holding `e`. Only the first, so `ps -u deploy` is a user name."""
    return not arg.startswith("-") and arg.isalpha() and "e" in arg


def _names_secrets(arg: str) -> bool:
    """`secret`, `secrets`, `secret/x`, `pods,secrets`, `secrets.v1` — any
    spelling of the resource. Flags are not resources."""
    if arg.startswith("-"):
        return False
    return any(
        part.split("/")[0].split(".")[0].lower() in {"secret", "secrets"}
        for part in arg.split(",")
    )


def _secret_path(arg: str) -> bool:
    """Whether `arg`, or the value of an `--opt=value`, is a path through a
    credential directory or to a credential file."""
    for value in (arg.lower(), arg.partition("=")[2].lower()):
        if not value:
            continue
        path = PurePosixPath(value)
        if any(part in SECRET_DIRS for part in path.parts):
            return True
        if path.name.endswith(_TEMPLATES):
            continue
        if any(fnmatch(path.name, glob) for glob in SECRET_FILES):
            return True
    return False


#: What every `grep` is run with, so a recursive one skips what `_secret_path`
#: would have refused by name. Both GNU and BSD grep take these.
_GREP_EXCLUDES = tuple(f"--exclude={glob}" for glob in SECRET_FILES) + tuple(
    f"--exclude-dir={d}" for d in sorted(SECRET_DIRS)
)


def refusal(command: str) -> str | None:
    """Why `command` may not run, or `None` when every segment is a read."""
    segments = _segments(command)
    if isinstance(segments, str):
        return segments
    for argv in segments:
        if (why := _segment_refusal(argv)) is not None:
            return why
    return None


def _script(command: str) -> str:
    """The pipeline re-quoted: each argument a literal for the shell that runs
    it. Only called on a command `refusal` passed."""
    segments = _segments(command)
    assert not isinstance(segments, str)
    return " | ".join(
        shlex.join([argv[0], *_GREP_EXCLUDES, *argv[1:]] if argv[0] == "grep" else argv)
        for argv in segments
    )


async def _run(host: str, script: str) -> tuple[int, str]:
    """`(exit code, output)` of `script` on `host`. stderr is folded in so a
    failure explains itself. Its own process group, so a timeout kills the
    whole pipeline, not only the `sh` or `ssh` in front of it."""
    argv = (
        ["sh", "-c", script] if host == LOCAL
        else ["ssh", "-o", "BatchMode=yes", "--", host, script]
    )
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        return -1, f"(stopped after {TIMEOUT_SECONDS}s)"
    return await process.wait(), out.decode(errors="replace")


def shell_tools(run: RunContext, *, hosts: Sequence[str], audit: Any) -> list:
    """`run_command`, bound to this run and the declared `hosts`. `audit` is
    the kernel's `AuditLog`; every refusal is written through it."""
    declared = tuple(hosts)

    async def refuse(host: str, command: str, reason: str) -> str:
        log.info("shell refused on %s: %r (%s)", host, command, reason)
        await audit.shell_refused(
            task_id=run.task_id, toolset=NAME, host=host, command=command, reason=reason
        )
        return f"refused: {reason}. Nothing ran."

    @tool
    async def run_command(host: str, command: str, save_to: str = "") -> str:
        """Run one read-only command on a declared host and read its output.

        Allowed: `kubectl get|logs|describe|top`, `cat`, `grep`, `tail`,
        `head`, `ls`, `ps`, `df`, `du`, `wc`, `find`, `journalctl`, joined by
        `|` when every part is one of these. Anything else — another command,
        `;`, `&&`, `>`, `$(…)`, a write flag like `find -delete` — is refused
        and nothing runs. Output lines come back numbered, so you can cite
        them. Arguments are taken literally: no globs (`*.log`) and no `~` —
        write the full path, or list the directory first.

        Args:
            host: where to run it — one of the declared hosts ("local" is this
                machine; any other is an SSH alias).
            command: the command, as you would type it in a shell.
            save_to: a file path in your workspace to write the whole output
                to instead of reading it here — for output too long to read.
        """
        if host not in declared:
            return await refuse(host, command, f"host {host!r} is not declared; declared: {list(declared)}")
        if (why := refusal(command)) is not None:
            return await refuse(host, command, why)
        code, output = await _run(host, _script(command))
        output = scrub(output)
        log.info("shell ran on %s: %r -> exit %s", host, command, code)
        lines = output.splitlines()
        if save_to:
            saved = await workspace.save(run.task_id, save_to, output)
            return f"exit {code}, {len(lines)} lines; {saved}"
        shown = lines[:MAX_LINES]
        text = run.evidence.show(shown) if run.evidence is not None else "\n".join(shown)
        if len(lines) > MAX_LINES:
            text += f"\n({len(lines) - MAX_LINES} more lines not shown — narrow the command or use save_to)"
        return f"exit {code}\n{text}"

    return [run_command]

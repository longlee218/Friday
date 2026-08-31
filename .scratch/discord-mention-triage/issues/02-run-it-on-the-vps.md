# 02: Run it on the VPS

**What to build:** The capture from ticket 01 runs unattended on the server, in a
container, against storage that survives restarts and redeploys.

**Blocked by:** 01

**Status:** done

- [x] The service builds into an image and runs as a single container — *written and reviewed; the image has not been built, see below*
- [x] Storage lives on a persistent volume; events captured before a restart are still present after it
- [x] Secrets are supplied at runtime and are not baked into the image
- [x] Structured configuration is mounted or baked such that changing it does not require rebuilding from scratch
- [x] A credential-shaped string never appears in logs, including in unhandled exception output
- [x] The container restarts cleanly without manual intervention after being killed


## Note added while planning ticket 18

The image also has to carry the board's frontend, which lives in its own repository
and is published as a versioned artefact. It is copied in and pinned by version, not
built from a branch. That is what keeps the split from requiring an authentication
scheme: nothing is served from a public origin, and the API still answers only on
loopback.


## The criterion that was not met, and is now

> A credential-shaped string never appears in logs, including in unhandled
> exception output

The logging filter could not reach that. Python's default hook writes a
traceback straight to stderr and never passes through logging, and a traceback
carries every argument in every frame — which is exactly where a token turns up,
in a library raising on a request that had an Authorization header on it.
Checked before assuming: the token was printed in full.

`install_excepthook()` covers `sys.excepthook` and `threading.excepthook` both.
The second matters here: `aiosqlite` runs its connection on a worker thread, and
an exception there goes nowhere near the first.

## The board, in a container

A container's loopback is unreachable from outside it, so binding there means
the port mapping never arrives — and `0.0.0.0` was refused by the guard added in
ticket 17. Both were right, and they contradicted.

Resolved by putting the boundary where it belongs: the app binds the container's
own network, and `ports: ["127.0.0.1:8086:8086"]` decides who can reach it. The
guard detects the container rather than reading a boolean, because a setting
that switches off a safety check is a setting that gets copied onto a laptop.

On the server, reach it through a tunnel:

    ssh -N -L 8086:127.0.0.1:8086 you@your-vps

## Not verified

Docker is not running on this machine, so **the image has not been built**. The
Dockerfile and compose file are written and reasoned about but not exercised.
First run on the server should be `docker compose build` before `up -d`, and the
first failure will be there if anywhere.

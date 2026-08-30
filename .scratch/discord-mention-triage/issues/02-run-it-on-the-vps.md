# 02: Run it on the VPS

**What to build:** The capture from ticket 01 runs unattended on the server, in a
container, against storage that survives restarts and redeploys.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] The service builds into an image and runs as a single container
- [ ] Storage lives on a persistent volume; events captured before a restart are still present after it
- [ ] Secrets are supplied at runtime and are not baked into the image
- [ ] Structured configuration is mounted or baked such that changing it does not require rebuilding from scratch
- [ ] A credential-shaped string never appears in logs, including in unhandled exception output
- [ ] The container restarts cleanly without manual intervention after being killed

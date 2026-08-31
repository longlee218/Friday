# One process, one container. That follows from SQLite: several containers
# writing one file over a shared volume means writer contention and locking
# bugs, so the gateways, the runners, the outbox and the board are all asyncio
# tasks in here together.

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, and from the lockfile only, so editing application code
# does not re-resolve or re-download anything.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev


FROM python:3.13-slim-bookworm AS runtime

# Not root. The container reaches Discord and runs a model's tool calls; there
# is no reason for anything in it to be able to write outside its own data.
RUN useradd --create-home --uid 10001 friday

COPY --from=build --chown=friday:friday /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
USER friday

# The database lives here and the volume is mounted over it. Declared so that
# running without one is a visible mistake rather than a silent data loss.
VOLUME ["/app/data"]

# The board is loopback-only on purpose: it shows every captured message and
# every model prompt and has no authentication. Reach it over an SSH tunnel:
#   ssh -N -L 8086:127.0.0.1:8086 you@your-vps
EXPOSE 8086

CMD ["python", "run_agent.py"]

# --- build stage: install exact versions from uv.lock into /app/.venv ----------
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first (cached layer): versions and hashes from uv.lock, wheels only.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-build

# Then the app itself.
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-build

# --- runtime stage: just Python and the environment ---------------------------
FROM python:3.12-slim

RUN useradd --system --no-create-home app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER app

ENTRYPOINT ["planner"]
CMD ["--help"]

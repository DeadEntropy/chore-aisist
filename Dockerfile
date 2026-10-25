FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf build

COPY migrations ./migrations
COPY chores.json ./

ENV MIGRATIONS_DIR=/app/migrations \
    SEED_PATH=/app/chores.json \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["planner"]
CMD ["--help"]

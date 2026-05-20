FROM docker:29-cli AS docker-cli

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

ENTRYPOINT ["ai-obsidian"]

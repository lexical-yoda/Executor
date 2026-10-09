# One image, two roles: `web` (default) and `runner`.

FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM docker:28.5.2-cli AS dockercli

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EXECUTOR_STATIC=/app/static
WORKDIR /app

# Docker CLI and the compose plugin, used only by the runner role.
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
RUN docker --version && docker compose version

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/executor ./executor
COPY --from=web /web/dist ./static

# The web role runs as this user (set in compose); the runner stays root
# because it needs the Docker socket.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin executor

EXPOSE 1977
ENTRYPOINT ["python", "-m", "executor"]
CMD ["web"]

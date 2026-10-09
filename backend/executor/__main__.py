"""Entry point: ``python -m executor web`` or ``python -m executor runner``."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn


def _token() -> str:
    token = os.environ.get("RUNNER_TOKEN", "")
    if len(token) < 32:
        sys.exit("RUNNER_TOKEN must be set to at least 32 characters (openssl rand -hex 32).")
    return token


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    common = {"proxy_headers": False, "server_header": False, "date_header": False,
              "log_level": os.environ.get("LOG_LEVEL", "info").lower()}

    if role == "web":
        from .config import load_config
        from .monitor import RunnerClient
        from .web import create_web_app

        config = load_config(os.environ.get("EXECUTOR_CONFIG", "/config/config.yaml"))
        runner = RunnerClient(os.environ.get("RUNNER_URL", "http://executor-runner:8001"), _token())
        static = Path(os.environ.get("EXECUTOR_STATIC", "/app/static"))
        beszel = None
        settings = config.integrations.beszel
        if settings and os.environ.get("BESZEL_EMAIL") and os.environ.get("BESZEL_PASSWORD"):
            from .sources.beszel import Beszel

            beszel = Beszel(settings.url, os.environ["BESZEL_EMAIL"], os.environ["BESZEL_PASSWORD"],
                            timeout=settings.timeout)
        elif settings:
            logging.getLogger("executor").warning("Beszel configured but BESZEL_EMAIL/BESZEL_PASSWORD not set")
        jellyfin = None
        media = config.integrations.jellyfin
        if media and os.environ.get("JELLYFIN_API_KEY"):
            from .sources.jellyfin import Jellyfin

            jellyfin = Jellyfin(media.url, os.environ["JELLYFIN_API_KEY"], timeout=media.timeout)
        elif media:
            logging.getLogger("executor").warning("Jellyfin configured but JELLYFIN_API_KEY not set")
        duplicati = None
        backups = config.integrations.backups
        if backups and backups.duplicati and os.environ.get("DUPLICATI_PASSWORD"):
            from .sources.duplicati import Duplicati

            duplicati = Duplicati(backups.duplicati.url, os.environ["DUPLICATI_PASSWORD"],
                                  timeout=backups.duplicati.timeout)
        elif backups and backups.duplicati:
            logging.getLogger("executor").warning("Duplicati configured but DUPLICATI_PASSWORD not set")
        app = create_web_app(config, runner, static, beszel=beszel, jellyfin=jellyfin, duplicati=duplicati)
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "1977")), **common)

    elif role == "runner":
        from .config import load_actions
        from .docker_api import DockerAPI
        from .runner import create_runner_app

        actions = load_actions(os.environ.get("EXECUTOR_ACTIONS", "/config/actions.yaml"))
        docker = DockerAPI(os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"))
        data = os.environ.get("RUNNER_DATA", "/data")
        app = create_runner_app(actions, _token(), docker, Path(data) if data else None)
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("RUNNER_PORT", "8001")), **common)

    else:
        sys.exit("usage: python -m executor web|runner")


if __name__ == "__main__":
    main()

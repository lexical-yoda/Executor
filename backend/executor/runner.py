"""The runner: the only process that can touch Docker.

It holds the Docker socket and executes actions defined in its own
``actions.yaml``. Callers can only name an action; they can never supply a
command. It listens on an internal-only Docker network and every request except
``/health`` needs the shared bearer token.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .config import ENV_REF, Action, ActionsConfig, SizedFolder, Step, WatchedFolder
from .docker_api import DockerAPI

log = logging.getLogger("executor.runner")

MAX_LINES = 5000
MAX_ENTRIES = 500
TAIL_BYTES = 4096
TAIL_LINES = 5
KEEP_RUNS = 50
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Never hand these to a child process.
SECRET_ENV = {"RUNNER_TOKEN"}
# Credentials in URLs or form text, such as "apikey=..." in an app's error
# message about another app; their values never reach the log.
CREDENTIAL = re.compile(r"(?i)\b((?:api[_-]?key|apikey|passkey|access[_-]?token|token|password|secret)=)[^&\s\"'<>]+")


def redact(text: str, secrets: tuple[str, ...] = ()) -> str:
    """Hide credentials in a line of output: values of the runner's own secrets and key=value pairs."""
    for value in secrets:
        text = text.replace(value, "<redacted>")
    return CREDENTIAL.sub(r"\1<redacted>", text)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class StepState:
    name: str
    status: str = "pending"
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class Run:
    id: str
    action: str
    title: str
    requested_by: str
    started_at: str
    steps: list[StepState]
    status: str = "running"
    finished_at: str | None = None
    error: str | None = None
    lines: list[str] = field(default_factory=list)

    def say(self, text: str) -> None:
        # Set by the runner; not a field, so it is never saved or served.
        secrets: tuple[str, ...] = getattr(self, "secrets", ())
        for raw in text.replace("\r", "\n").split("\n"):
            line = redact(ANSI.sub("", raw).rstrip(), secrets)
            if line:
                self.lines.append(line)
        if len(self.lines) > MAX_LINES:
            del self.lines[: len(self.lines) - MAX_LINES]

    def summary(self) -> dict:
        data = asdict(self)
        data.pop("lines")
        return data

    def detail(self, offset: int) -> dict:
        offset = max(0, offset)
        return {**self.summary(), "lines": self.lines[offset:], "next_offset": len(self.lines)}


def describe_folder(folder: WatchedFolder) -> dict:
    """Names, sizes and modification times of the files in a watched folder,
    newest first, plus the last lines of the files named in ``tail``. Never
    follows symlinks and never reads any other file."""
    path = Path(folder.path)
    entries: list[dict] = []
    try:
        with os.scandir(path) as listing:
            for entry in listing:
                if not entry.is_file(follow_symlinks=False):
                    continue
                info = entry.stat(follow_symlinks=False)
                entries.append({"name": entry.name, "size": info.st_size, "mtime": info.st_mtime})
    except OSError as exc:
        return {"id": folder.id, "ok": False, "error": exc.strerror or exc.__class__.__name__,
                "entries": [], "tails": {}}
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    tails: dict[str, list[str]] = {}
    for name in folder.tail:
        file = path / name
        if file.is_symlink() or not file.is_file():
            continue
        try:
            with file.open("rb") as handle:
                handle.seek(max(0, file.stat().st_size - TAIL_BYTES))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        tails[name] = [line for line in text.splitlines() if line.strip()][-TAIL_LINES:]
    return {"id": folder.id, "ok": True, "error": None, "entries": entries[:MAX_ENTRIES], "tails": tails}


def measure_folder(folder: SizedFolder) -> dict:
    """Total size and number of files under a folder. Never follows symlinks
    and reports nothing but the two numbers."""
    total = files = 0
    pending = [folder.path]
    try:
        os.scandir(folder.path).close()
    except OSError as exc:
        return {"id": folder.id, "ok": False, "error": exc.strerror or exc.__class__.__name__,
                "bytes": None, "files": None}
    while pending:
        try:
            with os.scandir(pending.pop()) as listing:
                for entry in listing:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return {"id": folder.id, "ok": True, "error": None, "bytes": total, "files": files}


# An http step with `wait: false` gives the request this long to be refused
# before moving on, and the request itself this long to finish on its own.
HTTP_SEND_GRACE = 3.0
HTTP_SEND_TIMEOUT = 900.0

SIZES_EVERY = 1800.0
# How long a request just after a start waits for the first walk to finish.
SIZES_FIRST_WAIT = 5.0


class FolderSizes:
    """Sizes of the folders under `sizes:`, measured in the background. A cold
    walk of a large library (100k files) takes longer than the web's request
    timeout, so a request only ever reads the last result."""

    def __init__(self, folders: list[SizedFolder], every: float = SIZES_EVERY) -> None:
        self.folders = folders
        self.every = every
        self.results: dict[str, dict] = {}
        self.first = asyncio.Event()

    def current(self) -> list[dict]:
        return [self.results.get(f.id) or {"id": f.id, "ok": False, "error": "still measuring",
                                           "bytes": None, "files": None} for f in self.folders]

    async def run(self) -> None:
        while True:
            for folder in self.folders:
                try:
                    self.results[folder.id] = await asyncio.to_thread(measure_folder, folder)
                except Exception as exc:  # noqa: BLE001 - one bad folder must not stop the others
                    log.warning("measuring %s failed: %s", folder.id, exc)
            self.first.set()
            await asyncio.sleep(self.every)


COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")


def list_stacks(folder: str | None) -> list[str] | None:
    """Names of the subfolders of the stacks folder that hold a compose file.
    Never follows symlinks and reports nothing but the names."""
    if not folder:
        return None
    names = []
    try:
        with os.scandir(folder) as listing:
            for entry in listing:
                if entry.is_dir(follow_symlinks=False) and any(
                        os.path.isfile(os.path.join(entry.path, f)) for f in COMPOSE_FILES):
                    names.append(entry.name)
    except OSError:
        return None
    return sorted(names)


class StartRequest(BaseModel):
    action: str
    requested_by: str = "unknown"


class Runner:
    def __init__(self, actions: ActionsConfig, docker: DockerAPI, data_dir: Path | None,
                 http_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.actions = actions
        self.docker = docker
        self.data_dir = data_dir
        self.http_transport = http_transport
        # Requests sent by `wait: false` steps, kept referenced until they finish.
        self._sent: set[asyncio.Task] = set()
        # The runner token and every variable an http step reads stay out of
        # child processes.
        self.secret_env = SECRET_ENV | actions.secret_names()
        # Their values, hidden wherever they turn up in a run's output.
        self.secret_values = tuple(v for k in sorted(self.secret_env) if len(v := os.environ.get(k, "")) >= 8)
        self.runs: dict[str, Run] = {}
        self.busy: str | None = None
        self._tasks: set[asyncio.Task] = set()
        self._load_history()

    # --- history -----------------------------------------------------------
    @property
    def _log_path(self) -> Path | None:
        return self.data_dir / "runs.jsonl" if self.data_dir else None

    def _load_history(self) -> None:
        path = self._log_path
        if not path or not path.exists():
            return
        try:
            rows = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("cannot read run history: %s", exc)
            return
        rows = self._scrub(path, rows)[-KEEP_RUNS:]
        for row in rows:
            try:
                data = json.loads(row)
                data["steps"] = [StepState(**s) for s in data["steps"]]
                run = Run(**data)
                self.runs[run.id] = run
            except (ValueError, TypeError, KeyError):
                continue

    def _scrub(self, path: Path, rows: list[str]) -> list[str]:
        """Hide credentials that earlier versions wrote to the history, rewriting the file if any."""
        clean: list[str] = []
        changed = False
        for row in rows:
            try:
                data = json.loads(row)
                lines = [redact(line, self.secret_values) for line in data.get("lines") or []]
                error = redact(data["error"], self.secret_values) if data.get("error") else data.get("error")
            except (ValueError, TypeError, AttributeError):
                clean.append(row)
                continue
            if lines != (data.get("lines") or []) or error != data.get("error"):
                changed = True
                data["lines"], data["error"] = lines, error
                row = json.dumps(data)
            clean.append(row)
        if changed:
            try:
                temp = path.with_suffix(".tmp")
                temp.write_text("".join(r + "\n" for r in clean), encoding="utf-8")
                temp.replace(path)
                log.info("hid credentials in the run history")
            except OSError as exc:
                log.warning("cannot rewrite run history: %s", exc)
        return clean

    def _persist(self, run: Run) -> None:
        path = self._log_path
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(run)) + "\n")
        except OSError as exc:
            log.warning("cannot write run history: %s", exc)

    def _trim(self) -> None:
        while len(self.runs) > KEEP_RUNS:
            oldest = next(iter(self.runs))
            if self.runs[oldest].status == "running":
                break
            del self.runs[oldest]

    # --- execution ---------------------------------------------------------
    def start(self, action: Action, requested_by: str) -> Run:
        if self.busy:
            raise HTTPException(409, "Another action is still running.")
        run = Run(
            id=uuid.uuid4().hex[:12],
            action=action.id,
            title=action.title,
            requested_by=requested_by,
            started_at=now(),
            steps=[StepState(step.name) for step in action.steps],
        )
        run.secrets = self.secret_values  # type: ignore[attr-defined]
        # Set before the first await, so two requests cannot both start.
        self.busy = run.id
        self.runs[run.id] = run
        self._trim()
        task = asyncio.create_task(self._execute(action, run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        log.info("run %s started: %s (requested by %s)", run.id, action.id, requested_by)
        return run

    async def _execute(self, action: Action, run: Run) -> None:
        try:
            for step, state in zip(action.steps, run.steps):
                state.status = "running"
                state.started_at = now()
                run.say(f"==> {step.name}")
                try:
                    if step.wait_healthy:
                        await self._wait_healthy(step, run)
                    else:
                        await self._with_retry(step, run)
                except Exception as exc:  # noqa: BLE001 - every failure must end the run cleanly
                    state.status = "failed"
                    state.finished_at = now()
                    run.error = redact(f"{step.name}: {exc}", self.secret_values)
                    run.say(f"!! {run.error}")
                    for rest in run.steps:
                        if rest.status == "pending":
                            rest.status = "skipped"
                    run.status = "failed"
                    return
                state.status = "succeeded"
                state.finished_at = now()
            run.status = "succeeded"
            run.say("==> Done")
        finally:
            run.finished_at = now()
            self.busy = None
            self._persist(run)
            log.info("run %s finished: %s", run.id, run.status)

    async def _with_retry(self, step: Step, run: Run) -> None:
        deadline = time.monotonic() + step.timeout
        while True:
            remaining = max(1.0, deadline - time.monotonic())
            try:
                await self._once(step, run, remaining)
                return
            except RuntimeError as exc:
                wait = step.retry_every
                if not wait or time.monotonic() + wait >= deadline:
                    raise
                run.say(f"   {exc}; retrying in {wait:.0f}s")
                await asyncio.sleep(wait)

    async def _once(self, step: Step, run: Run, timeout: float) -> None:
        if step.run:
            run.say("$ " + " ".join(step.run))
            await self._command(step.run, step.cwd, run, timeout)
        elif step.ssh:
            target = self.actions.ssh[step.ssh.target]
            run.say(f"$ ssh {target.user}@{target.host} {step.ssh.command}")
            argv = [
                "ssh", "-F", "/dev/null", "-T",
                "-i", target.key, "-p", str(target.port),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={target.known_hosts}",
                "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR",
                f"{target.user}@{target.host}", step.ssh.command,
            ]
            code = await self._command(argv, None, run, timeout, check=False)
            if code == 255:
                raise RuntimeError("SSH connection failed (exit 255)")
            if code != 0:
                raise RuntimeError(f"exited with code {code}")
        elif step.http:
            await self._http(step, run, timeout)

    async def _command(self, argv: list[str], cwd: str | None, run: Run, timeout: float,
                       check: bool = True) -> int:
        env = {k: v for k, v in os.environ.items() if k not in self.secret_env}
        env["NO_COLOR"] = "1"
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def pump() -> None:
            assert process.stdout is not None
            while chunk := await process.stdout.readline():
                run.say(chunk.decode("utf-8", errors="replace"))

        try:
            await asyncio.wait_for(asyncio.gather(pump(), process.wait()), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"timed out after {timeout:.0f}s") from None
        code = process.returncode or 0
        if check and code != 0:
            raise RuntimeError(f"exited with code {code}")
        return code

    async def _http(self, step: Step, run: Run, timeout: float) -> None:
        call = step.http
        assert call is not None

        def fill(text: str) -> str:
            def value(match: re.Match) -> str:
                name = match.group(1)
                if not os.environ.get(name):
                    raise RuntimeError(f"{name} is not set in the runner's environment")
                return os.environ[name]
            return ENV_REF.sub(value, text)

        url = fill(call.url)
        headers = {key: fill(val) for key, val in call.headers.items()}
        # Log the configured URL, so a secret in it is never printed.
        run.say(f"$ {call.method} {call.url}")
        if not call.wait:
            await self._http_send(step, run, url, headers)
            return
        try:
            async with httpx.AsyncClient(timeout=min(timeout, 60), verify=call.verify_tls,
                                         transport=self.http_transport) as client:
                response = await client.request(call.method, url, headers=headers, json=call.json_body)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"request failed: {exc.__class__.__name__}") from None
        run.say(f"HTTP {response.status_code}")
        if response.text.strip():
            run.say(response.text[:2000])
        ok = response.status_code in call.expect if call.expect else 200 <= response.status_code < 300
        if not ok:
            raise RuntimeError(f"unexpected HTTP {response.status_code}")

    async def _http_send(self, step: Step, run: Run, url: str, headers: dict[str, str]) -> None:
        """Send a request and leave it running: the work it starts carries on
        on its own. A refusal that comes at once still fails the step."""
        call = step.http
        assert call is not None

        async def send() -> httpx.Response:
            async with httpx.AsyncClient(timeout=HTTP_SEND_TIMEOUT, verify=call.verify_tls,
                                         transport=self.http_transport) as client:
                return await client.request(call.method, url, headers=headers, json=call.json_body)

        def finished(task: asyncio.Task) -> None:
            self._sent.discard(task)
            if not task.cancelled() and task.exception() is None:
                log.info("%s: answered HTTP %s", step.name, task.result().status_code)

        task = asyncio.create_task(send())
        self._sent.add(task)
        task.add_done_callback(finished)
        done, _ = await asyncio.wait({task}, timeout=HTTP_SEND_GRACE)
        if not done:
            run.say("sent; not waiting for the answer")
            return
        if task.exception() is not None:
            raise RuntimeError(f"request failed: {task.exception().__class__.__name__}")
        status = task.result().status_code
        run.say(f"HTTP {status}")
        if status in (401, 403, 404):
            raise RuntimeError(f"refused with HTTP {status}")

    async def _wait_healthy(self, step: Step, run: Run) -> None:
        name = step.wait_healthy or ""
        deadline = time.monotonic() + step.timeout
        last = None
        while True:
            status = await self.docker.health(name)
            if status != last:
                run.say(f"{name}: {status or 'not found'}")
                last = status
            if status == "healthy":
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{name} not healthy after {step.timeout:.0f}s (last: {status})")
            await asyncio.sleep(3)


def create_runner_app(actions: ActionsConfig, token: str, docker: DockerAPI, data_dir: Path | None,
                      http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    runner = Runner(actions, docker, data_dir, http_transport)
    folder_sizes = FolderSizes(actions.sizes)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        measuring = asyncio.create_task(folder_sizes.run()) if actions.sizes else None
        yield
        if measuring:
            measuring.cancel()
        await docker.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.runner = runner

    def authorised(request: Request) -> None:
        header = request.headers.get("authorization", "")
        supplied = header.removeprefix("Bearer ").strip()
        if not supplied or not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "Unauthorised")

    guarded = [Depends(authorised)]

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/containers", dependencies=guarded)
    async def containers() -> list[dict]:
        try:
            return await docker.containers()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Docker unavailable: {exc}") from None

    @app.get("/files", dependencies=guarded)
    async def files() -> list[dict]:
        return await asyncio.gather(*(asyncio.to_thread(describe_folder, f) for f in actions.files))

    @app.get("/sizes", dependencies=guarded)
    async def sizes() -> list[dict]:
        """Sizes of the folders under `sizes:`, as last measured (every half hour)."""
        if actions.sizes and not folder_sizes.first.is_set():
            # Just after a start, give the first walk a moment, but never long.
            try:
                await asyncio.wait_for(asyncio.shield(folder_sizes.first.wait()), SIZES_FIRST_WAIT)
            except asyncio.TimeoutError:
                pass
        return folder_sizes.current()

    @app.get("/stacks", dependencies=guarded)
    async def stacks() -> dict:
        names = await asyncio.to_thread(list_stacks, actions.stacks_dir)
        return {"configured": actions.stacks_dir is not None, "ok": names is not None, "stacks": names or []}

    @app.get("/actions", dependencies=guarded)
    async def list_actions() -> list[dict]:
        return [
            {
                "id": a.id,
                "title": a.title,
                "description": a.description,
                "confirm": a.confirm,
                "danger": a.danger,
                "show_streams": a.show_streams,
                "group": a.group,
                "attach": a.attach,
                "steps": [s.name for s in a.steps],
            }
            for a in actions.actions
        ]

    @app.post("/runs", dependencies=guarded)
    async def start(body: StartRequest) -> dict:
        action = actions.get(body.action)
        if not action:
            raise HTTPException(404, "No such action.")
        return runner.start(action, body.requested_by).detail(0)

    @app.get("/runs", dependencies=guarded)
    async def list_runs() -> dict:
        runs = sorted(runner.runs.values(), key=lambda r: r.started_at, reverse=True)
        return {"busy": runner.busy, "runs": [r.summary() for r in runs]}

    @app.get("/runs/{run_id}", dependencies=guarded)
    async def get_run(run_id: str, offset: int = 0) -> dict:
        run = runner.runs.get(run_id)
        if not run:
            raise HTTPException(404, "No such run.")
        return run.detail(offset)

    return app

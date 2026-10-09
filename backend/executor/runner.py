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

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from .config import Action, ActionsConfig, Step
from .docker_api import DockerAPI

log = logging.getLogger("executor.runner")

MAX_LINES = 5000
KEEP_RUNS = 50
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Never hand these to a child process.
SECRET_ENV = {"RUNNER_TOKEN"}


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
        for raw in text.replace("\r", "\n").split("\n"):
            line = ANSI.sub("", raw).rstrip()
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


class StartRequest(BaseModel):
    action: str
    requested_by: str = "unknown"


class Runner:
    def __init__(self, actions: ActionsConfig, docker: DockerAPI, data_dir: Path | None) -> None:
        self.actions = actions
        self.docker = docker
        self.data_dir = data_dir
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
            rows = path.read_text(encoding="utf-8").splitlines()[-KEEP_RUNS:]
        except OSError as exc:
            log.warning("cannot read run history: %s", exc)
            return
        for row in rows:
            try:
                data = json.loads(row)
                data["steps"] = [StepState(**s) for s in data["steps"]]
                run = Run(**data)
                self.runs[run.id] = run
            except (ValueError, TypeError, KeyError):
                continue

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
                    if step.run:
                        await self._command(step, run)
                    else:
                        await self._wait_healthy(step, run)
                except Exception as exc:  # noqa: BLE001 - every failure must end the run cleanly
                    state.status = "failed"
                    state.finished_at = now()
                    run.error = f"{step.name}: {exc}"
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

    async def _command(self, step: Step, run: Run) -> None:
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        env["NO_COLOR"] = "1"
        run.say("$ " + " ".join(step.run or []))
        process = await asyncio.create_subprocess_exec(
            *(step.run or []),
            cwd=step.cwd,
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
            await asyncio.wait_for(asyncio.gather(pump(), process.wait()), timeout=step.timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"timed out after {step.timeout:.0f}s") from None
        if process.returncode != 0:
            raise RuntimeError(f"exited with code {process.returncode}")

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


def create_runner_app(actions: ActionsConfig, token: str, docker: DockerAPI, data_dir: Path | None) -> FastAPI:
    runner = Runner(actions, docker, data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
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

    @app.get("/actions", dependencies=guarded)
    async def list_actions() -> list[dict]:
        return [
            {
                "id": a.id,
                "title": a.title,
                "description": a.description,
                "confirm": a.confirm,
                "danger": a.danger,
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

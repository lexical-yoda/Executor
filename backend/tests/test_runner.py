import os
import time
from pathlib import Path

from fastapi.testclient import TestClient

from executor.config import ActionsConfig
from executor.runner import create_runner_app

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ACTIONS = ActionsConfig.model_validate({"actions": [
    {"id": "ok", "title": "OK", "confirm": "sure?", "steps": [
        {"name": "one", "run": ["sh", "-c", "echo hello"]},
        {"name": "two", "run": ["sh", "-c", "echo runner-token:${RUNNER_TOKEN:-absent}"]},
        {"name": "three", "wait_healthy": "gluetun", "timeout": 5},
    ]},
    {"id": "bad", "title": "Bad", "confirm": "sure?", "steps": [
        {"name": "fails", "run": ["sh", "-c", "echo boom; exit 3"]},
        {"name": "skipped", "run": ["sh", "-c", "echo never"]},
    ]},
    {"id": "slow", "title": "Slow", "confirm": "sure?", "steps": [
        {"name": "sleep", "run": ["sh", "-c", "sleep 2"]},
    ]},
]})


class FakeDocker:
    async def containers(self):
        return [{"name": "gluetun", "state": "running", "health": "healthy", "status": "Up"}]

    async def health(self, name):
        return "healthy" if name == "gluetun" else None

    async def close(self):
        pass


def wait_for(client, run_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/runs/{run_id}", headers=AUTH).json()
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def make(tmp_path: Path) -> TestClient:
    return TestClient(create_runner_app(ACTIONS, TOKEN, FakeDocker(), tmp_path))


def test_token_required(tmp_path, monkeypatch):
    with make(tmp_path) as c:
        assert c.get("/actions").status_code == 401
        assert c.get("/actions", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/actions", headers=AUTH).status_code == 200


def test_successful_run_hides_token_and_is_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TOKEN", TOKEN)
    with make(tmp_path) as c:
        run = c.post("/runs", json={"action": "ok", "requested_by": "10.8.0.2"}, headers=AUTH).json()
        done = wait_for(c, run["id"])
    assert done["status"] == "succeeded"
    assert [s["status"] for s in done["steps"]] == ["succeeded"] * 3
    assert "hello" in done["lines"]
    assert "runner-token:absent" in done["lines"]
    assert TOKEN not in "\n".join(done["lines"])
    assert (tmp_path / "runs.jsonl").read_text().count("\n") == 1


def test_failed_step_skips_the_rest(tmp_path):
    with make(tmp_path) as c:
        run = c.post("/runs", json={"action": "bad"}, headers=AUTH).json()
        done = wait_for(c, run["id"])
    assert done["status"] == "failed"
    assert [s["status"] for s in done["steps"]] == ["failed", "skipped"]
    assert "exited with code 3" in done["error"]


def test_one_action_at_a_time(tmp_path):
    with make(tmp_path) as c:
        first = c.post("/runs", json={"action": "slow"}, headers=AUTH)
        second = c.post("/runs", json={"action": "ok"}, headers=AUTH)
        assert first.status_code == 200
        assert second.status_code == 409
        wait_for(c, first.json()["id"])


def test_unknown_action(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/runs", json={"action": "rm-rf"}, headers=AUTH).status_code == 404


def test_history_survives_restart(tmp_path):
    with make(tmp_path) as c:
        run = c.post("/runs", json={"action": "bad"}, headers=AUTH).json()
        wait_for(c, run["id"])
    with make(tmp_path) as c:
        runs = c.get("/runs", headers=AUTH).json()["runs"]
    assert [r["id"] for r in runs] == [run["id"]]


# --- ssh, http and retry steps ------------------------------------------------

import httpx  # noqa: E402

SECRET = "s3cret-key-value"


def http_actions() -> ActionsConfig:
    return ActionsConfig.model_validate({"actions": [
        {"id": "scan", "title": "Scan", "confirm": "sure?", "show_streams": True, "steps": [
            {"name": "refresh", "http": {"url": "http://media.example:8096/Library/Refresh",
                                         "headers": {"Authorization": 'MediaBrowser Token="${MEDIA_KEY}"'},
                                         "expect": [204]}},
            {"name": "env", "run": ["sh", "-c", "echo key=${MEDIA_KEY:-absent}"]},
        ]},
        {"id": "teapot", "title": "Teapot", "confirm": "sure?", "steps": [
            {"name": "brew", "http": {"method": "GET", "url": "http://media.example/brew"}},
        ]},
    ]})


def test_http_step_fills_secrets_without_logging_them(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_KEY", SECRET)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["method"] = request.method
        if request.url.path == "/brew":
            return httpx.Response(418, text="short and stout")
        return httpx.Response(204)

    app = create_runner_app(http_actions(), TOKEN, FakeDocker(), tmp_path, httpx.MockTransport(handler))
    with TestClient(app) as c:
        listed = c.get("/actions", headers=AUTH).json()
        assert listed[0]["show_streams"] is True and listed[1]["show_streams"] is False
        done = wait_for(c, c.post("/runs", json={"action": "scan"}, headers=AUTH).json()["id"])
        assert done["status"] == "succeeded", done
        assert seen == {"auth": f'MediaBrowser Token="{SECRET}"', "method": "POST"}
        log = "\n".join(done["lines"])
        assert SECRET not in log
        assert "HTTP 204" in log
        # Child processes never see a variable that an http step reads.
        assert "key=absent" in done["lines"]

        failed = wait_for(c, c.post("/runs", json={"action": "teapot"}, headers=AUTH).json()["id"])
        assert failed["status"] == "failed"
        assert "unexpected HTTP 418" in failed["error"]
        assert "short and stout" in failed["lines"]


def test_http_step_fails_cleanly_without_its_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIA_KEY", raising=False)
    app = create_runner_app(http_actions(), TOKEN, FakeDocker(), tmp_path,
                            httpx.MockTransport(lambda r: httpx.Response(204)))
    with TestClient(app) as c:
        done = wait_for(c, c.post("/runs", json={"action": "scan"}, headers=AUTH).json()["id"])
    assert done["status"] == "failed"
    assert "MEDIA_KEY is not set" in done["error"]


def test_retry_every_repeats_until_success(tmp_path):
    counter = tmp_path / "count"
    actions = ActionsConfig.model_validate({"actions": [
        {"id": "wait", "title": "Wait", "confirm": "sure?", "steps": [
            {"name": "third time lucky", "retry_every": 0.2, "timeout": 10,
             "run": ["sh", "-c", f"echo x >> {counter}; [ $(wc -l < {counter}) -ge 3 ]"]},
        ]},
        {"id": "never", "title": "Never", "confirm": "sure?", "steps": [
            {"name": "gives up", "retry_every": 0.3, "timeout": 1, "run": ["false"]},
        ]},
    ]})
    with TestClient(create_runner_app(actions, TOKEN, FakeDocker(), tmp_path)) as c:
        done = wait_for(c, c.post("/runs", json={"action": "wait"}, headers=AUTH).json()["id"])
        assert done["status"] == "succeeded"
        assert sum("retrying in" in line for line in done["lines"]) == 2
        never = wait_for(c, c.post("/runs", json={"action": "never"}, headers=AUTH).json()["id"])
        assert never["status"] == "failed"
        assert "exited with code 1" in never["error"]


def fake_ssh(tmp_path: Path, monkeypatch, exit_code: int) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "ssh"
    script.write_text(f'#!/bin/sh\necho "args: $*"\nexit {exit_code}\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return script


SSH_ACTIONS = {
    "ssh": {"vps": {"host": "10.8.0.1", "user": "executor", "key": "/ssh/key", "known_hosts": "/ssh/kh"}},
    "actions": [{"id": "reload", "title": "Reload", "confirm": "sure?", "steps": [
        {"name": "reload nginx", "ssh": {"target": "vps", "command": "nginx-reload"}},
    ]}],
}


def test_ssh_step_pins_the_host_key_and_names_a_command(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, 0)
    actions = ActionsConfig.model_validate(SSH_ACTIONS)
    with TestClient(create_runner_app(actions, TOKEN, FakeDocker(), tmp_path)) as c:
        done = wait_for(c, c.post("/runs", json={"action": "reload"}, headers=AUTH).json()["id"])
    assert done["status"] == "succeeded"
    args = next(line for line in done["lines"] if line.startswith("args: "))
    for part in ("-F /dev/null", "-i /ssh/key", "BatchMode=yes", "StrictHostKeyChecking=yes",
                 "UserKnownHostsFile=/ssh/kh", "executor@10.8.0.1 nginx-reload"):
        assert part in args
    assert args.endswith("executor@10.8.0.1 nginx-reload")


def test_ssh_connection_failure_is_explained(tmp_path, monkeypatch):
    fake_ssh(tmp_path, monkeypatch, 255)
    actions = ActionsConfig.model_validate(SSH_ACTIONS)
    with TestClient(create_runner_app(actions, TOKEN, FakeDocker(), tmp_path)) as c:
        done = wait_for(c, c.post("/runs", json={"action": "reload"}, headers=AUTH).json()["id"])
    assert done["status"] == "failed"
    assert "SSH connection failed" in done["error"]


def test_credentials_in_responses_and_old_history_are_hidden(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("MEDIA_KEY", SECRET)
    # A run saved by an earlier version, with another app's key in an error message.
    old = {"id": "abc", "action": "scan", "title": "Scan", "requested_by": "x", "started_at": "t",
           "steps": [{"name": "refresh", "status": "failed"}], "status": "failed", "finished_at": "t",
           "error": "refresh: failed at http://idx:9696/1/api?t=caps&apikey=0123456789abcdef",
           "lines": ["GET http://idx:9696/1/api?apikey=0123456789abcdef&t=caps", "plain line"]}
    (tmp_path / "runs.jsonl").write_text(json.dumps(old) + "\n")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f'[{{"error": "failed at http://idx:9696/2/api?apikey=feedbeef1234 '
                                        f'with key {SECRET}"}}]')

    actions = ActionsConfig.model_validate({"actions": [
        {"id": "test", "title": "Test", "confirm": "sure?", "steps": [
            {"name": "testall", "http": {"url": "http://media.example/testall",
                                         "headers": {"X-Api-Key": "${MEDIA_KEY}"}}},
        ]},
    ]})
    app = create_runner_app(actions, TOKEN, FakeDocker(), tmp_path, httpx.MockTransport(handler))
    with TestClient(app) as c:
        history = c.get("/runs/abc", headers=AUTH).json()
        assert "0123456789abcdef" not in json.dumps(history)
        assert "apikey=<redacted>&t=caps" in history["lines"][0] and "plain line" in history["lines"]
        done = wait_for(c, c.post("/runs", json={"action": "test"}, headers=AUTH).json()["id"])
    log = "\n".join(done["lines"])
    assert "feedbeef1234" not in log and SECRET not in log
    assert "apikey=<redacted>" in log and "with key <redacted>" in log
    saved = (tmp_path / "runs.jsonl").read_text()
    assert "0123456789abcdef" not in saved and "feedbeef1234" not in saved and SECRET not in saved


def test_http_step_without_waiting_moves_on(tmp_path, monkeypatch):
    import asyncio

    from executor import runner as runner_module

    monkeypatch.setattr(runner_module, "HTTP_SEND_GRACE", 0.3)
    monkeypatch.setenv("ARR_KEY", SECRET)
    answers = {"/slow": 200, "/bad": 400, "/denied": 401}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/slow":
            await asyncio.sleep(2)
        return httpx.Response(answers[request.url.path], text="results nobody waits for")

    def action(path: str) -> dict:
        return {"id": path.strip("/"), "title": path, "confirm": "sure?", "steps": [
            {"name": "test all", "http": {"url": f"http://arr.example{path}", "wait": False,
                                          "headers": {"X-Api-Key": "${ARR_KEY}"}}},
            {"name": "after", "run": ["true"]},
        ]}

    actions = ActionsConfig.model_validate({"actions": [action(p) for p in answers]})
    app = create_runner_app(actions, TOKEN, FakeDocker(), tmp_path, httpx.MockTransport(handler))
    with TestClient(app) as c:
        began = time.monotonic()
        slow = wait_for(c, c.post("/runs", json={"action": "slow"}, headers=AUTH).json()["id"])
        assert slow["status"] == "succeeded" and time.monotonic() - began < 1.8, slow
        assert "sent; not waiting for the answer" in slow["lines"]
        # An answer that comes at once is fine, whatever it says about the work...
        bad = wait_for(c, c.post("/runs", json={"action": "bad"}, headers=AUTH).json()["id"])
        assert bad["status"] == "succeeded" and "HTTP 400" in bad["lines"]
        # ...unless the request itself was refused.
        denied = wait_for(c, c.post("/runs", json={"action": "denied"}, headers=AUTH).json()["id"])
        assert denied["status"] == "failed" and "refused with HTTP 401" in denied["error"]

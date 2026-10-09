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
        {"name": "two", "run": ["sh", "-c", "echo token=${RUNNER_TOKEN:-absent}"]},
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
    assert "token=absent" in done["lines"]
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

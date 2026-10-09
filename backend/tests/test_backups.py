import asyncio
import json
import os
import time

import httpx
from fastapi.testclient import TestClient

from executor.config import ActionsConfig, FileBackup
from executor.runner import create_runner_app, describe_folder
from executor.sources.backups import evaluate_files
from executor.sources.duplicati import (Duplicati, parse_duration, parse_repeat, parse_time,
                                        summarize_job)

NOW = parse_time("20261009T120000Z")


def result_log(log_id, result, end, warnings=0):
    return {"ID": log_id, "Type": "Result", "Timestamp": 0, "Message": json.dumps({
        "ParsedResult": result, "MainOperation": "Backup", "EndTime": end, "Duration": "00:13:14.18",
        "WarningsActualLength": warnings, "ErrorsActualLength": 0, "SizeOfAddedFiles": 1000})}


def job(repeat="1D", finished="20261008T214314Z"):
    return {"Backup": {"ID": "1", "Name": "Nightly", "TargetURL": "s3://key:secret@bucket",
                       "Metadata": {"LastBackupFinished": finished, "LastBackupDuration": "00:13:14.1852476",
                                    "SourceFilesSize": "92924408462", "TargetFilesSize": "89774889389",
                                    "BackupListCount": "7"}},
            "Schedule": {"Time": "2026-10-09T21:30:00Z", "Repeat": repeat}}


def test_parsers():
    assert parse_time("20261008T214314Z") == parse_time("2026-10-08T21:43:14Z")
    assert parse_time("0001-01-01T00:00:00Z") is None
    assert round(parse_duration("00:13:14.1852476")) == 794
    assert parse_duration("1.02:00:00") == 93600
    assert parse_repeat("1D") == 86400 and parse_repeat("1W") == 604800 and parse_repeat("x") is None


def test_job_states():
    logs = [result_log(2, "Success", "2026-10-08T21:43:14Z"), result_log(1, "Warning", "2026-10-07T21:43:00Z", 3)]
    ok = summarize_job(job(), logs, None, NOW)
    assert ok["status"] == "ok" and ok["versions"] == 7 and ok["last_result"] == "Success"
    assert [h["result"] for h in ok["history"]] == ["Warning", "Success"]
    assert "secret" not in json.dumps(ok)

    warned = summarize_job(job(), [result_log(3, "Warning", "2026-10-08T21:43:14Z", 2)], None, NOW)
    assert warned["status"] == "warning"
    failed = summarize_job(job(), [result_log(3, "Error", "2026-10-08T21:43:14Z")], None, NOW)
    assert failed["status"] == "failed"
    # A daily job whose last run finished two days ago is stale; a weekly one is not.
    assert summarize_job(job("1D", "20261007T090000Z"), [], None, NOW)["status"] == "stale"
    assert summarize_job(job("1W", "20261007T090000Z"), [], None, NOW)["status"] == "ok"
    running = summarize_job(job(), logs, {"phase": "Backup_ProcessingFiles", "fraction": 0.4}, NOW)
    assert running["status"] == "running" and running["progress"]["fraction"] == 0.4


def test_duplicati_client_logs_in_again_after_expiry():
    calls = {"login": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "192.168.0.10":
            return httpx.Response(403, json={"Error": f"Invalid hostname: {request.url.host}", "Code": 403})
        if request.url.path == "/api/v1/auth/login":
            calls["login"] += 1
            body = json.loads(request.content)
            if body["Password"] != "pw":
                return httpx.Response(401)
            return httpx.Response(200, json={"AccessToken": f"t{calls['login']}"})
        # The first token expires straight away.
        if request.headers["authorization"] == "Bearer t1":
            return httpx.Response(401)
        if request.url.path == "/api/v1/backups":
            return httpx.Response(200, json=[job()])
        if request.url.path == "/api/v1/serverstate":
            return httpx.Response(200, json={"ActiveTask": {"Item1": 9, "Item2": "1"}, "ProgramState": "Running"})
        if request.url.path == "/api/v1/progressstate":
            return httpx.Response(200, json={"Phase": "Backup_ProcessingFiles", "OverallProgress": 0.5})
        if request.url.path == "/api/v1/backup/1/log":
            return httpx.Response(200, json=[result_log(2, "Success", "2026-10-08T21:43:14Z")])
        return httpx.Response(404)

    async def check():
        client = Duplicati("http://192.168.0.10:8200", "pw", transport=httpx.MockTransport(handler))
        status = await client.status(NOW)
        assert calls["login"] == 2
        assert status["jobs"][0]["status"] == "running"
        assert status["jobs"][0]["progress"] == {"phase": "Backup_ProcessingFiles", "fraction": 0.5}
        for url, password, message in (("http://192.168.0.10:8200", "nope", "refused the password"),
                                        ("http://backup.example:8200", "pw", "Invalid hostname")):
            other = Duplicati(url, password, transport=httpx.MockTransport(handler))
            try:
                await other.status(NOW)
            except RuntimeError as exc:
                assert message in str(exc)
            else:
                raise AssertionError("expected a refusal")

    asyncio.run(check())


def folder(entries, tails=None):
    return {"id": "f", "ok": True, "error": None, "entries": entries, "tails": tails or {}}


def test_expected_files():
    now = time.time()
    item = FileBackup(folder="f", name="Database dumps", expect=["a.sql", "b.sqlite3", "c.sql"])
    good = folder([{"name": "a.sql", "size": 10, "mtime": now - 3600},
                   {"name": "b.sqlite3", "size": 10, "mtime": now - 3600},
                   {"name": "c.sql", "size": 10, "mtime": now - 7200}])
    assert evaluate_files(item, good, now)["status"] == "ok"
    stale = folder([{"name": "a.sql", "size": 10, "mtime": now - 3600},
                    {"name": "b.sqlite3", "size": 10, "mtime": now - 30 * 3600},
                    {"name": "c.sql", "size": 10, "mtime": now - 3600}])
    assert evaluate_files(item, stale, now)["status"] == "stale"
    missing = folder([{"name": "a.sql", "size": 10, "mtime": now}, {"name": "b.sqlite3", "size": 0, "mtime": now}])
    result = evaluate_files(item, missing, now)
    assert result["status"] == "failed"
    assert [f["state"] for f in result["files"]] == ["ok", "failed", "missing"]


def test_pattern_error_file_and_log_line():
    now = time.time()
    item = FileBackup(folder="f", name="Config pull", pattern="cfg-*.tar.gz",
                      error_file="last-error.txt", log_file="history.log")
    entries = [{"name": "cfg-2.tar.gz", "size": 5, "mtime": now - 3600},
               {"name": "cfg-1.tar.gz", "size": 5, "mtime": now - 90000},
               {"name": "last-error.txt", "size": 0, "mtime": now - 3600},
               {"name": "history.log", "size": 99, "mtime": now - 3600}]
    good = evaluate_files(item, folder(entries, {"history.log": ["OK 1", "OK 2"]}), now)
    assert good["status"] == "ok" and good["kept"] == 2 and good["log_line"] == "OK 2"
    entries[2] = {"name": "last-error.txt", "size": 12, "mtime": now}
    bad = evaluate_files(item, folder(entries, {"last-error.txt": ["ssh: timeout"]}), now)
    assert bad["status"] == "failed" and bad["error"] == "ssh: timeout"
    assert evaluate_files(item, None, now)["status"] == "unknown"


def test_runner_reports_folders_without_following_symlinks(tmp_path):
    watched = tmp_path / "dumps"
    watched.mkdir()
    (watched / "a.sql").write_text("data")
    (watched / "history.log").write_text("".join(f"line {i}\n" for i in range(10)))
    (watched / "sub").mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("do not read")
    os.symlink(secret, watched / "link.txt")
    actions = ActionsConfig.model_validate({"files": [
        {"id": "dumps", "path": str(watched), "tail": ["history.log", "link.txt"]},
        {"id": "gone", "path": str(tmp_path / "missing")},
    ]})
    report = describe_folder(actions.files[0])
    assert sorted(e["name"] for e in report["entries"]) == ["a.sql", "history.log"]
    assert report["tails"] == {"history.log": [f"line {i}" for i in range(5, 10)]}
    assert "do not read" not in json.dumps(report)

    class NoDocker:
        async def close(self):
            pass

    token = "t" * 40
    with TestClient(create_runner_app(actions, token, NoDocker(), tmp_path)) as c:
        assert c.get("/files").status_code == 401
        body = c.get("/files", headers={"Authorization": f"Bearer {token}"}).json()
    assert body[0]["ok"] and not body[1]["ok"]

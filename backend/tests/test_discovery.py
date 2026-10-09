from executor.config import Config, Discovery, Service
from executor.discovery import plan, pretty, remember
from executor.monitor import Monitor
from executor.runner import list_stacks


def container(name, project, state="running", ports=(), labels=None, working_dir=None):
    return {"name": name, "state": state, "health": None, "status": "Up 2 hours", "project": project,
            "service": name, "working_dir": working_dir or f"/srv/stacks/{project}", "labels": labels or {},
            "ports": list(ports)}


WEB = Service(id="web", name="Web", group="Apps", containers=["web-app", "web-db"])
PROBE_ONLY = Service(id="site", name="Site", group="Edge")
SETTINGS = Discovery(link_host="10.8.0.10", probe_host="host.docker.internal", ignore=["executor"],
                     names={"wiki": "The Wiki"})


def test_unconfigured_stacks_appear_and_configured_ones_stay():
    containers = {c["name"]: c for c in [
        container("web-app", "web"), container("web-db", "web"),
        container("photos", "photos", ports=[2283, 80]),
        container("photos-redis", "photos"),
        container("wiki", "wiki", labels={"executor.group": "Docs"}),
        container("executor", "executor"),
        container("tracker", "tracker", labels={"executor.hide": "true"}),
    ]}
    result = plan([WEB, PROBE_ONLY], containers, ["web", "photos", "wiki", "executor", "tracker", "old"], {}, SETTINGS)
    assert [(s.id, state) for s, state in result.configured] == [("web", "active"), ("site", "active")]
    found = {s.id: (s, stack, stopped) for s, stack, stopped in result.discovered}
    assert set(found) == {"stack-photos", "stack-wiki", "stack-old"}
    photos = found["stack-photos"][0]
    assert photos.name == "Photos" and photos.group == "Discovered" and photos.containers == ["photos", "photos-redis"]
    assert photos.url == "http://10.8.0.10:80" and photos.check.port == 80 and photos.check.host == "host.docker.internal"
    wiki = found["stack-wiki"][0]
    assert wiki.name == "The Wiki" and wiki.group == "Docs" and wiki.url is None
    assert found["stack-old"][2] is True and found["stack-old"][0].containers == []


def test_a_removed_stack_takes_its_configured_service_with_it():
    memory: dict = {}
    remember(memory, {"web-app": container("web-app", "web"), "web-db": container("web-db", "web")})
    assert memory["web-app"] == ["web"]
    # Containers gone, folder still there: stopped, not removed.
    result = plan([WEB], {}, ["web"], memory, SETTINGS)
    assert result.configured == [(WEB, "stopped")] and result.removed == []
    # Folder gone too: removed.
    result = plan([WEB], {}, [], memory, SETTINGS)
    assert result.configured == [] and result.removed == ["web"]
    # Without a stacks folder Executor cannot tell, so the service stays (and shows down).
    assert plan([WEB], {}, None, memory, SETTINGS).configured == [(WEB, "active")]
    # A stack folder named differently from its compose project still counts.
    other = {}
    remember(other, {"web-app": container("web-app", "proj", working_dir="/srv/stacks/web-folder")})
    assert plan([WEB], {}, ["web-folder"], other, SETTINGS).configured == [(WEB, "stopped")]


def test_without_discovery_settings_nothing_extra_appears():
    containers = {"photos": container("photos", "photos")}
    result = plan([WEB], containers, None, {}, None)
    assert result.discovered == [] and [s.id for s, _ in result.configured] == ["web"]


def test_pretty_names():
    assert pretty("paperless-ngx") == "Paperless ngx"
    assert pretty("home_assistant") == "Home assistant"


def test_runner_lists_only_stack_folders(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "compose.yaml").write_text("services: {}")
    (tmp_path / "notes").mkdir()
    (tmp_path / "loose.yaml").write_text("x")
    (tmp_path / "link").symlink_to(tmp_path / "web")
    assert list_stacks(str(tmp_path)) == ["web"]
    assert list_stacks(None) is None
    assert list_stacks(str(tmp_path / "missing")) is None


CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
    "discovery": {"link_host": "10.8.0.10"},
    "groups": ["Apps"],
    "machines": [],
    "services": [{"id": "web", "name": "Web", "group": "Apps", "containers": ["web-app"]}],
})


def test_monitor_shows_discovered_stacks_and_logs_changes():
    monitor = Monitor(CONFIG, None)
    monitor.containers = {"web-app": container("web-app", "web"), "photos": container("photos", "photos")}
    monitor.stack_dirs = ["web", "photos"]
    remember(monitor.stack_memory, monitor.containers)
    monitor._replan()
    snapshot = monitor.snapshot()
    assert [s["id"] for s in snapshot["services"]] == ["web", "stack-photos"]
    assert snapshot["groups"] == ["Apps", "Discovered"]
    photos = snapshot["services"][1]
    assert photos["discovered"] is True and photos["stack"] == "photos" and photos["status"] == "up"
    # A new stack is logged once it appears; a removed one when it goes.
    monitor.containers["notes"] = container("notes", "notes")
    monitor.stack_dirs.append("notes")
    monitor._replan()
    del monitor.containers["web-app"]
    monitor.stack_dirs.remove("web")
    monitor._replan()
    titles = [e["title"] for e in monitor.tracker.recent()]
    assert titles == ["Web was removed", "New stack found: Notes"]
    assert "web" not in monitor.service_ids()
    # A stopped stack is grey, not red.
    monitor.containers.pop("notes")
    monitor._replan()
    notes = next(s for s in monitor.snapshot()["services"] if s["id"] == "stack-notes")
    assert notes["status"] == "unknown" and notes["error"] == "Stack stopped"


def test_a_service_gone_before_it_was_seen_matches_its_folder_by_name():
    firefox = Service(id="firefox", name="Firefox", group="Tools", containers=["safe-firefox"])
    kuma = Service(id="uptime-kuma", name="Uptime Kuma", group="Infra", containers=["uptime-kuma"])
    memory: dict = {}
    result = plan([firefox, kuma], {}, ["firefox", "uptimekuma"], memory, SETTINGS)
    assert result.configured == [(firefox, "stopped"), (kuma, "stopped")]
    assert result.discovered == []  # no duplicate tile for the same stack
    assert result.learned and memory["safe-firefox"] == ["firefox"]
    # The folder is deleted later: the service goes with it.
    assert plan([firefox], {}, [], memory, SETTINGS).removed == ["firefox"]
    # Never linked to any folder and no folder by its names: removed as well.
    assert plan([firefox], {}, [], {}, SETTINGS).removed == ["firefox"]
    # A stopped (exited) container is still listed, so the service shows down, not removed.
    exited = {"safe-firefox": container("safe-firefox", "firefox", state="exited")}
    assert plan([firefox], exited, [], {}, SETTINGS).configured == [(firefox, "active")]

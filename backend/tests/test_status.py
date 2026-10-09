from executor.checks import Probe
from executor.config import Service
from executor.monitor import service_status

SERVICE = Service(id="immich", name="Immich", group="Photos",
                  containers=["immich-server", "immich-postgres"])


def running(name, health=None):
    return {"name": name, "state": "running", "health": health, "status": "Up"}


def test_up_when_probe_ok_and_containers_running():
    containers = {"immich-server": running("immich-server", "healthy"),
                  "immich-postgres": running("immich-postgres")}
    status, states = service_status(SERVICE, Probe(ok=True), containers)
    assert status == "up"
    assert [s["name"] for s in states] == ["immich-server", "immich-postgres"]


def test_down_when_probe_fails():
    containers = {"immich-server": running("immich-server"), "immich-postgres": running("immich-postgres")}
    assert service_status(SERVICE, Probe(ok=False), containers)[0] == "down"


def test_down_when_primary_container_stopped():
    containers = {"immich-server": {**running("immich-server"), "state": "exited"},
                  "immich-postgres": running("immich-postgres")}
    assert service_status(SERVICE, Probe(ok=True), containers)[0] == "down"


def test_degraded_when_secondary_missing_or_unhealthy():
    status, states = service_status(SERVICE, Probe(ok=True), {"immich-server": running("immich-server")})
    assert status == "degraded"
    assert states[1]["state"] == "missing"
    containers = {"immich-server": running("immich-server", "unhealthy"),
                  "immich-postgres": running("immich-postgres")}
    assert service_status(SERVICE, Probe(ok=True), containers)[0] == "degraded"


def test_probe_only_when_runner_unavailable():
    assert service_status(SERVICE, Probe(ok=True), None) == ("up", [])
    assert service_status(SERVICE, None, None) == ("unknown", [])

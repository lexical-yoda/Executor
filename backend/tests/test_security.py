from fastapi.testclient import TestClient

from executor.config import Config
from executor.web import create_web_app

CONFIG = Config.model_validate({
    "security": {"allowed_clients": ["10.8.0.0/24"], "denied_clients": ["10.8.0.1/32"],
                 "allowed_hosts": ["10.8.0.10"]},
    "machines": [{"id": "nas", "name": "NAS", "role": "x", "local": True}],
    "services": [],
})

ACTION_HEADERS = {"X-Executor": "1", "Content-Type": "application/json"}


def client(ip: str, host: str = "10.8.0.10:1977") -> TestClient:
    app = create_web_app(CONFIG, runner=None, static_dir=None, start_monitor=False)
    return TestClient(app, base_url=f"http://{host}", client=(ip, 5000))


def test_allowed_peer_can_read_status():
    response = client("10.8.0.2").get("/api/status")
    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_denied_hub_and_lan_are_refused():
    # The hub sits inside the allowed /24 but is denied explicitly.
    assert client("10.8.0.1").get("/api/status").status_code == 403
    assert client("192.168.0.8").get("/api/status").status_code == 403


def test_unknown_host_header_is_refused():
    assert client("10.8.0.2", host="evil.example:1977").get("/api/status").status_code == 400


def test_healthz_from_loopback_only():
    assert client("127.0.0.1", host="127.0.0.1:1977").get("/healthz").status_code == 200
    assert client("10.8.0.1").get("/healthz").status_code == 403


def test_action_needs_custom_header():
    c = client("10.8.0.2")
    assert c.post("/api/actions/x/run", content="{}",
                  headers={"Content-Type": "application/json"}).status_code == 403


def test_action_needs_json():
    c = client("10.8.0.2")
    assert c.post("/api/actions/x/run", content="a=b",
                  headers={"X-Executor": "1",
                           "Content-Type": "application/x-www-form-urlencoded"}).status_code == 415


def test_cross_origin_action_is_refused():
    c = client("10.8.0.2")
    headers = {**ACTION_HEADERS, "Origin": "https://evil.example"}
    assert c.post("/api/actions/x/run", content="{}", headers=headers).status_code == 403
    headers = {**ACTION_HEADERS, "Sec-Fetch-Site": "cross-site"}
    assert c.post("/api/actions/x/run", content="{}", headers=headers).status_code == 403


def test_same_origin_action_reaches_the_app():
    c = client("10.8.0.2")
    headers = {**ACTION_HEADERS, "Origin": "http://10.8.0.10:1977", "Sec-Fetch-Site": "same-origin"}
    # No runner is configured in this test, so passing the guard means a 503.
    assert c.post("/api/actions/x/run", content="{}", headers=headers).status_code == 503


def test_responses_are_compressed_except_map_archives(tmp_path):
    from fastapi.testclient import TestClient

    from executor.config import Config
    from executor.web import create_web_app

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>" + "<p>deck</p>" * 400)
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    (tiles / "world.pmtiles").write_bytes(b"PMTiles" + b"\0" * 4000)
    config = Config.model_validate({
        "security": {"allowed_clients": ["10.8.0.0/24"], "allowed_hosts": ["10.8.0.10"]},
        "machines": [], "services": [],
    })
    app = create_web_app(config, runner=None, static_dir=static, start_monitor=False, tiles_dir=tiles)
    c = TestClient(app, base_url="http://10.8.0.10:1977", client=("10.8.0.2", 5000))
    gzip = {"Accept-Encoding": "gzip"}
    page = c.get("/", headers=gzip)
    assert page.headers.get("content-encoding") == "gzip" and "deck" in page.text
    archive = c.get("/tiles/world.pmtiles", headers={**gzip, "Range": "bytes=0-6"})
    assert archive.status_code == 206 and "content-encoding" not in archive.headers
    assert archive.content == b"PMTiles"
    # Refused clients still get nothing at all.
    outsider = TestClient(app, base_url="http://10.8.0.10:1977", client=("172.16.0.5", 5000))
    assert outsider.get("/", headers=gzip).status_code == 403

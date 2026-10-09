import ipaddress
from pathlib import Path

import pytest
from pydantic import ValidationError

from executor.config import ActionsConfig, Config, load_actions, load_config

ROOT = Path(__file__).resolve().parents[2]

EXAMPLES = [
    (ROOT / "deploy/config/config.example.yaml", ROOT / "deploy/config/actions.example.yaml"),
    (ROOT / "dev/config.example.yaml", ROOT / "dev/actions.yaml"),
]

# Real configs are git-ignored; these tests run only where they exist.
PRIVATE = [p for p in (ROOT / "deploy/config/config.yaml", ROOT / "dev/config.yaml") if p.exists()]


@pytest.mark.parametrize("config_path,actions_path", EXAMPLES)
def test_example_configs_are_valid(config_path, actions_path):
    config = load_config(config_path)
    actions = load_actions(actions_path)
    assert config.services and actions.actions


@pytest.mark.skipif(not PRIVATE, reason="no private config on this machine")
@pytest.mark.parametrize("path", PRIVATE)
def test_private_configs_are_valid(path):
    load_config(path)
    actions = path.with_name("actions.yaml")
    if actions.exists():
        load_actions(actions)


@pytest.mark.skipif(not (ROOT / "deploy/config/config.yaml").exists(), reason="no private deploy config")
def test_deploy_config_denies_something_and_allows_nothing_denied():
    security = load_config(ROOT / "deploy/config/config.yaml").security
    assert security.denied_clients, "the deploy config must deny the WireGuard hub and the LAN"
    denied = [ipaddress.ip_network(d, strict=False) for d in security.denied_clients]
    for allowed in security.allowed_clients:
        network = ipaddress.ip_network(allowed, strict=False)
        assert not any(network.overlaps(d) for d in denied), f"{allowed} overlaps a denied network"


def test_step_needs_exactly_one_kind():
    with pytest.raises(ValidationError):
        ActionsConfig.model_validate({"actions": [{"id": "x", "title": "x", "confirm": "x",
                                                   "steps": [{"name": "empty"}]}]})


def test_duplicate_service_ids_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({
            "security": {"allowed_clients": ["10.8.0.2/32"], "allowed_hosts": ["x"]},
            "machines": [],
            "services": [{"id": "a", "name": "A", "group": "g"}, {"id": "a", "name": "B", "group": "g"}],
        })


def test_unknown_icon_rejected():
    with pytest.raises(ValidationError):
        Config.model_validate({
            "security": {"allowed_clients": ["10.8.0.2/32"], "allowed_hosts": ["x"]},
            "machines": [{"id": "m", "name": "M", "role": "r", "icon": "toaster"}],
            "services": [],
        })

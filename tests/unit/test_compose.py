from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"


def test_no_public_ports_except_loopback_api():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = data["services"]
    for name in ("postgres", "redis", "worker", "migrate"):
        assert "ports" not in services[name], f"{name} publica puertos"
    api_ports = services["api"]["ports"]
    assert len(api_ports) == 1
    assert str(api_ports[0]).startswith("127.0.0.1:")


def test_healthchecks_and_depends_on():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = data["services"]
    assert "healthcheck" in services["postgres"]
    assert "healthcheck" in services["redis"]
    assert services["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_job_timeout():
    from bore.orchestration.jobs import compute_job_timeout

    cfg = {"limits": {"max_duration_seconds": 5400}}
    assert compute_job_timeout(cfg) == 5700

"""Bounded operational metrics and the token-gated scrape endpoint."""

import pytest
from app.core.config import Settings
from app.core.metrics import LATENCY_BUCKETS, Metrics, route_class, status_class
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr

TOKEN = "t" * 40


def test_route_and_status_classes_are_fixed_vocabularies():
    assert route_class("/api/v1/projects/p1/glyphs/g1") == "projects"
    assert route_class("/api/v1/storage/objects/abc") == "storage"
    assert route_class("/api/v1/unknown/thing") == "other"
    assert route_class("/internal/metrics") == "metrics"
    assert route_class("/anything") == "other"
    assert status_class(200) == "2xx"
    assert status_class(429) == "4xx"
    assert status_class(503) == "5xx"


def test_unknown_route_cannot_become_a_label():
    metrics = Metrics()
    metrics.request(route_class("/api/v1/projects/x/glyphs/y"), 200, 0.02)
    metrics.request("/api/v1/projects/x/glyphs/y", 500, 0.4)
    rendered = metrics.render()
    assert 'route="projects"' in rendered
    assert 'route="other"' in rendered
    assert "/x/" not in rendered and "glyphs" not in rendered
    assert 'status="5xx"' in rendered


def test_histogram_buckets_are_cumulative_and_terminated():
    metrics = Metrics()
    for seconds in (0.005, 0.02, 0.2, 3.0):
        metrics.request("jobs", 200, seconds)
    rendered = metrics.render()
    counts = {}
    for line in rendered.splitlines():
        if line.startswith("penforge_request_duration_seconds_bucket") and 'route="jobs"' in line:
            bound = line.split('le="')[1].split('"')[0]
            counts[bound] = int(line.rsplit(" ", 1)[1])
    assert list(counts) == [f"{bound:g}" for bound in LATENCY_BUCKETS] + ["+Inf"]
    values = list(counts.values())
    assert values == sorted(values)
    assert values[-1] == 4
    assert 'penforge_request_duration_seconds_count{route="jobs"} 4' in rendered


def test_invalid_samples_and_reasons_are_ignored():
    metrics = Metrics()
    metrics.request("jobs", 200, float("nan"))
    metrics.request("jobs", 200, -1.0)
    metrics.rejection("user@example.com")
    assert metrics.snapshot()["requests"] == {}
    assert metrics.snapshot()["rejections"] == {}


def config(tmp_path, **overrides):
    return Settings(
        app_env="test",
        storage_root=tmp_path / "objects",
        auth_backend="disabled",
        storage_backend="disabled",
        readiness_token=SecretStr(TOKEN),
        **overrides,
    )


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer " + "w" * 40}, {"Authorization": TOKEN}]
)
def test_scrape_is_404_without_the_exact_bearer_token(tmp_path, headers):
    with TestClient(create_app(config(tmp_path))) as client:
        response = client.get("/internal/metrics", headers=headers)
        assert response.status_code == 404
        assert "penforge_requests_total" not in response.text


def test_scrape_requires_a_configured_token_at_all(tmp_path):
    settings = Settings(
        app_env="test",
        storage_root=tmp_path / "objects",
        auth_backend="disabled",
        storage_backend="disabled",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/internal/metrics", headers={"Authorization": "Bearer " + TOKEN})
        assert response.status_code == 404


def test_scrape_reports_api_counters_and_a_db_outage_without_failing(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        client.get("/api/v1/health")
        response = client.get("/internal/metrics", headers={"Authorization": "Bearer " + TOKEN})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        assert 'penforge_requests_total{route="health",status="2xx"}' in body
        # No database is wired in this configuration; the scrape must degrade, not 500.
        assert "penforge_scrape_db_ok 0" in body
        assert "penforge_queue_depth" not in body


def test_scrape_does_not_expose_request_paths_or_identities(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        client.get("/api/v1/projects/11111111-1111-1111-1111-111111111111")
        body = client.get("/internal/metrics", headers={"Authorization": "Bearer " + TOKEN}).text
        assert "11111111" not in body
        assert "authorization" not in body.lower()
        assert "/api/v1/projects/" not in body


def test_metrics_endpoint_is_absent_from_the_public_contract(tmp_path):
    with TestClient(create_app(config(tmp_path))) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/internal/metrics" not in paths


def test_rate_limit_errors_increment_bounded_counter_and_response_bytes(tmp_path):
    from app.core.errors import ApiError

    app = create_app(config(tmp_path))

    @app.get("/rate-fixture")
    async def limited():
        raise ApiError("rate_limited", 429)

    with TestClient(app) as client:
        response = client.get("/rate-fixture")
        assert response.status_code == 429
        metrics = app.state.metrics.snapshot()
        assert metrics["rejections"]["rate_limited"] == 1
        assert metrics["transfer"]["sent"] == len(response.content)
        assert metrics["transfer"]["received"] == 0

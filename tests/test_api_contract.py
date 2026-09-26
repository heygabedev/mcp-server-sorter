import pytest
import schemathesis
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import positive_data_acceptance

from mcp_sorter.api import create_app
from mcp_sorter.settings import Settings

schema = schemathesis.pytest.from_fixture("api_schema")


@pytest.fixture
def api_schema(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False))
    return schemathesis.openapi.from_asgi("/openapi.json", app)


@schema.parametrize()
@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_openapi_contract(case):
    # IDs and checksums can be structurally valid while referencing absent local state.
    # Successful domain-specific journeys are covered by API/CLI and browser tests.
    case.call_and_validate(excluded_checks=[positive_data_acceptance])


def test_error_schema_strict_inputs_and_repeated_lifespans(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, worker_enabled=False))
    for _ in range(2):
        with TestClient(app) as client:
            assert client.get("/health/ready").status_code == 200
            assert app.state.telemetry.reader.get_metrics_data().resource_metrics
            for body in ({"limit": "20"}, {"filters": {"include_deprecated": 0}}):
                assert client.post("/api/v1/rankings", json=body).status_code == 422
            assert (
                client.post("/api/v1/configurations", json={"schema_version": True}).status_code
                == 422
            )
            response = client.options("/api/v1/collections")
            assert response.status_code == 405
            assert set(response.headers["allow"].split(", ")) == {"GET", "POST"}
            error_schema = client.get("/openapi.json").json()["components"]["schemas"][
                "ErrorResponse"
            ]
            assert "anyOf" in error_schema["properties"]["detail"]

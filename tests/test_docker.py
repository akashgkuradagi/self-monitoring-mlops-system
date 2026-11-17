"""Unit and Verification Tests for Docker Packaging & Multi-Container Compose Configuration (PKG-01).

Validates:
- NFR-06: Portability and reproducible packaging via Docker & Docker Compose.
- TC-15: Container build configuration, health check probes, and volume persistence assertions.
- Acceptance Criteria 1-4 of Issue #15: Multi-stage build, non-root user, persistent volumes, and MLflow networking.
"""

from pathlib import Path
import re
import yaml

from app.monitoring.alerting import AlertManager
from app.registry.mlflow_adapter import MLflowAdapter
from app.security.audit import AuditLogger


def test_dockerfile_exists_and_contains_required_directives():
    """Verify Dockerfile presence, multi-stage structure, non-root user, and health check."""
    dockerfile_path = Path("Dockerfile")
    assert dockerfile_path.is_file(), "Dockerfile must exist at repository root"

    content = dockerfile_path.read_text(encoding="utf-8")

    # Multi-stage build verification
    assert "FROM python:3.11-slim AS builder" in content
    assert "FROM python:3.11-slim AS runner" in content

    # Non-root unprivileged user
    assert "useradd" in content
    assert "USER mlops" in content

    # Port and Entrypoint
    assert "EXPOSE 8000" in content
    assert 'ENTRYPOINT ["uvicorn", "app.main:app"]' in content

    # Container healthcheck probe
    assert "HEALTHCHECK" in content
    assert "http://localhost:8000/health" in content


def test_dockerignore_excludes_transient_and_sensitive_files():
    """Verify that .dockerignore prevents committing secrets, venvs, and local databases."""
    ignore_path = Path(".dockerignore")
    assert ignore_path.is_file(), ".dockerignore must exist"

    content = ignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]

    assert any(".venv" in line for line in lines)
    assert any(".env" in line for line in lines)
    assert any("telemetry.db" in line for line in lines)
    assert any("mlflow.db" in line for line in lines)
    assert any("mlruns" in line for line in lines)


def test_docker_compose_validity_and_service_topology():
    """Verify docker-compose.yml syntax, service dependencies, networking, and volumes."""
    compose_path = Path("docker-compose.yml")
    assert compose_path.is_file(), "docker-compose.yml must exist"

    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "services" in data

    services = data["services"]
    assert "web" in services, "Service 'web' must be configured in docker-compose.yml"
    assert "mlflow" in services, "Service 'mlflow' must be configured in docker-compose.yml"

    web_svc = services["web"]
    mlflow_svc = services["mlflow"]

    # Port mappings
    assert "8000:8000" in web_svc.get("ports", [])
    assert "5000:5000" in mlflow_svc.get("ports", [])

    # Internal service networking & address resolution
    web_env = web_svc.get("environment", [])
    assert any("MLFLOW_TRACKING_URI=http://mlflow:5000" in env for env in web_env)

    # Dependency ordering with healthcheck
    depends = web_svc.get("depends_on", {})
    assert "mlflow" in depends
    assert depends["mlflow"].get("condition") == "service_healthy"

    # Named persistent volume
    assert "volumes" in data
    assert "mlops_data" in data["volumes"]

    # Both services mount mlops_data
    assert any("mlops_data:/data" in v for v in web_svc.get("volumes", []))
    assert any("mlops_data:/data" in v for v in mlflow_svc.get("volumes", []))

    # Health checks on both services
    assert "healthcheck" in web_svc
    assert "healthcheck" in mlflow_svc


def test_database_initialization_resilience_in_mounted_paths(tmp_path):
    """Ensure audit, alert, and MLflow adapters cleanly create nested directories in container volumes."""
    nested_dir = tmp_path / "mounted_volume" / "data"
    telemetry_db = str(nested_dir / "telemetry.db")
    mlflow_db = str(nested_dir / "mlflow.db")

    # Assert directory does not exist prior to initialization
    assert not nested_dir.exists()

    # Initializing adapters must auto-create directory and SQLite tables
    audit_mgr = AuditLogger(db_path=telemetry_db)
    alert_mgr = AlertManager(db_path=telemetry_db)
    mlflow_adp = MLflowAdapter(db_path=mlflow_db)

    assert nested_dir.exists()
    assert Path(telemetry_db).is_file()
    assert Path(mlflow_db).is_file()
    assert mlflow_adp.get_active_model_state() is None

    # Operations succeed without error
    evt = audit_mgr.log_event(
        actor="container_probe",
        role="Administrator",
        action="MOUNT_TEST",
        resource="volume",
        outcome="SUCCESS",
    )
    assert evt.event_id.startswith("audit-")

    alert, _ = alert_mgr.record_or_deduplicate(
        metric_name="CONTAINER_HEALTH",
        severity="LOW",
        observed_value=1.0,
        threshold=1.0,
        message="Container volume probe",
    )
    assert alert.alert_id.startswith("alt-")


def test_no_hardcoded_secrets_in_docker_configurations():
    """Verify Dockerfile and docker-compose.yml contain no hardcoded secrets or credentials."""
    for file_path in [Path("Dockerfile"), Path("docker-compose.yml")]:
        content = file_path.read_text(encoding="utf-8")
        assert not re.search(r"password\s*:\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)
        assert not re.search(r"secret_key\s*:\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)
        assert not re.search(r"BEGIN (RSA|OPENSSH|PRIVATE) KEY", content)

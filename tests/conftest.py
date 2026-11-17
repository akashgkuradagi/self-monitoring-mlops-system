"""Pytest Shared Fixtures."""

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.monitoring.collector import telemetry_collector
from app.security.audit import audit_logger


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    """Ensure telemetry collector state is completely reset before each test."""
    telemetry_collector.reset()
    yield
    telemetry_collector.reset()


@pytest.fixture(autouse=True)
def isolate_audit_db(tmp_path):
    """Ensure audit logger uses an isolated temporary SQLite database for each test."""
    original_db = audit_logger.db_path
    test_db = str(tmp_path / "test_audit.db")
    audit_logger.db_path = test_db
    audit_logger._init_db()
    yield
    audit_logger.db_path = original_db


@pytest.fixture(autouse=True)
def cleanup_mlflow_runs():
    """Ensure no dangling active MLflow runs leak between tests."""
    import mlflow
    while mlflow.active_run():
        mlflow.end_run()
    yield
    while mlflow.active_run():
        mlflow.end_run()


@pytest.fixture
def client():
    """Create a synchronous TestClient for testing HTTP endpoints."""
    with TestClient(app) as test_client:
        yield test_client

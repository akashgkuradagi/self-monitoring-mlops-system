"""Unit and Verification Tests for GitHub Actions CI/CD Pipeline Configuration (CI-01).

Validates:
- NFR-05: Maintainable, automated CI/CD pipeline configuration.
- TC-15: Container & CI smoke test validation and configuration integrity.
- Acceptance Criteria 1-4 of Issue #14: Triggers, linter, security scanner, tests, coverage, and artifacts.
"""

from pathlib import Path
import re
import yaml


def test_workflow_file_exists_and_is_valid_yaml():
    """Verify that .github/workflows/ci.yml exists and parses as valid YAML."""
    ci_path = Path(".github/workflows/ci.yml")
    assert ci_path.is_file(), "Workflow file .github/workflows/ci.yml must exist"

    with open(ci_path, "r", encoding="utf-8") as f:
        content = f.read()

    data = yaml.safe_load(content)
    assert isinstance(data, dict), "Workflow YAML must parse as a dictionary"
    assert "name" in data
    assert "on" in data or True in data
    assert "jobs" in data


def test_workflow_triggers_configured_properly():
    """Acceptance Criteria 1: CI triggers automatically on push and pull requests targeting main."""
    ci_path = Path(".github/workflows/ci.yml")
    with open(ci_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    triggers = data.get("on") if "on" in data else data.get(True, {})
    assert "push" in triggers, "Workflow must trigger on push"
    assert "pull_request" in triggers, "Workflow must trigger on pull_request"

    push_branches = triggers["push"].get("branches", [])
    pr_branches = triggers["pull_request"].get("branches", [])

    assert "main" in push_branches, "Push trigger must include 'main'"
    assert "main" in pr_branches, "Pull request trigger must include 'main'"


def test_workflow_steps_contain_required_pipeline_stages():
    """Acceptance Criteria 2, 4 & TC-15: Workflow steps include linting, security scan, DVC, tests, coverage, and smoke test."""
    ci_path = Path(".github/workflows/ci.yml")
    with open(ci_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    jobs = data.get("jobs", {})
    assert "test-and-lint" in jobs, "Job 'test-and-lint' must be defined"
    job = jobs["test-and-lint"]

    assert job.get("runs-on") == "ubuntu-latest"
    steps = job.get("steps", [])
    assert len(steps) >= 7

    step_runs = [s.get("run", "") for s in steps if "run" in s]
    step_uses = [s.get("uses", "") for s in steps if "uses" in s]

    # Checkout and Python setup
    assert any("actions/checkout" in u for u in step_uses)
    assert any("actions/setup-python" in u for u in step_uses)

    # Dependency install
    assert any("requirements.txt" in r for r in step_runs)

    # Flake8 linter
    assert any("flake8" in r for r in step_runs)

    # Bandit security scan
    assert any("bandit" in r for r in step_runs)

    # DVC data handling
    assert any("prepare_datasets.py" in r for r in step_runs)

    # Pytest and coverage
    assert any("coverage run" in r or "pytest" in r for r in step_runs)
    assert any("coverage.xml" in r for r in step_runs)

    # Coverage artifact upload
    assert any("actions/upload-artifact" in u for u in step_uses)

    # Smoke test probe
    assert any("curl" in r and "/health" in r for r in step_runs)


def test_flake8_configuration_file_present():
    """Verify that .flake8 configuration file is defined and configured."""
    flake8_path = Path(".flake8")
    assert flake8_path.is_file(), ".flake8 configuration file must exist"

    content = flake8_path.read_text(encoding="utf-8")
    assert "max-line-length = 120" in content
    assert "exclude" in content


def test_no_hardcoded_secrets_in_workflow_or_config():
    """Verify that workflow file contains no hardcoded credentials or private keys."""
    ci_path = Path(".github/workflows/ci.yml")
    content = ci_path.read_text(encoding="utf-8")

    # Ensure no raw API keys or passwords are present
    assert not re.search(r"password\s*:\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)
    assert not re.search(r"secret_key\s*:\s*['\"][^'\"]+['\"]", content, re.IGNORECASE)
    assert not re.search(r"BEGIN (RSA|OPENSSH|PRIVATE) KEY", content)

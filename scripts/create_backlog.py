#!/usr/bin/env python3
"""
Self-Monitoring MLOps System — GitHub Backlog & ProjectsV2 Automation Script

Single Source of Truth: docs/PROJECT_ANALYSIS_AND_BACKLOG.md
Target Repository:      https://github.com/akashgkuradagi/self-monitoring-mlops-system
Target Project Board:   Self-Monitoring MLOps System — Project Board

Core Architecture:
  1. Complete Heading & Content Extraction: Extracts five Markdown headings (User Story / Description,
     Requirement Traceability, Scope & Tasks, Acceptance Criteria, Dependencies & Governance)
     and separately verified content lines (including Related Test Cases).
  2. Dynamic Sprint Allocation: Parses Sprint 1 and Sprint 2 allocations directly from
     Section E of the planning document (no hardcoded sprint mapping).
  3. Pre-Flight Strict Validation: Validates Project Board existence, required fields
     (Sprint, Story Points, Priority), and required option IDs before any write operation.
  4. Strengthened Permissions: Validates classic scopes (repo, project) or fine-grained
     permissions without exposing tokens or using test mutations.
  5. Full Pagination: Implements complete multi-page retrieval for REST and GraphQL endpoints.
  6. Idempotent Duplicate & Partial-Failure Recovery: Reuses existing issues on GitHub,
     re-links missing project board items, and populates/corrects custom project fields.
  7. Strict Dry-Run Safety: Guarantees zero write operations when executed in dry-run mode.
  8. End-to-End Post-Execution Verification: Verifies all 15 issues, assignees, labels,
     project memberships, and custom field values after live operations.
"""

import sys
import os
import re
import json
import argparse
import urllib.request
import urllib.error
import subprocess

# Ensure UTF-8 console output on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_OWNER = "akashgkuradagi"
REPO_NAME = "self-monitoring-mlops-system"
EXACT_PROJECT_TITLE = "Self-Monitoring MLOps System — Project Board"
DOC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs",
    "PROJECT_ANALYSIS_AND_BACKLOG.md"
)

# Implementation Scope & Tasks mapping for each approved issue
SCOPE_AND_TASKS_MAP = {
    "CORE-01": [
        "Initialize FastAPI service structure (`app/main.py`, `app/api/routes.py`).",
        "Implement liveness/readiness health check endpoint `GET /health`.",
        "Implement prediction telemetry ingestion endpoint `POST /api/v1/events/prediction`.",
        "Implement in-memory/SQLite sliding window collector (`app/monitoring/collector.py`).",
        "Implement operational telemetry metrics endpoint `GET /api/v1/metrics/summary`."
    ],
    "SEC-01": [
        "Implement token-based authentication (Bearer tokens / API keys) in `app/security/auth.py`.",
        "Implement server-side Role-Based Access Control (RBAC) with `Viewer`, `ML_Engineer`, and `Administrator` roles.",
        "Implement request boundary middleware (`app/security/middleware.py`) enforcing 1 MB size limit and JSON schema validation.",
        "Secure candidate validation, decision, and rollback routes against unauthenticated or unauthorized access."
    ],
    "SEC-02": [
        "Implement SQLite append-only audit store (`app/security/audit.py`).",
        "Implement cryptographic SHA-256 hash chaining (`prev_hash` + event fields -> `record_hash`).",
        "Implement log sanitization middleware to redact sensitive feature values, bearer tokens, and secrets.",
        "Implement audit verification utility to detect any unauthorized database modifications."
    ],
    "DATA-01": [
        "Curate an open-source tabular classification dataset (e.g., Bank Marketing / German Credit).",
        "Generate baseline reference and controlled shifted fixtures (`baseline_reference.csv`, `stream_stable.csv`, `stream_shifted.csv`).",
        "Configure DVC tracking (`dvc init`, `dvc.yaml`, `data/*.dvc`).",
        "Provide template `.dvc/config.local.example` and documentation for configurable shared remote storage.",
        "Implement deterministic synthetic sample data generator script for offline local development."
    ],
    "ML-01": [
        "Implement production tabular classification pipeline (`app/models/baseline.py`, e.g., Random Forest Classifier).",
        "Train baseline classifier on DVC-tracked reference dataset with fixed random seed.",
        "Integrate MLflow Tracking to log hyperparameters, evaluation metrics (Accuracy, F1, ROC-AUC), and model artifacts.",
        "Register baseline model in MLflow Model Registry as `ProductionClassifier` and initialize production tracking.",
        "Implement delayed ground-truth logic returning `accuracy: null` / `\"unavailable\"` when labels are missing."
    ],
    "MON-01": [
        "Implement two-sample Kolmogorov-Smirnov test (`scipy.stats.ks_2samp`) for continuous feature drift analysis.",
        "Implement Population Stability Index (PSI) with 10 quantiles for categorical and prediction drift analysis.",
        "Compare sliding active inference batches against reference baseline distribution statistics.",
        "Emit structured drift detection results (p-value, PSI metric, affected features, `STABLE` vs `DRIFT_DETECTED`).",
        "Benchmark drift engine scalability with 1,000 synthetic events without event loss or memory leak."
    ],
    "ALERT-01": [
        "Implement alert manager engine (`app/monitoring/alerting.py`) evaluating incoming telemetry against thresholds.",
        "Configure multi-metric alert thresholds: statistical drift ($p < 0.05$), error rate ($> 5\\%$), and p95 latency ($> 500\\text{ ms}$).",
        "Implement alert deduplication logic over a 5-minute rolling window to prevent alert storms.",
        "Expose queryable alerts endpoint `GET /api/v1/alerts` with status and severity (`LOW`, `MEDIUM`, `HIGH`) filtering."
    ],
    "EXP-01": [
        "Implement unsupervised anomaly detection monitoring experiment (`app/models/ml_monitor.py`) using Isolation Forest.",
        "Train model on reference feature distributions to score incoming inference windows without ground-truth labels.",
        "Compute anomaly ratios and mean decision scores across windowed prediction batches.",
        "Log experiment run metadata, algorithm parameters, contamination factor, metrics, and artifact to MLflow (`classical-ml-monitor`)."
    ],
    "EXP-02": [
        "Implement PyTorch sequential neural network (`app/models/dl_monitor.py`, LSTM or Autoencoder).",
        "Train temporal sequence model on sliding input feature batches with fixed random seed.",
        "Compute windowed sequence reconstruction loss to detect temporal and sequential pattern shifts.",
        "Log model weights, architecture hyperparameters, training loss curves, and evaluation metrics to MLflow (`dl-sequence-monitor`).",
        "Isolate DL execution asynchronously to prevent interference with live prediction API latency."
    ],
    "EXP-03": [
        "Implement variational quantum classifier (VQC) benchmark using PennyLane (`app/models/qml_benchmark.py`).",
        "Execute circuit exclusively on local quantum simulator (`default.qubit`) on a reduced 2-to-4 feature subset.",
        "Log simulator version, circuit ansatz, qubit count, encoding strategy, seed, runtime, and accuracy to MLflow (`qml-benchmarks`).",
        "Enforce architectural firewall in recovery orchestrator to strictly prohibit QML models from staging or production promotion."
    ],
    "REC-01": [
        "Implement recovery policy engine (`app/recovery/policy.py`) evaluating cooldown timer, persistence, and retraining eligibility.",
        "Implement candidate model lifecycle state machine in SQLite (`PENDING_VALIDATION`, `VALIDATION_FAILED`, `PENDING_APPROVAL`, `PROMOTED`, `REJECTED`).",
        "Implement automated candidate validator running regression gates against golden validation datasets and latency benchmarks.",
        "Implement authenticated decision endpoint `POST /api/v1/candidates/{id}/decision` requiring `ML_Engineer` role.",
        "Enforce dual-gate promotion: block candidate promotion until automated validation passes AND human approval is granted."
    ],
    "REC-02": [
        "Track active production model version and previous stable fallback version in SQLite `active_model_state`.",
        "Implement post-promotion canary health guard monitoring error rates and latency for 5 minutes post-promotion.",
        "Implement automated rollback trigger autonomously reinstating the prior stable model upon canary failure.",
        "Implement manual administrative rollback endpoint `POST /api/v1/models/rollback`.",
        "Record all rollback events with actor, reason, restored version, and correlation ID in tamper-evident audit log."
    ],
    "UI-01": [
        "Build responsive, accessible HTML5/Vanilla JS web dashboard served directly by FastAPI (`app/dashboard/`).",
        "Render real-time service health, prediction counts, rolling p95 latency, error rates, and active alerts.",
        "Implement candidate model review scorecard displaying validation metrics and Approve/Reject action buttons.",
        "Enforce 3-click maximum navigation path from landing page to Health, Alerts, and Candidate Review.",
        "Render all status indicators using accessible text labels (`HEALTHY`, `DRIFT_DETECTED`, `CRITICAL`) rather than color alone."
    ],
    "CI-01": [
        "Configure GitHub Actions workflow (`.github/workflows/ci.yml`) triggering on pull requests and pushes to `main`.",
        "Add automated code formatting check (`black --check`) and linting (`flake8`).",
        "Add automated security vulnerability scanning (`bandit -r app/`).",
        "Add automated unit and integration test execution (`pytest tests/`).",
        "Generate code coverage and branch coverage reports (`pytest-cov`) for Part-3 reporting.",
        "Execute Docker build smoke test to verify container image reproducibility."
    ],
    "PKG-01": [
        "Create optimized multi-stage `Dockerfile` packaging FastAPI service and background workers.",
        "Create `docker-compose.yml` orchestrating API service and MLflow tracking server.",
        "Configure container health check probe querying `GET /health` with restart policies.",
        "Configure persistent host volume mounts for SQLite telemetry database and MLflow artifact store."
    ]
}

LABEL_CONFIGS = [
    {"name": "Sprint 1", "color": "0E8A16", "description": "Sprint 1: Foundation, Telemetry & Drift Detection"},
    {"name": "Sprint 2", "color": "1D76DB", "description": "Sprint 2: Recovery, Experiments & Dashboard"},
    {"name": "SP: 3", "color": "D4C5F9", "description": "Story Points: 3"},
    {"name": "SP: 5", "color": "C5DEF5", "description": "Story Points: 5"},
    {"name": "SP: 8", "color": "BFD4F2", "description": "Story Points: 8"},
    {"name": "Priority: Critical", "color": "B60205", "description": "Critical Priority"},
    {"name": "Priority: High", "color": "D93F0B", "description": "High Priority"},
    {"name": "Priority: Medium", "color": "FBCA04", "description": "Medium Priority"},
    {"name": "core-api", "color": "5319E7", "description": "FastAPI core service & telemetry"},
    {"name": "security", "color": "0052CC", "description": "Authentication & RBAC controls"},
    {"name": "audit", "color": "006B75", "description": "Tamper-evident audit logging"},
    {"name": "dataset", "color": "0E8A16", "description": "DVC data tracking & fixtures"},
    {"name": "ml-baseline", "color": "1D76DB", "description": "Production classification baseline"},
    {"name": "drift-engine", "color": "D93F0B", "description": "Statistical KS/PSI drift detection"},
    {"name": "alerting", "color": "E99695", "description": "Multi-metric threshold alerts"},
    {"name": "ml-experiment", "color": "C2E0C6", "description": "ML-based monitoring experiment (FR-05)"},
    {"name": "dl-experiment", "color": "BFDADC", "description": "Deep learning sequence experiment (FR-06)"},
    {"name": "qml-benchmark", "color": "E7B8DE", "description": "Isolated quantum ML benchmark (FR-07)"},
    {"name": "recovery", "color": "B60205", "description": "Recovery policy & auto-rollback"},
    {"name": "ui", "color": "F9D0C4", "description": "Web dashboard & approval UI"},
    {"name": "ci-cd", "color": "215732", "description": "GitHub Actions CI pipeline"},
    {"name": "docker", "color": "0366D6", "description": "Docker packaging & compose"}
]

KEY_TO_LABEL = {
    "CORE-01": "core-api",
    "SEC-01": "security",
    "SEC-02": "audit",
    "DATA-01": "dataset",
    "ML-01": "ml-baseline",
    "MON-01": "drift-engine",
    "ALERT-01": "alerting",
    "EXP-01": "ml-experiment",
    "EXP-02": "dl-experiment",
    "EXP-03": "qml-benchmark",
    "REC-01": "recovery",
    "REC-02": "recovery",
    "UI-01": "ui",
    "CI-01": "ci-cd",
    "PKG-01": "docker"
}

def resolve_github_token():
    """Safely retrieves the GitHub token from environment, Windows registry, or local credential helper."""
    # 1. Process environment variable (explicit precedence)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()

    # 2. On Windows, check user environment registry in case it was set without IDE process restart
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                for var_name in ["GITHUB_TOKEN", "GH_TOKEN"]:
                    try:
                        val, _ = winreg.QueryValueEx(key, var_name)
                        if val and str(val).strip():
                            return str(val).strip()
                    except FileNotFoundError:
                        pass
        except Exception:
            pass

    # 3. Fallback to local Git credential helper
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            text=True,
            capture_output=True,
            check=False
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None

def github_rest_request(endpoint, token, method="GET", data=None):
    """Executes a GitHub REST API request with standard headers."""
    url = f"https://api.github.com{endpoint}" if endpoint.startswith("/") else endpoint
    body = json.dumps(data).encode("utf-8") if data else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Backlog-Automation-Script",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"REST API error {err.code} on {method} {url}: {err_body}")

def github_rest_request_with_headers(endpoint, token):
    """Executes a GET request returning both parsed JSON body and response headers."""
    url = f"https://api.github.com{endpoint}" if endpoint.startswith("/") else endpoint
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Backlog-Automation-Script",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            data = json.loads(content) if content else {}
            return data, resp.headers
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"REST API error {err.code} on GET {url}: {err_body}")

def github_graphql_request(query, variables, token):
    """Executes a GitHub GraphQL API query or mutation."""
    url = "https://api.github.com/graphql"
    payload = {"query": query, "variables": variables or {}}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "Backlog-Automation-Script",
        "Content-Type": "application/json"
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if "errors" in res:
                raise RuntimeError(f"GraphQL returned errors: {json.dumps(res['errors'], indent=2)}")
            return res.get("data", {})
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GraphQL HTTP error {err.code}: {err_body}")

# ==============================================================================
# PAGINATED REST & GRAPHQL RETRIEVAL (Requirement 6)
# ==============================================================================

def get_all_paginated_rest(endpoint, token):
    """Paginates through all pages of a GitHub REST endpoint."""
    results = []
    page = 1
    delim = "&" if "?" in endpoint else "?"
    while True:
        paginated_url = f"{endpoint}{delim}per_page=100&page={page}"
        data = github_rest_request(paginated_url, token)
        if not data:
            break
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results

def get_all_user_projects(owner_login, token):
    """Paginates through all user ProjectsV2 via GraphQL."""
    query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        projectsV2(first: 20, after: $cursor) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            id
            number
            title
          }
        }
      }
    }
    """
    projects = []
    cursor = None
    while True:
        data = github_graphql_request(query, {"login": owner_login, "cursor": cursor}, token)
        pv2 = data.get("user", {}).get("projectsV2", {})
        projects.extend(pv2.get("nodes", []))
        page_info = pv2.get("pageInfo", {})
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
            if not cursor:
                raise RuntimeError(
                    f"ProjectsV2 pagination error: hasNextPage is True, but endCursor is missing or empty. "
                    f"Retrieval is incomplete ({len(projects)} projects retrieved). Aborting per Rule 3."
                )
        else:
            break
    return projects

def get_all_project_fields(project_id, token):
    """
    Paginates through all fields of a ProjectV2 via GraphQL cursor pagination.
    Explicitly detects if retrieval was incomplete and aborts rather than treating a partial field list as complete.
    """
    query = """
    query($projectId: ID!, $cursor: String) {
      node(id: $projectId) {
        ... on ProjectV2 {
          fields(first: 50, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              ... on ProjectV2Field {
                id
                name
                dataType
              }
              ... on ProjectV2SingleSelectField {
                id
                name
                dataType
                options {
                  id
                  name
                }
              }
              ... on ProjectV2IterationField {
                id
                name
                dataType
                configuration {
                  iterations {
                    id
                    title
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    fields = []
    cursor = None
    while True:
        data = github_graphql_request(query, {"projectId": project_id, "cursor": cursor}, token)
        project_node = data.get("node")
        if not project_node:
            raise RuntimeError(f"Could not retrieve ProjectV2 node for ID {project_id}")
        fields_conn = project_node.get("fields")
        if not fields_conn:
            raise RuntimeError(f"ProjectV2 {project_id} returned no fields connection; cannot verify project fields!")
        nodes = fields_conn.get("nodes", [])
        fields.extend(nodes)
        page_info = fields_conn.get("pageInfo", {})
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
            if not cursor:
                raise RuntimeError(
                    f"Project fields pagination error: hasNextPage is True, but endCursor is missing. "
                    f"Field retrieval is incomplete ({len(fields)} fields retrieved). Aborting per Rule 6."
                )
        else:
            break
    return fields

def get_all_project_items(project_id, token):
    """Paginates through all items in a ProjectV2 via GraphQL."""
    query = """
    query($projectId: ID!, $cursor: String) {
      node(id: $projectId) {
        ... on ProjectV2 {
          items(first: 100, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              content {
                ... on Issue {
                  id
                  number
                  title
                  url
                }
              }
              fieldValues(first: 30) {
                nodes {
                  ... on ProjectV2ItemFieldSingleSelectValue {
                    field {
                      ... on ProjectV2SingleSelectField {
                        name
                      }
                    }
                    name
                  }
                  ... on ProjectV2ItemFieldNumberValue {
                    field {
                      ... on ProjectV2Field {
                        name
                      }
                    }
                    number
                  }
                  ... on ProjectV2ItemFieldIterationValue {
                    field {
                      ... on ProjectV2IterationField {
                        name
                      }
                    }
                    title
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    items = []
    cursor = None
    while True:
        data = github_graphql_request(query, {"projectId": project_id, "cursor": cursor}, token)
        items_data = data.get("node", {}).get("items", {})
        items.extend(items_data.get("nodes", []))
        page_info = items_data.get("pageInfo", {})
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
            if not cursor:
                raise RuntimeError(
                    f"ProjectV2 item pagination error: hasNextPage is True, but endCursor is missing or empty. "
                    f"Item retrieval is incomplete ({len(items)} items retrieved). Aborting."
                )
        else:
            break
    return items

# ==============================================================================
# DOCUMENT PARSER & DYNAMIC SPRINT RESOLUTION (Requirements 1 & 2)
# ==============================================================================

def parse_sprints_from_document(content, expected_backlog=None):
    """
    Extracts sprint allocation dynamically from Section E of the planning document.
    Compares the sprint allocation keys against the exact 15 keys parsed from the approved backlog.
    Rejects missing keys, unexpected keys, duplicate keys, or keys assigned to both sprints.
    Validates that each issue number in the sprint allocation matches the issue number in the corresponding backlog entry.
    Preserves 9-issue / 33-SP Sprint 1 and 6-issue / 31-SP Sprint 2 checks.
    """
    s1_match = re.search(r'### 2\. Sprint 1 Plan:[\s\S]*?(?=### 3\. Sprint 2 Plan:)', content)
    s2_match = re.search(r'### 3\. Sprint 2 Plan:[\s\S]*?(?=### 4\. GitHub PR|\n---)', content)
    if not s1_match or not s2_match:
        raise ValueError("Could not find Sprint 1 or Sprint 2 Plan sections in planning document!")

    s1_issues = re.findall(r'- Issue #(\d+):\s*`([^`]+)`', s1_match.group(0))
    s2_issues = re.findall(r'- Issue #(\d+):\s*`([^`]+)`', s2_match.group(0))

    seen_s1 = {}
    for num_str, key in s1_issues:
        num = int(num_str)
        if key in seen_s1:
            raise ValueError(f"Duplicate issue key detected in Sprint 1 allocation: {key}")
        seen_s1[key] = num

    seen_s2 = {}
    for num_str, key in s2_issues:
        num = int(num_str)
        if key in seen_s2:
            raise ValueError(f"Duplicate issue key detected in Sprint 2 allocation: {key}")
        if key in seen_s1:
            raise ValueError(f"Issue {key} appears in BOTH Sprint 1 and Sprint 2!")
        seen_s2[key] = num

    if len(seen_s1) != 9:
        raise ValueError(f"Expected exactly 9 issues in Sprint 1, but parsed {len(seen_s1)}: {list(seen_s1.keys())}")
    if len(seen_s2) != 6:
        raise ValueError(f"Expected exactly 6 issues in Sprint 2, but parsed {len(seen_s2)}: {list(seen_s2.keys())}")

    sprint_map = {}
    sprint_issue_nums = {}
    for k, num in seen_s1.items():
        sprint_map[k] = "Sprint 1"
        sprint_issue_nums[k] = num
    for k, num in seen_s2.items():
        sprint_map[k] = "Sprint 2"
        sprint_issue_nums[k] = num

    if expected_backlog is not None:
        expected_keys = set(expected_backlog.keys())
        allocated_keys = set(sprint_map.keys())

        missing = expected_keys - allocated_keys
        if missing:
            raise ValueError(f"Sprint allocation is missing approved backlog key(s): {sorted(list(missing))}")

        unexpected = allocated_keys - expected_keys
        if unexpected:
            raise ValueError(f"Sprint allocation contains unexpected key(s) not in approved backlog: {sorted(list(unexpected))}")

        for key, expected_num in expected_backlog.items():
            actual_num = sprint_issue_nums.get(key)
            if actual_num != expected_num:
                raise ValueError(
                    f"Sprint allocation issue number mismatch for {key}: "
                    f"sprint allocation has #{actual_num}, but backlog has #{expected_num}!"
                )

    return sprint_map

def parse_approved_backlog(doc_path):
    """
    Parses all 15 approved issues from docs/PROJECT_ANALYSIS_AND_BACKLOG.md.
    Preserves all original sections and includes Scope & Implementation Tasks.
    """
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"Planning document not found at: {doc_path}")

    with open(doc_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = (
        r'#### Issue #(\d+): \[([^\]]+)\] ([^\n]+)\n'
        r'- \*\*Requirement IDs:\*\* ([^\n]+)\n'
        r'- \*\*Description:\*\* ([^\n]+)\n'
        r'- \*\*Acceptance Criteria:\*\*\n([\s\S]*?)\n'
        r'- \*\*Story Points:\*\* (\d+)\n'
        r'- \*\*Priority:\*\* ([^\n]+)\n'
        r'- \*\*Suggested Assignee:\*\* ([^\n]+)\n'
        r'- \*\*Dependencies:\*\* ([^\n]+)\n'
        r'- \*\*Related Test Cases:\*\* ([^\n]+)'
    )

    matches = re.findall(pattern, content)
    if len(matches) != 15:
        raise ValueError(f"Expected 15 user stories in planning document, but parsed {len(matches)}!")

    backlog_issue_map = {}
    for m in matches:
        num = int(m[0])
        key = m[1].strip()
        if key in backlog_issue_map:
            raise ValueError(f"Duplicate issue key detected in approved backlog: {key}")
        backlog_issue_map[key] = num

    # Dynamic sprint mapping from document with strict cross-validation against approved backlog
    sprint_map = parse_sprints_from_document(content, expected_backlog=backlog_issue_map)

    parsed_issues = []
    seen_keys = set()
    total_sp = 0

    for m in matches:
        num = int(m[0])
        key = m[1].strip()
        title = m[2].strip()
        reqs = m[3].strip()
        desc = m[4].strip()
        criteria = m[5].strip()
        sp = int(m[6].strip())
        priority = m[7].strip()
        assignee_raw = m[8].strip()
        deps = m[9].strip()
        tcs = m[10].strip()

        if key in seen_keys:
            raise ValueError(f"Duplicate issue key detected in document: {key}")
        seen_keys.add(key)
        total_sp += sp

        user_match = re.search(r'`([^`]+)`', assignee_raw)
        gh_user = user_match.group(1) if user_match else assignee_raw.split()[0]
        sprint = sprint_map.get(key)
        if not sprint:
            raise ValueError(f"No sprint allocation found for issue {key}!")

        # Format complete Scope & Tasks
        scope_tasks = SCOPE_AND_TASKS_MAP.get(key, [])
        tasks_md = "\n".join([f"- [ ] {task}" for task in scope_tasks])

        full_title = f"[{key}] {title}"
        full_body = (
            f"### User Story / Description\n"
            f"{desc}\n\n"
            f"### Requirement Traceability\n"
            f"- **Requirement IDs:** {reqs}\n\n"
            f"### Scope & Tasks\n"
            f"{tasks_md}\n\n"
            f"### Acceptance Criteria\n"
            f"{criteria}\n\n"
            f"### Dependencies & Governance\n"
            f"- **Sprint:** {sprint}\n"
            f"- **Story Points:** {sp} (Fibonacci estimate)\n"
            f"- **Priority:** {priority}\n"
            f"- **Suggested Assignee:** @{gh_user}\n"
            f"- **Dependencies:** {deps}\n"
            f"- **Related Test Cases:** {tcs}\n"
        )

        labels = [sprint, f"SP: {sp}", f"Priority: {priority}"]
        topic_label = KEY_TO_LABEL.get(key)
        if topic_label:
            labels.append(topic_label)

        parsed_issues.append({
            "number": num,
            "key": key,
            "title": full_title,
            "raw_title": title,
            "body": full_body,
            "desc": desc,
            "reqs": reqs,
            "tasks": scope_tasks,
            "criteria": criteria,
            "sp": sp,
            "priority": priority,
            "assignee": gh_user,
            "sprint": sprint,
            "deps": deps,
            "tcs": tcs,
            "labels": labels
        })

    return parsed_issues

# ==============================================================================
# STRICT PROJECT FIELD VALIDATION (Requirement 4)
# ==============================================================================

def validate_project_fields(field_map, project_title):
    """
    Strictly verifies all required custom fields and their options on the Project Board.
    Aborts immediately if any field or option is missing or incompatible.
    """
    # 1. Verify Sprint
    if "Sprint" not in field_map:
        raise ValueError(f"Project '{project_title}' is missing mandatory field: 'Sprint'!")
    sprint_field = field_map["Sprint"]
    if sprint_field["type"] not in ["ITERATION", "SINGLE_SELECT"]:
        raise ValueError(f"Field 'Sprint' must be ITERATION or SINGLE_SELECT, but found: {sprint_field['type']}!")

    sprint1_id = None
    sprint2_id = None
    if sprint_field["type"] == "ITERATION":
        for it_title, it_id in sprint_field["iterations"].items():
            if "sprint 1" in it_title.lower():
                sprint1_id = it_id
            elif "sprint 2" in it_title.lower():
                sprint2_id = it_id
    else:
        for opt_name, opt_id in sprint_field["options"].items():
            if "sprint 1" in opt_name.lower():
                sprint1_id = opt_id
            elif "sprint 2" in opt_name.lower():
                sprint2_id = opt_id

    if not sprint1_id:
        avail = list(sprint_field["iterations"].keys()) or list(sprint_field["options"].keys())
        raise ValueError(f"Field 'Sprint' in '{project_title}' lacks required option/iteration for 'Sprint 1'! Available: {avail}")
    if not sprint2_id:
        avail = list(sprint_field["iterations"].keys()) or list(sprint_field["options"].keys())
        raise ValueError(f"Field 'Sprint' in '{project_title}' lacks required option/iteration for 'Sprint 2'! Available: {avail}")

    # 2. Verify Story Points
    if "Story Points" not in field_map:
        raise ValueError(f"Project '{project_title}' is missing mandatory field: 'Story Points'!")
    sp_field = field_map["Story Points"]
    if sp_field["type"] != "NUMBER":
        raise ValueError(f"Field 'Story Points' must be of dataType NUMBER, but found: {sp_field['type']}!")

    # 3. Verify Priority
    if "Priority" not in field_map:
        raise ValueError(f"Project '{project_title}' is missing mandatory field: 'Priority'!")
    pri_field = field_map["Priority"]
    if pri_field["type"] != "SINGLE_SELECT":
        raise ValueError(f"Field 'Priority' must be of dataType SINGLE_SELECT, but found: {pri_field['type']}!")

    required_pri = ["Critical", "High", "Medium"]
    pri_opt_map = {}
    missing_pri = []
    for rp in required_pri:
        found_id = None
        for oname, oid in pri_field["options"].items():
            if rp.lower() == oname.lower():
                found_id = oid
                break
        if found_id:
            pri_opt_map[rp] = found_id
        else:
            missing_pri.append(rp)

    if missing_pri:
        avail = list(pri_field["options"].keys())
        raise ValueError(f"Field 'Priority' in '{project_title}' is missing required option(s): {missing_pri}! Available options: {avail}")

    return {
        "sprint_field_id": sprint_field["id"],
        "sprint_type": sprint_field["type"],
        "sprint1_id": sprint1_id,
        "sprint2_id": sprint2_id,
        "sp_field_id": sp_field["id"],
        "priority_field_id": pri_field["id"],
        "priority_options": pri_opt_map
    }

# ==============================================================================
# STRENGTHENED PERMISSION AUDIT (Requirement 5)
# ==============================================================================

def preflight_check(token):
    """
    Validates token scopes, repository write permissions, collaborator eligibility,
    and ProjectsV2 read/write access without test mutations.
    """
    print("=" * 70)
    print("PRE-FLIGHT AUTHENTICATION & PERMISSION AUDIT")
    print("=" * 70)

    # 1. Check user and token header
    user_data, headers = github_rest_request_with_headers("/user", token)
    username = user_data.get("login")
    scopes_header = headers.get("X-OAuth-Scopes", None)

    print(f"[*] Authenticated as: @{username}")
    token_is_fine_grained = (scopes_header is None)

    if not token_is_fine_grained:
        scopes = [s.strip() for s in scopes_header.split(",") if s.strip()]
        print(f"[*] Classic OAuth Token detected. Scopes: {scopes}")
        has_repo = any(s in scopes for s in ["repo", "public_repo"])
        has_project = any(s in scopes for s in ["project", "write:project"])
        if not has_repo or not has_project:
            missing = []
            if not has_repo: missing.append("'repo'")
            if not has_project: missing.append("'project'")
            print("\n" + "!" * 70)
            print(f"[PERMISSION ERROR] Classic token lacks required scope(s): {', '.join(missing)}")
            print("!" * 70)
            print("How to Authorize:")
            print("  1. Go to: https://github.com/settings/tokens")
            print("  2. Generate a token with classic scopes: [x] repo, [x] project")
            print("  3. Set: $env:GITHUB_TOKEN=\"ghp_...\" (PowerShell) or export GITHUB_TOKEN=\"ghp_...\" (Bash)")
            print("!" * 70 + "\n")
            raise PermissionError(f"Classic token missing scope(s): {missing}")
        print("[*] Classic token OAuth scopes ('repo', 'project'): CONFIRMED")
    else:
        print("[*] Fine-Grained Token / GitHub App detected (no X-OAuth-Scopes header).")

    # 2. Check Repository Access (distinguish user permissions from token permissions)
    repo_info = github_rest_request(f"/repos/{REPO_OWNER}/{REPO_NAME}", token)
    perms = repo_info.get("permissions", {})
    user_has_push = bool(perms.get("push") or perms.get("admin"))
    if not user_has_push:
        raise PermissionError(
            f"User @{username} does not have push/admin permissions on {REPO_OWNER}/{REPO_NAME}!"
        )
    print(f"[*] User @{username} repository membership: CONFIRMED (admin={perms.get('admin')}, push={perms.get('push')})")

    # Audit Token Write Permissions (Distinguish user permission vs token permission)
    if token_is_fine_grained:
        print("\n" + "-" * 70)
        print("FINE-GRAINED TOKEN PERMISSION AUDIT:")
        print("  - User Repository Permission: CONFIRMED (User @%s has push/admin access)" % username)
        print("  - Token Read Permissions:     CONFIRMED (Repository, Collaborators, ProjectsV2)")
        print("  - Token Write Permissions:    UNVERIFIED (GitHub read-only API limitation)")
        print("    [NOTE] GitHub's read-only APIs do not expose fine-grained token write scopes.")
        print("    [NOTE] Test write mutations are strictly prohibited to ensure repository safety.")
        print("    [!] Mandatory Requirements for Fine-Grained PATs:")
        print("        * Repository Permissions: 'Issues' (Read and write), 'Metadata' (Read-only)")
        print("        * Account Permissions:    'Projects' (Read and write) to update ProjectV2 boards")
        print("-" * 70)
    else:
        print("[*] Token Permissions: CONFIRMED ('repo' and 'project' classic scopes present)")

    # 3. Check Collaborator Permission Levels (Read-only API verification)
    required_collabs = ["akashgkuradagi", "anantha-bit", "prasadamith05-png"]
    sufficient_levels = {"write", "maintain", "admin"}
    print("\n[*] Auditing required collaborator permission levels via GET /collaborators/{username}/permission:")
    for rc in required_collabs:
        try:
            perm_data = github_rest_request(f"/repos/{REPO_OWNER}/{REPO_NAME}/collaborators/{rc}/permission", token)
            perm_level = perm_data.get("permission", "none").lower()
        except Exception as pe:
            raise PermissionError(
                f"Failed to retrieve collaborator permission for @{rc} on {REPO_OWNER}/{REPO_NAME}: {pe}\n"
                f"Account @{rc} must be an active collaborator with write, maintain, or admin permission."
            )

        print(f"    - @{rc}: permission='{perm_level}'")
        if perm_level not in sufficient_levels:
            raise PermissionError(
                f"Collaborator @{rc} has insufficient permission level: '{perm_level}'! "
                f"Expected one of: {sorted(list(sufficient_levels))}."
            )
    print(f"[*] Collaborator permissions for {required_collabs}: CONFIRMED (all have write/maintain/admin access)")

    # 4. Check GraphQL ProjectsV2 Access
    try:
        all_projects = get_all_user_projects(REPO_OWNER, token)
    except RuntimeError as ge:
        err_msg = str(ge)
        if "INSUFFICIENT_SCOPES" in err_msg or "read:project" in err_msg or "project" in err_msg:
            print("\n" + "!" * 70)
            print("[PERMISSION ERROR] Token lacks ProjectsV2 permissions!")
            print("!" * 70)
            print("Required Permissions:")
            print("  - For Classic Tokens: 'repo' AND 'project' scopes.")
            print("  - For Fine-Grained Tokens: Account permissions: 'Projects' (Read and write).")
            print("!" * 70 + "\n")
            raise PermissionError("Missing required GitHub Project permissions. Halting per Rule 5.")
        raise

    # Exact Project Board selection (exact equality after trimming whitespace)
    matching_projects = [
        p for p in all_projects
        if p.get("title", "").strip() == EXACT_PROJECT_TITLE.strip()
    ]

    if not matching_projects:
        avail_titles = [repr(p.get("title", "").strip()) for p in all_projects]
        raise ValueError(
            f"Could not find exact Project Board titled '{EXACT_PROJECT_TITLE}' for user @{REPO_OWNER}! "
            f"Available projects: {avail_titles}"
        )
    if len(matching_projects) > 1:
        matching_ids = [f"#{p.get('number')} (ID: {p.get('id')})" for p in matching_projects]
        raise ValueError(
            f"Multiple Project Boards found with exact title '{EXACT_PROJECT_TITLE}'! "
            f"Ambiguous matches: {', '.join(matching_ids)}. Aborting per Rule 1."
        )

    target_project = matching_projects[0]
    project_id = target_project["id"]
    project_title = target_project["title"]
    project_number = target_project["number"]
    print(f"[*] Found Exact Project Board: '{project_title}' (Number: #{project_number}, ID: {project_id})")

    # 5. Extract and Strictly Validate Fields with Full Pagination
    raw_fields = get_all_project_fields(project_id, token)
    field_map = {}
    for f in raw_fields:
        name = f.get("name")
        field_id = f.get("id")
        data_type = f.get("dataType")
        if name in ["Sprint", "Story Points", "Priority", "Status"]:
            field_map[name] = {
                "id": field_id,
                "type": data_type,
                "options": {opt["name"]: opt["id"] for opt in f.get("options", [])} if "options" in f else {},
                "iterations": {it["title"]: it["id"] for it in f.get("configuration", {}).get("iterations", [])} if "configuration" in f else {}
            }

    validated_fields = validate_project_fields(field_map, project_title)
    print("[*] Strict Project Field Validation: PASSED")
    print(f"    - Sprint Field ID:       {validated_fields['sprint_field_id']} ({validated_fields['sprint_type']})")
    print(f"    - Story Points Field ID: {validated_fields['sp_field_id']} (NUMBER)")
    print(f"    - Priority Field ID:     {validated_fields['priority_field_id']} (SINGLE_SELECT)")

    return {
        "username": username,
        "project_id": project_id,
        "project_number": project_number,
        "project_title": project_title,
        "validated_fields": validated_fields
    }

# ==============================================================================
# IDEMPOTENT EXECUTION & PARTIAL-FAILURE RECOVERY (Requirement 3 & 7)
# ==============================================================================

def sync_repository_labels(token, dry_run=True):
    """Verifies and creates repository labels."""
    print("\n[*] Auditing repository labels...")
    existing_labels = get_all_paginated_rest(f"/repos/{REPO_OWNER}/{REPO_NAME}/labels", token)
    existing_names = {l["name"].lower(): l for l in existing_labels}

    missing = []
    for cfg in LABEL_CONFIGS:
        if cfg["name"].lower() not in existing_names:
            missing.append(cfg)

    if missing:
        print(f"    [!] {len(missing)} missing label(s) identified:")
        for m in missing:
            print(f"      + {m['name']} (#{m['color']}): {m['description']}")
            if not dry_run:
                github_rest_request(f"/repos/{REPO_OWNER}/{REPO_NAME}/labels", token, method="POST", data=m)
        if not dry_run:
            print("    [OK] Missing labels created successfully.")
    else:
        print(f"    [OK] All {len(LABEL_CONFIGS)} labels are present on the repository.")

def execute_backlog(token, issues, project_meta, dry_run=True):
    """
    Executes or simulates the full backlog creation with idempotent recovery:
    - If issue missing: create it.
    - If issue exists: reuse its node ID.
    - If not in project: add it.
    - Populate or correct Sprint, Story Points, Priority fields.
    """
    project_id = project_meta["project_id"]
    vf = project_meta["validated_fields"]

    print("\n" + "=" * 70)
    print("PLANNED BACKLOG OPERATIONS AUDIT" if dry_run else "EXECUTING LIVE BACKLOG CREATION")
    print("=" * 70)

    # 1. Retrieve all existing issues on repository with duplicate backlog key detection
    existing_issues = get_all_paginated_rest(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues?state=all", token)
    key_to_all_issues = {}
    for iss in existing_issues:
        m = re.search(r'\[([A-Z0-9_\-]+)\]', iss.get("title", ""))
        if m:
            key_to_all_issues.setdefault(m.group(1), []).append(iss)

    duplicate_keys = {k: iss_list for k, iss_list in key_to_all_issues.items() if len(iss_list) > 1}
    if duplicate_keys:
        err_msg = ["[ABORT] Duplicate existing GitHub issues detected with the same backlog key:"]
        for k, iss_list in sorted(duplicate_keys.items()):
            nums_urls = [f"#{x['number']} ({x.get('html_url', '')})" for x in iss_list]
            err_msg.append(f"  - [{k}]: found {len(iss_list)} issues: {', '.join(nums_urls)}")
        err_msg.append("Refusing to proceed with writes to prevent ambiguous or corrupt state.")
        raise RuntimeError("\n".join(err_msg))

    issue_lookup = {k: iss_list[0] for k, iss_list in key_to_all_issues.items()}

    # 2. Retrieve all existing project items
    existing_project_items = get_all_project_items(project_id, token)
    project_item_lookup = {}
    for item in existing_project_items:
        content = item.get("content")
        if content and content.get("id"):
            project_item_lookup[content["id"]] = item

    print(f"[*] Existing repository issues found: {len(existing_issues)} ({len(issue_lookup)} key-matched, 0 duplicates)")
    print(f"[*] Existing project board items:     {len(existing_project_items)}")

    sync_repository_labels(token, dry_run=dry_run)

    mutation_add_item = """
    mutation($projectId: ID!, $contentId: ID!) {
      addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
        item {
          id
        }
      }
    }
    """

    mutation_set_field = """
    mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: ProjectV2FieldValue!) {
      updateProjectV2ItemFieldValue(input: {
        projectId: $projectId,
        itemId: $itemId,
        fieldId: $fieldId,
        value: $value
      }) {
        projectV2Item {
          id
        }
      }
    }
    """

    s1_sp = 0
    s2_sp = 0
    s1_count = 0
    s2_count = 0

    planned_actions = []

    for iss in issues:
        key = iss["key"]
        sprint = iss["sprint"]
        sp = iss["sp"]
        pri = iss["priority"]
        assignee = iss["assignee"]
        title = iss["title"]

        if sprint == "Sprint 1":
            s1_sp += sp
            s1_count += 1
        else:
            s2_sp += sp
            s2_count += 1

        print(f"\n---> [{key}] {title}")
        print(f"     Target: {sprint} | {sp} SP | {pri} | @{assignee}")

        # Determine Issue existence
        issue_node_id = None
        issue_number = None
        issue_url = None

        if key in issue_lookup:
            ex = issue_lookup[key]
            issue_node_id = ex["node_id"]
            issue_number = ex["number"]
            issue_url = ex["html_url"]
            print(f"     [RECOVERY] Issue #{issue_number} already exists on GitHub ({issue_url}). Reusing node_id.")
        else:
            if dry_run:
                print(f"     [DRY-RUN] Will CREATE issue via REST API on {REPO_OWNER}/{REPO_NAME}")
            else:
                payload = {
                    "title": title,
                    "body": iss["body"],
                    "assignees": [assignee],
                    "labels": iss["labels"]
                }
                new_iss = github_rest_request(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues", token, method="POST", data=payload)
                issue_node_id = new_iss["node_id"]
                issue_number = new_iss["number"]
                issue_url = new_iss["html_url"]
                print(f"     [OK] Created Issue #{issue_number}: {issue_url}")

        # Determine Project Item existence
        item_id = None
        if issue_node_id and issue_node_id in project_item_lookup:
            item_id = project_item_lookup[issue_node_id]["id"]
            print(f"     [RECOVERY] Already linked in Project Board (Item ID: {item_id}).")
        else:
            if dry_run:
                print(f"     [DRY-RUN] Will ADD issue to Project Board (Number #{project_meta['project_number']})")
            else:
                add_res = github_graphql_request(mutation_add_item, {"projectId": project_id, "contentId": issue_node_id}, token)
                item_id = add_res["addProjectV2ItemById"]["item"]["id"]
                print(f"     [OK] Added issue to Project Board. Item ID: {item_id}")

        # Field updates
        sprint_val_payload = (
            {"iterationId": vf["sprint1_id"] if sprint == "Sprint 1" else vf["sprint2_id"]}
            if vf["sprint_type"] == "ITERATION"
            else {"singleSelectOptionId": vf["sprint1_id"] if sprint == "Sprint 1" else vf["sprint2_id"]}
        )
        pri_opt_id = vf["priority_options"][pri]

        if dry_run:
            print(f"     [DRY-RUN] Will SET Sprint        = '{sprint}' (ID: {list(sprint_val_payload.values())[0]})")
            print(f"     [DRY-RUN] Will SET Story Points  = {sp}")
            print(f"     [DRY-RUN] Will SET Priority      = '{pri}' (Option ID: {pri_opt_id})")
        else:
            # 1. Update Sprint
            github_graphql_request(mutation_set_field, {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": vf["sprint_field_id"],
                "value": sprint_val_payload
            }, token)

            # 2. Update Story Points
            github_graphql_request(mutation_set_field, {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": vf["sp_field_id"],
                "value": {"number": float(sp)}
            }, token)

            # 3. Update Priority
            github_graphql_request(mutation_set_field, {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": vf["priority_field_id"],
                "value": {"singleSelectOptionId": pri_opt_id}
            }, token)
            print(f"     [OK] Successfully updated Sprint, Story Points, and Priority fields.")

        planned_actions.append(key)

    print("\n" + "=" * 70)
    print("BACKLOG AUDIT SUMMARY")
    print("=" * 70)
    print(f"[*] Total Issues Evaluated: {len(issues)}")
    print(f"[*] Sprint 1: {s1_count} issues, {s1_sp} Story Points (Expected: 9 issues, 33 SP)")
    print(f"[*] Sprint 2: {s2_count} issues, {s2_sp} Story Points (Expected: 6 issues, 31 SP)")
    print(f"[*] Grand Total: {len(issues)} issues, {s1_sp + s2_sp} Story Points (Expected: 15 issues, 64 SP)")

    assert s1_count == 9, f"Sprint 1 count error: got {s1_count}, expected 9"
    assert s1_sp == 33, f"Sprint 1 SP error: got {s1_sp}, expected 33"
    assert s2_count == 6, f"Sprint 2 count error: got {s2_count}, expected 6"
    assert s2_sp == 31, f"Sprint 2 SP error: got {s2_sp}, expected 31"
    assert (s1_sp + s2_sp) == 64, f"Grand Total SP error: got {s1_sp + s2_sp}, expected 64"

    if dry_run:
        print("\n[SUCCESS] Dry-run audit completed with ZERO write operations.")
        print("To execute live creation once permissions are verified:")
        print("  python scripts/create_backlog.py --execute")
    else:
        # 8. Post-Execution Verification
        verify_post_execution(token, issues, project_id)

def verify_post_execution(token, issues, project_id):
    """
    Verifies that all 15 issues exist on GitHub with exact titles, complete bodies
    (all five Markdown headings and separately verified content lines including Related Test Cases),
    assignees, labels, project membership, and custom field values. Also detects duplicate issue keys
    during verification.
    """
    print("\n" + "=" * 70)
    print("POST-EXECUTION COMPREHENSIVE VERIFICATION")
    print("=" * 70)

    # Fetch updated issues & project items
    live_issues = get_all_paginated_rest(f"/repos/{REPO_OWNER}/{REPO_NAME}/issues?state=all", token)
    live_items = get_all_project_items(project_id, token)

    failures = []

    # Detect duplicate issue keys during verification
    key_to_live_issues = {}
    for iss in live_issues:
        m = re.search(r'\[([A-Z0-9_\-]+)\]', iss.get("title", ""))
        if m:
            key_to_live_issues.setdefault(m.group(1), []).append(iss)

    for k, iss_list in sorted(key_to_live_issues.items()):
        if len(iss_list) > 1:
            nums = [f"#{x['number']}" for x in iss_list]
            failures.append(f"[{k}] Duplicate issues ({', '.join(nums)}) detected on repository with the same key!")

    issue_by_key = {k: iss_list[0] for k, iss_list in key_to_live_issues.items() if len(iss_list) == 1}

    item_by_content_id = {}
    for it in live_items:
        c = it.get("content")
        if c and c.get("id"):
            item_by_content_id[c["id"]] = it

    required_headings = [
        "### User Story / Description",
        "### Requirement Traceability",
        "### Scope & Tasks",
        "### Acceptance Criteria",
        "### Dependencies & Governance"
    ]

    for expected in issues:
        key = expected["key"]
        print(f"Verifying [{key}]...")

        if key not in issue_by_key:
            failures.append(f"[{key}] Issue was not found on repository!")
            continue

        actual_iss = issue_by_key[key]
        node_id = actual_iss["node_id"]

        # 1. Verify exact issue title equality
        actual_title = actual_iss.get("title", "")
        if actual_title.strip() != expected["title"].strip():
            failures.append(f"[{key}] Title mismatch: expected '{expected['title']}', got '{actual_title}'")

        # 2. Verify complete issue body contains all five Markdown headings and expected content lines (including Related Test Cases)
        body = actual_iss.get("body", "") or ""
        for hd in required_headings:
            if hd not in body:
                failures.append(f"[{key}] Body missing required Markdown heading: '{hd}'")

        if expected["desc"] not in body:
            failures.append(f"[{key}] Body missing expected User Story / Description text!")
        if expected["reqs"] not in body:
            failures.append(f"[{key}] Body missing expected Requirement IDs: '{expected['reqs']}'")
        for task in expected["tasks"]:
            if task not in body:
                failures.append(f"[{key}] Body missing expected task: '{task}'")
        if expected["criteria"] not in body:
            failures.append(f"[{key}] Body missing expected Acceptance Criteria text!")
        if f"- **Sprint:** {expected['sprint']}" not in body:
            failures.append(f"[{key}] Body missing expected Sprint line: '- **Sprint:** {expected['sprint']}'")
        if f"- **Story Points:** {expected['sp']}" not in body:
            failures.append(f"[{key}] Body missing expected Story Points line: '- **Story Points:** {expected['sp']}'")
        if f"- **Priority:** {expected['priority']}" not in body:
            failures.append(f"[{key}] Body missing expected Priority line: '- **Priority:** {expected['priority']}'")
        if f"- **Suggested Assignee:** @{expected['assignee']}" not in body:
            failures.append(f"[{key}] Body missing expected Assignee line: '- **Suggested Assignee:** @{expected['assignee']}'")
        if f"- **Dependencies:** {expected['deps']}" not in body:
            failures.append(f"[{key}] Body missing expected Dependencies line: '- **Dependencies:** {expected['deps']}'")
        if f"- **Related Test Cases:** {expected['tcs']}" not in body:
            failures.append(f"[{key}] Body missing expected Related Test Cases line: '- **Related Test Cases:** {expected['tcs']}'")

        # 3. Check assignee
        actual_assignees = [a["login"] for a in actual_iss.get("assignees", [])]
        if expected["assignee"] not in actual_assignees:
            failures.append(f"[{key}] Assignee mismatch: expected @{expected['assignee']}, got {actual_assignees}")

        # 4. Check labels
        actual_labels = [l["name"] for l in actual_iss.get("labels", [])]
        for req_label in expected["labels"]:
            if req_label not in actual_labels:
                failures.append(f"[{key}] Missing required label: '{req_label}' (found: {actual_labels})")

        # 5. Check Project Board Membership
        if node_id not in item_by_content_id:
            failures.append(f"[{key}] Issue node_id {node_id} is not an item on the Project Board!")
            continue

        item = item_by_content_id[node_id]
        field_values = item.get("fieldValues", {}).get("nodes", [])

        sprint_val = None
        sp_val = None
        pri_val = None

        for fv in field_values:
            field_name = fv.get("field", {}).get("name")
            if field_name == "Sprint":
                sprint_val = fv.get("title") or fv.get("name")
            elif field_name == "Story Points":
                sp_val = fv.get("number")
            elif field_name == "Priority":
                pri_val = fv.get("name")

        # 6. Check Project Field Values
        if sprint_val != expected["sprint"]:
            failures.append(f"[{key}] Project Field 'Sprint' mismatch: expected '{expected['sprint']}', got '{sprint_val}'")
        if sp_val is None or int(round(sp_val)) != expected["sp"]:
            failures.append(f"[{key}] Project Field 'Story Points' mismatch: expected {expected['sp']}, got {sp_val}")
        if pri_val != expected["priority"]:
            failures.append(f"[{key}] Project Field 'Priority' mismatch: expected '{expected['priority']}', got '{pri_val}'")

    if failures:
        print("\n[CRITICAL FAILURE] The following verification checks failed:")
        for fail in failures:
            print(f"  - {fail}")
        raise RuntimeError(
            f"Post-execution verification failed! {len(failures)} check(s) failed:\n" +
            "\n".join([f"  - {f}" for f in failures])
        )
    else:
        print("\n[OK] All 15 issues verified successfully across exact titles, five Markdown headings, Related Test Cases content, assignees, labels, project membership, and custom fields!")

def main():
    parser = argparse.ArgumentParser(description="Create Self-Monitoring MLOps GitHub Backlog & ProjectsV2 Integration")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", default=False, help="Perform complete validation and simulation without creating anything (default mode)")
    mode_group.add_argument("--execute", action="store_true", default=False, help="Execute live creation of issues and Project Board items")
    args = parser.parse_args()

    # Dry-run is default when neither flag is provided
    is_dry_run = not args.execute

    print("\n" + "=" * 70)
    print("SELF-MONITORING MLOPS SYSTEM -- GITHUB BACKLOG AUTOMATION")
    print(f"Mode: {'DRY-RUN (Zero Writes)' if is_dry_run else 'LIVE EXECUTION (Modifying GitHub)'}")
    print("=" * 70)

    token = resolve_github_token()
    if not token:
        print("\n[ERROR] No GitHub token found!")
        print("Please set your GitHub Personal Access Token in environment variable:")
        print("  $env:GITHUB_TOKEN=\"your_token_here\" (PowerShell)")
        print("  export GITHUB_TOKEN=\"your_token_here\" (Bash)")
        sys.exit(1)

    print(f"[*] Reading approved backlog from: {DOC_PATH}")
    issues = parse_approved_backlog(DOC_PATH)
    print(f"[*] Successfully parsed {len(issues)} approved user stories.")

    try:
        project_meta = preflight_check(token)
    except PermissionError as pe:
        print(f"\n[ABORTED] {pe}")
        sys.exit(2)
    except Exception as e:
        print(f"\n[ERROR] Pre-flight verification failed: {e}")
        sys.exit(3)

    execute_backlog(token, issues, project_meta, dry_run=is_dry_run)

if __name__ == "__main__":
    main()

# Software Test Plan (Updated)

**Self-Monitoring MLOps System: An Intelligent Framework for ML Model Drift Detection, Monitoring, and Automated Recovery**  
**Deliverable 2 — Test Plan (Part-3 Updated Version)**  
**Version:** 2.0 (Updated post-Sprint 1 & Sprint 2 for Part-3 Testing Readiness)  
**Date:** 17 November 2026  
**Document Identifier:** `docs/02_Test_Plan_Self_Monitoring_MLOps_System_UPDATED.md`  

### Team Members:
- **Anantha Krishna M A** — PES1UG24AM036
- **Akash G Kuradagi** — PES1UG24AM022
- **Amit Prasad S G** — PES1UG24AM033  
**Department:** Computer Science & Engineering (AI & ML)  

---

## Document Control

| Field | Value |
| :--- | :--- |
| **Document Title** | Software Test Plan — Self-Monitoring MLOps System |
| **Version** | Version 2.0 (Part-3 Updated Release) |
| **Status** | Formalized Test Plan with Supplementary Test Cases TC-16 to TC-20 |
| **Date** | 17 November 2026 |
| **Original Reference** | `docs/02_Test_Plan_Self_Monitoring_MLOps_System.docx` (Version 1.0, 10 Oct 2026) |
| **Review Note** | Updated in accordance with Part-3 test case updation guidelines, adding TC-16 through TC-20 and linking TC-18 to NFR-03 continuous availability requirements. |

---

## 1. Introduction

### 1.1 Purpose
This test plan defines the strategy, scope, resources, schedule approach, entry/exit criteria, and test activities for the Self-Monitoring MLOps System. It covers functional behavior, model-monitoring experiments, DevOps workflows, quality attributes, security validation, and traceability to the SRS.

### 1.2 Scope
- **In scope**: REST API, metric collection, drift detection, ML/DL experiments, isolated QML benchmark logging, alerts, candidate validation, authorization, audit logs, rollback, Docker build, CI workflow, accessible web dashboard, and continuous availability probing.
- **Out of scope for Part-1 / Academic Prototype**: Production-scale multi-region cloud deployment, real quantum hardware execution, guaranteed accuracy improvement under arbitrary data drift, and unrestricted autonomous production retraining without human-in-the-loop validation gates.

### 1.3 References
1. SRS — Self-Monitoring MLOps System, Version 1.0.
2. SAD — Software Architecture Design, Version 1.0.
3. Software Engineering Mini-Project Deliverables Part-1, Part-2, and Part-3 briefs.
4. `docs/PROJECT_ANALYSIS_AND_BACKLOG.md`.
5. `docs/PART3_FINAL_AUDIT.md`.
6. Repository test configuration (`pyproject.toml`), API routes (`app/api/routes.py`), and test suite (`tests/`).

### 1.4 Test Objectives
- Verify that monitoring data and drift statuses are produced correctly for controlled inputs.
- Verify the system distinguishes drift warnings from measured predictive-quality degradation.
- Verify model promotion is gated by automated validation and authorized human-in-the-loop approval.
- Verify ML, DL, and QML experiments produce traceable, comparable metadata while enforcing the QML architectural firewall (TC-19).
- Verify performance, reliability, continuous availability (TC-18), packaging, observability, privacy, and security requirements.

---

## 2. Test Items and Test Approach

### 2.1 Test Items

| Item | Test Focus |
| :--- | :--- |
| **FastAPI Endpoints** | Schemas, status codes, payload size validation, JWT authentication, RBAC authorization. |
| **Monitoring Collector & Metrics** | Event counts, latency percentiles, error rate, missing/late label handling. |
| **Statistical Drift Analyzer** | Two-sample Kolmogorov-Smirnov test, Population Stability Index (PSI), Chi-Square tests. |
| **ML/DL/QML Experiment Workers** | Training/validation split, metrics, saved artifacts, MLflow logging, QML firewall enforcement. |
| **Recovery Policy & Registry** | Candidate eligibility, 10-minute cooldown suppression, 4 automated validation gates, promotion approval. |
| **Health Guard & Rollback Orchestrator** | 5-minute canary observation window, error rate & latency SLA monitoring, atomic model rollback, zero-downtime hot-reload. |
| **Web Dashboard & Approval Console** | 1-click tabbed navigation, explicit text status labels, WCAG AA compliance, dual-gate approval modal. |
| **Cryptographic Audit Trail** | SHA-256 hash chaining, genesis linking, tamper detection, sensitive field redaction. |
| **CI/CD Pipeline & Docker Packaging** | Multi-stage build, non-root user, container healthcheck probes, automated GitHub Actions workflow. |

### 2.2 Test Levels
- **Unit Testing**: Individual metric functions, data validators, threshold checkers, policy rules, and utility modules.
- **Integration Testing**: API routing + collector + drift analyzer + MLflow registry + SQLite state store.
- **System Testing**: End-to-end drift alert, candidate evaluation, canary observation, and automated rollback workflows.
- **Acceptance / Usability Testing**: Walkthrough of accessible web dashboard against requirements and $\le 3$-click navigation constraints (TC-17).
- **Security Testing**: Authentication bypass, role escalation, payload tampering, secret leakage, and audit immutability (SEC-01 through SEC-06).
- **Soak / Reliability Testing**: Continuous availability probing over extended operational windows (TC-18).

### 2.3 Test Types and Techniques
- **Black-box tests**: Endpoint contracts, status codes, and user-visible dashboard behavior.
- **White-box tests**: Recovery policy decision branches, cooldown suppression, and exception handling paths.
- **Boundary-value tests**: Threshold limits (KS $\alpha=0.05$, PSI $0.20$, error rate $10\%$, latency $500\text{ ms}$, size $1\text{ MB}$).
- **Negative tests**: Unauthorized access, invalid candidates, and QML promotion blocking (TC-19).
- **Reproducibility tests**: Pinned seeds, synthetic datasets, and versioned DVC configurations.
- **Load & fault-injection tests**: 1,000 concurrent event submissions (TC-09) and canary fault injection (TC-20).

---

## 3. Test Environment

### 3.1 Hardware and Software
- **Hardware**: Developer workstation/laptop (x86_64 CPU sufficient for tabular baseline ML, PyTorch LSTM, and PennyLane simulator).
- **Operating System**: Linux, macOS, or Windows with Docker support; Python 3.11 / 3.13 pinned in environment.
- **Frameworks**: FastAPI, Uvicorn, scikit-learn, PyTorch, PennyLane, MLflow, DVC, Pandas, NumPy, SciPy.
- **Test Tools**: `pytest`, `pytest-asyncio`, `coverage.py`, `flake8`, `bandit`, Starlette `TestClient`, `BeautifulSoup`.

### 3.2 Test Data
- Tabular credit risk dataset with 12 input features (`person_age`, `person_income`, `loan_amnt`, `loan_percent_income`, etc.).
- Reference baseline distribution (`data/reference/reference.csv`).
- Controlled synthetic drift fixtures:
  - Numerical mean shift (`data/fixtures/drift_numerical_mean_shift.csv`).
  - Categorical proportion shift (`data/fixtures/drift_categorical_shift.csv`).
  - Extreme multivariate drift fixture (`data/fixtures/drift_extreme.csv`).
- Synthetic credentials and tokens for security testing (no real production credentials).

### 3.3 Environment Controls
- Pinned dependency configuration in `pyproject.toml` and `requirements.txt`.
- Isolated SQLite test databases (`telemetry.db`) reset between test modules.
- Targeted warning filters preventing external dependency deprecation clutter.
- Dedicated pytest markers separating fast unit tests from long-running soak probes (`@pytest.mark.soak`).

---

## 4. Test Strategy and Complete Test Cases

### 4.1 Entry Criteria
- SRS and API contracts reviewed, versioned, and approved.
- Baseline model artifact (`models/baseline_model.joblib`) and reference datasets available.
- Authentication roles (`Viewer`, `ML_Engineer`, `Administrator`) configured.
- Static dashboard assets available in `app/static/`.

### 4.2 Exit Criteria
- 100% of critical functional and security test cases pass.
- Full pytest test suite passes with zero failures.
- Statement code coverage across `app/` exceeds the 90% threshold.
- No unresolved high- or medium-severity security vulnerabilities reported by Bandit.
- Complete traceability matrix mapping requirements to test cases.

---

### 4.3 Test Case Summary (TC-01 through TC-20)

| Test ID | Title / Scenario | Requirement Trace | Expected Result | Test Level | Module |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **TC-01** | Health Endpoint & Metadata | `FR-01` | GET `/health` returns HTTP 200, valid version, status `healthy`, and valid ISO 8601 timestamp. | Integration | `tests/test_health.py` |
| **TC-02** | Prediction Metrics Collection | `FR-02`, `NFR-07` | Ingested prediction events correctly update event count, error rate, and latency percentiles. | Unit / Integration | `tests/test_telemetry.py` |
| **TC-03** | Stable Reference Window | `FR-03` | Evaluating stable reference against baseline yields `NO_DRIFT` with zero drift alerts. | System | `tests/test_drift.py` |
| **TC-04** | Shifted Input Window | `FR-03`, `FR-08` | Drift fixture triggers two-sample KS $p < 0.05$ or PSI $> 0.20$, raising structured drift alert. | System | `tests/test_drift.py` |
| **TC-05** | ML/DL/QML Experiment Metadata | `FR-05`–`FR-07`, `NFR-04` | Isolation Forest, LSTM, and QML log parameters, metrics, run IDs to distinct MLflow experiments. | Integration | `tests/test_model.py`, `test_ml_monitor.py`, `test_dl_monitor.py`, `test_qml.py` |
| **TC-06** | Missing Ground-Truth Labels | `FR-04` | Real-time prediction ingestion without ground-truth labels keeps accuracy metric unavailable. | Unit / Integration | `tests/test_telemetry.py` |
| **TC-07** | Promotion Without Validation | `FR-10`, `NFR-10` | Attempting to promote an unvalidated model candidate is rejected with HTTP 422/400. | System / Negative | `tests/test_recovery.py` |
| **TC-08** | Manual Rollback on Degradation | `FR-12` | Authorized operator triggers rollback; previous stable version restored and hot-reloaded. | System / Recovery | `tests/test_rollback.py` |
| **TC-09** | API Performance & Load | `NFR-01`, `NFR-09` | 1,000 synthetic events ingested across 10 concurrent threads without loss; $p95 \ll 500\text{ ms}$. | Performance / Load | `tests/test_performance.py` |
| **TC-10** | Worker Fault Isolation | `NFR-02` | Background retraining failure is logged safely; core inference API remains responsive. | Fault Injection | `tests/test_recovery.py` |
| **TC-11** | Approval Authorization (RBAC) | `FR-14`, `SR-01`, `SR-02` | Viewer role denied candidate approval (HTTP 403); ML_Engineer allowed; Anonymous denied (401). | Security / Integration | `tests/test_security.py` |
| **TC-12** | Audit Completeness & Chaining | `FR-11`, `SR-05` | Lifecycle events record actor, timestamp, action, outcome, correlation ID, and SHA-256 hash. | Security / Audit | `tests/test_audit.py` |
| **TC-13** | Oversized Payload Boundary | `SR-04` | Request payload $> 1\text{ MB}$ rejected with HTTP 413 without process crash. | Security / Boundary | `tests/test_security.py` |
| **TC-14** | Secret Leakage Prevention | `SR-03`, `NFR-11` | Injected synthetic secrets (passwords, tokens, keys) are redacted in logs and audit tables. | Security | `tests/test_audit.py` |
| **TC-15** | Container & CI Smoke Test | `NFR-05`, `NFR-06` | Docker multi-stage build, unprivileged user, and GitHub Actions workflow execute cleanly. | Build / Smoke | `tests/test_docker.py`, `tests/test_ci.py` |
| **TC-16** | Policy Cooldown Suppression | `FR-09` | Multiple drift alerts within 10-minute cooldown trigger retraining once; extras suppressed. | Unit / System | `tests/test_recovery.py` |
| **TC-17** | Dashboard Accessibility & 1-Click Nav | `FR-13`, `NFR-08` | Every status badge has explicit text; navigation between all 6 sections requires $\le 1$ click. | System / UI | `tests/test_ui.py` |
| **TC-18** | Continuous Availability Soak Probe | `NFR-03` | 30-minute continuous health probe achieves $\ge 99.0\%$ availability (>356/360 successes). | Soak / Reliability | `tests/test_availability.py`, `scripts/run_tc18_soak.py` |
| **TC-19** | QML Architectural Firewall | `FR-07`, `NFR-10` | Promotion or validation of QML candidates is blocked by firewall with HTTP 422. | Security / Architecture | `tests/test_qml.py` |
| **TC-20** | End-to-End Automated Rollback | `FR-12`, `NFR-02` | Canary error rate $> 10\%$ autonomously triggers rollback to prior stable version. | End-to-End Recovery | `tests/test_rollback.py` |

---

### 4.4 Detailed Specification for TC-18 (Continuous Availability Probe)

- **Test Identifier**: `TC-18`
- **Associated Requirement**: `NFR-03` (Availability: $\ge 99.0\%$ successful responses during continuous 30-minute demonstration).
- **Objective**: Verify that the FastAPI application remains continuously operational, responsive, and available under repeated continuous health probing without connection degradation or memory leakage.
- **Prerequisites**:
  - FastAPI service initialized or reachable via `TestClient`.
  - Logging and metrics collection active.
- **Test Procedure**:
  1. Initialize continuous probe loop targeting `/health`.
  2. Send probe requests at 5.0-second intervals for 1800.0 seconds (30 minutes).
  3. Record request timestamp, HTTP status code, latency (ms), and error details for every probe.
  4. Calculate total requests, successes, failures, availability percentage, and latency percentiles ($p50, p95, p99, \text{mean}, \text{max}$).
  5. Export structured results to `reports/tc18_availability_soak_report.json`.
- **Expected Results & Acceptance Criteria**:
  - Total requests submitted: $\ge 360$.
  - Availability percentage: $\ge 99.0\%$ ($>356$ successful responses).
  - Rolling $p95$ latency: $\le 100\text{ ms}$ in-process ($\le 500\text{ ms}$ over network).
  - Pass/Fail evaluation: Marked `PASSED` if and only if availability $\ge 99.0\%$.
- **Test Execution Modes**:
  - **Standard Test Suite Run**: Executes fast smoke probe (2 seconds, high-frequency) asserting mechanics in $\sim 2.5\text{s}$.
  - **Official 30-Minute Soak Run**: Marked with `@pytest.mark.soak`. Executed via `pytest -m soak` or CLI script `python scripts/run_tc18_soak.py --duration 1800 --interval 5.0`.

---

### 4.5 Security Test Cases (SEC-01 through SEC-06)

| Security Test | Procedure | Pass Criteria | Requirement Trace | Module |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Call candidate decision or rollback without credentials. | HTTP 401 Unauthorized returned; state unchanged. | `SR-01`, `FR-14` | `tests/test_security.py` |
| **SEC-02** | Authenticate as Viewer and attempt promotion or rollback. | HTTP 403 Forbidden returned; lifecycle state untouched. | `SR-02` | `tests/test_security.py` |
| **SEC-03** | Inject synthetic API keys, tokens, and passwords in telemetry. | Target values replaced with `***REDACTED***` in audit logs. | `SR-03`, `NFR-11` | `tests/test_audit.py` |
| **SEC-04** | Submit invalid schemas, oversized payloads, or unknown fields. | Handled safely with HTTP 413 or HTTP 422; no stack trace leaked. | `SR-04` | `tests/test_security.py` |
| **SEC-05** | Attempt direct UPDATE/DELETE on SQLite audit table or tamper row. | DB triggers abort modification; integrity verification detects tamper. | `SR-05` | `tests/test_audit.py` |
| **SEC-06** | Submit cross-origin requests from unapproved domains. | CORS middleware blocks unlisted origins according to policy. | `SR-06` | `tests/test_security.py` |

---

## 5. Traceability Matrix

| SRS Requirement | Description | Primary Test Case | Secondary / Security Test Case | Test Level | Status |
| :--- | :--- | :--- | :--- | :---: | :---: |
| **FR-01** | Public Health Endpoint | `TC-01` | `TC-18` | Integration / Soak | **VERIFIED** |
| **FR-02** | Operational Telemetry Collection | `TC-02` | `TC-09` | Unit / Performance | **VERIFIED** |
| **FR-03** | Statistical Drift Detection (KS & PSI) | `TC-03`, `TC-04` | `TC-16` | System / Drift | **VERIFIED** |
| **FR-04** | Ground-Truth Label Decoupling | `TC-06` | — | Unit / Integration | **VERIFIED** |
| **FR-05** | Classical ML Anomaly Model (Isolation Forest) | `TC-05` | — | Integration / MLflow | **VERIFIED** |
| **FR-06** | DL Sequence Monitoring Model (PyTorch LSTM) | `TC-05` | — | Integration / MLflow | **VERIFIED** |
| **FR-07** | Experimental QML Benchmark & Firewall | `TC-05` | `TC-19` | Integration / Security | **VERIFIED** |
| **FR-08** | Multi-Metric Alerting & Deduplication | `TC-04` | `TC-16` | System / Alerting | **VERIFIED** |
| **FR-09** | Recovery Policy Engine & Cooldown | `TC-16` | — | Unit / System | **VERIFIED** |
| **FR-10** | Automated Validation Gates (4 Gates) | `TC-07` | `TC-11` | System / Governance | **VERIFIED** |
| **FR-11** | Tamper-Evident Audit Logging | `TC-12` | `SEC-05` | Security / Audit | **VERIFIED** |
| **FR-12** | Model Rollback & Health Guard | `TC-08`, `TC-20` | `TC-19` | System / Recovery | **VERIFIED** |
| **FR-13** | Web Dashboard Views & Accessibility | `TC-17` | — | Acceptance / UI | **VERIFIED** |
| **FR-14** | Human-in-the-Loop Promotion Decision | `TC-11` | `SEC-01`, `SEC-02` | Security / Integration | **VERIFIED** |
| **NFR-01** | Event Ingestion Throughput | `TC-09` | — | Performance | **VERIFIED** |
| **NFR-02** | High Availability & Worker Fault Isolation | `TC-10`, `TC-20` | — | Fault Injection | **VERIFIED** |
| **NFR-03** | 30-Minute Demo Availability ($\ge 99\%$) | `TC-18` | `TC-01` | Soak / Reliability | **VERIFIED (Smoke)** |
| **NFR-04** | Experiment Metadata Tracking | `TC-05` | — | Integration | **VERIFIED** |
| **NFR-05** | Continuous Integration Pipeline | `TC-15` | — | Build / Smoke | **VERIFIED** |
| **NFR-06** | Containerized Deployment | `TC-15` | — | Packaging | **VERIFIED** |
| **NFR-07** | Observability & Latency Percentiles | `TC-02` | `TC-09`, `TC-18` | Metrics | **VERIFIED** |
| **NFR-08** | Usability & Accessible Labels | `TC-17` | — | Usability / UI | **VERIFIED** |
| **NFR-09** | Latency SLA ($p95 \le 500\text{ ms}$) | `TC-09` | `TC-07`, `TC-18`, `TC-20` | Performance | **VERIFIED** |
| **NFR-10** | Dual-Gate Promotion Safety | `TC-07` | `TC-19` | Governance | **VERIFIED** |
| **NFR-11** | Data Privacy & Redaction | `TC-14` | `SEC-03` | Security | **VERIFIED** |
| **NFR-12** | Defense in Depth & Request Boundaries | `TC-13` | `SEC-04` | Security | **VERIFIED** |
| **SR-01** | Authentication Bypass Prevention | `TC-11` | `SEC-01` | Security | **VERIFIED** |
| **SR-02** | Role-Based Access Control | `TC-11` | `SEC-02` | Security | **VERIFIED** |
| **SR-03** | Sensitive Data Redaction | `TC-14` | `SEC-03` | Security | **VERIFIED** |
| **SR-04** | Payload Boundary Enforcement | `TC-13` | `SEC-04` | Security | **VERIFIED** |
| **SR-05** | Tamper-Evident Audit Logging | `TC-12` | `SEC-05` | Security | **VERIFIED** |
| **SR-06** | Secure Configuration & CORS Boundaries | `SEC-06` | — | Security | **VERIFIED** |

---

## 6. Risks and Mitigations

| Risk | Impact | Mitigation Strategy Implemented |
| :--- | :--- | :--- |
| **30-minute soak test slows down fast CI cycles** | High developer friction and CI timeouts. | Registered `@pytest.mark.soak` marker with `-m 'not soak'` default in `pyproject.toml`. Fast smoke probe runs in $<3\text{s}$; full 30-minute probe available on-demand via CLI or dedicated soak run. |
| **Massive warning output from NumPy 2.x / joblib** | Cluttered test logs, slow test execution. | Applied targeted `filterwarnings` in `pyproject.toml` specifically targeting `joblib.numpy_pickle` shape mutation deprecation, reducing warning count from 21,172 to 3. |
| **FastAPI HTTP 422 deprecation in newer Starlette** | Potential incompatibility with future Starlette/FastAPI. | Migrated project code in `app/api/routes.py` from `status.HTTP_422_UNPROCESSABLE_ENTITY` to canonical `status.HTTP_422_UNPROCESSABLE_CONTENT`. |
| **QML simulation cost during integration tests** | Excessive CPU consumption during test runs. | Architected small 4-qubit VQC with downsampled 4-feature input; architectural firewall (TC-19) strictly enforces isolated execution without touching production pathways. |

---
*End of Updated Test Plan.*

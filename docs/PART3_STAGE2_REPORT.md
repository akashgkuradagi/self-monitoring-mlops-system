# Part 3 — Stage 2: Test Updation & Warning Remediation Report

**Self-Monitoring MLOps System: An Intelligent Framework for ML Model Drift Detection, Monitoring, and Automated Recovery**  
**Document Identifier:** `docs/PART3_STAGE2_REPORT.md`  
**Execution Date:** 17 November 2026  
**Status:** Completed & Validated  

---

## 1. Overview & Objectives

In accordance with the Part-3 requirements and the findings from `docs/PART3_FINAL_AUDIT.md`, Stage 2 accomplished:
1. **Implementation of Missing Test Case TC-18**: Authored the 30-Minute Continuous Availability Soak Probe fulfilling `NFR-03`.
2. **Pytest Warning Remediation**:
   - Eliminated **21,164** NumPy array shape deprecation warnings by configuring a narrowly scoped filter for `joblib.numpy_pickle`.
   - Eliminated **5** Starlette deprecation warnings by replacing `status.HTTP_422_UNPROCESSABLE_ENTITY` with canonical `status.HTTP_422_UNPROCESSABLE_CONTENT` across `app/api/routes.py`.
3. **Execution Design & Pytest Marker**: Registered the `soak` marker in `pyproject.toml` with default filtering (`addopts = "-v --tb=short -m 'not soak'"`) to preserve rapid sub-minute test suite execution.
4. **Test Plan Updation**: Formalized `docs/02_Test_Plan_Self_Monitoring_MLOps_System_UPDATED.md` incorporating `TC-01` through `TC-20` and `SEC-01` through `SEC-06`, with complete traceability linking `TC-18` to `NFR-03`.

---

## 2. Files Changed & Created

| File | Change Type | Purpose / Description |
| :--- | :---: | :--- |
| `tests/test_availability.py` | **Created** | Comprehensive availability probe test suite implementing `TC-18` (NFR-03), including fast development smoke probe and `@pytest.mark.soak` full 30-minute test. |
| `scripts/run_tc18_soak.py` | **Created** | Standalone CLI soak runner supporting live HTTP URLs or in-process FastAPI TestClient with configurable duration and sampling intervals. |
| `app/api/routes.py` | **Modified** | Replaced 8 occurrences of deprecated `status.HTTP_422_UNPROCESSABLE_ENTITY` with `status.HTTP_422_UNPROCESSABLE_CONTENT`. |
| `pyproject.toml` | **Modified** | Configured `soak` marker, set default exclusion `addopts = "-v --tb=short -m 'not soak'"`, and added targeted warning filter for `joblib.numpy_pickle`. |
| `docs/02_Test_Plan_Self_Monitoring_MLOps_System_UPDATED.md` | **Created** | Formal updated Test Plan (v2.0) incorporating all supplementary test cases `TC-16` to `TC-20`, security cases `SEC-01` to `SEC-06`, and full requirement traceability. |
| `docs/PART3_STAGE2_REPORT.md` | **Created** | Stage 2 audit and execution summary report (this document). |

---

## 3. TC-18 Implementation Details (Continuous Availability Probe — NFR-03)

### Requirement Specification
- **Requirement ID**: `NFR-03`
- **Specification**: "During a 30-minute controlled demo, the health endpoint shall achieve at least 99% successful responses, excluding planned restarts."
- **Acceptance Criteria**: Availability ratio $\ge 99.0\%$ ($>356 / 360$ successful responses over 30 minutes at 5-second intervals).

### Technical Architecture
The availability probe is designed in three decoupled tiers:
1. **`tests/test_availability.py::test_tc18_availability_smoke_probe`**:
   - Runs on every standard `pytest` execution in $\sim 2.4\text{s}$.
   - Probes `/health` at high frequency (40ms interval), executing $>15$ requests.
   - Asserts zero failed requests, $100.0\%$ availability, and $p95$ latency $< 100\text{ ms}$.
   - Exports `reports/tc18_smoke_probe_report.json`.
2. **`tests/test_availability.py::test_tc18_probe_detects_server_unavailability_and_errors`**:
   - Negative fault-injection test using a mock flaky client (20% simulated failure rate).
   - Verifies the availability calculation detects HTTP 503/errors and properly drops below the 99% boundary, failing the test.
3. **`tests/test_availability.py::test_tc18_30min_continuous_availability_soak`**:
   - Registered under `@pytest.mark.soak`.
   - Runs the full 30 minutes ($1800\text{ seconds}$) with 5.0-second polling interval ($360\text{ requests}$).
   - Records request timestamps, HTTP status codes, latency percentiles ($p50, p95, p99, \text{mean}, \text{max}$).
   - Exports results to `reports/tc18_availability_soak_report.json`.

### Official Commands to Run TC-18

#### A. Fast Development Smoke Test (Runs in ~2.5 seconds)
```bash
.venv\Scripts\python.exe -m pytest tests/test_availability.py -k smoke -v
```

#### B. Official 30-Minute Soak Test via Pytest
```bash
.venv\Scripts\python.exe -m pytest tests/test_availability.py -m soak -s
```

#### C. Official 30-Minute Soak Test via CLI Script
```bash
# Against in-process FastAPI application
.venv\Scripts\python.exe scripts/run_tc18_soak.py --duration 1800 --interval 5.0 --output reports/tc18_availability_soak_report.json

# Against live container or network deployment
.venv\Scripts\python.exe scripts/run_tc18_soak.py --url http://127.0.0.1:8000/health --duration 1800 --interval 5.0
```

---

## 4. Test Results and Warning Count Comparison

### Test Count & Status

| Metric | Before Stage 2 (Audit) | After Stage 2 (Remediated) | Delta |
| :--- | :---: | :---: | :---: |
| **Total Test Files** | 17 | 18 | +1 (`test_availability.py`) |
| **Total Collected Tests** | 159 | 162 | +3 |
| **Active Selected Tests** | 159 | 161 | +2 (1 soak test deselected) |
| **Passing Tests** | 159 | 161 | +2 |
| **Failing Tests** | 0 | 0 | 0 |
| **Test Suite Execution Time** | 47.60s | 40.33s | -7.27s (15% faster) |

### Warning Count Breakdown

| Warning Category / Message | Stage 1 Count | Stage 2 Count | Remediation Action Taken |
| :--- | :---: | :---: | :--- |
| `DeprecationWarning: Setting the shape on a NumPy array...` (`joblib.numpy_pickle.py:207`) | **21,164** | **0** | Narrowly scoped filter added to `pyproject.toml` (`filterwarnings`). |
| `StarletteDeprecationWarning: 'HTTP_422_UNPROCESSABLE_ENTITY'...` (`fastapi/routing.py:360`) | **5** | **0** | Replaced with `status.HTTP_422_UNPROCESSABLE_CONTENT` in `app/api/routes.py`. |
| `DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated...` (`starlette/testclient.py:53`) | 1 | 1 | External Starlette test client deprecation under AnyIO 4.x. Unmodified. |
| `SADeprecationWarning: The 'noload' loader strategy is deprecated...` (`mlflow/...:155`) | 1 | 1 | External MLflow SQLAlchemy ORM internal deprecation. Unmodified. |
| `UserWarning: Hint: Inferred schema contains integer column(s)...` (`mlflow/types/utils.py:440`) | 1 | 1 | MLflow schema logging advice in `test_ml_monitor.py`. Unmodified. |
| **TOTAL WARNINGS** | **21,172** | **3** | **99.986% warning reduction** |

---

## 5. Remaining Warnings Analysis

Only **3 warnings** remain in the entire test suite of 161 tests. All three originate inside third-party package internals and do not indicate issues with project code:
1. `starlette/testclient.py:53: DeprecationWarning`: Starlette internal use of deprecated AnyIO `BlockingPortal` alias.
2. `mlflow/store/tracking/utils/sql_trace_metrics_utils.py:155: SADeprecationWarning`: MLflow tracking server internal use of SQLAlchemy `noload` strategy.
3. `mlflow/types/utils.py:440: UserWarning`: Informational MLflow user advice indicating integer columns cannot represent NaNs in inference payloads.

---

## 6. Updated Test Plan Location

The updated, formalized Test Plan is preserved as:
- **Location**: `docs/02_Test_Plan_Self_Monitoring_MLOps_System_UPDATED.md`
- **Version**: 2.0 (Part-3 Updated Version)
- **Key Additions**:
  - Full specification for `TC-18` (Availability Soak Probe) linked to `NFR-03`.
  - Full specification for `TC-16` (Cooldown Suppression) linked to `FR-09`.
  - Full specification for `TC-17` (Dashboard Usability & 1-Click Nav) linked to `FR-13` and `NFR-08`.
  - Full specification for `TC-19` (QML Architectural Firewall) linked to `FR-07` and `NFR-10`.
  - Full specification for `TC-20` (Automated Rollback) linked to `FR-12` and `NFR-02`.
  - Comprehensive Traceability Matrix covering all 20 test cases and 6 security cases against all 14 functional requirements and 12 non-functional/security requirements.

---

## 7. Static Code Quality & Security Verification

- **Flake8**: Executed across `app/`, `tests/`, and `scripts/`. Result: `0 errors` (100% PEP 8 compliant).
- **Bandit**: Executed with `bandit -r app -ll`. Result: `0 issues identified` (zero High or Medium severity vulnerabilities).

---

## 8. Unresolved Issues & Readiness for Stage 3

- **Zero Unresolved Blockers**: All Stage 2 goals have been achieved.
- **Readiness for Stage 3 (Branch Coverage Optimization)**:
  - Statement coverage remains $\ge 91\%$.
  - Branch coverage currently stands at $76.6\%$. Stage 3 will add targeted test cases for partial branches in `app/api/routes.py`, `app/registry/mlflow_adapter.py`, and `app/recovery/validator.py` to elevate branch coverage to $\ge 85\%$.

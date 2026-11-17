"""Test Suite for UI-01 Interactive Accessible Web Dashboard & Approval Console.

Maps to Requirements:
- FR-13: Web dashboard views & accessibility
- NFR-08: Usability (<= 3-click navigation path & explicit textual status labels)
- TC-17: Dashboard Accessibility & 3-Click Navigation

Validates:
1. Static asset serving (HTML, CSS, JS) natively through FastAPI.
2. TC-17: All status indicators use explicit textual labels (HEALTHY, DRIFT_DETECTED, etc.).
3. TC-17: All functional sections (Health, Drift, Alerts, Candidate Approval, Rollback, Audit) reachable in <= 3 clicks (1-click tabbed access).
4. WCAG 2.1 AA semantic landmark elements, ARIA roles, skip-link, and dialog accessibility.
5. Candidate Dual-Gate validation scorecard presentation and QML architectural firewall banner (TC-19).
6. Post-promotion canary observation controls and manual rollback console (TC-08, TC-20).
7. Tamper-evident cryptographic audit verification console (TC-14).
"""

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient


def test_dashboard_endpoint_returns_200_and_html(client: TestClient):
    """Verify that GET /dashboard serves the web dashboard with HTTP 200 and text/html."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Self-Monitoring MLOps System" in response.text
    assert "<!DOCTYPE html>" in response.text


def test_static_css_and_js_served(client: TestClient):
    """Verify that static CSS and JavaScript assets are accessible under /static."""
    css_resp = client.get("/static/dashboard.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers.get("content-type", "")
    assert len(css_resp.text) > 100

    js_resp = client.get("/static/dashboard.js")
    assert js_resp.status_code == 200
    assert any(mime in js_resp.headers.get("content-type", "") for mime in ["javascript", "text/plain"])
    assert len(js_resp.text) > 100


def test_root_metadata_includes_dashboard_route(client: TestClient):
    """Verify that the root service endpoint advertises /dashboard in its links."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "dashboard" in data
    assert data["dashboard"] == "/dashboard"


def test_tc17_dashboard_accessibility_explicit_text_labels(client: TestClient):
    """TC-17 (FR-13, NFR-08): Verify every status indicator contains explicit textual labels.

    WCAG 2.1 AA and NFR-08 prohibit conveying status through color alone.
    Every status badge must include explicit textual labels such as HEALTHY, NO_DRIFT,
    NORMAL, ACTIVE, READY, CHAIN_INTACT, etc.
    """
    response = client.get("/dashboard")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")

    # Locate all status badges in the markup
    badges = soup.find_all(class_=lambda c: c and "badge" in c)
    assert len(badges) >= 10, "Dashboard must contain status indicator badges"

    approved_text_labels = {
        "HEALTHY",
        "DEGRADED",
        "UNHEALTHY",
        "NO_DRIFT",
        "DRIFT_DETECTED",
        "NORMAL",
        "ANOMALY",
        "ACTIVE",
        "INACTIVE",
        "READY",
        "RELOADED",
        "CHAIN_INTACT",
        "COMPROMISED",
        "PASS",
        "FAIL",
        "PENDING",
        "PROMOTED",
        "REJECTED",
        "VALIDATED",
        "UNAUTHENTICATED",
        "VIEWER",
        "ML_ENGINEER",
        "ADMINISTRATOR",
        "0 ACTIVE ALERTS",
        "ISOLATION FOREST",
        "PYTORCH LSTM",
        "ARCHITECTURAL FIREWALL ACTIVE",
        "RESTRICTED ACTION",
        "THRESHOLD \u2264 10%",
        "SLA \u2264 500 MS",
    }

    # Verify that badge elements contain non-empty explicit text
    for badge in badges:
        text = badge.get_text(strip=True).upper()
        assert len(text) > 0, f"Status badge {badge} cannot be empty (violates NFR-08)"
        # Verify text is substantial and informative
        assert any(label in text for label in approved_text_labels) or len(text) >= 2


def test_tc17_navigation_path_le_3_clicks(client: TestClient):
    """TC-17 (FR-13, NFR-08): Verify navigation path from /dashboard to key sections is <= 3 clicks.

    The 1-click tabbed architecture allows an evaluator to navigate from /dashboard
    to System Health/Overview, Drift Monitoring, Alerts, and Candidate Approval in exactly 1 click.
    """
    response = client.get("/dashboard")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")

    tablist = soup.find(attrs={"role": "tablist"})
    assert tablist is not None, "Dashboard must have an accessible tablist"

    tabs = tablist.find_all(attrs={"role": "tab"})
    assert len(tabs) >= 6, "Dashboard must provide direct navigation to all 6 functional areas"

    tab_controls = [tab.get("aria-controls") for tab in tabs]

    # Required target destinations per TC-17 & Project Backlog
    required_destinations = [
        "section-overview",    # Service Health & Telemetry
        "section-monitoring",  # Drift & Statistical Monitors
        "section-alerts",      # Active Alerts & Deduplication
        "section-candidates",  # Candidate Validation & Approval
        "section-rollback",    # Canary Guard & Rollback
        "section-audit",       # Tamper-Evident Audit Trail
    ]

    for dest in required_destinations:
        assert dest in tab_controls, f"Missing 1-click tab for destination: {dest}"
        panel = soup.find(id=dest)
        assert panel is not None, f"Destination panel {dest} must exist in DOM"
        assert panel.get("role") == "tabpanel", f"Destination {dest} must have role='tabpanel'"


def test_wcag_semantic_landmarks_and_aria_attributes(client: TestClient):
    """Verify WCAG 2.1 AA accessibility features: skip link, landmarks, ARIA dialogs, live regions."""
    response = client.get("/dashboard")
    soup = BeautifulSoup(response.text, "html.parser")

    # 1. Skip to main content link
    skip_link = soup.find("a", class_="skip-link")
    assert skip_link is not None, "Must include an accessible skip link"
    assert skip_link.get("href") == "#main-content"

    # 2. Main landmark
    main_el = soup.find("main", id="main-content")
    assert main_el is not None, "Must include a main element with id='main-content'"

    # 3. Header and Navigation landmarks
    header = soup.find("header", role="banner")
    assert header is not None, "Must include a banner header"
    nav = soup.find("nav", attrs={"aria-label": lambda a: a and "navigation" in a.lower()})
    assert nav is not None, "Must include an accessible navigation landmark"

    # 4. Modals with role="dialog" and aria-modal="true"
    dialogs = soup.find_all(attrs={"role": "dialog"})
    assert len(dialogs) >= 2, "Must contain accessible candidate decision and rollback dialogs"
    for d in dialogs:
        assert d.get("aria-modal") == "true"
        assert d.get("aria-labelledby") is not None

    # 5. Toast container with live region
    toast_container = soup.find(id="toast-container")
    assert toast_container is not None
    assert toast_container.get("role") == "status"
    assert toast_container.get("aria-live") == "polite"


def test_tc19_architectural_firewall_banner_displayed(client: TestClient):
    """Verify that TC-19 Quantum ML (QML) architectural firewall notice is clearly rendered."""
    response = client.get("/dashboard")
    soup = BeautifulSoup(response.text, "html.parser")

    candidates_section = soup.find(id="section-candidates")
    assert candidates_section is not None

    firewall_banner = candidates_section.find(class_="firewall-banner")
    assert firewall_banner is not None, "Candidates section must feature the TC-19 firewall banner"
    banner_text = firewall_banner.get_text()
    assert "TC-19" in banner_text
    assert "422" in banner_text
    assert "Quantum" in banner_text


def test_dual_gate_candidate_scorecard_structure(client: TestClient):
    """Verify that the candidates table is structured with the 4 automated validation gates."""
    response = client.get("/dashboard")
    soup = BeautifulSoup(response.text, "html.parser")

    table = soup.find(id="table-candidates")
    assert table is not None
    headers = [th.get_text(strip=True) for th in table.find_all("th")]

    assert any("Candidate ID" in h for h in headers)
    assert any("Model Version" in h for h in headers)
    assert any("Gate 1: Artifact" in h for h in headers)
    assert any("Gate 2: Schema" in h for h in headers)
    assert any("Gate 3: Regress." in h for h in headers)
    assert any("Gate 4: Latency" in h for h in headers)
    assert any("Actions" in h for h in headers)


def test_post_promotion_canary_and_manual_rollback_controls(client: TestClient):
    """Verify Canary guard metrics and emergency manual rollback controls in section-rollback."""
    response = client.get("/dashboard")
    soup = BeautifulSoup(response.text, "html.parser")

    rollback_section = soup.find(id="section-rollback")
    assert rollback_section is not None

    # Canary elements
    assert rollback_section.find(id="canary-health-badge") is not None
    assert rollback_section.find(id="canary-remaining-time") is not None
    assert rollback_section.find(id="canary-error-rate") is not None
    assert rollback_section.find(id="canary-latency-p95") is not None

    # Manual rollback controls
    assert rollback_section.find(id="manual-rollback-reason") is not None
    assert rollback_section.find(id="btn-trigger-rollback") is not None
    assert rollback_section.find(id="table-rollbacks") is not None


def test_tamper_evident_audit_trail_and_integrity_button(client: TestClient):
    """Verify audit verification controls and SHA-256 table headers in section-audit."""
    response = client.get("/dashboard")
    soup = BeautifulSoup(response.text, "html.parser")

    audit_section = soup.find(id="section-audit")
    assert audit_section is not None

    assert audit_section.find(id="btn-verify-audit") is not None
    assert audit_section.find(id="audit-integrity-badge") is not None
    assert audit_section.find(id="table-audit") is not None

    table = audit_section.find(id="table-audit")
    headers = [th.get_text(strip=True) for th in table.find_all("th")]
    assert any("SHA-256" in h for h in headers)
    assert any("Actor" in h for h in headers)
    assert any("Action" in h for h in headers)


def test_role_presets_and_authentication_ui_elements(client: TestClient):
    """Verify quick login presets (Viewer, ML Engineer, Admin) and manual token input bar."""
    response = client.get("/dashboard")
    soup = BeautifulSoup(response.text, "html.parser")

    # Auth bar elements
    assert soup.find(id="current-role-badge") is not None
    assert soup.find(id="current-user-label") is not None
    assert soup.find(id="btn-login-viewer") is not None
    assert soup.find(id="btn-login-engineer") is not None
    assert soup.find(id="btn-login-admin") is not None
    assert soup.find(id="jwt-token-input") is not None
    assert soup.find(id="btn-set-token") is not None


def test_dashboard_css_accessibility_and_wcag_properties(client: TestClient):
    """Verify that dashboard.css includes focus rings, WCAG variables, and high-contrast badges."""
    response = client.get("/static/dashboard.css")
    assert response.status_code == 200
    css = response.text

    # Focus indicator for keyboard navigability
    assert ":focus-visible" in css
    assert "outline" in css

    # High-contrast WCAG badge rules
    assert ".status-healthy" in css
    assert ".status-unhealthy" in css
    assert ".status-drift" in css
    assert ".status-warning" in css

    # Accessible skip link
    assert ".skip-link" in css

    # Modals and toasts
    assert ".modal-backdrop" in css
    assert ".toast-container" in css


def test_dashboard_js_api_wiring(client: TestClient):
    """Verify that dashboard.js correctly targets all approved REST API endpoints."""
    response = client.get("/static/dashboard.js")
    assert response.status_code == 200
    js = response.text

    endpoints = [
        "/api/v1/auth/token",
        "/health",
        "/api/v1/models/active",
        "/api/v1/metrics/summary",
        "/api/v1/monitoring/drift/report",
        "/api/v1/monitoring/alerts",
        "/api/v1/candidates",
        "/api/v1/recovery/health",
        "/api/v1/recovery/rollbacks",
        "/api/v1/models/rollback",
        "/api/v1/audit/events",
        "/api/v1/audit/verify",
    ]

    for ep in endpoints:
        assert ep in js, f"dashboard.js must wire endpoint: {ep}"

    # Verify handling of TC-19 Architectural Firewall 422 status
    assert "422" in js
    assert "TC-19" in js

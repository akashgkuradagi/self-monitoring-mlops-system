/**
 * Self-Monitoring MLOps System - Interactive Web Dashboard Client (UI-01)
 * 
 * Fulfills Requirements: FR-13, NFR-08, TC-17.
 * Plain vanilla JavaScript (ES6+), zero external CDN or framework dependencies.
 */

(function () {
  'use strict';

  // Application Global State
  const state = {
    token: localStorage.getItem('mlops_auth_token') || null,
    user: localStorage.getItem('mlops_auth_user') || 'Guest',
    role: localStorage.getItem('mlops_auth_role') || 'UNAUTHENTICATED',
    autoSync: true,
    syncIntervalId: null,
    activeTab: 'section-overview',
    pendingDecision: { candidateId: null, decision: null }
  };

  // Pre-configured Demo Credentials for Rapid Evaluation
  const DEMO_CREDENTIALS = {
    viewer: { username: 'viewer', password: 'viewer123', label: 'Viewer' },
    engineer: { username: 'engineer', password: 'engineer123', label: 'ML Engineer' },
    admin: { username: 'admin', password: 'admin123', label: 'Administrator' }
  };

  // Helper: DOM Element Selectors
  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => document.querySelectorAll(sel);

  // Helper: Display Toast Notification
  function showToast(message, type = 'info') {
    const container = $('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'status');
    toast.textContent = message;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transition = 'opacity 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 4000);
  }

  // Helper: Authenticated Fetch
  async function apiFetch(endpoint, options = {}) {
    const headers = options.headers || {};
    if (state.token) {
      headers['Authorization'] = `Bearer ${state.token}`;
    }
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }
    options.headers = headers;

    const response = await fetch(endpoint, options);
    return response;
  }

  // Auth: Update UI Role Badge and Controls
  function updateAuthUI() {
    const roleBadge = $('current-role-badge');
    const userLabel = $('current-user-label');
    const logoutBtn = $('btn-logout');

    if (!roleBadge || !userLabel) return;

    roleBadge.textContent = state.role;
    roleBadge.className = 'badge badge-role';

    if (state.role === 'Administrator') {
      roleBadge.classList.add('role-admin');
      userLabel.textContent = state.user;
      if (logoutBtn) logoutBtn.style.display = 'inline-flex';
    } else if (state.role === 'ML_Engineer') {
      roleBadge.classList.add('role-engineer');
      userLabel.textContent = state.user;
      if (logoutBtn) logoutBtn.style.display = 'inline-flex';
    } else if (state.role === 'Viewer') {
      roleBadge.classList.add('role-viewer');
      userLabel.textContent = state.user;
      if (logoutBtn) logoutBtn.style.display = 'inline-flex';
    } else {
      roleBadge.classList.add('role-unauth');
      roleBadge.textContent = 'UNAUTHENTICATED';
      userLabel.textContent = 'Guest';
      if (logoutBtn) logoutBtn.style.display = 'none';
    }

    // Update Action Button states according to RBAC
    updateActionButtonsPermission();
  }

  function updateActionButtonsPermission() {
    const isEngineerOrAdmin = state.role === 'ML_Engineer' || state.role === 'Administrator';
    const isAdmin = state.role === 'Administrator';

    // Verify Audit Integrity button (Admin only)
    const btnVerifyAudit = $('btn-verify-audit');
    if (btnVerifyAudit) {
      btnVerifyAudit.disabled = !isAdmin;
      btnVerifyAudit.title = isAdmin ? 'Verify SHA-256 Hash Chain' : 'Requires Administrator role';
    }

    // Manual Rollback Button (ML_Engineer or Admin)
    const btnTriggerRollback = $('btn-trigger-rollback');
    if (btnTriggerRollback) {
      btnTriggerRollback.disabled = !isEngineerOrAdmin;
      btnTriggerRollback.title = isEngineerOrAdmin ? 'Trigger Immediate Rollback' : 'Requires ML_Engineer role';
    }

    // Evaluate Canary Button
    const btnEvaluateCanary = $('btn-evaluate-canary');
    if (btnEvaluateCanary) {
      btnEvaluateCanary.disabled = !isEngineerOrAdmin;
    }

    // Candidate Action Buttons
    $$('.btn-candidate-action').forEach(btn => {
      btn.disabled = !isEngineerOrAdmin;
      if (!isEngineerOrAdmin) {
        btn.title = 'Requires ML_Engineer or Administrator role';
      }
    });

    // Alert Action Buttons
    $$('.btn-alert-action').forEach(btn => {
      btn.disabled = !isEngineerOrAdmin;
      if (!isEngineerOrAdmin) {
        btn.title = 'Requires ML_Engineer or Administrator role';
      }
    });
  }

  // Auth: Login with Credentials
  async function performLogin(username, password) {
    try {
      const resp = await fetch('/api/v1/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: 'Authentication failed' }));
        showToast(err.detail || 'Login failed', 'error');
        return;
      }

      const data = await resp.json();
      state.token = data.access_token;
      state.user = data.username || username;
      state.role = data.role;

      localStorage.setItem('mlops_auth_token', state.token);
      localStorage.setItem('mlops_auth_user', state.user);
      localStorage.setItem('mlops_auth_role', state.role);

      updateAuthUI();
      showToast(`Logged in successfully as ${state.user} (${state.role})`, 'success');

      // Refresh all panels with new permissions
      fetchAllTelemetry();
    } catch (e) {
      showToast('Network error during authentication: ' + e.message, 'error');
    }
  }

  function performLogout() {
    state.token = null;
    state.user = 'Guest';
    state.role = 'UNAUTHENTICATED';
    localStorage.removeItem('mlops_auth_token');
    localStorage.removeItem('mlops_auth_user');
    localStorage.removeItem('mlops_auth_role');
    const tokenInput = $('jwt-token-input');
    if (tokenInput) tokenInput.value = '';
    updateAuthUI();
    showToast('Logged out. Operational controls reset to read-only.', 'info');
    fetchAllTelemetry();
  }

  // Auth: Set Manual Token
  function setManualToken() {
    const input = $('jwt-token-input');
    if (!input || !input.value.trim()) {
      showToast('Please enter a valid JWT token', 'error');
      return;
    }
    const token = input.value.trim();
    try {
      // Decode payload to extract sub and role
      const parts = token.split('.');
      if (parts.length === 3) {
        const payload = JSON.parse(atob(parts[1]));
        state.token = token;
        state.user = payload.sub || 'User';
        state.role = payload.role || 'Viewer';
        localStorage.setItem('mlops_auth_token', state.token);
        localStorage.setItem('mlops_auth_user', state.user);
        localStorage.setItem('mlops_auth_role', state.role);
        updateAuthUI();
        showToast(`Token applied for user: ${state.user} (${state.role})`, 'success');
        fetchAllTelemetry();
      } else {
        showToast('Malformed JWT token structure', 'error');
      }
    } catch (e) {
      showToast('Failed to parse JWT payload', 'error');
    }
  }

  // Tab Navigation Handling (TC-17 1-Click Navigation)
  function initTabs() {
    const tabs = $$('.tab-btn');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const targetPanelId = tab.getAttribute('aria-controls');
        switchTab(targetPanelId);
      });

      // Keyboard arrow navigation
      tab.addEventListener('keydown', (e) => {
        let index = Array.from(tabs).indexOf(tab);
        if (e.key === 'ArrowRight') {
          const nextIndex = (index + 1) % tabs.length;
          tabs[nextIndex].focus();
          tabs[nextIndex].click();
        } else if (e.key === 'ArrowLeft') {
          const prevIndex = (index - 1 + tabs.length) % tabs.length;
          tabs[prevIndex].focus();
          tabs[prevIndex].click();
        }
      });
    });
  }

  function switchTab(panelId) {
    const tabs = $$('.tab-btn');
    const panels = $$('.tab-panel');

    tabs.forEach((tab) => {
      const controls = tab.getAttribute('aria-controls');
      if (controls === panelId) {
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        tab.setAttribute('tabindex', '0');
      } else {
        tab.classList.remove('active');
        tab.setAttribute('aria-selected', 'false');
        tab.setAttribute('tabindex', '-1');
      }
    });

    panels.forEach((panel) => {
      if (panel.id === panelId) {
        panel.classList.add('active');
      } else {
        panel.classList.remove('active');
      }
    });

    state.activeTab = panelId;
  }

  // Telemetry: Fetch System Health & Active Model
  async function fetchSystemHealth() {
    try {
      const resp = await apiFetch('/health');
      if (resp.ok) {
        const data = await resp.json();
        const healthBadge = $('overview-service-health');
        if (healthBadge) {
          const statusText = (data.status || 'HEALTHY').toUpperCase();
          healthBadge.textContent = statusText;
          healthBadge.className = `badge ${statusText === 'HEALTHY' ? 'status-healthy' : 'status-unhealthy'}`;
        }
        const appVer = $('overview-app-version');
        if (appVer && data.app_version) appVer.textContent = data.app_version;
      }
    } catch (e) {
      console.warn('Health fetch error:', e);
    }
  }

  // Telemetry: Fetch Active Model State
  async function fetchActiveModelState() {
    try {
      const resp = await apiFetch('/api/v1/models/active');
      if (resp.ok) {
        const data = await resp.json();
        const champ = $('overview-champion-version');
        const fallback = $('overview-fallback-version');
        const canaryBadge = $('overview-canary-status');
        const canaryTimer = $('overview-canary-timer');
        const hotReload = $('overview-hot-reload');

        if (champ) champ.textContent = data.active_champion_version || 'baseline-v1';
        if (fallback) fallback.textContent = data.stable_fallback_version || 'baseline-v1';

        const isCanary = data.canary_active || data.in_canary_window || false;
        if (canaryBadge) {
          canaryBadge.textContent = isCanary ? 'ACTIVE' : 'INACTIVE';
          canaryBadge.className = `badge ${isCanary ? 'status-warning' : 'status-inactive'}`;
        }
        if (canaryTimer && data.canary_remaining_seconds !== undefined) {
          canaryTimer.textContent = isCanary
            ? `Observation window: ${Math.round(data.canary_remaining_seconds)}s remaining`
            : 'Observation window: 300s';
        }

        if (hotReload) {
          hotReload.textContent = 'READY';
          hotReload.className = 'badge status-healthy';
        }

        // Also update Canary Tab model view
        const canaryActiveModel = $('canary-active-model');
        if (canaryActiveModel) {
          canaryActiveModel.textContent = data.active_champion_version || 'baseline-v1';
        }
      }
    } catch (e) {
      console.warn('Active model fetch error:', e);
    }
  }

  // Telemetry: Fetch Operational Metrics
  async function fetchOperationalMetrics() {
    try {
      const resp = await apiFetch('/api/v1/metrics/summary');
      if (resp.ok) {
        const data = await resp.json();
        const predCount = $('overview-prediction-count');
        const errRate = $('overview-error-rate');
        const p95Latency = $('overview-p95-latency');

        if (predCount) predCount.textContent = (data.count || 0).toLocaleString();
        if (errRate) {
          const ratePct = ((data.error_rate || 0) * 100).toFixed(2);
          errRate.textContent = `${ratePct}%`;
          errRate.className = (data.error_rate > 0.10) ? 'stat-highlight text-danger' : 'stat-highlight';
        }
        if (p95Latency) {
          const lat = (data.latency_p95_ms || 0).toFixed(2);
          p95Latency.textContent = `${lat} ms`;
          p95Latency.className = (data.latency_p95_ms > 500) ? 'stat-highlight text-danger' : 'stat-highlight';
        }
      }
    } catch (e) {
      console.warn('Metrics fetch error:', e);
    }
  }

  // Telemetry: Fetch Drift Report
  async function fetchDriftReport() {
    try {
      const resp = await apiFetch('/api/v1/monitoring/drift/report');
      if (!resp.ok) return;

      const data = await resp.json();
      const statusText = (data.overall_drift_status || (data.is_drifted ? 'DRIFT_DETECTED' : 'NO_DRIFT')).toUpperCase();

      // Overview badges
      const overviewDriftBadge = $('overview-drift-badge');
      if (overviewDriftBadge) {
        overviewDriftBadge.textContent = statusText;
        overviewDriftBadge.className = `badge ${statusText === 'DRIFT_DETECTED' ? 'status-drift' : 'status-healthy'}`;
      }

      // Monitoring Tab overall drift
      const monDriftBadge = $('monitoring-overall-drift');
      if (monDriftBadge) {
        monDriftBadge.textContent = statusText;
        monDriftBadge.className = `badge ${statusText === 'DRIFT_DETECTED' ? 'status-drift' : 'status-healthy'}`;
      }

      const monTimestamp = $('monitoring-drift-timestamp');
      if (monTimestamp && data.evaluation_timestamp) {
        monTimestamp.textContent = new Date(data.evaluation_timestamp).toLocaleTimeString();
      }

      // Populate Numerical Drift Table
      renderNumericalDriftTable(data.numerical_drift || []);

      // Populate Categorical Drift Table
      renderCategoricalDriftTable(data.categorical_drift || []);

    } catch (e) {
      console.warn('Drift report fetch error:', e);
    }
  }

  function renderNumericalDriftTable(items) {
    const tbody = $('table-numerical-drift').querySelector('tbody');
    if (!tbody) return;

    if (!items || items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No numerical features evaluated yet. Click "Run Drift Evaluation".</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(item => {
      const ksDrift = item.ks_drift ? '<span class="badge status-drift">DRIFT</span>' : '<span class="badge status-healthy">STABLE</span>';
      const psiDrift = item.psi_drift ? '<span class="badge status-drift">DRIFT</span>' : '<span class="badge status-healthy">STABLE</span>';
      const statusBadge = item.drift_detected
        ? '<span class="badge status-drift">DRIFT_DETECTED</span>'
        : '<span class="badge status-healthy">NO_DRIFT</span>';

      return `
        <tr>
          <td><strong>${escapeHtml(item.feature_name)}</strong></td>
          <td>${(item.ks_statistic || 0).toFixed(4)}</td>
          <td>${(item.ks_p_value || 0).toExponential(3)}</td>
          <td>${ksDrift}</td>
          <td>${(item.psi_value || 0).toFixed(4)}</td>
          <td>${psiDrift}</td>
          <td>${statusBadge}</td>
        </tr>
      `;
    }).join('');
  }

  function renderCategoricalDriftTable(items) {
    const tbody = $('table-categorical-drift').querySelector('tbody');
    if (!tbody) return;

    if (!items || items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No categorical features evaluated yet.</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(item => {
      const statusBadge = item.drift_detected
        ? '<span class="badge status-drift">DRIFT_DETECTED</span>'
        : '<span class="badge status-healthy">NO_DRIFT</span>';

      return `
        <tr>
          <td><strong>${escapeHtml(item.feature_name)}</strong></td>
          <td>${(item.psi_value || 0).toFixed(4)}</td>
          <td>${item.chi2_statistic !== null ? item.chi2_statistic.toFixed(3) : 'N/A'}</td>
          <td>${item.chi2_p_value !== null ? item.chi2_p_value.toExponential(3) : 'N/A'}</td>
          <td>${statusBadge}</td>
        </tr>
      `;
    }).join('');
  }

  // Telemetry: Fetch System Alerts
  async function fetchSystemAlerts() {
    try {
      const resp = await apiFetch('/api/v1/monitoring/alerts');
      if (!resp.ok) return;

      const data = await resp.json();
      const alerts = data.alerts || [];

      // Update counters
      const activeCount = data.total_active || alerts.filter(a => a.status === 'ACTIVE').length;
      const highCount = alerts.filter(a => a.severity === 'HIGH' && a.status === 'ACTIVE').length;
      const medCount = alerts.filter(a => a.severity === 'MEDIUM' && a.status === 'ACTIVE').length;
      const lowCount = alerts.filter(a => a.severity === 'LOW' && a.status === 'ACTIVE').length;

      if ($('alerts-count-active')) $('alerts-count-active').textContent = activeCount;
      if ($('alerts-count-high')) $('alerts-count-high').textContent = highCount;
      if ($('alerts-count-medium')) $('alerts-count-medium').textContent = medCount;
      if ($('alerts-count-low')) $('alerts-count-low').textContent = lowCount;

      const overviewAlertsBadge = $('overview-alerts-badge');
      if (overviewAlertsBadge) {
        overviewAlertsBadge.textContent = `${activeCount} ACTIVE ALERTS`;
        overviewAlertsBadge.className = `badge ${activeCount > 0 ? 'status-critical' : 'status-healthy'}`;
      }

      // Render alerts table
      renderAlertsTable(alerts);
    } catch (e) {
      console.warn('Alerts fetch error:', e);
    }
  }

  function renderAlertsTable(alerts) {
    const tbody = $('tbody-alerts');
    if (!tbody) return;

    if (!alerts || alerts.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">No active alerts. System operating within nominal thresholds.</td></tr>';
      return;
    }

    const isAuthorized = state.role === 'ML_Engineer' || state.role === 'Administrator';

    tbody.innerHTML = alerts.map(alert => {
      let sevClass = 'badge-info';
      if (alert.severity === 'HIGH') sevClass = 'badge-danger';
      else if (alert.severity === 'MEDIUM') sevClass = 'badge-warning';

      let statusBadge = '<span class="badge badge-warning">ACTIVE</span>';
      if (alert.status === 'ACKNOWLEDGED') statusBadge = '<span class="badge badge-info">ACKNOWLEDGED</span>';
      else if (alert.status === 'RESOLVED') statusBadge = '<span class="badge badge-success">RESOLVED</span>';

      const ackDisabled = (!isAuthorized || alert.status !== 'ACTIVE') ? 'disabled' : '';
      const resolveDisabled = (!isAuthorized || alert.status === 'RESOLVED') ? 'disabled' : '';

      return `
        <tr>
          <td><code>${escapeHtml(alert.alert_id)}</code></td>
          <td>
            <strong>${escapeHtml(alert.metric_name)}</strong>
            ${alert.feature_name ? `<br><small class="text-muted">${escapeHtml(alert.feature_name)}</small>` : ''}
          </td>
          <td><span class="badge ${sevClass}">${escapeHtml(alert.severity)}</span></td>
          <td>${(alert.observed_value || 0).toFixed(4)} / ${(alert.threshold || 0).toFixed(4)}</td>
          <td><span class="badge badge-secondary">${alert.dedup_count || 1}x</span></td>
          <td>${statusBadge}</td>
          <td><small class="text-muted">${new Date(alert.created_at).toLocaleTimeString()}</small></td>
          <td>
            <div style="display: flex; gap: 0.25rem;">
              <button type="button" class="btn btn-xs btn-outline btn-alert-action" 
                data-action="ack" data-id="${escapeHtml(alert.alert_id)}" ${ackDisabled}>Ack</button>
              <button type="button" class="btn btn-xs btn-outline btn-alert-action" 
                data-action="resolve" data-id="${escapeHtml(alert.alert_id)}" ${resolveDisabled}>Resolve</button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    // Attach button handlers
    tbody.querySelectorAll('.btn-alert-action').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.getAttribute('data-action');
        const alertId = btn.getAttribute('data-id');
        if (action === 'ack') handleAlertAck(alertId);
        if (action === 'resolve') handleAlertResolve(alertId);
      });
    });
  }

  async function handleAlertAck(alertId) {
    try {
      const resp = await apiFetch(`/api/v1/monitoring/alerts/${alertId}/ack`, {
        method: 'POST',
        body: { comment: `Acknowledged via UI by ${state.user}` }
      });
      if (resp.ok) {
        showToast(`Alert ${alertId} acknowledged`, 'success');
        fetchSystemAlerts();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Failed to acknowledge alert' }));
        showToast(err.detail || 'Failed to acknowledge', 'error');
      }
    } catch (e) {
      showToast('Error acknowledging alert: ' + e.message, 'error');
    }
  }

  async function handleAlertResolve(alertId) {
    try {
      const resp = await apiFetch(`/api/v1/monitoring/alerts/${alertId}/resolve`, {
        method: 'POST',
        body: { resolution_comment: `Resolved via UI by ${state.user}` }
      });
      if (resp.ok) {
        showToast(`Alert ${alertId} marked as resolved`, 'success');
        fetchSystemAlerts();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Failed to resolve alert' }));
        showToast(err.detail || 'Failed to resolve', 'error');
      }
    } catch (e) {
      showToast('Error resolving alert: ' + e.message, 'error');
    }
  }

  // Governance: Fetch Candidate Models & Scorecards
  async function fetchCandidateModels() {
    try {
      const resp = await apiFetch('/api/v1/candidates');
      if (!resp.ok) return;

      const data = await resp.json();
      const candidates = data.candidates || [];
      renderCandidatesTable(candidates);
    } catch (e) {
      console.warn('Candidates fetch error:', e);
    }
  }

  function renderCandidatesTable(candidates) {
    const tbody = $('tbody-candidates');
    if (!tbody) return;

    if (!candidates || candidates.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="text-center text-muted">No candidate models registered in SQLite registry.</td></tr>';
      return;
    }

    const isAuthorized = state.role === 'ML_Engineer' || state.role === 'Administrator';

    tbody.innerHTML = candidates.map(cand => {
      const report = cand.validation_report || {};
      const gates = report.gates || {};
      const checks = report.checks || {};

      function formatGate(gateKey) {
        const status = checks[gateKey] || (gates[gateKey] ? gates[gateKey].status : null);
        if (status === 'PASSED') return '<span class="badge status-healthy">PASS</span>';
        if (status === 'FAILED') return '<span class="badge status-unhealthy">FAIL</span>';
        return '<span class="badge status-inactive">PENDING</span>';
      }

      let statusBadgeClass = 'badge-secondary';
      if (cand.status === 'PROMOTED') statusBadgeClass = 'status-healthy';
      else if (cand.status === 'VALIDATED') statusBadgeClass = 'badge-info';
      else if (cand.status === 'REJECTED' || cand.status === 'VALIDATION_FAILED') statusBadgeClass = 'status-unhealthy';

      // Promotion allowed only if VALIDATED
      const canValidate = isAuthorized && (cand.status === 'REGISTERED' || cand.status === 'PENDING_VALIDATION' || cand.status === 'VALIDATION_FAILED');
      const canPromote = isAuthorized && (cand.status === 'VALIDATED');
      const canReject = isAuthorized && (cand.status !== 'PROMOTED' && cand.status !== 'REJECTED');

      return `
        <tr>
          <td><code>${escapeHtml(cand.candidate_id)}</code></td>
          <td><strong>${escapeHtml(cand.model_version)}</strong></td>
          <td><small class="text-muted">${escapeHtml(cand.trigger_reason || 'Retraining')}</small></td>
          <td><span class="badge ${statusBadgeClass}">${escapeHtml(cand.status)}</span></td>
          <td>${formatGate('artifact_verification')}</td>
          <td>${formatGate('feature_schema')}</td>
          <td>${formatGate('regression_prevention')}</td>
          <td>${formatGate('inference_latency_sla')}</td>
          <td>
            <div style="display: flex; gap: 0.25rem;">
              <button type="button" class="btn btn-xs btn-outline btn-candidate-action" 
                data-action="validate" data-id="${escapeHtml(cand.candidate_id)}" ${canValidate ? '' : 'disabled'}>Validate</button>
              <button type="button" class="btn btn-xs btn-primary btn-candidate-action" 
                data-action="approve" data-id="${escapeHtml(cand.candidate_id)}" ${canPromote ? '' : 'disabled'}>Approve</button>
              <button type="button" class="btn btn-xs btn-ghost btn-candidate-action" 
                data-action="reject" data-id="${escapeHtml(cand.candidate_id)}" ${canReject ? '' : 'disabled'}>Reject</button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    // Attach candidate button handlers
    tbody.querySelectorAll('.btn-candidate-action').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.getAttribute('data-action');
        const candId = btn.getAttribute('data-id');
        if (action === 'validate') handleCandidateValidate(candId);
        if (action === 'approve') openCandidateModal(candId, 'APPROVE');
        if (action === 'reject') openCandidateModal(candId, 'REJECT');
      });
    });
  }

  async function handleCandidateValidate(candidateId) {
    try {
      showToast(`Executing 4-gate automated validation for ${candidateId}...`, 'info');
      const resp = await apiFetch(`/api/v1/candidates/${candidateId}/validate`, {
        method: 'POST'
      });

      if (resp.ok) {
        const data = await resp.json();
        const outcome = data.all_passed ? 'PASSED ALL GATES' : 'FAILED VALIDATION';
        showToast(`Validation complete for ${candidateId}: ${outcome}`, data.all_passed ? 'success' : 'error');
        fetchCandidateModels();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Validation request failed' }));
        // Check for Architectural Firewall HTTP 422
        if (resp.status === 422) {
          showToast(`Architectural Firewall (TC-19): ${err.detail || 'QML candidates blocked from validation.'}`, 'error');
        } else {
          showToast(err.detail || 'Validation failed', 'error');
        }
      }
    } catch (e) {
      showToast('Validation request error: ' + e.message, 'error');
    }
  }

  function openCandidateModal(candidateId, decision) {
    state.pendingDecision = { candidateId, decision };
    $('modal-candidate-id').textContent = candidateId;
    $('modal-decision-type-badge').textContent = decision;
    $('modal-decision-type-badge').className = `badge ${decision === 'APPROVE' ? 'badge-success' : 'badge-danger'}`;
    
    const warning = $('modal-decision-warning');
    if (decision === 'APPROVE') {
      warning.style.display = 'block';
      warning.textContent = 'Promoting this candidate will replace the active production champion and initiate a 5-minute canary observation window.';
    } else {
      warning.style.display = 'none';
    }

    $('modal-decision-comment').value = `Authorized ${decision} via MLOps console by ${state.user}`;
    $('modal-candidate-decision').style.display = 'flex';
  }

  function closeCandidateModal() {
    $('modal-candidate-decision').style.display = 'none';
    state.pendingDecision = { candidateId: null, decision: null };
  }

  async function confirmCandidateDecision() {
    const { candidateId, decision } = state.pendingDecision;
    const comment = $('modal-decision-comment').value.trim();

    if (!comment) {
      showToast('Please provide a decision justification comment', 'error');
      return;
    }

    try {
      const resp = await apiFetch(`/api/v1/candidates/${candidateId}/decision`, {
        method: 'POST',
        body: { decision, comment }
      });

      closeCandidateModal();

      if (resp.ok) {
        showToast(`Candidate ${candidateId} successfully ${decision.toLowerCase()}d!`, 'success');
        fetchCandidateModels();
        fetchActiveModelState();
        fetchRollbackAndCanary();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Decision submission failed' }));
        if (resp.status === 422) {
          showToast(`Architectural Firewall Blocked (TC-19): ${err.detail}`, 'error');
        } else {
          showToast(err.detail || 'Decision submission failed', 'error');
        }
      }
    } catch (e) {
      showToast('Error submitting decision: ' + e.message, 'error');
    }
  }

  // Rollback & Canary: Fetch Canary Status and History
  async function fetchRollbackAndCanary() {
    try {
      // 1. Fetch Post-Promotion Health Report
      const healthResp = await apiFetch('/api/v1/recovery/health');
      if (healthResp.ok) {
        const data = await healthResp.json();
        const healthBadge = $('canary-health-badge');
        const activeModel = $('canary-active-model');
        const countdown = $('canary-remaining-time');
        const activeState = $('canary-active-state');
        const recAction = $('canary-recommended-action');
        const errRate = $('canary-error-rate');
        const latP95 = $('canary-latency-p95');

        if (healthBadge) {
          const st = (data.health_status || 'HEALTHY').toUpperCase();
          healthBadge.textContent = st;
          healthBadge.className = `badge ${st === 'HEALTHY' ? 'status-healthy' : 'status-unhealthy'}`;
        }
        if (activeModel) activeModel.textContent = data.model_version || 'baseline-v1';
        if (countdown) {
          countdown.textContent = data.is_canary_active
            ? `${Math.round(data.canary_remaining_seconds)}s`
            : '0s';
        }
        if (activeState) {
          activeState.textContent = data.is_canary_active
            ? 'Canary status: ACTIVE'
            : 'Canary status: INACTIVE';
        }
        if (recAction) recAction.textContent = data.recommended_action || 'NONE';
        if (errRate) {
          errRate.textContent = `${((data.error_rate || 0) * 100).toFixed(2)}%`;
        }
        if (latP95) {
          latP95.textContent = `${(data.latency_p95_ms || 0).toFixed(2)} ms`;
        }
      }

      // 2. Fetch Historical Rollback Records
      const rollbackResp = await apiFetch('/api/v1/recovery/rollbacks');
      if (rollbackResp.ok) {
        const rollbacks = await rollbackResp.json();
        renderRollbacksTable(rollbacks);
      }
    } catch (e) {
      console.warn('Canary/Rollback fetch error:', e);
    }
  }

  function renderRollbacksTable(rollbacks) {
    const tbody = $('tbody-rollbacks');
    if (!tbody) return;

    if (!rollbacks || rollbacks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No historical rollbacks recorded.</td></tr>';
      return;
    }

    tbody.innerHTML = rollbacks.map(r => {
      const typeBadge = r.trigger_type === 'AUTOMATED'
        ? '<span class="badge badge-warning">AUTOMATED</span>'
        : '<span class="badge badge-info">MANUAL</span>';

      return `
        <tr>
          <td><code>${escapeHtml(r.rollback_id)}</code></td>
          <td>${typeBadge}</td>
          <td><strong>${escapeHtml(r.source_model_version)}</strong></td>
          <td><strong class="text-highlight">${escapeHtml(r.restored_model_version)}</strong></td>
          <td><small class="text-muted">${escapeHtml(r.reason || 'Operational rollback')}</small></td>
          <td><span class="badge status-healthy">${escapeHtml(r.status)}</span></td>
          <td><small class="text-muted">${new Date(r.executed_at).toLocaleString()}</small></td>
        </tr>
      `;
    }).join('');
  }

  // Rollback: Trigger Manual Rollback Flow
  function openRollbackModal() {
    const reasonInput = $('manual-rollback-reason');
    const reason = reasonInput ? reasonInput.value.trim() : 'Manual operator rollback';
    $('modal-rollback-reason-text').textContent = reason || 'Manual operator rollback via UI console';
    $('modal-rollback-source').textContent = $('overview-champion-version').textContent;
    $('modal-rollback-target').textContent = $('overview-fallback-version').textContent;
    $('modal-rollback').style.display = 'flex';
  }

  function closeRollbackModal() {
    $('modal-rollback').style.display = 'none';
  }

  async function confirmManualRollback() {
    const reasonInput = $('manual-rollback-reason');
    const reason = (reasonInput && reasonInput.value.trim()) || 'Manual operator rollback via UI console';

    try {
      const resp = await apiFetch('/api/v1/models/rollback', {
        method: 'POST',
        body: { reason, triggered_by: state.user }
      });

      closeRollbackModal();

      if (resp.ok) {
        const data = await resp.json();
        showToast(`Rollback executed successfully! Restored: ${data.restored_model_version}`, 'success');
        fetchActiveModelState();
        fetchRollbackAndCanary();
        fetchSystemHealth();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Rollback operation failed' }));
        showToast(err.detail || 'Rollback failed', 'error');
      }
    } catch (e) {
      showToast('Error triggering rollback: ' + e.message, 'error');
    }
  }

  // Canary: Manual Health Guard Evaluation
  async function handleEvaluateCanary() {
    try {
      showToast('Evaluating post-promotion health guard...', 'info');
      const resp = await apiFetch('/api/v1/recovery/health/evaluate', {
        method: 'POST',
        body: { auto_rollback_on_failure: true }
      });

      if (resp.ok) {
        const data = await resp.json();
        showToast(`Canary Evaluation Result: ${data.health_status} (Action: ${data.recommended_action})`, 
          data.health_status === 'HEALTHY' ? 'success' : 'warning');
        fetchRollbackAndCanary();
        fetchActiveModelState();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Canary evaluation request failed' }));
        showToast(err.detail || 'Evaluation failed', 'error');
      }
    } catch (e) {
      showToast('Canary evaluation error: ' + e.message, 'error');
    }
  }

  // Audit: Fetch Audit Events & Verify Hash Chain
  async function fetchAuditEvents() {
    try {
      const resp = await apiFetch('/api/v1/audit/events?limit=50');
      const tbody = $('tbody-audit');
      if (!tbody) return;

      if (!resp.ok) {
        if (resp.status === 401 || resp.status === 403) {
          tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Audit trail requires ML_Engineer or Administrator role. Log in to inspect cryptographic events.</td></tr>';
        } else {
          tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">Failed to retrieve audit events.</td></tr>';
        }
        return;
      }

      const events = await resp.json();
      if (!events || events.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted">No audit events logged yet.</td></tr>';
        return;
      }

      tbody.innerHTML = events.map(evt => {
        let outcomeBadge = '<span class="badge status-healthy">SUCCESS</span>';
        if (evt.outcome === 'FAILURE' || evt.outcome === 'DENIED') {
          outcomeBadge = '<span class="badge status-unhealthy">' + escapeHtml(evt.outcome) + '</span>';
        }

        const shortHash = (evt.record_hash || '').substring(0, 16) + '...';

        return `
          <tr>
            <td><small class="text-muted">${new Date(evt.timestamp).toLocaleString()}</small></td>
            <td><strong>${escapeHtml(evt.actor)}</strong></td>
            <td><span class="badge badge-secondary">${escapeHtml(evt.role)}</span></td>
            <td><code>${escapeHtml(evt.action)}</code></td>
            <td><small>${escapeHtml(evt.resource)}</small></td>
            <td>${outcomeBadge}</td>
            <td title="${escapeHtml(evt.record_hash)}"><code>${escapeHtml(shortHash)}</code></td>
          </tr>
        `;
      }).join('');
    } catch (e) {
      console.warn('Audit events fetch error:', e);
    }
  }

  async function verifyAuditIntegrity() {
    try {
      showToast('Cryptographically verifying SHA-256 hash chain...', 'info');
      const resp = await apiFetch('/api/v1/audit/verify');

      if (resp.ok) {
        const data = await resp.json();
        const badge = $('audit-integrity-badge');
        const details = $('audit-integrity-details');
        const inspected = $('audit-inspected-count');
        const verified = $('audit-verified-count');
        const issues = $('audit-issues-count');

        if (badge) {
          badge.textContent = data.is_valid ? 'CHAIN_INTACT' : 'COMPROMISED';
          badge.className = `badge ${data.is_valid ? 'status-healthy' : 'status-unhealthy'}`;
        }
        if (details) {
          details.textContent = data.is_valid
            ? 'Cryptographic verification confirmed: 100% of SHA-256 records match continuous genesis chain.'
            : 'Warning: Hash chain integrity anomaly detected!';
        }
        if (inspected) inspected.textContent = data.total_records || 0;
        if (verified) verified.textContent = data.verified_records || 0;
        if (issues) issues.textContent = (data.issues || []).length;

        showToast(data.is_valid ? 'Audit chain verified: INTACT' : 'Integrity check FAILED', 
          data.is_valid ? 'success' : 'error');
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Verification failed' }));
        showToast(err.detail || 'Audit verification requires Administrator', 'error');
      }
    } catch (e) {
      showToast('Audit verification error: ' + e.message, 'error');
    }
  }

  // Drift: Trigger Explicit Drift Evaluation
  async function handleEvaluateDrift() {
    try {
      showToast('Calculating two-sample Kolmogorov-Smirnov and PSI statistics...', 'info');
      const resp = await apiFetch('/api/v1/monitoring/drift/evaluate', {
        method: 'POST',
        body: { trigger_alerts: true }
      });

      if (resp.ok) {
        showToast('Drift evaluation completed successfully', 'success');
        fetchDriftReport();
        fetchSystemAlerts();
      } else {
        const err = await resp.json().catch(() => ({ detail: 'Drift evaluation failed' }));
        showToast(err.detail || 'Drift evaluation failed', 'error');
      }
    } catch (e) {
      showToast('Drift evaluation request error: ' + e.message, 'error');
    }
  }

  // Helper: Synchronize All Telemetry Data
  function fetchAllTelemetry() {
    const timeEl = $('last-refreshed-time');
    if (timeEl) timeEl.textContent = `Last sync: ${new Date().toLocaleTimeString()}`;

    fetchSystemHealth();
    fetchActiveModelState();
    fetchOperationalMetrics();
    fetchDriftReport();
    fetchSystemAlerts();
    fetchCandidateModels();
    fetchRollbackAndCanary();
    fetchAuditEvents();
  }

  // Helper: Sanitize Strings for HTML Table Insertion
  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // DOMContentLoaded Initialization
  document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    updateAuthUI();

    // Quick Login Preset Buttons
    $('btn-login-viewer')?.addEventListener('click', () => {
      performLogin(DEMO_CREDENTIALS.viewer.username, DEMO_CREDENTIALS.viewer.password);
    });
    $('btn-login-engineer')?.addEventListener('click', () => {
      performLogin(DEMO_CREDENTIALS.engineer.username, DEMO_CREDENTIALS.engineer.password);
    });
    $('btn-login-admin')?.addEventListener('click', () => {
      performLogin(DEMO_CREDENTIALS.admin.username, DEMO_CREDENTIALS.admin.password);
    });
    $('btn-logout')?.addEventListener('click', performLogout);
    $('btn-set-token')?.addEventListener('click', setManualToken);

    // Refresh & Sync Controls
    $('btn-refresh-all')?.addEventListener('click', () => {
      fetchAllTelemetry();
      showToast('Telemetry refreshed', 'info');
    });

    const autoSyncToggle = $('auto-refresh-toggle');
    if (autoSyncToggle) {
      autoSyncToggle.addEventListener('change', (e) => {
        state.autoSync = e.target.checked;
        if (state.autoSync) {
          startAutoSync();
          showToast('Auto-sync enabled (10s)', 'info');
        } else {
          stopAutoSync();
          showToast('Auto-sync paused', 'info');
        }
      });
    }

    // Drift Evaluation
    $('btn-evaluate-drift')?.addEventListener('click', handleEvaluateDrift);

    // Canary & Rollback Controls
    $('btn-evaluate-canary')?.addEventListener('click', handleEvaluateCanary);
    $('btn-trigger-rollback')?.addEventListener('click', openRollbackModal);
    $('btn-close-rollback-modal')?.addEventListener('click', closeRollbackModal);
    $('btn-cancel-rollback')?.addEventListener('click', closeRollbackModal);
    $('btn-confirm-rollback')?.addEventListener('click', confirmManualRollback);

    // Candidate Decision Modal Controls
    $('btn-close-decision-modal')?.addEventListener('click', closeCandidateModal);
    $('btn-cancel-decision')?.addEventListener('click', closeCandidateModal);
    $('btn-confirm-decision')?.addEventListener('click', confirmCandidateDecision);

    // Audit Hash Chain Verification
    $('btn-verify-audit')?.addEventListener('click', verifyAuditIntegrity);

    // Keyboard dismissal for modals (Escape key)
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeCandidateModal();
        closeRollbackModal();
      }
    });

    // Initial Telemetry Load
    fetchAllTelemetry();

    // Start Auto Sync (every 10 seconds)
    startAutoSync();
  });

  function startAutoSync() {
    stopAutoSync();
    state.syncIntervalId = setInterval(fetchAllTelemetry, 10000);
  }

  function stopAutoSync() {
    if (state.syncIntervalId) {
      clearInterval(state.syncIntervalId);
      state.syncIntervalId = null;
    }
  }

})();

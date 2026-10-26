'use strict';

(function () {
    var page = document.getElementById('healthPage');
    if (!page) return; // Non-SA render has no health markup and must not poll.

    var POLL_MS = 15000;
    var API_URL = '/api/v1/system/health';

    var COMPONENTS = [
        { key: 'db', title: 'Database', subtitle: 'PostgreSQL connection, version and pool' },
        { key: 'data_integrity', title: 'Data correctness', subtitle: 'Saved records and period checks' },
        { key: 'data_flow', title: 'Data flow', subtitle: 'New records, tasks and activity' },
        { key: 'security', title: 'Security', subtitle: 'Failed logins, locks and sessions' },
        { key: 'user_activity', title: 'User activity', subtitle: 'Logins and active users' },
        { key: 'minio', title: 'File storage', subtitle: 'MinIO object storage' },
        { key: 'redis', title: 'Cache', subtitle: 'Redis cache' }
    ];

    var PROBE_LABELS = {
        departed_records_missing_name: 'Departed employee records missing a name',
        departed_tasks_missing_owner: 'Departed employee tasks missing an owner',
        invalid_period_dates: 'Periods with an end date before the start date'
    };

    var PROBE_RESULTS = {
        ok: 'passed',
        slow: 'slow',
        failed: 'failed',
        disabled: 'not configured',
        not_configured: 'not configured'
    };

    var overallEl = document.getElementById('healthOverall');
    var overallDot = document.getElementById('healthOverallDot');
    var overallLabel = document.getElementById('healthOverallLabel');
    var overallMeta = document.getElementById('healthOverallMeta');
    var errorEl = document.getElementById('healthError');
    var attentionEl = document.getElementById('healthAttention');
    var attentionList = document.getElementById('healthAttentionList');
    var gridEl = document.getElementById('healthGrid');
    var refreshBtn = document.getElementById('healthRefreshBtn');

    var timer = null;
    var stopped = false;
    var lastStatus = {};
    var hasRendered = false;

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatTime(iso) {
        if (!iso) return 'Not recorded yet';
        var date = new Date(iso);
        return isNaN(date.getTime()) ? String(iso) : date.toLocaleString();
    }

    function statusClass(status) {
        return (status || 'disabled').toLowerCase();
    }

    function detailLines(key, component) {
        var d = component.details || {};
        var lines = [];
        if (key === 'db') {
            if (component.latency_ms != null) lines.push(['Response time', component.latency_ms + ' ms']);
            lines.push(['Version matches latest', d.migration_head_match === true ? 'Yes' : d.migration_head_match === false ? 'No' : 'Not checked']);
            if (d.pool) lines.push(['Connection pool', d.pool.checked_out + ' of ' + d.pool.size + ' in use (' + d.pool.checked_out_pct + '%)']);
        } else if (key === 'data_integrity') {
            var failed = d.failed_probes || [];
            var skipped = d.skipped_probes || [];
            if (failed.length === 0) lines.push(['Checks', 'All checks passed']);
            failed.forEach(function (probe) {
                lines.push(['Problem found', PROBE_LABELS[probe] || probe]);
            });
            if (skipped.length > 0) lines.push(['Not checked yet', skipped.map(function (probe) { return PROBE_LABELS[probe] || probe; }).join(', ')]);
        } else if (key === 'data_flow') {
            ['last_1h', 'last_24h'].forEach(function (windowKey) {
                var w = d[windowKey];
                if (!w) return;
                var label = windowKey === 'last_1h' ? 'New in the last hour' : 'New in the last 24 hours';
                lines.push([label, w.total + ' items (' + w.kpi_records + ' records, ' + w.tasks + ' tasks, ' + w.submission_batches + ' batches, ' + w.audit_logs + ' audit entries)']);
            });
        } else if (key === 'security') {
            lines.push(['Failed logins (1 hour)', d.failed_logins_1h]);
            lines.push(['Locked accounts', d.locked_accounts]);
            lines.push(['Unlock requests waiting', d.pending_unlocks]);
            lines.push(['Active sessions', d.active_sessions]);
        } else if (key === 'user_activity') {
            lines.push(['Active sessions', d.active_sessions]);
            lines.push(['Logins (24 hours)', d.logins_24h]);
            lines.push(['Active users (24 hours)', d.active_users_24h]);
            lines.push(['Last login', formatTime(d.last_login_at)]);
        } else if (key === 'minio') {
            if (d.bucket) lines.push(['Storage bucket', d.bucket]);
            if (d.probe) lines.push(['Probe', PROBE_RESULTS[d.probe] || d.probe]);
            if (component.latency_ms != null) lines.push(['Response time', component.latency_ms + ' ms']);
        } else if (key === 'redis') {
            if (d.probe) lines.push(['Probe', PROBE_RESULTS[d.probe] || d.probe]);
            if (component.latency_ms != null) lines.push(['Response time', component.latency_ms + ' ms']);
        }
        return lines;
    }

    function render(data) {
        var components = data.components || {};
        var overall = statusClass(data.overall);
        var overallText = {
            ok: 'All systems OK',
            warning: 'Something needs attention',
            fail: 'System problem detected',
            disabled: 'System status unavailable'
        }[overall] || 'System status unavailable';

        overallEl.className = 'health-overall health-overall--' + overall;
        overallDot.className = 'health-dot health-dot--' + overall;
        overallLabel.textContent = overallText;
        overallMeta.textContent = 'Last checked: ' + formatTime(data.checked_at) + ' · Next check in 15 seconds.';

        var attention = COMPONENTS.filter(function (meta) {
            var c = components[meta.key];
            return c && (c.status === 'FAIL' || c.status === 'WARNING');
        });
        attention.sort(function (a, b) {
            return (components[a.key].status === 'FAIL' ? 0 : 1) - (components[b.key].status === 'FAIL' ? 0 : 1);
        });
        if (attention.length > 0) {
            attentionList.innerHTML = attention.map(function (meta) {
                var c = components[meta.key];
                return '<li><strong>' + esc(meta.title) + '</strong> — ' + esc(c.message || '') + '</li>';
            }).join('');
            attentionEl.hidden = false;
        } else {
            attentionEl.hidden = true;
        }

        var changed = {};
        COMPONENTS.forEach(function (meta) {
            var c = components[meta.key];
            if (!c) return;
            if (hasRendered && lastStatus[meta.key] && lastStatus[meta.key] !== c.status) changed[meta.key] = true;
            lastStatus[meta.key] = c.status;
        });

        gridEl.innerHTML = COMPONENTS.map(function (meta) {
            var c = components[meta.key];
            if (!c) return '';
            var cls = statusClass(c.status);
            var lines = detailLines(meta.key, c).map(function (pair) {
                return '<li><strong>' + esc(pair[0]) + ':</strong> ' + esc(pair[1]) + '</li>';
            }).join('');
            return '<article class="health-card health-card--' + cls + (changed[meta.key] ? ' health-card--changed' : '') + '" data-component="' + esc(meta.key) + '">'
                + '<div class="health-card__head"><div><div class="health-card__title">' + esc(meta.title) + '</div>'
                + '<div class="health-card__subtitle">' + esc(meta.subtitle) + '</div></div>'
                + '<span class="health-badge health-badge--' + cls + '">' + esc(c.status) + '</span></div>'
                + '<p class="health-card__message">' + esc(c.message || '') + '</p>'
                + (lines ? '<ul class="health-card__details">' + lines + '</ul>' : '')
                + '</article>';
        }).join('');
        hasRendered = true;
    }

    function showError(message) {
        errorEl.textContent = message;
        errorEl.hidden = false;
    }

    function clearError() {
        errorEl.hidden = true;
        errorEl.textContent = '';
    }

    function stopPolling() {
        stopped = true;
        if (timer) { clearInterval(timer); timer = null; }
    }

    async function fetchHealth() {
        if (stopped) return;
        try {
            var response = await fetch(API_URL, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
            if (response.status === 401) {
                stopPolling();
                window.location.href = '/auth/login';
                return;
            }
            if (response.status === 403) {
                stopPolling();
                showError('You do not have access to system health.');
                return;
            }
            if (!response.ok) throw new Error('Health request failed');
            var data = await response.json();
            clearError();
            render(data);
        } catch (error) {
            showError('Could not reach the health service. The last shown status is kept, and the next check runs in 15 seconds.');
        }
    }

    function startPolling() {
        if (timer || stopped) return;
        timer = setInterval(fetchHealth, POLL_MS);
    }

    if (refreshBtn) refreshBtn.addEventListener('click', fetchHealth);

    document.addEventListener('visibilitychange', function () {
        if (document.hidden) {
            if (timer) { clearInterval(timer); timer = null; }
        } else if (!stopped) {
            fetchHealth();
            startPolling();
        }
    });

    window.addEventListener('pagehide', function () {
        if (timer) { clearInterval(timer); timer = null; }
    });

    fetchHealth();
    startPolling();
})();

// d365_ringcx.js
// Manages the D365 + RingCX Demo tab: environments, leads, scoring, routing, outcomes.

let d365CurrentEnvId = null;
let d365CurrentLeads = [];
let d365DemoLeadCount = 0;
let d365RingCXRefreshInterval = null;
const D365_REFRESH_INTERVAL_MS = 4 * 60 * 1000;

const MOVE_TYPE_MAP = {
    322750000: 'Residential Local',
    322750001: 'Residential Interstate',
    322750002: 'Commercial Local',
    322750003: 'Commercial Interstate',
};

document.addEventListener('DOMContentLoaded', () => {
    if (!document.getElementById('d365-env-select')) return;

    // --- Element refs ---
    const envSelect             = document.getElementById('d365-env-select');
    const envDetail             = document.getElementById('d365-env-detail');
    const envUrlSpan            = document.getElementById('d365-env-url');
    const envOwnerSpan          = document.getElementById('d365-env-owner');
    const addEnvBtn             = document.getElementById('d365-add-env-btn');

    const statusBadge           = document.getElementById('d365-ringcx-status-badge');
    const connectBtn            = document.getElementById('d365-ringcx-connect-btn');
    const disconnectBtn         = document.getElementById('d365-ringcx-disconnect-btn');
    const accountRow            = document.getElementById('d365-ringcx-account-row');
    const accountIdSpan         = document.getElementById('d365-ringcx-account-id');

    const humanCampaignSelect   = document.getElementById('d365-human-campaign-id');
    const aiCampaignSelect      = document.getElementById('d365-ai-campaign-id');
    const bookingCampaignSelect = document.getElementById('d365-booking-campaign-id');
    const saveStateBtn          = document.getElementById('d365-save-state-btn');
    const reloadCampaignsBtn    = document.getElementById('d365-reload-campaigns-btn');

    const phoneNumbersArea      = document.getElementById('d365-phone-numbers');
    const createLeadsBtn        = document.getElementById('d365-create-leads-btn');
    const createLeadsStatus     = document.getElementById('d365-create-leads-status');
    const deleteDemoBtn         = document.getElementById('d365-delete-demo-btn');
    const testConnBtn           = document.getElementById('d365-test-conn-btn');
    const testConnStatus        = document.getElementById('d365-test-conn-status');
    const refreshLeadsBtn       = document.getElementById('d365-refresh-leads-btn');
    const leadsLoading          = document.getElementById('d365-leads-loading');
    const noEnvMsg              = document.getElementById('d365-no-env');
    const leadsTableWrapper     = document.getElementById('d365-leads-table-wrapper');
    const leadsTableBody        = document.getElementById('d365-leads-table-body');

    const scoreBtn              = document.getElementById('d365-score-btn');
    const pushBtn               = document.getElementById('d365-push-btn');
    const checkRingcxBtn        = document.getElementById('d365-check-ringcx-btn');
    const scoreRouteStatus      = document.getElementById('d365-score-route-status');
    const humanCountInput       = document.getElementById('d365-human-count');
    const aiCountInput          = document.getElementById('d365-ai-count');
    const splitHint             = document.getElementById('d365-split-hint');
    const scoreResults          = document.getElementById('d365-score-results');
    const scoreResultsBody      = document.getElementById('d365-score-results-body');

    const outcomesTotal         = document.getElementById('outcomes-total');
    const outcomesCalled        = document.getElementById('outcomes-called');
    const outcomesHot           = document.getElementById('outcomes-hot');
    const outcomesNotHot        = document.getElementById('outcomes-not-hot');
    const outcomesStatus        = document.getElementById('outcomes-status');
    const refreshOutcomesBtn    = document.getElementById('d365-refresh-outcomes-btn');
    const liveFeedDot           = document.getElementById('live-feed-status-dot');
    const liveFeedLabel         = document.getElementById('live-feed-status-label');
    const liveFeedEmpty         = document.getElementById('live-feed-empty');
    const liveFeedTableWrap     = document.getElementById('live-feed-table-wrap');
    const liveFeedBody          = document.getElementById('live-feed-body');
    const postbackUrlDisplay    = document.getElementById('postback-url-display');

    const addEnvModal           = document.getElementById('d365-add-env-modal');
    const modalName             = document.getElementById('d365-modal-name');
    const modalTenant           = document.getElementById('d365-modal-tenant');
    const modalClientId         = document.getElementById('d365-modal-client-id');
    const modalSecret           = document.getElementById('d365-modal-secret');
    const modalUrl              = document.getElementById('d365-modal-url');
    const modalSaveBtn          = document.getElementById('d365-modal-save-btn');
    const modalCancelBtn        = document.getElementById('d365-modal-cancel-btn');
    const modalCloseBtn         = document.getElementById('d365-modal-close-btn');

    const deleteModal           = document.getElementById('d365-delete-modal');
    const deleteConfirmBtn      = document.getElementById('d365-delete-confirm-btn');
    const deleteCancelBtn       = document.getElementById('d365-delete-cancel-btn');

    // -------------------------------------------------------------------------
    // Init
    // -------------------------------------------------------------------------

    loadStatus();

    // -------------------------------------------------------------------------
    // Status / bootstrap
    // -------------------------------------------------------------------------

    async function loadStatus() {
        try {
            const response = await fetch('/api/d365_ringcx/status');
            const data = await response.json();
            if (!response.ok) return;

            if (data.ringcx_connected) {
                setRingCXConnectedState(data.ringcx_account_id);
                await loadCampaigns({
                    human:   data.human_campaign_id   || '',
                    ai:      data.ai_campaign_id      || '',
                    booking: data.booking_campaign_id || '',
                });
            } else {
                setRingCXDisconnectedState();
            }

            if (data.is_admin) addEnvBtn.classList.remove('hidden');

            populateEnvironments(data.environments || [], data.last_env_id);

        } catch (err) {
            console.error('loadStatus failed:', err);
        }
    }

    // -------------------------------------------------------------------------
    // RingCX campaigns
    // -------------------------------------------------------------------------

    async function loadCampaigns(savedIds) {
        const placeholder = '<option value="">Select a campaign...</option>';
        [humanCampaignSelect, aiCampaignSelect, bookingCampaignSelect].forEach(s => {
            s.innerHTML = '<option value="">Loading campaigns...</option>';
            s.disabled = true;
        });

        try {
            const response = await fetch('/api/d365_ringcx/ringcx-campaigns');
            const data = await response.json();

            if (!response.ok) {
                const msg = `<option value="">${data.error || 'Failed to load campaigns'}</option>`;
                [humanCampaignSelect, aiCampaignSelect, bookingCampaignSelect].forEach(s => {
                    s.innerHTML = msg;
                });
                return;
            }

            const campaigns = Array.isArray(data.campaigns) ? data.campaigns : [];
            const options = placeholder + campaigns.map(c => {
                const id   = c.campaignId || c.id || '';
                const name = c.campaignName || c.name || id;
                return `<option value="${id}">${name} (${id})</option>`;
            }).join('');

            [humanCampaignSelect, aiCampaignSelect, bookingCampaignSelect].forEach(s => {
                s.innerHTML = options;
                s.disabled = false;
            });

            // Restore saved selections
            if (savedIds?.human)   humanCampaignSelect.value   = savedIds.human;
            if (savedIds?.ai)      aiCampaignSelect.value      = savedIds.ai;
            if (savedIds?.booking) bookingCampaignSelect.value = savedIds.booking;

            reloadCampaignsBtn.classList.remove('hidden');

        } catch {
            const msg = '<option value="">Network error loading campaigns</option>';
            [humanCampaignSelect, aiCampaignSelect, bookingCampaignSelect].forEach(s => {
                s.innerHTML = msg;
                s.disabled = false;
            });
        }
    }

    reloadCampaignsBtn.addEventListener('click', () => loadCampaigns());

    // -------------------------------------------------------------------------
    // Environments
    // -------------------------------------------------------------------------

    function populateEnvironments(envs, lastEnvId) {
        envSelect.innerHTML = '<option value="">Select an environment...</option>';
        envs.forEach(env => {
            const option = document.createElement('option');
            option.value = env.id;
            option.textContent = env.name;
            option.dataset.envUrl     = env.env_url || '';
            option.dataset.ownerEmail = env.owner_email || '';
            envSelect.appendChild(option);
        });

        if (lastEnvId && envSelect.querySelector(`option[value="${lastEnvId}"]`)) {
            envSelect.value = lastEnvId;
            onEnvChange(lastEnvId);
        }
    }

    envSelect.addEventListener('change', () => {
        const envId = envSelect.value;
        if (envId) {
            onEnvChange(envId);
        } else {
            d365CurrentEnvId = null;
            envDetail.classList.add('hidden');
            leadsTableWrapper.classList.add('hidden');
            deleteDemoBtn.classList.add('hidden');
            noEnvMsg.textContent = 'Select an environment to view leads.';
            noEnvMsg.classList.remove('hidden');
        }
    });

    function onEnvChange(envId) {
        d365CurrentEnvId = envId;
        const selected = envSelect.querySelector(`option[value="${envId}"]`);
        if (selected) {
            envUrlSpan.textContent   = selected.dataset.envUrl || '—';
            envOwnerSpan.textContent = selected.dataset.ownerEmail || '—';
            envDetail.classList.remove('hidden');
        }
        // Reset test connection status on env change
        if (testConnStatus) {
            testConnStatus.textContent = '';
            testConnStatus.className = '';
        }
        // Don't auto-load — prompt user to click Pull Leads
        leadsTableWrapper.classList.add('hidden');
        deleteDemoBtn.classList.add('hidden');
        noEnvMsg.textContent = 'Click Pull Leads to fetch the top 10 leads.';
        noEnvMsg.classList.remove('hidden');

        // Wire up live feed SSE and load past dispositions
        connectLiveFeed(envId);
        loadOutcomes();

        // Show the postback URL for RingCX web service config
        const base = window.location.origin;
        postbackUrlDisplay.textContent = `POST ${base}/api/d365_ringcx/postback?secret=<your_webhook_secret>`;
    }

    // -------------------------------------------------------------------------
    // Leads
    // -------------------------------------------------------------------------

    async function loadLeads(envId) {
        leadsLoading.classList.remove('hidden');
        leadsLoading.textContent = 'Loading leads...';
        leadsTableWrapper.classList.add('hidden');
        leadsTableBody.innerHTML = '';
        deleteDemoBtn.classList.add('hidden');

        try {
            const response = await fetch(`/api/d365_ringcx/leads?env_id=${encodeURIComponent(envId)}`);
            const data = await response.json();
            if (!response.ok) {
                leadsLoading.textContent = data.error || 'Failed to load leads.';
                return;
            }
            d365CurrentLeads = data.leads || [];
            renderLeadsTable(d365CurrentLeads);
            leadsLoading.classList.add('hidden');
        } catch {
            leadsLoading.textContent = 'Network error loading leads.';
        }
    }

    function renderLeadsTable(leads) {
        leadsTableBody.innerHTML = '';

        // Update demo lead count and split controls
        d365DemoLeadCount = leads.filter(l => l._is_demo).length;
        updateSplitControls();

        if (leads.length === 0) {
            const row = document.createElement('tr');
            row.innerHTML = `<td colspan="8" class="py-6 text-center text-sm text-slate-400 italic">No leads found in this environment.</td>`;
            leadsTableBody.appendChild(row);
            leadsTableWrapper.classList.remove('hidden');
            return;
        }

        const hasDemoLeads = leads.some(l => l._is_demo);
        if (hasDemoLeads) deleteDemoBtn.classList.remove('hidden');

        leads.forEach(lead => {
            const tr = document.createElement('tr');
            tr.className = `border-b border-slate-50 hover:bg-slate-50 transition${lead._is_demo ? ' bg-blue-50' : ''}`;

            const moveTypeRaw = lead.crd67_movetype;
            const moveType = MOVE_TYPE_MAP[moveTypeRaw] || (moveTypeRaw != null ? String(moveTypeRaw) : '—');

            const origin = lead.crd67_originsuburb || '';
            const dest   = lead.crd67_destinationsuburb || '';
            const route  = (origin || dest) ? `${escHtml(origin)} → ${escHtml(dest)}` : '—';

            const score = lead._webscore_tracked ?? lead.crd67_webscore ?? null;
            let scoreBadge = '—';
            if (score !== null && score !== undefined) {
                const cls = score >= 60
                    ? 'bg-green-100 text-green-800'
                    : 'bg-orange-100 text-orange-800';
                scoreBadge = `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}">${score}</span>`;
            }

            const disposition = lead._disposition || lead.rc_disposition || null;
            let dispositionBadge = '—';
            if (disposition) {
                const upper = disposition.toUpperCase();
                let cls = 'bg-slate-100 text-slate-600';
                if (upper === 'HOT')            cls = 'bg-green-100 text-green-800';
                else if (upper === 'NOT HOT')   cls = 'bg-slate-200 text-slate-700';
                else if (upper === 'CALLBACK')  cls = 'bg-blue-100 text-blue-800';
                else if (upper === 'NO ANSWER') cls = 'bg-orange-100 text-orange-800';
                dispositionBadge = `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}">${escHtml(disposition)}</span>`;
            }

            const demoBadge = lead._is_demo
                ? '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">Demo</span>'
                : '';

            const name     = escHtml(lead.fullname || `${lead.firstname || ''} ${lead.lastname || ''}`.trim() || '—');
            const phone    = escHtml(lead.mobilephone || lead.telephone1 || '—');
            const campaign = escHtml(lead._campaign || lead.rc_campaignassigned || '—');

            tr.innerHTML = `
                <td class="py-2.5 pr-3 font-medium text-slate-800 whitespace-nowrap">${name}</td>
                <td class="py-2.5 pr-3 font-mono text-slate-600 text-xs whitespace-nowrap">${phone}</td>
                <td class="py-2.5 pr-3 text-slate-600 whitespace-nowrap">${escHtml(moveType)}</td>
                <td class="py-2.5 pr-3 text-slate-600 text-xs">${route}</td>
                <td class="py-2.5 pr-3">${scoreBadge}</td>
                <td class="py-2.5 pr-3 text-slate-600 text-xs">${campaign}</td>
                <td class="py-2.5 pr-3">${dispositionBadge}</td>
                <td class="py-2.5">${demoBadge}</td>
            `;
            leadsTableBody.appendChild(tr);
        });

        leadsTableWrapper.classList.remove('hidden');
    }

    refreshLeadsBtn.addEventListener('click', () => {
        if (d365CurrentEnvId) loadLeads(d365CurrentEnvId);
        else showMessage('Please select an environment first.', true);
    });

    testConnBtn.addEventListener('click', async () => {
        if (!d365CurrentEnvId) {
            showMessage('Please select an environment first.', true);
            return;
        }
        testConnBtn.disabled = true;
        testConnStatus.textContent = 'Testing...';
        testConnStatus.className = 'text-xs text-slate-500';
        try {
            const response = await fetch(`/api/d365_ringcx/test-connection?env_id=${encodeURIComponent(d365CurrentEnvId)}`);
            const data = await response.json();
            if (response.ok) {
                testConnStatus.textContent = `✓ ${data.message}`;
                testConnStatus.className = 'text-xs text-green-600 font-medium';
            } else {
                testConnStatus.textContent = `✗ ${data.error || 'Connection failed'}`;
                testConnStatus.className = 'text-xs text-red-600 font-medium';
            }
        } catch {
            testConnStatus.textContent = '✗ Network error';
            testConnStatus.className = 'text-xs text-red-600 font-medium';
        } finally {
            testConnBtn.disabled = false;
        }
    });

    // -------------------------------------------------------------------------
    // RingCX connect / disconnect
    // -------------------------------------------------------------------------

    connectBtn.addEventListener('click', async () => {
        connectBtn.disabled = true;
        connectBtn.textContent = 'Connecting...';
        try {
            const response = await fetch('/api/d365_ringcx/ringcx-connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            if (response.ok) {
                setRingCXConnectedState(data.accountId);
                showMessage('Connected to RingCX successfully.');
            } else if (response.status === 401) {
                showMessage('Please connect to RingCentral first via the RC Auth tab.', true);
                setRingCXDisconnectedState();
            } else {
                showMessage(data.error || 'Failed to connect to RingCX.', true);
                setRingCXDisconnectedState();
            }
        } catch {
            showMessage('Network error connecting to RingCX.', true);
            setRingCXDisconnectedState();
        }
    });

    disconnectBtn.addEventListener('click', async () => {
        try {
            await fetch('/api/d365_ringcx/ringcx-disconnect', { method: 'POST' });
        } catch {}
        setRingCXDisconnectedState();
        showMessage('Disconnected from RingCX.');
    });

    function setRingCXConnectedState(accountId) {
        statusBadge.textContent = 'Connected';
        statusBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800';
        connectBtn.classList.add('hidden');
        connectBtn.disabled = false;
        connectBtn.textContent = 'Connect to RingCX';
        disconnectBtn.classList.remove('hidden');
        accountRow.classList.remove('hidden');
        accountIdSpan.textContent = accountId || 'Unknown';
        startD365RefreshTimer();
        loadCampaigns();
    }

    function setRingCXDisconnectedState() {
        statusBadge.textContent = 'Disconnected';
        statusBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600';
        connectBtn.disabled = false;
        connectBtn.textContent = 'Connect to RingCX';
        connectBtn.classList.remove('hidden');
        disconnectBtn.classList.add('hidden');
        accountRow.classList.add('hidden');
        accountIdSpan.textContent = '';
        stopD365RefreshTimer();
        const disconnectedOpt = '<option value="">Connect to RingCX to load...</option>';
        [humanCampaignSelect, aiCampaignSelect, bookingCampaignSelect].forEach(s => {
            s.innerHTML = disconnectedOpt;
            s.disabled = true;
        });
        reloadCampaignsBtn.classList.add('hidden');
    }

    function startD365RefreshTimer() {
        stopD365RefreshTimer();
        d365RingCXRefreshInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/audio_streaming/ringcx-refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                if (!response.ok) {
                    stopD365RefreshTimer();
                    setRingCXDisconnectedState();
                    showMessage('RingCX session expired. Please reconnect.', true);
                }
            } catch {
                console.warn('D365 RingCX token refresh failed.');
            }
        }, D365_REFRESH_INTERVAL_MS);
    }

    function stopD365RefreshTimer() {
        if (d365RingCXRefreshInterval) {
            clearInterval(d365RingCXRefreshInterval);
            d365RingCXRefreshInterval = null;
        }
    }

    // -------------------------------------------------------------------------
    // Campaign IDs / user state
    // -------------------------------------------------------------------------

    saveStateBtn.addEventListener('click', async () => {
        saveStateBtn.disabled = true;
        try {
            const response = await fetch('/api/d365_ringcx/user-state', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    human_campaign_id:   humanCampaignSelect.value,
                    ai_campaign_id:      aiCampaignSelect.value,
                    booking_campaign_id: bookingCampaignSelect.value,
                    last_env_id:         d365CurrentEnvId || '',
                })
            });
            const data = await response.json();
            if (response.ok) {
                showMessage('Campaign IDs saved.');
            } else {
                showMessage(data.error || 'Failed to save.', true);
            }
        } catch {
            showMessage('Network error saving campaign IDs.', true);
        } finally {
            saveStateBtn.disabled = false;
        }
    });

    // -------------------------------------------------------------------------
    // Create demo leads
    // -------------------------------------------------------------------------

    createLeadsBtn.addEventListener('click', async () => {
        if (!d365CurrentEnvId) {
            showMessage('Please select an environment first.', true);
            return;
        }
        const raw = phoneNumbersArea.value.trim();
        if (!raw) {
            showMessage('Please enter at least one phone number.', true);
            return;
        }
        const phoneNumbers = raw.split('\n').map(s => s.trim()).filter(Boolean);

        createLeadsBtn.disabled = true;
        createLeadsStatus.textContent = `Creating ${phoneNumbers.length} lead(s)...`;
        try {
            const response = await fetch('/api/d365_ringcx/leads/create-demo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ env_id: d365CurrentEnvId, phone_numbers: phoneNumbers })
            });
            const data = await response.json();
            if (response.status === 501) {
                createLeadsStatus.textContent = '';
                showMessage('Lead creation is coming soon — not yet implemented.', true);
            } else if (response.ok) {
                createLeadsStatus.textContent = `Created ${(data.leads || []).length} lead(s).`;
                phoneNumbersArea.value = '';
                loadLeads(d365CurrentEnvId);
            } else {
                createLeadsStatus.textContent = '';
                showMessage(data.error || 'Failed to create leads.', true);
            }
        } catch {
            createLeadsStatus.textContent = '';
            showMessage('Network error creating leads.', true);
        } finally {
            createLeadsBtn.disabled = false;
        }
    });

    // -------------------------------------------------------------------------
    // Delete demo leads
    // -------------------------------------------------------------------------

    deleteDemoBtn.addEventListener('click', () => {
        deleteModal.classList.remove('hidden');
    });

    deleteCancelBtn.addEventListener('click', () => {
        deleteModal.classList.add('hidden');
    });

    deleteModal.addEventListener('click', (e) => {
        if (e.target === deleteModal) deleteModal.classList.add('hidden');
    });

    deleteConfirmBtn.addEventListener('click', async () => {
        deleteModal.classList.add('hidden');
        if (!d365CurrentEnvId) return;

        deleteConfirmBtn.disabled = true;
        try {
            const response = await fetch('/api/d365_ringcx/leads/delete-demo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ env_id: d365CurrentEnvId })
            });
            const data = await response.json();
            if (response.status === 501) {
                showMessage('Lead deletion is coming soon — not yet implemented.', true);
            } else if (response.ok) {
                showMessage('All demo leads deleted.');
                loadLeads(d365CurrentEnvId);
            } else {
                showMessage(data.error || 'Failed to delete leads.', true);
            }
        } catch {
            showMessage('Network error deleting leads.', true);
        } finally {
            deleteConfirmBtn.disabled = false;
        }
    });

    // -------------------------------------------------------------------------
    // Split controls
    // -------------------------------------------------------------------------

    function updateSplitControls() {
        if (d365DemoLeadCount === 0) {
            splitHint.textContent = 'Pull leads first to enable scoring.';
            splitHint.className = 'text-xs text-slate-400';
            humanCountInput.max = 0;
            aiCountInput.max = 0;
            return;
        }
        const h = parseInt(humanCountInput.value) || 0;
        const a = parseInt(aiCountInput.value) || 0;
        humanCountInput.max = d365DemoLeadCount;
        aiCountInput.max = d365DemoLeadCount;
        if (h + a !== d365DemoLeadCount) {
            splitHint.textContent = `${h + a} of ${d365DemoLeadCount} assigned — must total ${d365DemoLeadCount}.`;
            splitHint.className = 'text-xs text-amber-500';
        } else {
            splitHint.textContent = `${h} → Human Agent · ${a} → AI Agent`;
            splitHint.className = 'text-xs text-slate-500';
        }
    }

    humanCountInput.addEventListener('input', () => {
        const h = Math.max(0, Math.min(parseInt(humanCountInput.value) || 0, d365DemoLeadCount));
        humanCountInput.value = h;
        aiCountInput.value = Math.max(0, d365DemoLeadCount - h);
        updateSplitControls();
    });

    aiCountInput.addEventListener('input', () => {
        const a = Math.max(0, Math.min(parseInt(aiCountInput.value) || 0, d365DemoLeadCount));
        aiCountInput.value = a;
        humanCountInput.value = Math.max(0, d365DemoLeadCount - a);
        updateSplitControls();
    });

    // -------------------------------------------------------------------------
    // Score
    // -------------------------------------------------------------------------

    scoreBtn.addEventListener('click', async () => {
        if (!d365CurrentEnvId) { showMessage('Please select an environment first.', true); return; }
        if (d365DemoLeadCount === 0) { showMessage('No demo leads found. Create and pull leads first.', true); return; }

        const humanCount = parseInt(humanCountInput.value) || 0;
        const aiCount    = parseInt(aiCountInput.value) || 0;
        if (humanCount + aiCount !== d365DemoLeadCount) {
            showMessage(`Human + AI counts must add up to ${d365DemoLeadCount} (your demo lead total).`, true);
            return;
        }

        scoreBtn.disabled = true;
        pushBtn.classList.add('hidden');
        checkRingcxBtn.classList.add('hidden');
        scoreResults.classList.add('hidden');
        scoreRouteStatus.textContent = `Scoring ${d365DemoLeadCount} leads...`;

        try {
            const response = await fetch('/api/d365_ringcx/leads/score', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ env_id: d365CurrentEnvId, human_count: humanCount })
            });
            const data = await response.json();
            if (response.ok) {
                scoreRouteStatus.textContent = `Scored ${data.scored} leads — ${data.human} human · ${data.ai} AI`;
                renderScoreResults(data.results);
                scoreResults.classList.remove('hidden');
                pushBtn.classList.remove('hidden');
                checkRingcxBtn.classList.remove('hidden');
                loadLeads(d365CurrentEnvId);
            } else {
                scoreRouteStatus.textContent = '';
                showMessage(data.error || 'Error scoring leads.', true);
            }
        } catch {
            scoreRouteStatus.textContent = '';
            showMessage('Network error scoring leads.', true);
        } finally {
            scoreBtn.disabled = false;
        }
    });

    function renderScoreResults(results) {
        scoreResultsBody.innerHTML = '';
        results.forEach(r => {
            const moveLabel   = MOVE_TYPE_MAP[r.movetype] || '—';
            const moveDateStr = r.movedate
                ? new Date(r.movedate).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' })
                : '—';
            const isHuman   = r.bucket === 'human';
            const scoreClass = isHuman ? 'text-green-700 bg-green-100' : 'text-purple-700 bg-purple-100';
            const routeHTML  = isHuman
                ? `<span class="inline-flex items-center gap-1.5 text-green-700"><span class="w-2 h-2 rounded-full bg-green-500 inline-block"></span>Human Agent</span>`
                : `<span class="inline-flex items-center gap-1.5 text-purple-700"><span class="w-2 h-2 rounded-full bg-purple-500 inline-block"></span>AI Agent</span>`;

            const tr = document.createElement('tr');
            tr.className = `border-b border-slate-50 ${isHuman ? 'bg-green-50/40' : 'bg-purple-50/30'}`;
            tr.innerHTML = `
                <td class="py-2 pr-4 font-medium text-slate-800">${escHtml(r.firstname)} ${escHtml(r.lastname)}</td>
                <td class="py-2 pr-4 text-slate-600 text-xs">${escHtml(moveLabel)}</td>
                <td class="py-2 pr-4 text-slate-600 text-xs">${moveDateStr}</td>
                <td class="py-2 pr-4">
                    <span class="inline-block px-2 py-0.5 rounded text-xs font-bold tabular-nums ${scoreClass}">${r.score}</span>
                </td>
                <td class="py-2 text-sm">${routeHTML}</td>
            `;
            scoreResultsBody.appendChild(tr);
        });
    }

    // -------------------------------------------------------------------------
    // Push to RingCX
    // -------------------------------------------------------------------------

    pushBtn.addEventListener('click', async () => {
        if (!d365CurrentEnvId) { showMessage('Please select an environment first.', true); return; }
        if (!humanCampaignSelect.value || !aiCampaignSelect.value) {
            showMessage('Please select and save the Human and AI campaign IDs before pushing.', true);
            return;
        }
        pushBtn.disabled = true;
        scoreRouteStatus.textContent = 'Pushing to RingCX...';
        try {
            const response = await fetch('/api/d365_ringcx/leads/push', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ env_id: d365CurrentEnvId })
            });
            const data = await response.json();
            if (response.status === 501) {
                scoreRouteStatus.textContent = 'Coming soon.';
                showMessage('Push to RingCX is coming soon — not yet implemented.', true);
            } else if (response.ok) {
                scoreRouteStatus.textContent = `Pushed — ${data.human || 0} to Human · ${data.ai || 0} to AI campaign.`;
                showMessage('Leads are now queued in RingCX. Switch to RingCX to see them dialling.');
            } else {
                scoreRouteStatus.textContent = '';
                showMessage(data.error || 'Error pushing leads.', true);
            }
        } catch {
            scoreRouteStatus.textContent = '';
            showMessage('Network error pushing leads.', true);
        } finally {
            pushBtn.disabled = false;
        }
    });

    checkRingcxBtn.addEventListener('click', async () => {
        checkRingcxBtn.disabled = true;
        scoreRouteStatus.textContent = 'Querying RingCX campaigns...';
        try {
            const response = await fetch(
                `/api/d365_ringcx/ringcx-campaign-leads?env_id=${encodeURIComponent(d365CurrentEnvId)}`
            );
            const data = await response.json();
            if (!response.ok) {
                scoreRouteStatus.textContent = '';
                showMessage(data.error || 'Error querying RingCX.', true);
                return;
            }
            const human = data.human || {};
            const ai    = data.ai    || {};
            const humanErr = human.error ? ` (error: ${human.error})` : '';
            const aiErr    = ai.error    ? ` (error: ${ai.error})`    : '';
            scoreRouteStatus.textContent =
                `RingCX: Human campaign ${human.campaign_id} → ${human.total ?? '?'} leads${humanErr} · ` +
                `AI campaign ${ai.campaign_id} → ${ai.total ?? '?'} leads${aiErr}`;

            // Log sample leads for debugging
            console.log('RingCX human campaign leads:', human.leads);
            console.log('RingCX AI campaign leads:', ai.leads);
        } catch {
            scoreRouteStatus.textContent = '';
            showMessage('Network error querying RingCX.', true);
        } finally {
            checkRingcxBtn.disabled = false;
        }
    });

    // -------------------------------------------------------------------------
    // Live Call Feed — SSE + outcomes
    // -------------------------------------------------------------------------

    let liveFeedEventSource = null;

    function dispositionBadge(disposition) {
        const d = (disposition || '').toUpperCase();
        if (d === 'HOT')  return `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold bg-green-100 text-green-800">${disposition}</span>`;
        if (d === 'WARM') return `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold bg-amber-100 text-amber-800">${disposition}</span>`;
        return `<span class="inline-block px-2 py-0.5 rounded text-xs font-bold bg-slate-100 text-slate-600">${disposition || '—'}</span>`;
    }

    function campaignBadge(campaign) {
        if (campaign === 'human') return `<span class="text-xs font-medium text-blue-700">Human</span>`;
        if (campaign === 'ai')    return `<span class="text-xs font-medium text-purple-700">AI</span>`;
        return `<span class="text-xs text-slate-400">${campaign || '—'}</span>`;
    }

    function formatTime(iso) {
        if (!iso) return '—';
        try {
            return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        } catch { return iso; }
    }

    function renderFeedRow(event, prepend = false) {
        const isHot = (event.disposition || '').toUpperCase() === 'HOT';
        const rowId = `feed-row-${event.leadid}`;

        // Remove existing row for this lead if re-rendering
        const existing = document.getElementById(rowId);
        if (existing) existing.remove();

        const tr = document.createElement('tr');
        tr.id = rowId;
        tr.className = `border-b border-slate-100 ${isHot ? 'bg-green-50' : ''}`;
        tr.innerHTML = `
            <td class="py-2 pr-3 font-medium text-slate-800">${event.firstname} ${event.lastname}</td>
            <td class="py-2 pr-3 text-slate-500 font-mono text-xs">${event.phone || '—'}</td>
            <td class="py-2 pr-3">${campaignBadge(event.campaign)}</td>
            <td class="py-2 pr-3">${dispositionBadge(event.disposition)}</td>
            <td class="py-2 pr-3 text-xs text-slate-400">${formatTime(event.disposed_at)}</td>
            <td class="py-2">
                ${event.booking_pushed
                    ? `<span class="text-xs font-medium text-green-700">✓ Sent to Booking</span>`
                    : `<button data-leadid="${event.leadid}"
                            class="feed-book-btn ${isHot ? 'bg-green-600 hover:bg-green-700' : 'bg-slate-500 hover:bg-slate-600'} text-white text-xs font-semibold py-1 px-2 rounded transition mr-1 disabled:opacity-50">
                            Push to Booking
                        </button>
                        <button data-leadid="${event.leadid}"
                            class="feed-dismiss-btn text-xs text-slate-400 hover:text-slate-600 transition py-1 px-2">
                            Dismiss
                        </button>`
                }
            </td>`;

        if (prepend && liveFeedBody.firstChild) {
            liveFeedBody.insertBefore(tr, liveFeedBody.firstChild);
        } else {
            liveFeedBody.appendChild(tr);
        }

        liveFeedEmpty.classList.add('hidden');
        liveFeedTableWrap.classList.remove('hidden');
    }

    function connectLiveFeed(envId) {
        if (liveFeedEventSource) {
            liveFeedEventSource.close();
            liveFeedEventSource = null;
        }
        if (!envId) return;

        liveFeedDot.className   = 'inline-block w-2 h-2 rounded-full bg-amber-400';
        liveFeedLabel.textContent = 'connecting…';

        const es = new EventSource(`/api/d365_ringcx/live-feed?env_id=${encodeURIComponent(envId)}`);
        liveFeedEventSource = es;

        es.onopen = () => {
            liveFeedDot.className    = 'inline-block w-2 h-2 rounded-full bg-green-500';
            liveFeedLabel.textContent = 'live';
        };

        es.onmessage = (e) => {
            try {
                const event = JSON.parse(e.data);
                renderFeedRow(event, true);   // prepend — newest at top
                loadOutcomeSummary();
            } catch { /* ignore malformed */ }
        };

        es.onerror = () => {
            liveFeedDot.className    = 'inline-block w-2 h-2 rounded-full bg-red-400';
            liveFeedLabel.textContent = 'reconnecting…';
        };
    }

    async function loadOutcomes() {
        if (!d365CurrentEnvId) {
            outcomesStatus.textContent = 'Select an environment first.';
            return;
        }
        outcomesStatus.textContent = 'Loading…';
        try {
            const response = await fetch(`/api/d365_ringcx/outcomes?env_id=${encodeURIComponent(d365CurrentEnvId)}`);
            const data = await response.json();
            if (!response.ok) {
                outcomesStatus.textContent = data.error || 'Failed to load outcomes.';
                return;
            }
            // Summary counters
            outcomesTotal.textContent  = data.total   ?? '—';
            outcomesCalled.textContent = data.called  ?? '—';
            outcomesHot.textContent    = data.hot     ?? '—';
            outcomesNotHot.textContent = data.not_hot ?? '—';
            outcomesStatus.textContent = '';

            // Render disposed leads (clear first to avoid duplicates on refresh)
            liveFeedBody.innerHTML = '';
            if (data.leads && data.leads.length > 0) {
                data.leads.forEach(l => renderFeedRow(l));
                liveFeedEmpty.classList.add('hidden');
                liveFeedTableWrap.classList.remove('hidden');
            } else {
                liveFeedEmpty.classList.remove('hidden');
                liveFeedTableWrap.classList.add('hidden');
            }
        } catch {
            outcomesStatus.textContent = 'Network error loading outcomes.';
        }
    }

    async function loadOutcomeSummary() {
        if (!d365CurrentEnvId) return;
        try {
            const response = await fetch(`/api/d365_ringcx/outcomes?env_id=${encodeURIComponent(d365CurrentEnvId)}`);
            const data = await response.json();
            if (response.ok) {
                outcomesTotal.textContent  = data.total   ?? '—';
                outcomesCalled.textContent = data.called  ?? '—';
                outcomesHot.textContent    = data.hot     ?? '—';
                outcomesNotHot.textContent = data.not_hot ?? '—';
            }
        } catch { /* silent */ }
    }

    // Push to Booking / Dismiss — delegated to tbody
    liveFeedBody.addEventListener('click', async (e) => {
        const bookBtn    = e.target.closest('.feed-book-btn');
        const dismissBtn = e.target.closest('.feed-dismiss-btn');

        if (bookBtn) {
            const leadid = bookBtn.dataset.leadid;
            bookBtn.disabled = true;
            try {
                const resp = await fetch('/api/d365_ringcx/leads/push-booking', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ env_id: d365CurrentEnvId, leadid }),
                });
                const result = await resp.json();
                if (resp.ok) {
                    // Replace the action cell with a confirmation badge
                    const row = document.getElementById(`feed-row-${leadid}`);
                    if (row) {
                        const actCell = row.querySelector('td:last-child');
                        actCell.innerHTML = `<span class="text-xs font-medium text-green-700">✓ Sent to Booking</span>`;
                    }
                    showMessage(`Lead pushed to booking campaign.`);
                } else {
                    showMessage(result.error || 'Failed to push to booking.', true);
                    bookBtn.disabled = false;
                }
            } catch {
                showMessage('Network error.', true);
                bookBtn.disabled = false;
            }
        }

        if (dismissBtn) {
            const row = document.getElementById(`feed-row-${dismissBtn.dataset.leadid}`);
            if (row) row.remove();
            if (!liveFeedBody.querySelector('tr')) {
                liveFeedEmpty.classList.remove('hidden');
                liveFeedTableWrap.classList.add('hidden');
            }
        }
    });

    refreshOutcomesBtn.addEventListener('click', loadOutcomes);

    // -------------------------------------------------------------------------
    // Add environment modal
    // -------------------------------------------------------------------------

    function openAddEnvModal() {
        modalName.value     = '';
        modalTenant.value   = '';
        modalClientId.value = '';
        modalSecret.value   = '';
        modalUrl.value      = '';
        addEnvModal.classList.remove('hidden');
    }

    function closeAddEnvModal() {
        addEnvModal.classList.add('hidden');
    }

    addEnvBtn.addEventListener('click', openAddEnvModal);
    modalCancelBtn.addEventListener('click', closeAddEnvModal);
    modalCloseBtn.addEventListener('click', closeAddEnvModal);

    addEnvModal.addEventListener('click', (e) => {
        if (e.target === addEnvModal) closeAddEnvModal();
    });

    modalSaveBtn.addEventListener('click', async () => {
        const name     = modalName.value.trim();
        const tenant   = modalTenant.value.trim();
        const clientId = modalClientId.value.trim();
        const secret   = modalSecret.value.trim();
        const url      = modalUrl.value.trim();

        if (!name || !tenant || !clientId || !secret || !url) {
            showMessage('All fields are required.', true);
            return;
        }

        modalSaveBtn.disabled = true;
        try {
            const response = await fetch('/api/d365_ringcx/environments', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    tenant_id:     tenant,
                    client_id:     clientId,
                    client_secret: secret,
                    env_url:       url,
                })
            });
            const data = await response.json();
            if (response.ok) {
                closeAddEnvModal();
                showMessage('Environment saved.');
                loadStatus();
            } else {
                showMessage(data.error || 'Failed to save environment.', true);
            }
        } catch {
            showMessage('Network error saving environment.', true);
        } finally {
            modalSaveBtn.disabled = false;
        }
    });

    // -------------------------------------------------------------------------
    // Utility
    // -------------------------------------------------------------------------

    function escHtml(str) {
        if (str === null || str === undefined) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
});

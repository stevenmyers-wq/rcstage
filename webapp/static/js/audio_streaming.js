// audio_streaming.js
// Handles RingCX connection, active call polling, SSE transcript display.

let ringcxRefreshInterval = null;
let dialogPollInterval = null;
let eventSource = null;
const interimBubbles = {};
const REFRESH_INTERVAL_MS = 4 * 60 * 1000;
const DIALOG_POLL_INTERVAL_MS = 5000;

document.addEventListener('DOMContentLoaded', () => {
    const connectBtn = document.getElementById('ringcx-connect-btn');
    const disconnectBtn = document.getElementById('ringcx-disconnect-btn');
    const statusBadge = document.getElementById('ringcx-status-badge');
    const accountInfo = document.getElementById('ringcx-account-info');
    const mainAccountIdSpan = document.getElementById('ringcx-main-account-id');
    const subAccountSelect = document.getElementById('ringcx-sub-account-select');
    const subAccountSection = document.getElementById('ringcx-sub-account-section');
    const subAccountLoadingMsg = document.getElementById('ringcx-sub-account-loading');
    const grpcUrlSection = document.getElementById('ringcx-grpc-url-section');
    const grpcUrlDisplay = document.getElementById('grpc-url-display');
    const copyGrpcUrlBtn = document.getElementById('copy-grpc-url-btn');
    const activeDialogSelect = document.getElementById('active-dialog-select');
    const refreshDialogsBtn = document.getElementById('refresh-dialogs-btn');
    const startListeningBtn = document.getElementById('start-listening-btn');
    const stopListeningBtn = document.getElementById('stop-listening-btn');
    const sseStatus = document.getElementById('sse-status');
    const transcriptContainer = document.getElementById('transcript-container');
    const transcriptPlaceholder = document.getElementById('transcript-placeholder');
    const transcriptCallInfo = document.getElementById('transcript-call-info');
    const clearBtn = document.getElementById('clear-transcript-btn');

    if (!connectBtn) return;

    checkRingCXStatus();

    // --- Connect ---
    connectBtn.addEventListener('click', async () => {
        connectBtn.disabled = true;
        connectBtn.textContent = 'Connecting...';
        try {
            const response = await fetch('/api/audio_streaming/ringcx-token', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            const data = await response.json();
            if (response.ok) {
                setConnectedState(data.accountId);
                showMessage('Connected to RingCX successfully.');
                await loadSubAccounts();
                await loadGrpcUrl();
                startDialogPolling();
            } else if (response.status === 401) {
                showMessage('Please connect to RingCentral first via the RC Auth tab.', true);
                setDisconnectedState();
            } else {
                showMessage(data.error || 'Failed to connect to RingCX.', true);
                setDisconnectedState();
            }
        } catch {
            showMessage('Network error connecting to RingCX.', true);
            setDisconnectedState();
        }
    });

    // --- Disconnect ---
    disconnectBtn.addEventListener('click', async () => {
        try { await fetch('/api/audio_streaming/ringcx-disconnect', { method: 'POST' }); } catch {}
        stopRefreshTimer();
        stopDialogPolling();
        setDisconnectedState();
        showMessage('Disconnected from RingCX.');
    });

    // --- Page load status check ---
    async function checkRingCXStatus() {
        try {
            const response = await fetch('/api/audio_streaming/ringcx-status');
            const data = await response.json();
            if (data.connected) {
                setConnectedState(data.accountId);
                await loadSubAccounts();
                await loadGrpcUrl();
                startDialogPolling();
            } else {
                setDisconnectedState();
            }
        } catch {
            setDisconnectedState();
        }
    }

    // --- Sub-accounts ---
    async function loadSubAccounts() {
        subAccountSection.classList.remove('hidden');
        subAccountLoadingMsg.classList.remove('hidden');
        subAccountSelect.classList.add('hidden');
        try {
            const response = await fetch('/api/audio_streaming/accounts');
            const data = await response.json();
            if (response.ok && data.accounts) {
                populateSubAccounts(data.accounts);
            } else {
                subAccountLoadingMsg.textContent = data.error || 'Failed to load accounts.';
            }
        } catch {
            subAccountLoadingMsg.textContent = 'Network error loading accounts.';
        }
    }

    function populateSubAccounts(accounts) {
        subAccountSelect.innerHTML = '';
        const subAccounts = accounts.filter(a =>
            a.accountType === 'ACCOUNT' || a.mainAccount === false || a.hasOwnProperty('mainAccountId')
        );
        const displayAccounts = subAccounts.length > 0 ? subAccounts : accounts;
        if (displayAccounts.length === 0) {
            subAccountLoadingMsg.textContent = 'No accounts found.';
            return;
        }
        displayAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.accountId || account.id;
            option.textContent = `${account.accountName || account.name} (${account.accountId || account.id})`;
            subAccountSelect.appendChild(option);
        });
        subAccountLoadingMsg.classList.add('hidden');
        subAccountSelect.classList.remove('hidden');
    }

    // --- gRPC URL ---
    async function loadGrpcUrl() {
        try {
            const response = await fetch('/api/audio_streaming/grpc-service-url');
            const data = await response.json();
            if (response.ok && data.url) {
                grpcUrlDisplay.textContent = data.url;
                grpcUrlSection.classList.remove('hidden');
            }
        } catch {}
    }

    copyGrpcUrlBtn.addEventListener('click', () => {
        const url = grpcUrlDisplay.textContent;
        if (!url) return;
        navigator.clipboard.writeText(url).then(() => {
            copyGrpcUrlBtn.textContent = 'Copied!';
            setTimeout(() => { copyGrpcUrlBtn.textContent = 'Copy'; }, 2000);
        });
    });

    // --- Active dialog polling ---
    function startDialogPolling() {
        stopDialogPolling();
        loadActiveDialogs();
        dialogPollInterval = setInterval(loadActiveDialogs, DIALOG_POLL_INTERVAL_MS);
    }

    function stopDialogPolling() {
        if (dialogPollInterval) {
            clearInterval(dialogPollInterval);
            dialogPollInterval = null;
        }
    }

    async function loadActiveDialogs() {
        try {
            const response = await fetch('/api/audio_streaming/active-dialogs');
            const data = await response.json();
            if (response.ok) {
                updateDialogDropdown(data.dialogs || []);
            }
        } catch {}
    }

    function updateDialogDropdown(dialogs) {
        const currentValue = activeDialogSelect.value;
        activeDialogSelect.innerHTML = '';

        if (dialogs.length === 0) {
            activeDialogSelect.appendChild(new Option('No active calls', ''));
            startListeningBtn.disabled = true;
            return;
        }

        dialogs.forEach(d => {
            const label = `${d.ani || 'Unknown'} → ${d.dnis || 'Unknown'}`;
            const option = new Option(label, d.dialog_id);
            option.dataset.ani = d.ani || '';
            option.dataset.dnis = d.dnis || '';
            activeDialogSelect.appendChild(option);
        });

        startListeningBtn.disabled = false;

        // Re-select previous if still active
        if (currentValue && [...activeDialogSelect.options].some(o => o.value === currentValue)) {
            activeDialogSelect.value = currentValue;
        } else if (dialogs.length === 1) {
            // Auto-select if only one call
            activeDialogSelect.value = dialogs[0].dialog_id;
        }
    }

    refreshDialogsBtn.addEventListener('click', loadActiveDialogs);

    // --- Listen / Stop ---
    startListeningBtn.addEventListener('click', () => {
        const dialogId = activeDialogSelect.value;
        if (!dialogId) {
            showMessage('No active call selected.', true);
            return;
        }
        const selected = activeDialogSelect.options[activeDialogSelect.selectedIndex];
        const ani = selected.dataset.ani || '';
        const dnis = selected.dataset.dnis || '';
        startSSE(dialogId, ani, dnis);
    });

    stopListeningBtn.addEventListener('click', () => stopSSE());

    function startSSE(dialogId, ani, dnis) {
        stopSSE();
        clearTranscript();

        eventSource = new EventSource(`/api/audio_streaming/transcript-stream/${dialogId}`);

        eventSource.onopen = () => {
            sseStatus.textContent = `Listening to ${ani} → ${dnis}`;
            sseStatus.className = 'mt-2 text-xs text-green-600';
            if (transcriptCallInfo) {
                transcriptCallInfo.textContent = `${ani} → ${dnis}`;
            }
            startListeningBtn.classList.add('hidden');
            stopListeningBtn.classList.remove('hidden');
        };

        eventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'connected') return;
            handleTranscriptEvent(data);
        };

        eventSource.onerror = () => {
            sseStatus.textContent = 'Connection lost.';
            sseStatus.className = 'mt-2 text-xs text-red-500';
            stopSSE();
        };
    }

    function stopSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
        startListeningBtn.classList.remove('hidden');
        stopListeningBtn.classList.add('hidden');
        sseStatus.textContent = '';
        if (transcriptCallInfo) transcriptCallInfo.textContent = '';
    }

    // --- Transcript display ---
    function handleTranscriptEvent(data) {
        if (transcriptPlaceholder) transcriptPlaceholder.style.display = 'none';
        const isFinal = data.is_final;
        const segmentId = data.segment_id;

        if (!isFinal) {
            if (interimBubbles[segmentId]) {
                interimBubbles[segmentId].querySelector('.bubble-text').textContent = data.text;
            } else {
                const bubble = createBubble(data, false);
                interimBubbles[segmentId] = bubble;
                transcriptContainer.appendChild(bubble);
                transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
            }
        } else {
            if (interimBubbles[segmentId]) {
                const existing = interimBubbles[segmentId];
                existing.querySelector('.bubble-text').textContent = data.text;
                existing.classList.remove('opacity-60');
                existing.querySelector('.bubble-label').textContent = data.participant_name || data.participant_type;
                delete interimBubbles[segmentId];
            } else {
                const bubble = createBubble(data, true);
                transcriptContainer.appendChild(bubble);
                transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
            }
        }
    }

    function createBubble(data, isFinal) {
        const isAgent = data.participant_type === 'AGENT';
        const isContact = data.participant_type === 'CONTACT';

        const wrapper = document.createElement('div');
        wrapper.className = `flex ${isAgent ? 'justify-end' : 'justify-start'}`;
        if (!isFinal) wrapper.classList.add('opacity-60');

        const inner = document.createElement('div');
        inner.className = 'max-w-xs lg:max-w-md xl:max-w-lg';

        const label = document.createElement('div');
        label.className = `bubble-label text-xs font-medium mb-1 ${isAgent ? 'text-right text-blue-600' : 'text-left text-slate-500'}`;
        label.textContent = data.participant_name || data.participant_type;

        const bubble = document.createElement('div');
        bubble.className = `bubble-text rounded-2xl px-4 py-2 text-sm leading-relaxed ${
            isAgent
                ? 'bg-blue-600 text-white rounded-tr-sm'
                : isContact
                    ? 'bg-slate-100 text-slate-800 rounded-tl-sm'
                    : 'bg-purple-100 text-purple-800 rounded-tl-sm'
        }`;
        bubble.textContent = data.text;

        inner.appendChild(label);
        inner.appendChild(bubble);
        wrapper.appendChild(inner);
        return wrapper;
    }

    function clearTranscript() {
        Array.from(transcriptContainer.children).forEach(child => {
            if (child.id !== 'transcript-placeholder') child.remove();
        });
        if (transcriptPlaceholder) transcriptPlaceholder.style.display = '';
        Object.keys(interimBubbles).forEach(k => delete interimBubbles[k]);
    }

    clearBtn.addEventListener('click', clearTranscript);

    // --- Token refresh ---
    function startRefreshTimer() {
        stopRefreshTimer();
        ringcxRefreshInterval = setInterval(async () => {
            try {
                const response = await fetch('/api/audio_streaming/ringcx-refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                if (!response.ok) {
                    stopRefreshTimer();
                    setDisconnectedState();
                    showMessage('RingCX session expired. Please reconnect.', true);
                }
            } catch {
                console.warn('RingCX token refresh attempt failed.');
            }
        }, REFRESH_INTERVAL_MS);
    }

    function stopRefreshTimer() {
        if (ringcxRefreshInterval) {
            clearInterval(ringcxRefreshInterval);
            ringcxRefreshInterval = null;
        }
    }

    // --- UI state helpers ---
    function setConnectedState(accountId) {
        statusBadge.textContent = 'Connected';
        statusBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800';
        connectBtn.classList.add('hidden');
        disconnectBtn.classList.remove('hidden');
        accountInfo.classList.remove('hidden');
        mainAccountIdSpan.textContent = accountId || 'Unknown';
        startRefreshTimer();
    }

    function setDisconnectedState() {
        statusBadge.textContent = 'Disconnected';
        statusBadge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-600';
        connectBtn.disabled = false;
        connectBtn.textContent = 'Connect to RingCX';
        connectBtn.classList.remove('hidden');
        disconnectBtn.classList.add('hidden');
        accountInfo.classList.add('hidden');
        subAccountSection.classList.add('hidden');
        grpcUrlSection.classList.add('hidden');
        mainAccountIdSpan.textContent = '';
        subAccountSelect.innerHTML = '';
        grpcUrlDisplay.textContent = '';
        activeDialogSelect.innerHTML = '<option value="">No active calls</option>';
        stopSSE();
    }
});

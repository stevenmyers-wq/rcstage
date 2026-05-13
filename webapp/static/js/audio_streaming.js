// audio_streaming.js
// Handles RingCX token exchange, auto-refresh, account loading, SSE transcript display.

let ringcxRefreshInterval = null;
let eventSource = null;
// Tracks interim transcript bubbles by segment_id so we can update them in place
const interimBubbles = {};

const REFRESH_INTERVAL_MS = 4 * 60 * 1000; // 4 minutes

document.addEventListener('DOMContentLoaded', () => {
    const connectBtn = document.getElementById('ringcx-connect-btn');
    const disconnectBtn = document.getElementById('ringcx-disconnect-btn');
    const statusBadge = document.getElementById('ringcx-status-badge');
    const accountInfo = document.getElementById('ringcx-account-info');
    const mainAccountIdSpan = document.getElementById('ringcx-main-account-id');
    const subAccountSelect = document.getElementById('ringcx-sub-account-select');
    const subAccountSection = document.getElementById('ringcx-sub-account-section');
    const subAccountLoadingMsg = document.getElementById('ringcx-sub-account-loading');
    const dialogIdInput = document.getElementById('dialog-id-input');
    const startListeningBtn = document.getElementById('start-listening-btn');
    const stopListeningBtn = document.getElementById('stop-listening-btn');
    const sseStatus = document.getElementById('sse-status');
    const transcriptContainer = document.getElementById('transcript-container');
    const transcriptPlaceholder = document.getElementById('transcript-placeholder');
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
            } else if (response.status === 401) {
                showMessage('Please connect to RingCentral first via the RC Auth tab.', true);
                setDisconnectedState();
            } else {
                showMessage(data.error || 'Failed to connect to RingCX.', true);
                setDisconnectedState();
            }
        } catch (err) {
            showMessage('Network error connecting to RingCX.', true);
            setDisconnectedState();
        }
    });

    // --- Disconnect ---
    disconnectBtn.addEventListener('click', async () => {
        try { await fetch('/api/audio_streaming/ringcx-disconnect', { method: 'POST' }); } catch {}
        stopRefreshTimer();
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
            } else {
                setDisconnectedState();
            }
        } catch {
            setDisconnectedState();
        }
    }

    // --- Load sub-accounts ---
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

    // --- SSE: Start listening ---
    startListeningBtn.addEventListener('click', () => {
        const dialogId = dialogIdInput.value.trim();
        if (!dialogId) {
            showMessage('Please enter a Dialog ID.', true);
            return;
        }
        startSSE(dialogId);
    });

    stopListeningBtn.addEventListener('click', () => {
        stopSSE();
    });

    function startSSE(dialogId) {
        stopSSE(); // Close any existing connection

        eventSource = new EventSource(`/api/audio_streaming/transcript-stream/${dialogId}`);

        eventSource.onopen = () => {
            sseStatus.textContent = `Listening to dialog: ${dialogId}`;
            sseStatus.className = 'mt-2 text-xs text-green-600';
            startListeningBtn.classList.add('hidden');
            stopListeningBtn.classList.remove('hidden');
            dialogIdInput.disabled = true;
        };

        eventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'connected') return; // Initial confirmation, ignore
            handleTranscriptEvent(data);
        };

        eventSource.onerror = () => {
            sseStatus.textContent = 'Connection lost. Try listening again.';
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
        dialogIdInput.disabled = false;
        sseStatus.textContent = '';
    }

    // --- Transcript display ---
    function handleTranscriptEvent(data) {
        if (transcriptPlaceholder) transcriptPlaceholder.style.display = 'none';

        const isAgent = data.participant_type === 'AGENT';
        const isFinal = data.is_final;
        const segmentId = data.segment_id;

        if (!isFinal) {
            // Interim result — update existing bubble or create new one
            if (interimBubbles[segmentId]) {
                interimBubbles[segmentId].querySelector('.bubble-text').textContent = data.text;
            } else {
                const bubble = createBubble(data, false);
                interimBubbles[segmentId] = bubble;
                transcriptContainer.appendChild(bubble);
                transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
            }
        } else {
            // Final result — replace interim bubble if exists, otherwise create new
            if (interimBubbles[segmentId]) {
                const existing = interimBubbles[segmentId];
                existing.querySelector('.bubble-text').textContent = data.text;
                existing.classList.remove('opacity-60');
                existing.querySelector('.bubble-label').textContent =
                    `${data.participant_name || data.participant_type}`;
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
        inner.className = `max-w-xs lg:max-w-md xl:max-w-lg`;

        const label = document.createElement('div');
        label.className = `bubble-label text-xs font-medium mb-1 ${isAgent ? 'text-right text-blue-600' : 'text-left text-slate-500'}`;
        label.textContent = data.participant_name || data.participant_type;

        const bubble = document.createElement('div');
        bubble.className = `bubble-text rounded-2xl px-4 py-2 text-sm leading-relaxed ${
            isAgent
                ? 'bg-blue-600 text-white rounded-tr-sm'
                : isContact
                    ? 'bg-slate-100 text-slate-800 rounded-tl-sm'
                    : 'bg-purple-100 text-purple-800 rounded-tl-sm' // BOT
        }`;
        bubble.textContent = data.text;

        inner.appendChild(label);
        inner.appendChild(bubble);
        wrapper.appendChild(inner);
        return wrapper;
    }

    // --- Clear transcript ---
    clearBtn.addEventListener('click', () => {
        // Remove all bubbles but keep the placeholder
        Array.from(transcriptContainer.children).forEach(child => {
            if (child.id !== 'transcript-placeholder') child.remove();
        });
        if (transcriptPlaceholder) transcriptPlaceholder.style.display = '';
        Object.keys(interimBubbles).forEach(k => delete interimBubbles[k]);
    });

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
        mainAccountIdSpan.textContent = '';
        subAccountSelect.innerHTML = '';
        stopSSE();
    }
});

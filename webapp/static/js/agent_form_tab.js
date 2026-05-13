// agent_form_tab.js
// Handles active call polling, transcript mirror, and iframe loading for the Agent Form tab.

let afEventSource = null;
let afPollInterval = null;
let afInterimBubbles = {};
const AF_POLL_INTERVAL_MS = 5000;

document.addEventListener('DOMContentLoaded', () => {
    const dialogSelect = document.getElementById('af-dialog-select');
    const refreshBtn = document.getElementById('af-refresh-btn');
    const loadBtn = document.getElementById('af-load-btn');
    const iframeUrlDisplay = document.getElementById('af-iframe-url');
    const copyUrlBtn = document.getElementById('af-copy-url-btn');
    const callInfo = document.getElementById('af-call-info');
    const transcriptContainer = document.getElementById('af-transcript-container');
    const transcriptPlaceholder = document.getElementById('af-transcript-placeholder');
    const clearBtn = document.getElementById('af-clear-btn');
    const iframePlaceholder = document.getElementById('af-iframe-placeholder');
    const formIframe = document.getElementById('af-form-iframe');

    if (!dialogSelect) return;

    // Start polling active dialogs immediately
    loadActiveDialogs();
    afPollInterval = setInterval(loadActiveDialogs, AF_POLL_INTERVAL_MS);

    // Update iframe URL when dialog selection changes
    dialogSelect.addEventListener('change', () => {
        updateIframeUrl();
    });

    refreshBtn.addEventListener('click', loadActiveDialogs);

    loadBtn.addEventListener('click', () => {
        const dialogId = dialogSelect.value;
        if (!dialogId) {
            showMessage('No active call selected.', true);
            return;
        }
        loadForm(dialogId);
    });

    copyUrlBtn.addEventListener('click', () => {
        const url = iframeUrlDisplay.textContent;
        if (!url || url === 'Select a call to generate URL') return;
        navigator.clipboard.writeText(url).then(() => {
            copyUrlBtn.textContent = 'Copied!';
            setTimeout(() => { copyUrlBtn.textContent = 'Copy'; }, 2000);
        });
    });

    clearBtn.addEventListener('click', clearTranscript);

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
        const currentValue = dialogSelect.value;
        dialogSelect.innerHTML = '';

        if (dialogs.length === 0) {
            dialogSelect.appendChild(new Option('No active calls — make sure RingCX Streaming tab is connected', ''));
            iframeUrlDisplay.textContent = 'Select a call to generate URL';
            return;
        }

        dialogs.forEach(d => {
            const label = `${d.ani || 'Unknown'} → ${d.dnis || 'Unknown'}`;
            const option = new Option(label, d.dialog_id);
            option.dataset.ani = d.ani || '';
            option.dataset.dnis = d.dnis || '';
            dialogSelect.appendChild(option);
        });

        // Re-select previous or auto-select single call
        if (currentValue && [...dialogSelect.options].some(o => o.value === currentValue)) {
            dialogSelect.value = currentValue;
        } else if (dialogs.length === 1) {
            dialogSelect.value = dialogs[0].dialog_id;
        }

        updateIframeUrl();
    }

    function updateIframeUrl() {
        const dialogId = dialogSelect.value;
        if (!dialogId) {
            iframeUrlDisplay.textContent = 'Select a call to generate URL';
            return;
        }
        const selected = dialogSelect.options[dialogSelect.selectedIndex];
        const ani = selected.dataset.ani || '';
        const base = window.location.origin;
        const url = `${base}/agent-form/?dialog_id=${encodeURIComponent(dialogId)}&ani=${encodeURIComponent(ani)}`;
        iframeUrlDisplay.textContent = url;
    }

    function loadForm(dialogId) {
        const selected = dialogSelect.options[dialogSelect.selectedIndex];
        const ani = selected.dataset.ani || '';
        const dnis = selected.dataset.dnis || '';

        // Update call info
        if (callInfo) callInfo.textContent = `${ani} → ${dnis}`;

        // Load iframe
        const url = `/agent-form/?dialog_id=${encodeURIComponent(dialogId)}&ani=${encodeURIComponent(ani)}`;
        formIframe.src = url;
        formIframe.classList.remove('hidden');
        iframePlaceholder.classList.add('hidden');

        // Start transcript mirror SSE
        startTranscriptMirror(dialogId, ani, dnis);
    }

    function startTranscriptMirror(dialogId, ani, dnis) {
        if (afEventSource) {
            afEventSource.close();
            afEventSource = null;
        }
        clearTranscript();

        afEventSource = new EventSource(`/api/audio_streaming/transcript-stream/${dialogId}`);

        afEventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            if (data.type === 'connected') return;
            handleTranscriptEvent(data);
        };

        afEventSource.onerror = () => {
            if (afEventSource) {
                afEventSource.close();
                afEventSource = null;
            }
        };
    }

    function handleTranscriptEvent(data) {
        if (transcriptPlaceholder) transcriptPlaceholder.style.display = 'none';
        const isAgent = data.participant_type === 'AGENT';
        const segmentId = data.segment_id;

        if (!data.is_final) {
            if (afInterimBubbles[segmentId]) {
                afInterimBubbles[segmentId].querySelector('.af-bubble-text').textContent = data.text;
            } else {
                const bubble = createBubble(data, false);
                afInterimBubbles[segmentId] = bubble;
                transcriptContainer.appendChild(bubble);
                transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
            }
        } else {
            if (afInterimBubbles[segmentId]) {
                afInterimBubbles[segmentId].querySelector('.af-bubble-text').textContent = data.text;
                afInterimBubbles[segmentId].classList.remove('opacity-50');
                delete afInterimBubbles[segmentId];
            } else {
                transcriptContainer.appendChild(createBubble(data, true));
                transcriptContainer.scrollTop = transcriptContainer.scrollHeight;
            }
        }
    }

    function createBubble(data, isFinal) {
        const isAgent = data.participant_type === 'AGENT';
        const wrapper = document.createElement('div');
        wrapper.className = `flex ${isAgent ? 'justify-end' : 'justify-start'}`;
        if (!isFinal) wrapper.classList.add('opacity-50');

        const inner = document.createElement('div');
        inner.className = 'max-w-xs';

        const label = document.createElement('div');
        label.className = `text-xs font-medium mb-0.5 ${isAgent ? 'text-right text-blue-500' : 'text-left text-slate-400'}`;
        label.textContent = data.participant_name || data.participant_type;

        const bubble = document.createElement('div');
        bubble.className = `af-bubble-text rounded-xl px-3 py-1.5 text-xs leading-relaxed ${
            isAgent ? 'bg-blue-500 text-white' : 'bg-slate-100 text-slate-800'
        }`;
        bubble.textContent = data.text;

        inner.appendChild(label);
        inner.appendChild(bubble);
        wrapper.appendChild(inner);
        return wrapper;
    }

    function clearTranscript() {
        Array.from(transcriptContainer.children).forEach(child => {
            if (child.id !== 'af-transcript-placeholder') child.remove();
        });
        if (transcriptPlaceholder) transcriptPlaceholder.style.display = '';
        Object.keys(afInterimBubbles).forEach(k => delete afInterimBubbles[k]);
    }
});

// As-Built Documentation — frontend
(function () {
    const root = document.getElementById('as-built-tab');
    if (!root) return;

    const sectionsEl = document.getElementById('ab-sections');
    const previewEl = document.getElementById('ab-preview');
    const previewEmpty = document.getElementById('ab-preview-empty');
    const statusEl = document.getElementById('ab-status');
    const genBtn = document.getElementById('ab-generate');
    const pdfBtn = document.getElementById('ab-export-pdf');
    const wordBtn = document.getElementById('ab-export-word');
    const xlsxBtn = document.getElementById('ab-export-xlsx');
    const nameEl = document.getElementById('ab-customer-name');
    const detailEl = document.getElementById('ab-detail-level');
    const historyCard = document.getElementById('ab-history-card');
    const historyList = document.getElementById('ab-history-list');
    const historyRefresh = document.getElementById('ab-history-refresh');

    function setStatus(msg, isError) {
        if (!msg) { statusEl.classList.add('hidden'); return; }
        statusEl.textContent = msg;
        statusEl.classList.remove('hidden');
        statusEl.classList.toggle('text-rose-500', !!isError);
        statusEl.classList.toggle('text-slate-500', !isError);
    }

    function renderCatalog(sections) {
        sectionsEl.innerHTML = '';
        sections.forEach(sec => {
            const row = document.createElement('div');
            row.className = 'ab-section-row';

            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.className = 'ab-cb h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500';
            cb.dataset.key = sec.key;
            cb.checked = true;
            if (sec.always) { cb.checked = true; cb.disabled = true; }

            const labelWrap = document.createElement('div');
            labelWrap.className = 'flex-1 min-w-0';
            labelWrap.innerHTML =
                '<div class="text-sm font-bold text-slate-700 dark:text-slate-200">' + sec.label +
                (sec.always ? ' <span class="text-[10px] font-bold text-blue-500 uppercase">always</span>' : '') +
                '</div>' +
                '<div class="text-xs text-slate-400 truncate" title="' + (sec.description || '') + '">' + (sec.description || '') + '</div>';

            row.appendChild(cb);
            row.appendChild(labelWrap);
            sectionsEl.appendChild(row);
        });
    }

    function collectSelections() {
        const detail = detailEl ? detailEl.value : 'standard';
        const out = [];
        sectionsEl.querySelectorAll('.ab-cb').forEach(cb => {
            if (cb.checked) out.push({ key: cb.dataset.key, detail: detail });
        });
        return out;
    }

    async function loadCatalog() {
        try {
            const res = await fetch('/api/as_built/catalog');
            const data = await res.json();
            if (data.success) {
                renderCatalog(data.sections || []);
            } else {
                sectionsEl.innerHTML = '<div class="text-sm text-rose-500 py-4 text-center">' + (data.error || 'Failed to load sections.') + '</div>';
            }
        } catch (e) {
            sectionsEl.innerHTML = '<div class="text-sm text-rose-500 py-4 text-center">Network error loading sections.</div>';
        }
    }

    function resetGenBtn() {
        genBtn.disabled = false;
        genBtn.textContent = 'Generate Document';
    }

    // Collection runs in a background thread on the server; the browser starts a
    // job and polls for it. Each request is short, so a large account no longer
    // holds one long connection open (which timed out and surfaced as a bare
    // "Network error during generation."). Transient poll failures are retried
    // rather than aborting the whole run, because the work continues server-side.
    let pollTimer = null;
    let pollMisses = 0;
    const POLL_INTERVAL_MS = 2000;
    const MAX_POLL_MISSES = 10;  // ~30s of consecutive network hiccups

    async function pollStatus(taskId) {
        try {
            const res = await fetch('/api/as_built/generate/status?task_id=' + encodeURIComponent(taskId));
            let data = {};
            try { data = await res.json(); } catch (e) {}

            if (res.status === 404) {
                setStatus(data.error || 'Generation task expired — please try again.', true);
                resetGenBtn();
                return;
            }
            if (data.status === 'completed') {
                previewEl.innerHTML = data.document;
                previewEl.classList.remove('hidden');
                previewEmpty.classList.add('hidden');
                pdfBtn.disabled = false; wordBtn.disabled = false; xlsxBtn.disabled = false;
                if (data.section_errors && data.section_errors.length) {
                    setStatus(data.section_errors.length + ' section(s) had errors — see the document.', true);
                } else {
                    setStatus('Document generated. Download below.');
                }
                resetGenBtn();
                loadHistory();  // the new document is now saved — refresh the list
                return;
            }
            if (data.status === 'error' || (!res.ok && data.success === false)) {
                setStatus(data.error || 'Generation failed.', true);
                resetGenBtn();
                return;
            }
            // Still running.
            pollMisses = 0;
            setStatus(data.message || 'Collecting configuration from the account…');
            pollTimer = setTimeout(() => pollStatus(taskId), POLL_INTERVAL_MS);
        } catch (e) {
            // Transient network blip — keep polling; the job runs server-side.
            pollMisses += 1;
            if (pollMisses > MAX_POLL_MISSES) {
                setStatus('Lost contact with the server during generation.', true);
                resetGenBtn();
                return;
            }
            setStatus('Working… (waiting for the server)');
            pollTimer = setTimeout(() => pollStatus(taskId), POLL_INTERVAL_MS + 1000);
        }
    }

    async function generate() {
        const selections = collectSelections();
        if (!selections.length) { setStatus('Select at least one section.', true); return; }
        if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
        pollMisses = 0;
        genBtn.disabled = true;
        genBtn.textContent = 'Generating…';
        setStatus('Starting generation…');
        pdfBtn.disabled = true; wordBtn.disabled = true; xlsxBtn.disabled = true;
        try {
            const res = await fetch('/api/as_built/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sections: selections, customer_name: nameEl.value.trim() })
            });
            const data = await res.json();
            if (res.ok && data.success && data.task_id) {
                pollStatus(data.task_id);
            } else {
                setStatus(data.error || 'Generation failed.', true);
                resetGenBtn();
            }
        } catch (e) {
            setStatus('Network error starting generation.', true);
            resetGenBtn();
        }
    }

    async function exportDoc(fmt) {
        const btn = fmt === 'pdf' ? pdfBtn : (fmt === 'xlsx' ? xlsxBtn : wordBtn);
        const extByFmt = { pdf: 'pdf', word: 'doc', xlsx: 'xlsx' };
        const orig = btn.textContent;
        btn.disabled = true; btn.textContent = 'Preparing…';
        try {
            const res = await fetch('/api/as_built/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ format: fmt })
            });
            if (!res.ok) {
                let msg = 'Export failed.';
                try { msg = (await res.json()).error || msg; } catch (e) {}
                setStatus(msg, true);
                return;
            }
            const blob = await res.blob();
            const cd = res.headers.get('Content-Disposition') || '';
            const match = cd.match(/filename="?([^"]+)"?/);
            const filename = match ? match[1] : ('As_Built.' + (extByFmt[fmt] || 'pdf'));
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            setStatus('Downloaded ' + filename + '.');
        } catch (e) {
            setStatus('Network error during export.', true);
        } finally {
            btn.disabled = false; btn.textContent = orig;
        }
    }

    // ---- Recent As-Builts (durable storage, 7-day retention) ----------------
    function fmtWhen(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d)) return '';
        return d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    }

    function renderHistory(items) {
        historyList.innerHTML = '';
        if (!items.length) {
            historyList.innerHTML = '<div class="text-xs text-slate-400 py-2 text-center">No documents in the last 7 days.</div>';
            return;
        }
        items.forEach(it => {
            const row = document.createElement('div');
            row.className = 'ab-section-row';
            const info = document.createElement('div');
            info.className = 'flex-1 min-w-0';
            const name = it.customer_name || it.account_name || 'As-Built';
            info.innerHTML =
                '<div class="text-sm font-bold text-slate-700 dark:text-slate-200 truncate" title="' + name + '">' + name + '</div>' +
                '<div class="text-[11px] text-slate-400">' + fmtWhen(it.created_at) + '</div>';
            const load = document.createElement('button');
            load.className = 'text-xs font-bold text-blue-600 hover:text-blue-700 shrink-0';
            load.textContent = 'Load';
            load.addEventListener('click', () => loadHistoryDoc(it.task_id, load));
            row.appendChild(info);
            row.appendChild(load);
            historyList.appendChild(row);
        });
    }

    async function loadHistory() {
        try {
            const res = await fetch('/api/as_built/history');
            const data = await res.json();
            if (!data.success || !data.enabled) { historyCard.classList.add('hidden'); return; }
            historyCard.classList.remove('hidden');
            renderHistory(data.items || []);
        } catch (e) {
            historyCard.classList.add('hidden');
        }
    }

    async function loadHistoryDoc(taskId, btn) {
        const orig = btn.textContent;
        btn.disabled = true; btn.textContent = 'Loading…';
        setStatus('Loading saved document…');
        try {
            const res = await fetch('/api/as_built/history/load?task_id=' + encodeURIComponent(taskId));
            const data = await res.json();
            if (res.ok && data.success) {
                previewEl.innerHTML = data.document;
                previewEl.classList.remove('hidden');
                previewEmpty.classList.add('hidden');
                pdfBtn.disabled = false; wordBtn.disabled = false; xlsxBtn.disabled = false;
                if (data.section_errors && data.section_errors.length) {
                    setStatus(data.section_errors.length + ' section(s) had errors — see the document.', true);
                } else {
                    setStatus('Loaded saved document. Download below.');
                }
            } else {
                setStatus(data.error || 'Could not load that document.', true);
                loadHistory();
            }
        } catch (e) {
            setStatus('Network error loading document.', true);
        } finally {
            btn.disabled = false; btn.textContent = orig;
        }
    }

    if (historyRefresh) historyRefresh.addEventListener('click', loadHistory);

    document.getElementById('ab-select-all').addEventListener('click', () => {
        sectionsEl.querySelectorAll('.ab-cb').forEach(cb => { if (!cb.disabled) cb.checked = true; });
    });
    document.getElementById('ab-select-none').addEventListener('click', () => {
        sectionsEl.querySelectorAll('.ab-cb').forEach(cb => { if (!cb.disabled) cb.checked = false; });
    });
    genBtn.addEventListener('click', generate);
    pdfBtn.addEventListener('click', () => exportDoc('pdf'));
    wordBtn.addEventListener('click', () => exportDoc('word'));
    xlsxBtn.addEventListener('click', () => exportDoc('xlsx'));

    loadCatalog();
    loadHistory();
})();

// webapp/static/js/account_migration.js
document.addEventListener('DOMContentLoaded', () => {
    const btnExport = document.getElementById('btn-start-export');
    const btnAudit = document.getElementById('btn-start-audit');
    const btnImport = document.getElementById('btn-start-import');
    const fileInput = document.getElementById('import-zip-file');
    const filenameDisplay = document.getElementById('import-filename');
    
    const modal = document.getElementById('migration-modal-progress');
    const progTitle = document.getElementById('mig-progress-title');
    const progBar = document.getElementById('mig-progress-bar');
    const progPct = document.getElementById('mig-progress-pct');
    const progMsg = document.getElementById('mig-progress-msg');
    const btnClose = document.getElementById('mig-progress-close-btn');
    const btnStop = document.getElementById('mig-progress-stop-btn');
    const resultBtn = document.getElementById('mig-result-btn');

    let migResultRows = null;
    UCResults.attachButton(resultBtn, () => migResultRows, 'Account_Migration_Results', 'Results');

    let pollInterval = null;
    // Set to the current import's task_id while an import runs (null otherwise).
    // Export is read-only, so no Stop is offered for it.
    let currentImportTaskId = null;

    if (btnStop) {
        btnStop.addEventListener('click', async () => {
            if (!currentImportTaskId) return;
            btnStop.disabled = true;
            btnStop.textContent = 'Stopping...';
            progMsg.textContent = 'Stop requested — objects already created remain; the rest will be skipped.';
            try {
                await fetch(`/api/migration/cancel?task_id=${currentImportTaskId}`, { method: 'POST' });
            } catch (e) { /* worker also polls the flag */ }
        });
    }

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            filenameDisplay.textContent = e.target.files[0].name;
            btnImport.disabled = false;
        } else {
            filenameDisplay.textContent = 'Select ZIP Archive';
            btnImport.disabled = true;
        }
    });

    function openProgressModal(title, showStop = false) {
        progTitle.textContent = title;
        progBar.style.width = '0%';
        progBar.className = 'bg-blue-600 h-3 rounded-full transition-all duration-300';
        progPct.textContent = '0%';
        progMsg.textContent = 'Starting...';
        progMsg.className = 'text-xs font-mono text-blue-600 text-left mt-2 truncate';
        btnClose.classList.add('hidden');
        if (btnStop) {
            btnStop.classList.toggle('hidden', !showStop);
            btnStop.disabled = false;
            btnStop.textContent = '■ Stop';
        }
        migResultRows = null;
        resultBtn.classList.add('hidden');

        modal.classList.remove('hidden');
        setTimeout(() => modal.classList.remove('opacity-0'), 10);
    }

    function closeProgressModal() {
        modal.classList.add('opacity-0');
        setTimeout(() => modal.classList.add('hidden'), 200);
        if (pollInterval) clearInterval(pollInterval);
    }

    function setActionsDisabled(disabled) {
        btnExport.disabled = disabled;
        btnImport.disabled = disabled;
        if (btnAudit) btnAudit.disabled = disabled;
    }

    btnClose.addEventListener('click', closeProgressModal);

    function showMigResults(results) {
        if (Array.isArray(results) && results.length) {
            migResultRows = results;
            resultBtn.classList.remove('hidden');
        }
    }

    function startPolling(taskId, onSuccess) {
        pollInterval = setInterval(async () => {
            try {
                const res = await fetch(`/api/migration/status?task_id=${taskId}`);
                if (!res.ok) return;
                
                const data = await res.json();
                
                if (data.total > 0) {
                    const pct = Math.round((data.current / data.total) * 100);
                    progBar.style.width = `${pct}%`;
                    progPct.textContent = `${pct}%`;
                    progMsg.textContent = data.message;
                }

                if (data.status === 'completed') {
                    clearInterval(pollInterval);
                    currentImportTaskId = null;
                    if (btnStop) btnStop.classList.add('hidden');
                    progBar.classList.replace('bg-blue-600', 'bg-green-500');
                    progTitle.textContent = 'Success!';
                    progMsg.classList.replace('text-blue-600', 'text-green-600');
                    btnClose.classList.remove('hidden');
                    showMigResults(data.results);
                    setActionsDisabled(false);
                    if (onSuccess) onSuccess();
                    loadHistory();  // a new artifact was stored — refresh the list
                } else if (data.status === 'cancelled') {
                    clearInterval(pollInterval);
                    currentImportTaskId = null;
                    if (btnStop) btnStop.classList.add('hidden');
                    progBar.classList.replace('bg-blue-600', 'bg-amber-500');
                    progTitle.textContent = 'Stopped';
                    progMsg.textContent = data.message;
                    progMsg.classList.replace('text-blue-600', 'text-amber-600');
                    btnClose.classList.remove('hidden');
                    showMigResults(data.results);
                    setActionsDisabled(false);
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    currentImportTaskId = null;
                    if (btnStop) btnStop.classList.add('hidden');
                    progBar.classList.replace('bg-blue-600', 'bg-red-500');
                    progTitle.textContent = 'Failed';
                    progMsg.textContent = data.message;
                    progMsg.classList.replace('text-blue-600', 'text-red-600');
                    btnClose.classList.remove('hidden');
                    showMigResults(data.results);
                    setActionsDisabled(false);
                }
            } catch (e) {
                // Ignore silent polling network errors
            }
        }, 1000);
    }

    // Download a finished artifact (export ZIP / audit XLSX) from the server.
    async function downloadResult(taskId, fallbackName) {
        try {
            const res = await fetch('/api/migration/result/download?task_id=' + encodeURIComponent(taskId));
            if (!res.ok) return;
            const blob = await res.blob();
            const cd = res.headers.get('Content-Disposition') || '';
            const m = cd.match(/filename="?([^"]+)"?/);
            const name = m ? m[1] : fallbackName;
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = name;
            document.body.appendChild(a); a.click(); a.remove();
            window.URL.revokeObjectURL(url);
        } catch (e) { /* the file also stays available in Recent migrations */ }
    }

    function failModal(title, msg) {
        if (pollInterval) clearInterval(pollInterval);
        progBar.classList.replace('bg-blue-600', 'bg-red-500');
        progTitle.textContent = title;
        progMsg.textContent = msg;
        progMsg.classList.replace('text-blue-600', 'text-red-600');
        btnClose.classList.remove('hidden');
        setActionsDisabled(false);
    }

    // ---- Recent migrations (durable storage, 7-day retention) ---------------
    const historyCard = document.getElementById('mig-history-card');
    const historyList = document.getElementById('mig-history-list');
    const historyRefresh = document.getElementById('mig-history-refresh');
    const KIND_LABEL = { export: 'Export (ZIP)', audit: 'Audit (XLSX)', import: 'Import log (XLSX)' };

    function fmtWhen(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return isNaN(d) ? '' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    }

    function renderHistory(items) {
        if (!historyList) return;
        historyList.innerHTML = '';
        if (!items.length) {
            historyList.innerHTML = '<div class="text-xs text-slate-400 py-2 text-center">No migration outputs in the last 7 days.</div>';
            return;
        }
        items.forEach(it => {
            const row = document.createElement('div');
            row.className = 'flex items-center justify-between gap-3 py-2 border-b border-slate-100 dark:border-slate-800 last:border-0';
            const label = (KIND_LABEL[it.kind] || it.kind || 'Output');
            const acct = it.account_name ? (' · ' + it.account_name) : '';
            const info = document.createElement('div');
            info.className = 'min-w-0';
            info.innerHTML =
                '<div class="text-sm font-bold text-slate-700 dark:text-slate-200 truncate">' + label + acct + '</div>' +
                '<div class="text-[11px] text-slate-400">' + fmtWhen(it.created_at) + '</div>';
            const dl = document.createElement('button');
            dl.className = 'text-xs font-bold text-blue-600 hover:text-blue-700 shrink-0';
            dl.textContent = '⬇ Download';
            dl.addEventListener('click', () => downloadResult(it.task_id, it.filename || 'download'));
            row.appendChild(info);
            row.appendChild(dl);
            historyList.appendChild(row);
        });
    }

    async function loadHistory() {
        if (!historyCard) return;
        try {
            const res = await fetch('/api/migration/history');
            const data = await res.json();
            if (!data.success || !data.enabled) { historyCard.classList.add('hidden'); return; }
            historyCard.classList.remove('hidden');
            renderHistory(data.items || []);
        } catch (e) {
            historyCard.classList.add('hidden');
        }
    }

    if (historyRefresh) historyRefresh.addEventListener('click', loadHistory);
    loadHistory();

    btnExport.addEventListener('click', async () => {
        setActionsDisabled(true);
        const taskId = 'export_' + Date.now();
        openProgressModal("Exporting Account Data");

        try {
            const res = await fetch('/api/migration/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId })
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.success) throw new Error(data.error || "Export failed to start.");
            startPolling(taskId, () => downloadResult(taskId, `RC_Migration_Export_${Date.now()}.zip`));
        } catch (err) {
            failModal('Export Failed', err.message);
        }
    });

    if (btnAudit) {
        btnAudit.addEventListener('click', async () => {
            setActionsDisabled(true);
            const taskId = 'audit_' + Date.now();
            openProgressModal("Building Reader-Friendly Audit");

            try {
                const res = await fetch('/api/migration/audit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ task_id: taskId })
                });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.success) throw new Error(data.error || "Audit failed to start.");
                startPolling(taskId, () => downloadResult(taskId, `RC_Account_Audit_${Date.now()}.xlsx`));
            } catch (err) {
                failModal('Audit Failed', err.message);
            }
        });
    }

    btnImport.addEventListener('click', async () => {
        if (fileInput.files.length === 0) return;

        setActionsDisabled(true);

        const taskId = 'import_' + Date.now();
        currentImportTaskId = taskId;
        openProgressModal("Importing Account Data", true);

        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        formData.append('task_id', taskId);

        try {
            const res = await fetch('/api/migration/import', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (!res.ok) throw new Error(data.error || "Failed to start import.");
            startPolling(taskId);
        } catch (err) {
            clearInterval(pollInterval);
            progBar.classList.replace('bg-blue-600', 'bg-red-500');
            progTitle.textContent = 'Import Failed';
            progMsg.textContent = err.message;
            progMsg.classList.replace('text-blue-600', 'text-red-600');
            btnClose.classList.remove('hidden');
            setActionsDisabled(false);
        }
    });
});
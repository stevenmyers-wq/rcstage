// webapp/static/js/account_migration.js
document.addEventListener('DOMContentLoaded', () => {
    const btnExport = document.getElementById('btn-start-export');
    const btnImport = document.getElementById('btn-start-import');
    const unbindCb = document.getElementById('unbind-devices-cb');
    const fileInput = document.getElementById('import-zip-file');
    const filenameDisplay = document.getElementById('import-filename');
    
    const modal = document.getElementById('migration-modal-progress');
    const progTitle = document.getElementById('mig-progress-title');
    const progBar = document.getElementById('mig-progress-bar');
    const progPct = document.getElementById('mig-progress-pct');
    const progMsg = document.getElementById('mig-progress-msg');
    const btnClose = document.getElementById('mig-progress-close-btn');

    let pollInterval = null;

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            filenameDisplay.textContent = e.target.files[0].name;
            btnImport.disabled = false;
        } else {
            filenameDisplay.textContent = 'Select ZIP Archive';
            btnImport.disabled = true;
        }
    });

    function openProgressModal(title) {
        progTitle.textContent = title;
        progBar.style.width = '0%';
        progBar.className = 'bg-blue-600 h-3 rounded-full transition-all duration-300';
        progPct.textContent = '0%';
        progMsg.textContent = 'Starting...';
        progMsg.className = 'text-xs font-mono text-blue-600 text-left mt-2 truncate';
        btnClose.classList.add('hidden');
        
        modal.classList.remove('hidden');
        setTimeout(() => modal.classList.remove('opacity-0'), 10);
    }

    function closeProgressModal() {
        modal.classList.add('opacity-0');
        setTimeout(() => modal.classList.add('hidden'), 200);
        if (pollInterval) clearInterval(pollInterval);
    }

    btnClose.addEventListener('click', closeProgressModal);

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
                    progBar.classList.replace('bg-blue-600', 'bg-green-500');
                    progTitle.textContent = 'Success!';
                    progMsg.classList.replace('text-blue-600', 'text-green-600');
                    btnClose.classList.remove('hidden');
                    btnExport.disabled = false;
                    btnImport.disabled = false;
                    if (onSuccess) onSuccess();
                } else if (data.status === 'error') {
                    clearInterval(pollInterval);
                    progBar.classList.replace('bg-blue-600', 'bg-red-500');
                    progTitle.textContent = 'Failed';
                    progMsg.textContent = data.message;
                    progMsg.classList.replace('text-blue-600', 'text-red-600');
                    btnClose.classList.remove('hidden');
                    btnExport.disabled = false;
                    btnImport.disabled = false;
                }
            } catch (e) {
                // Ignore silent polling network errors
            }
        }, 1000);
    }

    btnExport.addEventListener('click', async () => {
        if (unbindCb.checked) {
            if (!confirm("WARNING: You have selected to UNBIND physical devices. This will remove digital lines from phones. Proceed?")) return;
        }

        btnExport.disabled = true;
        btnImport.disabled = true;

        const taskId = 'export_' + Date.now();
        openProgressModal("Exporting Account Data");
        startPolling(taskId);

        try {
            const res = await fetch('/api/migration/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: taskId, unbind_devices: unbindCb.checked })
            });

            if (!res.ok) throw new Error("Export failed on server.");

            const blob = await res.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `RC_Migration_Export_${new Date().getTime()}.zip`;
            document.body.appendChild(a);
            a.click();
            a.remove();
        } catch (err) {
            clearInterval(pollInterval);
            progBar.classList.replace('bg-blue-600', 'bg-red-500');
            progTitle.textContent = 'Export Failed';
            progMsg.textContent = err.message;
            progMsg.classList.replace('text-blue-600', 'text-red-600');
            btnClose.classList.remove('hidden');
            btnExport.disabled = false;
            btnImport.disabled = false;
        }
    });

    btnImport.addEventListener('click', async () => {
        if (fileInput.files.length === 0) return;

        btnExport.disabled = true;
        btnImport.disabled = true;

        const taskId = 'import_' + Date.now();
        openProgressModal("Importing Account Data");

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
            btnExport.disabled = false;
            btnImport.disabled = false;
        }
    });
});
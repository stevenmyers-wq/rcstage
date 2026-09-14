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

    async function generate() {
        const selections = collectSelections();
        if (!selections.length) { setStatus('Select at least one section.', true); return; }
        genBtn.disabled = true;
        genBtn.textContent = 'Generating…';
        setStatus('Collecting configuration from the account…');
        pdfBtn.disabled = true; wordBtn.disabled = true; xlsxBtn.disabled = true;
        try {
            const res = await fetch('/api/as_built/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sections: selections, customer_name: nameEl.value.trim() })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                previewEl.innerHTML = data.document;
                previewEl.classList.remove('hidden');
                previewEmpty.classList.add('hidden');
                pdfBtn.disabled = false; wordBtn.disabled = false; xlsxBtn.disabled = false;
                if (data.section_errors && data.section_errors.length) {
                    setStatus(data.section_errors.length + ' section(s) had errors — see the document.', true);
                } else {
                    setStatus('Document generated. Download below.');
                }
            } else {
                setStatus(data.error || 'Generation failed.', true);
            }
        } catch (e) {
            setStatus('Network error during generation.', true);
        } finally {
            genBtn.disabled = false;
            genBtn.textContent = 'Generate Document';
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
})();

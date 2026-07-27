// Network Requirements Generator
// Renders the selection checkboxes from the backend catalog, then posts the
// chosen options to /api/network_requirements/generate and shows the returned
// document with copy / PDF / Word controls. The PDF and Word documents are
// rendered server-side in a fixed house style and downloaded directly (no
// browser print dialog). No RingCentral API calls.

document.addEventListener('DOMContentLoaded', () => {
    const tab = document.getElementById('network-requirements-tab');
    if (!tab) return; // Only run on this tab.

    const servicesEl = document.getElementById('nr-services');
    const vendorsEl = document.getElementById('nr-vendors');
    const vendorGroup = document.getElementById('nr-vendor-group');
    const integrationsEl = document.getElementById('nr-integrations');
    const regionsEl = document.getElementById('nr-regions');
    const generateBtn = document.getElementById('nrGenerateBtn');
    const emptyState = document.getElementById('nrEmptyState');
    const docEl = document.getElementById('nrDocument');
    const actions = document.getElementById('nrActions');

    // Payload used to build the document currently shown in the preview. Reused
    // when exporting so the downloaded PDF / Word file always matches the
    // preview, even if the form is edited afterwards without regenerating.
    let lastPayload = null;

    // --- Small self-contained toast (does not depend on global showMessage) ---
    function toast(message, isError) {
        const box = document.getElementById('message-box');
        if (!box) return;
        box.textContent = message;
        box.className = 'fixed top-4 left-1/2 transform -translate-x-1/2 w-96 z-50 p-4 rounded-xl shadow-xl text-sm font-medium text-center transition-all ' +
            (isError ? 'bg-rose-600 text-white' : 'bg-emerald-600 text-white');
        box.classList.remove('hidden');
        clearTimeout(box._nrTimer);
        box._nrTimer = setTimeout(() => box.classList.add('hidden'), 3500);
    }

    function checkboxRow(groupClass, value, label) {
        const id = `${groupClass}-${value}`;
        const wrap = document.createElement('label');
        wrap.className = 'flex items-start gap-2.5 text-sm text-slate-600 dark:text-slate-300 cursor-pointer';
        wrap.setAttribute('for', id);
        wrap.innerHTML =
            `<input type="checkbox" id="${id}" class="${groupClass} mt-0.5 w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500" value="${value}">` +
            `<span>${label}</span>`;
        return wrap;
    }

    function regionRow(value, label, checked) {
        const id = `nr-region-${value}`;
        const wrap = document.createElement('label');
        wrap.className = 'flex items-center gap-2.5 text-sm text-slate-600 dark:text-slate-300 cursor-pointer';
        wrap.setAttribute('for', id);
        wrap.innerHTML =
            `<input type="radio" name="nr-region" id="${id}" class="nr-region w-4 h-4 border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500" value="${value}"${checked ? ' checked' : ''}>` +
            `<span>${label}</span>`;
        return wrap;
    }

    function renderCatalog(catalog) {
        regionsEl.innerHTML = '';
        const def = catalog.default_region || 'global';
        (catalog.regions || []).forEach(r => {
            regionsEl.appendChild(regionRow(r.key, r.label, r.key === def));
        });

        servicesEl.innerHTML = '';
        (catalog.services || []).forEach(s => {
            servicesEl.appendChild(checkboxRow('nr-service', s.key, s.label));
        });

        vendorsEl.innerHTML = '';
        (catalog.deskphone_vendors || []).forEach(v => {
            vendorsEl.appendChild(checkboxRow('nr-vendor', v.key, v.label));
        });

        integrationsEl.innerHTML = '';
        (catalog.integrations || []).forEach(i => {
            integrationsEl.appendChild(checkboxRow('nr-integration', i.key, i.label));
        });

        // Reveal desk-phone vendor sub-list only when "Desk Phones" is ticked.
        const deskphonesCb = document.getElementById('nr-service-deskphones');
        if (deskphonesCb) {
            const sync = () => {
                if (deskphonesCb.checked) {
                    vendorGroup.classList.remove('hidden');
                } else {
                    vendorGroup.classList.add('hidden');
                    document.querySelectorAll('.nr-vendor').forEach(cb => { cb.checked = false; });
                }
            };
            deskphonesCb.addEventListener('change', sync);
            sync();
        }
    }

    function checkedValues(selector) {
        return Array.from(document.querySelectorAll(selector))
            .filter(cb => cb.checked)
            .map(cb => cb.value);
    }

    async function loadCatalog() {
        try {
            const res = await fetch('/api/network_requirements/catalog');
            const data = await res.json();
            if (res.ok && data.success) {
                renderCatalog(data.catalog);
            } else {
                toast(data.error || 'Could not load options.', true);
            }
        } catch (err) {
            toast('Network error loading options.', true);
        }
    }

    async function generate() {
        const customerName = document.getElementById('nrCustomerName').value.trim();
        if (!customerName) {
            toast('Enter a customer name first.', true);
            document.getElementById('nrCustomerName').focus();
            return;
        }

        const regionEl = document.querySelector('.nr-region:checked');
        const payload = {
            customer_name: customerName,
            site: document.getElementById('nrSite').value.trim(),
            prepared_by: document.getElementById('nrPreparedBy').value.trim(),
            notes: document.getElementById('nrNotes').value.trim(),
            region: regionEl ? regionEl.value : 'global',
            services: checkedValues('.nr-service'),
            deskphone_vendors: checkedValues('.nr-vendor'),
            integrations: checkedValues('.nr-integration')
        };

        generateBtn.disabled = true;
        const original = generateBtn.textContent;
        generateBtn.textContent = 'Generating...';
        try {
            const res = await fetch('/api/network_requirements/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (res.ok && data.success) {
                docEl.innerHTML = data.document;
                docEl.classList.remove('hidden');
                emptyState.classList.add('hidden');
                actions.classList.remove('hidden');
                lastPayload = payload;
                docEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
                toast('Document generated.');
            } else {
                toast(data.error || 'Generation failed.', true);
            }
        } catch (err) {
            toast('Network error during generation.', true);
        } finally {
            generateBtn.disabled = false;
            generateBtn.textContent = original;
        }
    }

    function currentCustomerSlug() {
        const name = document.getElementById('nrCustomerName').value.trim() || 'Customer';
        return name.replace(/[^a-z0-9]+/gi, '_').replace(/^_+|_+$/g, '') || 'Customer';
    }

    // Ask the backend to render the current document as a PDF or Word file and
    // trigger a direct download. The backend builds the file in the fixed house
    // style, so there is no browser print dialog and the output is identical
    // across browsers.
    async function exportDocument(format, btn) {
        if (!lastPayload) {
            toast('Generate a document first.', true);
            return;
        }
        const original = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'Preparing...';
        try {
            const res = await fetch('/api/network_requirements/export', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign({}, lastPayload, { format }))
            });
            if (!res.ok) {
                let msg = 'Export failed.';
                try { const d = await res.json(); msg = d.error || msg; } catch (e) { /* non-JSON */ }
                toast(msg, true);
                return;
            }
            const blob = await res.blob();
            const ext = format === 'word' ? 'doc' : 'pdf';
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `Network_Requirements_${currentCustomerSlug()}.${ext}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            toast(`${format === 'word' ? 'Word document' : 'PDF'} downloaded.`);
        } catch (err) {
            toast('Network error during export.', true);
        } finally {
            btn.disabled = false;
            btn.textContent = original;
        }
    }

    // --- Action buttons ---
    generateBtn.addEventListener('click', generate);

    document.getElementById('nrCopyBtn').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(docEl.innerText);
            toast('Document text copied to clipboard.');
        } catch (err) {
            toast('Copy failed - select and copy manually.', true);
        }
    });

    const pdfBtn = document.getElementById('nrPdfBtn');
    const wordBtn = document.getElementById('nrWordBtn');
    pdfBtn.addEventListener('click', () => exportDocument('pdf', pdfBtn));
    wordBtn.addEventListener('click', () => exportDocument('word', wordBtn));

    loadCatalog();
});

// Network Requirements Generator
// Renders the selection checkboxes from the backend catalog, then posts the
// chosen options to /api/network_requirements/generate and shows the returned
// document with copy / download / print controls. No RingCentral API calls.

document.addEventListener('DOMContentLoaded', () => {
    const tab = document.getElementById('network-requirements-tab');
    if (!tab) return; // Only run on this tab.

    const servicesEl = document.getElementById('nr-services');
    const vendorsEl = document.getElementById('nr-vendors');
    const vendorGroup = document.getElementById('nr-vendor-group');
    const integrationsEl = document.getElementById('nr-integrations');
    const generateBtn = document.getElementById('nrGenerateBtn');
    const emptyState = document.getElementById('nrEmptyState');
    const docEl = document.getElementById('nrDocument');
    const actions = document.getElementById('nrActions');

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

    function renderCatalog(catalog) {
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

        const payload = {
            customer_name: customerName,
            site: document.getElementById('nrSite').value.trim(),
            prepared_by: document.getElementById('nrPreparedBy').value.trim(),
            notes: document.getElementById('nrNotes').value.trim(),
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

    // Wrap the document body in a standalone printable HTML page.
    function standaloneHtml() {
        const inner = docEl.innerHTML;
        return `<!doctype html><html><head><meta charset="utf-8">` +
            `<title>Network Requirements — ${currentCustomerSlug()}</title>` +
            `<style>` +
            `body{font-family:Inter,Arial,sans-serif;color:#0f172a;max-width:900px;margin:2rem auto;padding:0 1.5rem;line-height:1.55;}` +
            `h1{font-size:1.6rem;margin:0 0 .25rem;}` +
            `h2{font-size:1.2rem;margin:1.6rem 0 .6rem;padding-bottom:.35rem;border-bottom:2px solid #2563eb;}` +
            `h3{font-size:1rem;color:#1d4ed8;margin:1.1rem 0 .4rem;}` +
            `table.nr-table{width:100%;border-collapse:collapse;margin:.5rem 0 1rem;font-size:.85rem;}` +
            `table.nr-table th{text-align:left;background:#f1f5f9;border:1px solid #e2e8f0;padding:.45rem .6rem;}` +
            `table.nr-table td{border:1px solid #e2e8f0;padding:.45rem .6rem;vertical-align:top;}` +
            `ul.nr-list{padding-left:1.2rem;} .nr-kv{display:flex;gap:.5rem;} .nr-kv .nr-k{font-weight:700;min-width:110px;}` +
            `.nr-chips{display:flex;flex-wrap:wrap;gap:.4rem;margin:.5rem 0 .8rem;} .nr-chip{display:inline-block;padding:.2rem .55rem;border-radius:.5rem;background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af;font-size:.78rem;font-family:ui-monospace,Menlo,monospace;}` +
            `.nr-warning{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;padding:.7rem .9rem;border-radius:.6rem;font-size:.82rem;}` +
            `.nr-note{color:#475569;font-size:.86rem;} .nr-freetext{white-space:pre-wrap;}` +
            `.nr-footer{margin-top:1.5rem;padding-top:.6rem;border-top:1px solid #e2e8f0;color:#94a3b8;font-size:.78rem;}` +
            `a{color:#2563eb;word-break:break-all;}` +
            `</style></head><body class="nr-doc">${inner}</body></html>`;
    }

    // --- Action buttons ---
    generateBtn.addEventListener('click', generate);

    document.getElementById('nrCopyBtn').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(docEl.innerText);
            toast('Document text copied to clipboard.');
        } catch (err) {
            toast('Copy failed — select and copy manually.', true);
        }
    });

    document.getElementById('nrDownloadBtn').addEventListener('click', () => {
        const blob = new Blob([standaloneHtml()], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `Network_Requirements_${currentCustomerSlug()}.html`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });

    document.getElementById('nrPrintBtn').addEventListener('click', () => {
        const w = window.open('', '_blank');
        if (!w) {
            toast('Pop-up blocked — allow pop-ups to print.', true);
            return;
        }
        w.document.write(standaloneHtml());
        w.document.close();
        w.focus();
        setTimeout(() => w.print(), 300);
    });

    loadCatalog();
});

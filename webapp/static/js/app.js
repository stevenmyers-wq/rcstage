// webapp/static/js/app.js

// --- Theme Toggle Logic ---
function initTheme() {
    const themeToggleBtn = document.getElementById('theme-toggle');
    const darkIcon = document.getElementById('theme-toggle-dark-icon');
    const lightIcon = document.getElementById('theme-toggle-light-icon');
    const htmlElement = document.documentElement;

    // Apply the current theme (from localStorage, falling back to the system
    // preference) to this document and sync the toggle icons. Safe to call
    // repeatedly.
    function applyTheme() {
        const isDark = localStorage.getItem('color-theme') === 'dark'
            || (!('color-theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches);
        htmlElement.classList.toggle('dark', isDark);
        if (darkIcon) darkIcon.classList.toggle('hidden', isDark);
        if (lightIcon) lightIcon.classList.toggle('hidden', !isDark);
    }

    applyTheme();

    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', () => {
            const nextDark = !htmlElement.classList.contains('dark');
            localStorage.setItem('color-theme', nextDark ? 'dark' : 'light');
            applyTheme();
        });
    }

    // Tool tabs render in same-origin iframes that each run initTheme() once at
    // load. When the theme changes in another document (the parent shell's
    // toggle, or another browser tab), the localStorage write fires a 'storage'
    // event here — re-apply so open tabs follow the new theme instead of staying
    // stuck on whatever they loaded with. The 'storage' event only fires in
    // *other* documents, so this never double-handles the toggle click above.
    window.addEventListener('storage', (e) => {
        if (e.key === 'color-theme' || e.key === null) applyTheme();
    });
}

// --- Shared: download an operation's result listing as .xlsx ---------------
// Every UC Tools upload flow can offer "Download results (.xlsx)" by handing a
// row array to UCResults.download(). SheetJS is fetched from the CDN on first
// use (same on-demand pattern as the export ZIP builder) so no page pays for it
// unless a user actually asks for a results spreadsheet.
window.UCResults = (function () {
    function loadSheetJS() {
        return new Promise((resolve, reject) => {
            if (window.XLSX) { resolve(window.XLSX); return; }
            const s = document.createElement('script');
            s.src = 'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js';
            s.onload = () => window.XLSX ? resolve(window.XLSX) : reject(new Error('Spreadsheet library failed to initialize.'));
            s.onerror = () => reject(new Error('Could not load the spreadsheet library (SheetJS) from CDN.'));
            document.head.appendChild(s);
        });
    }

    // A filesystem-safe timestamp for result filenames, e.g. 2026-07-25-14-30-05.
    function stamp() {
        return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    }

    // rows: array of plain objects (keys become column headers).
    async function download(rows, filenameBase, sheetName) {
        if (!rows || !rows.length) return;
        const XLSX = await loadSheetJS();
        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, (sheetName || 'Results').slice(0, 31));
        XLSX.writeFile(wb, `${filenameBase || 'Results'}_${stamp()}.xlsx`);
    }

    // Fallback for text-log style flows: turn a progress-log element's lines into
    // rows of { Status, Detail }, inferring status from ✅/❌ markers or CSS class.
    function rowsFromLog(el) {
        if (!el) return [];
        return Array.from(el.children).map(node => {
            const raw = (node.textContent || '').replace(/^>\s*/, '').trim();
            let status = 'Info';
            if (/❌|\berror\b|\bfailed\b/i.test(raw) || /text-rose|text-red/.test(node.className)) status = 'Failed';
            else if (/✅|\bsuccess\b/i.test(raw) || /text-emerald|text-green/.test(node.className)) status = 'Success';
            return { Status: status, Detail: raw };
        }).filter(r => r.Detail);
    }

    // Wire a button so clicking it downloads whatever getRows() returns at click
    // time. getRows may return an array or a Promise of one. Shows a busy label
    // and never throws out of the handler.
    function attachButton(btn, getRows, filenameBase, sheetName) {
        if (!btn) return;
        const resolve = (v) => (typeof v === 'function') ? v() : v;
        btn.addEventListener('click', async () => {
            let rows = resolve(getRows);
            if (rows && typeof rows.then === 'function') rows = await rows;
            if (!rows || !rows.length) return;
            const original = btn.textContent;
            btn.disabled = true;
            btn.textContent = 'Preparing…';
            try {
                await download(rows, resolve(filenameBase), resolve(sheetName));
            } catch (e) {
                alert('Could not build the results spreadsheet: ' + e.message);
            } finally {
                btn.disabled = false;
                btn.textContent = original;
            }
        });
    }

    return { loadSheetJS, stamp, download, rowsFromLog, attachButton };
})();

// --- Shared: tick-box multi-select --------------------------------------------
// Renders a searchable checkbox list into a mount element, replacing the old
// <select multiple> "Ctrl/Cmd-click" pattern. Selections live in a Set that is
// independent of the current search/filter view, so ticking an item and then
// searching for something else never loses the earlier tick.
//
// CheckboxSelect.create(mountEl, config) -> instance
//   config.items            array of strings or objects
//   config.getValue(item)   -> unique string  (default: item.value ?? String(item))
//   config.getLabel(item)   -> display text   (default: item.label ?? value)
//   config.getSub(item)     -> secondary text (optional)
//   config.getFilter(item)  -> group value for the filter dropdown (optional; e.g. Site)
//   config.filterLabel      label for the "All …" filter option (default 'Sites')
//   config.searchable       show a search box (default true)
//   config.searchPlaceholder
//   config.listHeightClass  tailwind height class for the scroll area (default 'h-56')
//   config.onChange(values) called after any selection change
// instance: getSelected(), getSelectedItems(), setItems(items), clear(), el
window.CheckboxSelect = (function () {
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
        ));
    }

    function create(mount, config) {
        config = config || {};
        const getValue = config.getValue || (it => (it && typeof it === 'object') ? String(it.value) : String(it));
        const getLabel = config.getLabel || (it => (it && typeof it === 'object') ? (it.label != null ? it.label : String(it.value)) : String(it));
        const getSub = config.getSub || (() => '');
        const getFilter = config.getFilter || null;
        const searchable = config.searchable !== false;
        const listHeightClass = config.listHeightClass || 'h-56';
        const onChange = config.onChange || function () {};

        let items = Array.isArray(config.items) ? config.items.slice() : [];
        const selected = new Set();

        mount.innerHTML = '';
        const wrap = document.createElement('div');
        wrap.className = 'space-y-2';

        const controls = document.createElement('div');
        controls.className = 'flex flex-col sm:flex-row gap-2';
        let searchInput = null;
        if (searchable) {
            searchInput = document.createElement('input');
            searchInput.type = 'text';
            searchInput.placeholder = config.searchPlaceholder || 'Search…';
            searchInput.className = 'input-field !py-2 text-sm flex-grow';
            controls.appendChild(searchInput);
        }
        // Support one or many filter dimensions. Back-compat: a single
        // getFilter/filterLabel is treated as one filter def. Pass
        // config.filters = [{ get, label, allLabel? }, …] for several.
        const filterDefs = (Array.isArray(config.filters) && config.filters.length)
            ? config.filters
            : (getFilter ? [{ get: getFilter, label: config.filterLabel || 'Sites' }] : []);
        const filterSelects = filterDefs.map(def => {
            const sel = document.createElement('select');
            sel.className = 'input-field !py-2 text-sm sm:w-44';
            controls.appendChild(sel);
            return { def, el: sel };
        });
        if (searchable || filterSelects.length) wrap.appendChild(controls);

        const header = document.createElement('div');
        header.className = 'flex items-center justify-between text-xs font-semibold text-slate-500 dark:text-slate-400 px-1';
        const countEl = document.createElement('span');
        const actions = document.createElement('div');
        actions.className = 'flex gap-3';
        const selectVisibleBtn = document.createElement('button');
        selectVisibleBtn.type = 'button';
        selectVisibleBtn.textContent = 'Select visible';
        selectVisibleBtn.className = 'hover:text-blue-600 dark:hover:text-blue-400 transition';
        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.textContent = 'Clear';
        clearBtn.className = 'hover:text-blue-600 dark:hover:text-blue-400 transition';
        actions.appendChild(selectVisibleBtn);
        actions.appendChild(clearBtn);
        header.appendChild(countEl);
        header.appendChild(actions);
        wrap.appendChild(header);

        const list = document.createElement('div');
        list.className = `border border-slate-200 dark:border-slate-700 rounded-lg ${listHeightClass} overflow-y-auto bg-white dark:bg-slate-900/40 divide-y divide-slate-100 dark:divide-slate-800`;
        wrap.appendChild(list);
        mount.appendChild(wrap);

        function distinctFilters(def) {
            const set = new Set();
            items.forEach(it => { const f = def.get(it); if (f) set.add(f); });
            return Array.from(set).sort();
        }
        function rebuildFilterOptions() {
            filterSelects.forEach(({ def, el }) => {
                const prev = el.value;
                const allLbl = def.allLabel || ('All ' + (def.label || ''));
                el.innerHTML = ['<option value="">' + esc(allLbl) + '</option>']
                    .concat(distinctFilters(def).map(f => `<option value="${esc(f)}">${esc(f)}</option>`)).join('');
                if (prev) el.value = prev;
            });
        }
        function visibleItems() {
            const q = searchInput ? searchInput.value.trim().toLowerCase() : '';
            return items.filter(it => {
                for (const { def, el } of filterSelects) {
                    if (el.value && def.get(it) !== el.value) return false;
                }
                if (q) {
                    const hay = (getLabel(it) + ' ' + (getSub(it) || '')).toLowerCase();
                    if (hay.indexOf(q) === -1) return false;
                }
                return true;
            });
        }
        function updateCount() { countEl.textContent = `${selected.size} selected`; }
        function renderList() {
            const vis = visibleItems();
            if (vis.length === 0) {
                list.innerHTML = '<div class="p-4 text-center text-sm text-slate-400 dark:text-slate-500 italic">No matches.</div>';
                updateCount();
                return;
            }
            const frag = document.createDocumentFragment();
            vis.forEach(it => {
                const val = getValue(it);
                const row = document.createElement('label');
                row.className = 'flex items-center gap-3 px-3 py-2 text-sm cursor-pointer hover:bg-slate-50 dark:hover:bg-slate-800/40 transition-colors';
                const cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.className = 'w-4 h-4 rounded border-slate-300 dark:border-slate-600 text-blue-600 focus:ring-blue-500 flex-shrink-0';
                cb.checked = selected.has(val);
                cb.addEventListener('change', () => {
                    if (cb.checked) selected.add(val); else selected.delete(val);
                    updateCount();
                    onChange(getSelected());
                });
                const txt = document.createElement('span');
                const sub = getSub(it);
                txt.innerHTML = esc(getLabel(it)) + (sub ? ` <span class="text-slate-400 dark:text-slate-500">${esc(sub)}</span>` : '');
                row.appendChild(cb);
                row.appendChild(txt);
                frag.appendChild(row);
            });
            list.innerHTML = '';
            list.appendChild(frag);
            updateCount();
        }

        if (searchInput) searchInput.addEventListener('input', renderList);
        filterSelects.forEach(({ el }) => el.addEventListener('change', renderList));
        selectVisibleBtn.addEventListener('click', () => {
            visibleItems().forEach(it => selected.add(getValue(it)));
            renderList();
            onChange(getSelected());
        });
        clearBtn.addEventListener('click', () => {
            selected.clear();
            renderList();
            onChange(getSelected());
        });

        function getSelected() { return Array.from(selected); }
        function getSelectedItems() { return items.filter(it => selected.has(getValue(it))); }
        function setItems(next) {
            items = Array.isArray(next) ? next.slice() : [];
            const valid = new Set(items.map(getValue));
            Array.from(selected).forEach(v => { if (!valid.has(v)) selected.delete(v); });
            rebuildFilterOptions();
            renderList();
        }
        // Replace the current ticks with `values` (restricted to present items).
        // Does not fire onChange — the caller is driving the state. Handy when an
        // external filter swaps the item set but selections are tracked outside.
        function setSelected(values) {
            selected.clear();
            const valid = new Set(items.map(getValue));
            (values || []).forEach(v => { const s = String(v); if (valid.has(s)) selected.add(s); });
            renderList();
        }
        function clear() { selected.clear(); renderList(); onChange(getSelected()); }

        rebuildFilterOptions();
        renderList();
        return { getSelected, getSelectedItems, setItems, setSelected, clear, el: wrap };
    }

    return { create };
})();

// --- Shared: Excel workbook sheet picker --------------------------------------
// Every upload flow that accepts an .xlsx/.xls workbook can offer a "Target
// Worksheet" dropdown so the user chooses which sheet to process, instead of the
// backend silently assuming the first sheet. Sheet names come from the shared
// /api/common/sheets endpoint.
//
// Usage:
//   const picker = SheetPicker.create(mountEl, { preferred: ['Assignment Template'] });
//   picker.update(file);              // whenever the chosen file changes
//   picker.appendTo(formData);        // adds sheet_name when a sheet is chosen
//   picker.value();                   // selected sheet name, or '' (csv/no file)
//   picker.reset();                   // hide + clear (e.g. after a successful run)
//
// The dropdown hides itself for CSV uploads and when no file is selected, so
// value() safely returns '' and appendTo() adds nothing — preserving the old
// "first sheet" behaviour for those cases.
window.SheetPicker = (function () {
    const CSV_SENTINEL = 'CSV Format (No Sheets)';

    function create(mount, options) {
        options = options || {};
        const endpoint = options.endpoint || '/api/common/sheets';
        const label = options.label || 'Target Worksheet';
        const preferred = (options.preferred || []).map(s => String(s).toLowerCase());
        const onReady = typeof options.onReady === 'function' ? options.onReady : function () {};

        mount.innerHTML =
            '<div class="input-wrapper" data-sp-wrap style="display:none">' +
            '<label class="input-label">' + label + '</label>' +
            '<select class="input-field" data-sp-select></select>' +
            '</div>';
        const wrap = mount.querySelector('[data-sp-wrap]');
        const select = mount.querySelector('[data-sp-select]');

        // Monotonic token so a slow response for an earlier file can't overwrite
        // the dropdown after the user has already picked a different file.
        let reqToken = 0;
        let visible = false;

        function hide() {
            visible = false;
            wrap.style.display = 'none';
            select.innerHTML = '';
        }

        function fill(sheets) {
            select.innerHTML = '';
            sheets.forEach(sheet => {
                const opt = document.createElement('option');
                opt.value = sheet;
                opt.textContent = sheet;
                select.appendChild(opt);
            });
            let idx = 0;
            if (preferred.length) {
                const match = sheets.findIndex(s => preferred.includes(String(s).toLowerCase()));
                if (match >= 0) idx = match;
            }
            select.selectedIndex = idx;
        }

        async function update(file) {
            const myToken = ++reqToken;
            if (!file) { hide(); return; }
            const name = (file.name || '').toLowerCase();
            if (name.endsWith('.csv')) { hide(); onReady([CSV_SENTINEL]); return; }

            visible = true;
            wrap.style.display = '';
            select.innerHTML = '<option value="">Reading file…</option>';

            try {
                const fd = new FormData();
                fd.append('file', file);
                const res = await fetch(endpoint, { method: 'POST', body: fd });
                const sheets = await res.json();
                if (myToken !== reqToken) return;   // superseded by a newer file
                if (res.ok && Array.isArray(sheets)) {
                    fill(sheets);
                    onReady(sheets);
                } else {
                    select.innerHTML = '<option value="">Could not read sheets</option>';
                }
            } catch (e) {
                if (myToken !== reqToken) return;
                select.innerHTML = '<option value="">Error reading sheets</option>';
            }
        }

        function value() {
            if (!visible) return '';
            const v = select.value;
            return (v && v !== CSV_SENTINEL) ? v : '';
        }

        function appendTo(formData, field) {
            const v = value();
            if (v) formData.append(field || 'sheet_name', v);
        }

        return { update, reset: hide, value, appendTo, select, el: wrap };
    }

    // Modal variant for compact upload buttons that can't host an inline
    // dropdown (e.g. a hidden <input> inside a small card that uploads on
    // change). choose(file) returns a Promise:
    //   ''            -> no choice needed (csv, single-sheet, or no SheetPicker)
    //   '<sheetName>' -> the worksheet the user picked
    //   null          -> the user cancelled (caller should abort the upload)
    // The modal is only shown when the workbook actually has 2+ sheets, so
    // single-sheet uploads keep their one-click behaviour.
    function choose(file, options) {
        options = options || {};
        const endpoint = options.endpoint || '/api/common/sheets';
        const title = options.title || 'Choose worksheet';
        const preferred = (options.preferred || []).map(s => String(s).toLowerCase());

        return new Promise(async (resolve) => {
            if (!file) { resolve(''); return; }
            const name = (file.name || '').toLowerCase();
            if (name.endsWith('.csv')) { resolve(''); return; }

            let sheets;
            try {
                const fd = new FormData();
                fd.append('file', file);
                const res = await fetch(endpoint, { method: 'POST', body: fd });
                sheets = await res.json();
            } catch (e) { resolve(''); return; }   // fall back to backend default

            if (!Array.isArray(sheets) || sheets.length === 0) { resolve(''); return; }
            if (sheets.length === 1) { resolve(''); return; }   // nothing to choose

            // Build the modal.
            const overlay = document.createElement('div');
            overlay.className = 'fixed inset-0 z-50 bg-slate-900/60 dark:bg-black/70 backdrop-blur-sm flex items-center justify-center p-4';
            const optionsHtml = sheets.map(s =>
                '<option value="' + String(s).replace(/"/g, '&quot;') + '">' + String(s) + '</option>'
            ).join('');
            overlay.innerHTML =
                '<div class="card w-full max-w-md shadow-2xl">' +
                '<h3 class="text-heading mb-2">' + title + '</h3>' +
                '<p class="text-subheading mb-5">This workbook has multiple worksheets. Choose which one to process.</p>' +
                '<div class="input-wrapper mb-6"><label class="input-label">Target Worksheet</label>' +
                '<select class="input-field" data-sp-modal-select>' + optionsHtml + '</select></div>' +
                '<div class="flex justify-end gap-3">' +
                '<button type="button" class="btn-secondary" data-sp-cancel>Cancel</button>' +
                '<button type="button" class="btn-primary" data-sp-confirm>Continue</button>' +
                '</div></div>';
            document.body.appendChild(overlay);

            const select = overlay.querySelector('[data-sp-modal-select]');
            if (preferred.length) {
                const idx = sheets.findIndex(s => preferred.includes(String(s).toLowerCase()));
                if (idx >= 0) select.selectedIndex = idx;
            }

            function close(result) { overlay.remove(); resolve(result); }
            overlay.querySelector('[data-sp-confirm]').addEventListener('click', () => close(select.value));
            overlay.querySelector('[data-sp-cancel]').addEventListener('click', () => close(null));
            overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });
        });
    }

    return { create, choose };
})();

// ... Keep all your existing app.js code below here (handleLogin, handleRcConnect, etc) ...

// Ensure initTheme is called on load
document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  // ... your existing DOMContentLoaded code ...
  const loginForm = document.getElementById("login-form");
  if (document.getElementById("app-dashboard")) {
    checkRcStatus();
    checkCxoneStatus();
  }
});

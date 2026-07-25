// webapp/static/js/app.js

// --- Theme Toggle Logic ---
function initTheme() {
    const themeToggleBtn = document.getElementById('theme-toggle');
    const darkIcon = document.getElementById('theme-toggle-dark-icon');
    const lightIcon = document.getElementById('theme-toggle-light-icon');
    const htmlElement = document.documentElement;

    // Check localStorage or system preference
    if (localStorage.getItem('color-theme') === 'dark' || (!('color-theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
        htmlElement.classList.add('dark');
        if (lightIcon) lightIcon.classList.remove('hidden');
    } else {
        htmlElement.classList.remove('dark');
        if (darkIcon) darkIcon.classList.remove('hidden');
    }

    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', () => {
            darkIcon.classList.toggle('hidden');
            lightIcon.classList.toggle('hidden');

            if (htmlElement.classList.contains('dark')) {
                htmlElement.classList.remove('dark');
                localStorage.setItem('color-theme', 'light');
            } else {
                htmlElement.classList.add('dark');
                localStorage.setItem('color-theme', 'dark');
            }
        });
    }
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

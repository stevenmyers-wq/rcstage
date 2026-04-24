// webapp/static/js/cxone_script_analyzer.js
document.addEventListener('DOMContentLoaded', () => {
    // Guard: Exit immediately if we aren't on the CXone tab
    const btnConnect = document.getElementById('cx-connect-btn');
    if (!btnConnect) return; 

    let cxState = { token: null, base_uri: null, scriptsMap: {} };
    let currentPdfBase64 = null;

    // Elements
    const workspace = document.getElementById('cx-workspace');
    const folderSelect = document.getElementById('cx-folder');
    const scriptSelect = document.getElementById('cx-script');
    const outputArea = document.getElementById('cx-output-area');
    const mdDisplay = document.getElementById('cx-markdown-display');
    const pdfBtn = document.getElementById('cx-download-pdf-btn');

    // --- Inner Tab Switching ---
    document.querySelectorAll('.cx-tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            // UI styling
            document.querySelectorAll('.cx-tab-btn').forEach(b => {
                b.classList.remove('border-blue-600', 'text-blue-600');
                b.classList.add('border-transparent', 'text-gray-500');
            });
            e.target.classList.remove('border-transparent', 'text-gray-500');
            e.target.classList.add('border-blue-600', 'text-blue-600');
            
            // Show correct pane
            document.querySelectorAll('.cx-pane').forEach(p => p.classList.add('hidden'));
            document.getElementById(e.target.dataset.target).classList.remove('hidden');
        });
    });

    // --- 1. Auth ---
    btnConnect.addEventListener('click', async () => {
        const region = document.getElementById('cx-region').value;
        const access_key = document.getElementById('cx-access').value;
        const secret_key = document.getElementById('cx-secret').value;

        if (!access_key || !secret_key) {
            if (typeof showMessage === 'function') showMessage("Access Key and Secret Key are required.", true);
            else alert("Access Key and Secret Key are required.");
            return;
        }

        btnConnect.disabled = true;
        btnConnect.textContent = "Connecting...";
        
        try {
            const res = await fetch('/api/cxone/auth', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({region, access_key, secret_key})
            });
            const data = await res.json();
            
            if (data.success) {
                cxState.token = data.token;
                cxState.base_uri = data.base_uri;
                document.getElementById('cx-auth-status').textContent = "✅ Connected!";
                document.getElementById('cx-auth-status').className = "ml-3 text-sm font-semibold text-green-600";
                workspace.classList.remove('hidden');
                loadFolders();
            } else {
                if (typeof showMessage === 'function') showMessage(data.error || "Auth failed", true);
            }
        } catch (e) {
            console.error("CXone Auth Error:", e);
            if (typeof showMessage === 'function') showMessage("Network error during auth.", true);
        } finally {
            btnConnect.disabled = false;
            btnConnect.textContent = "Connect to CXone";
        }
    });

    // --- 2. Load Folders & Scripts ---
    async function loadFolders() {
        const res = await fetch('/api/cxone/folders', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token: cxState.token, base_uri: cxState.base_uri})
        });
        const data = await res.json();
        if(data.success && data.folders) {
            folderSelect.innerHTML = data.folders.map(f => `<option value="${f}">${f}</option>`).join('');
            loadScripts();
        }
    }

    folderSelect.addEventListener('change', loadScripts);

    async function loadScripts() {
        scriptSelect.innerHTML = '<option>Loading...</option>';
        const res = await fetch('/api/cxone/scripts', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token: cxState.token, base_uri: cxState.base_uri, folder: folderSelect.value})
        });
        const data = await res.json();
        scriptSelect.innerHTML = '';
        cxState.scriptsMap = {};

        if(data.success && data.scripts) {
            data.scripts.forEach(s => {
                const s_id = s.masterID || s.scriptId || s.fileId;
                const s_name = s.scriptName || s.fileName || 'Unknown';
                const display = s_name.replace(/\\/g, " / ");
                if (s_id) {
                    cxState.scriptsMap[s_id] = { path: s_name, name: display };
                    scriptSelect.innerHTML += `<option value="${s_id}">${display} (ID: ${s_id})</option>`;
                }
            });
        }
    }

    // --- 3. Pane: Compare Versions ---
    let compareHistoryData = [];
    document.getElementById('cx-load-history-btn').addEventListener('click', async () => {
        if (scriptSelect.selectedOptions.length !== 1) {
            if (typeof showMessage === 'function') showMessage("Please select exactly one script.", true); 
            return;
        }
        const sid = scriptSelect.value;
        const res = await fetch('/api/cxone/history', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({token: cxState.token, base_uri: cxState.base_uri, script_path: cxState.scriptsMap[sid].path})
        });
        const data = await res.json();
        compareHistoryData = data.history || [];
        
        if (compareHistoryData.length < 2) {
            if (typeof showMessage === 'function') showMessage("Script needs at least 2 versions to compare.", true); 
            return;
        }

        const opts = compareHistoryData.map(h => `<option value="${h.scriptId}">${h.modifyDate} by ${h.modifyUser}</option>`).join('');
        document.getElementById('cx-prev-ver').innerHTML = opts;
        document.getElementById('cx-curr-ver').innerHTML = opts;
        document.getElementById('cx-prev-ver').selectedIndex = 1; // Default to N-1
        
        document.getElementById('compare-selectors').classList.remove('hidden');
        document.getElementById('cx-run-compare-btn').classList.remove('hidden');
    });

    document.getElementById('cx-run-compare-btn').addEventListener('click', () => {
        const payload = {
            mode: 'compare',
            script_name: cxState.scriptsMap[scriptSelect.value].name,
            prev_id: document.getElementById('cx-prev-ver').value,
            curr_id: document.getElementById('cx-curr-ver').value
        };
        runAnalysis(payload, document.getElementById('cx-run-compare-btn'));
    });

    // --- 4. Pane: As-Built ---
    document.getElementById('cx-run-asbuilt-btn').addEventListener('click', () => {
        if (scriptSelect.selectedOptions.length === 0) {
            if (typeof showMessage === 'function') showMessage("Please select at least one script.", true); 
            return;
        }
        const scripts = Array.from(scriptSelect.selectedOptions).map(opt => ({
            id: opt.value,
            name: cxState.scriptsMap[opt.value].name,
            path: cxState.scriptsMap[opt.value].path
        }));
        
        runAnalysis({ mode: 'as-built', scripts: scripts }, document.getElementById('cx-run-asbuilt-btn'));
    });

    // --- Core Executor ---
    async function runAnalysis(customPayload, btnElement) {
        const originalText = btnElement.textContent;
        btnElement.disabled = true;
        btnElement.textContent = "Analyzing with Gemini (Please wait)...";
        outputArea.classList.add('hidden');

        const payload = {
            token: cxState.token,
            base_uri: cxState.base_uri,
            ...customPayload
        };

        try {
            const res = await fetch('/api/cxone/analyze', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            
            if (data.success) {
                currentPdfBase64 = data.pdf_b64;
                mdDisplay.innerHTML = marked.parse(data.markdown);
                outputArea.classList.remove('hidden');
                outputArea.scrollIntoView({ behavior: 'smooth' });
                if (typeof showMessage === 'function') showMessage("Analysis complete!", false);
            } else {
                if (typeof showMessage === 'function') showMessage(data.error || "Analysis failed.", true);
            }
        } catch (e) {
            console.error("Analysis Error:", e);
            if (typeof showMessage === 'function') showMessage("Network timeout or error during analysis.", true);
        } finally {
            btnElement.disabled = false;
            btnElement.textContent = originalText;
        }
    }

    // --- PDF Download ---
    pdfBtn.addEventListener('click', () => {
        if (!currentPdfBase64) return;
        const link = document.createElement('a');
        link.href = `data:application/pdf;base64,${currentPdfBase64}`;
        link.download = `CXone_Analysis_${new Date().getTime()}.pdf`;
        link.click();
    });
});
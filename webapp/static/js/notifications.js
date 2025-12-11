let isRunning = false;
const logBox = document.getElementById('notifications-log');
const statusContainer = document.getElementById('status-container');
const statusText = document.getElementById('status-text');
const progressBar = document.getElementById('progress-bar');
const fileInput = document.getElementById('file-upload');
const btnUpdate = document.getElementById('btn-update-start');

// --- UTILS: Logging ---
function log(msg) {
    const time = new Date().toLocaleTimeString();
    logBox.value += `[${time}] ${msg}\n`;
    logBox.scrollTop = logBox.scrollHeight;
}

// --- UTILS: CSV Generation ---
function downloadCSV(data, filename) {
    const headers = Object.keys(data[0]);
    const csvRows = [headers.join(',')];
    
    for (const row of data) {
        const values = headers.map(header => {
            const escaped = ('' + row[header]).replace(/"/g, '\\"');
            return `"${escaped}"`;
        });
        csvRows.push(values.join(','));
    }
    
    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
}

// --- FEATURE 1: AUDIT & EXPORT ---
document.getElementById('btn-audit-start').addEventListener('click', async () => {
    setupUI(true);
    let results = [];
    
    try {
        log("Fetching extension list...");
        const listResp = await fetch('/api/notifications/get-targets');
        const listData = await listResp.json();
        
        const targets = listData.targets;
        const total = targets.length;
        log(`Found ${total} users. Starting audit...`);

        for (let i = 0; i < total; i++) {
            if (!isRunning) throw new Error("Stopped by user.");
            
            const t = targets[i];
            updateStatus(i, total, `Auditing Ext ${t.ext}...`);
            
            // Call API
            const resp = await fetch('/api/notifications/audit-single', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({id: t.id})
            });
            const json = await resp.json();
            
            if (json.status === 'success') {
                // Merge basic info with fetched settings
                results.push({
                    "Name": t.name, 
                    "Extension": t.ext, 
                    ...json.data 
                });
            } else {
                log(`[ERR] Failed to audit ${t.ext}`);
            }
        }

        log("Audit complete. Generating CSV...");
        if (results.length > 0) downloadCSV(results, `audit_notifications_${Date.now()}.csv`);
        
    } catch (e) {
        log(`Error: ${e.message}`);
    } finally {
        setupUI(false);
    }
});

// --- FEATURE 2: DOWNLOAD TEMPLATE ---
document.getElementById('btn-download-template').addEventListener('click', () => {
    const template = [
        { "Extension ID": "12345678", "Emails": "user@email.com; manager@email.com" },
        { "Extension ID": "87654321", "Emails": "audit_output_file_has_ids.com" }
    ];
    downloadCSV(template, 'notification_update_template.csv');
    log("Template downloaded.");
});

// --- FEATURE 3: BULK UPDATE ---
// Enable button only when file selected
fileInput.addEventListener('change', () => {
    btnUpdate.disabled = !fileInput.files.length;
});

btnUpdate.addEventListener('click', () => {
    const file = fileInput.files[0];
    if (!file) return;

    setupUI(true);
    const reader = new FileReader();
    
    reader.onload = async function(e) {
        try {
            const text = e.target.result;
            const rows = text.split('\n').map(r => r.trim()).filter(r => r);
            const headers = rows[0].split(',').map(h => h.replace(/"/g, '').trim());
            
            // Simple CSV Parse
            const tasks = [];
            for (let i = 1; i < rows.length; i++) {
                const cols = rows[i].split(',').map(c => c.replace(/"/g, '').trim());
                if (cols.length < 2) continue; // Skip empty
                
                let rowObj = {};
                headers.forEach((h, idx) => rowObj[h] = cols[idx]);
                tasks.push(rowObj);
            }

            log(`Parsed ${tasks.length} rows from file.`);
            
            // Loop through updates
            for (let i = 0; i < tasks.length; i++) {
                if (!isRunning) throw new Error("Stopped by user.");
                
                const task = tasks[i];
                const extId = task['Extension ID'];
                const emailsRaw = task['Emails'] || "";
                const emailList = emailsRaw.split(';').map(e => e.trim()).filter(e => e);

                if (!extId) {
                    log(`[SKIP] Row ${i+1}: Missing Extension ID`);
                    continue;
                }

                updateStatus(i, tasks.length, `Updating ID ${extId}...`);

                const resp = await fetch('/api/notifications/update-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: extId, emails: emailList })
                });
                
                const resJson = await resp.json();
                if (resJson.status === 'success') {
                    log(`[OK] Updated ${extId}`);
                } else {
                    log(`[FAIL] Could not update ${extId}`);
                }
            }
            log("Bulk update complete.");

        } catch (err) {
            log(`Error: ${err.message}`);
        } finally {
            setupUI(false);
            fileInput.value = ''; // Reset
        }
    };
    reader.readAsText(file);
});

// --- UI HELPERS ---
function setupUI(active) {
    isRunning = active;
    statusContainer.style.display = active ? 'block' : 'none';
    document.getElementById('btn-audit-start').disabled = active;
    btnUpdate.disabled = active || !fileInput.files.length;
}

function updateStatus(current, total, text) {
    statusText.innerText = text;
    const pct = Math.round(((current + 1) / total) * 100);
    progressBar.style.width = `${pct}%`;
    progressBar.innerText = `${pct}%`;
}

document.getElementById('btn-stop').addEventListener('click', () => {
    isRunning = false;
    statusText.innerText = "Stopping...";
});

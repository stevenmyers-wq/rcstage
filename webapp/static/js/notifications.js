let isRunning = false;
const logBox = document.getElementById('notifications-log');
const statusContainer = document.getElementById('status-container');
const statusText = document.getElementById('status-text');
const progressBar = document.getElementById('progress-bar');
const fileInput = document.getElementById('file-upload');
const btnUpdate = document.getElementById('btn-update-start');

// --- UTILS: Logging ---
function log(msg, type='info') {
    const entry = document.createElement('div');
    entry.className = type === 'error' ? 'text-red-400' : (type === 'success' ? 'text-green-400' : 'text-blue-300');
    entry.innerText = `[${new Date().toLocaleTimeString()}] ${msg}`;
    logBox.appendChild(entry);
    logBox.scrollTop = logBox.scrollHeight;
}

function updateStatus(active, text='', percent=0) {
    if (active) {
        statusContainer.classList.remove('hidden');
        statusText.innerText = text;
        progressBar.style.width = `${percent}%`;
    } else {
        statusContainer.classList.add('hidden');
    }
}

// --- UTILS: CSV Generation ---
function downloadCSV(data, filename) {
    const headers = Object.keys(data[0]);
    const csvRows = [headers.join(',')];
    for (const row of data) {
        const values = headers.map(header => {
            const val = row[header];
            const escaped = ('' + (val || '')).replace(/"/g, '\\"');
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

// --- 1. AUDIT ---
document.getElementById('btn-audit-start').addEventListener('click', async function() {
    isRunning = true;
    updateStatus(true, "Fetching extension list...", 0);
    log("Starting Audit...", "info");
    
    try {
        const listResp = await fetch('/api/notifications/get-targets');
        const listData = await listResp.json();
        
        if (!listData.targets) throw new Error("Failed to get list");
        
        const targets = listData.targets;
        const total = targets.length;
        let results = [];
        
        log(`Found ${total} extensions. Auditing...`);

        for (let i = 0; i < total; i++) {
            if (!isRunning) break;
            
            const t = targets[i];
            updateStatus(true, `Auditing (${i+1}/${total}): ${t.ext}`, Math.round(((i+1)/total)*100));
            
            let success = false;
            let attempt = 0;
            
            while (!success && attempt < 3) {
                attempt++;
                const resp = await fetch('/api/notifications/audit-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({id: t.id})
                });
                
                // Rate Limit Handler
                if (resp.status === 429) {
                    const errData = await resp.json();
                    const waitSecs = errData.retry_after || 60;
                    log(`Rate limit hit! Pausing for ${waitSecs} seconds...`, "error");
                    updateStatus(true, `Rate limited. Paused for ${waitSecs}s...`, Math.round(((i+1)/total)*100));
                    await new Promise(r => setTimeout(r, waitSecs * 1000));
                    continue; 
                }
                
                const json = await resp.json();
                
                if (json.status === 'success') {
                    results.push({
                        "Name": t.name,
                        "Extension": t.ext,
                        "Type": t.type,
                        ...json.data
                    });
                } else {
                    // Fallback to indicate API failure / unprovisioned
                    results.push({
                        "Name": t.name,
                        "Extension": t.ext,
                        "Type": t.type,
                        "Advanced Mode": "API ERROR / UNASSIGNED"
                    });
                    log(`[SKIP] Could not fetch settings for ${t.ext} - may be unassigned.`, 'error');
                }
                success = true;
            }
        }
        
        if (isRunning && results.length > 0) {
            downloadCSV(results, `Notification_Audit_${Date.now()}.csv`);
            log("Audit Complete! File downloaded.", "success");
        }
        
    } catch (e) {
        log(`Error: ${e.message}`, "error");
    } finally {
        isRunning = false;
        updateStatus(false);
    }
});

// --- 2. TEMPLATE ---
document.getElementById('btn-download-template').addEventListener('click', () => {
    const template = [
        { 
            "Extension ID": "1001", 
            "Advanced Mode": "FALSE", 
            "Global Emails": "queue_manager@email.com",
            "Enable Voicemail": "TRUE",
            "Enable MissedCalls": "TRUE",
            "Enable Faxes": "FALSE",
            "Enable SMS": "TRUE",
            "Voicemail Emails": "", 
            "Fax Emails": "", 
            "SMS Emails": "",
            "MissedCall Emails": ""
        },
        { 
            "Extension ID": "1002", 
            "Advanced Mode": "TRUE", 
            "Global Emails": "",
            "Enable Voicemail": "TRUE",
            "Enable MissedCalls": "FALSE",
            "Enable Faxes": "TRUE",
            "Enable SMS": "FALSE",
            "Voicemail Emails": "my_vm@email.com", 
            "Fax Emails": "my_fax@email.com", 
            "SMS Emails": "",
            "MissedCall Emails": ""
        }
    ];
    downloadCSV(template, 'notification_template.csv');
    log("Template downloaded. See examples for Basic vs Advanced.", "success");
});

// --- 3. UPDATE ---
document.getElementById('file-upload').addEventListener('change', function() {
    const file = this.files[0];
    if (!file) return;
    
    const reader = new FileReader();
    reader.onload = async function(e) {
        const text = e.target.result;
        const rows = text.split('\n').map(r => r.trim()).filter(r => r);
        const headers = rows[0].split(',').map(h => h.replace(/"/g, '').trim());
        
        const tasks = [];
        for (let i = 1; i < rows.length; i++) {
            const cols = rows[i].split(',').map(c => c.replace(/"/g, '').trim());
            if (cols.length < 2) continue; 
            let obj = {};
            headers.forEach((h, idx) => obj[h] = cols[idx]);
            tasks.push(obj);
        }
        
        if (tasks.length === 0) return log("No valid rows found.", "error");
        
        if (confirm(`Ready to update ${tasks.length} records?`)) {
            startUpdateLoop(tasks);
        }
        document.getElementById('file-upload').value = '';
    };
    reader.readAsText(file);
});

async function startUpdateLoop(tasks) {
    isRunning = true;
    log(`Starting bulk update...`);
    
    for (let i = 0; i < tasks.length; i++) {
        if (!isRunning) break;
        
        const task = tasks[i];
        const extId = task['Extension ID'];
        updateStatus(true, `Updating (${i+1}/${tasks.length}): ID ${extId}`, Math.round(((i+1)/tasks.length)*100));
        
        const payload = { 
            id: extId,
            advanced_mode: task['Advanced Mode'],
            global_emails: task['Global Emails'],
            enable_vm: task['Enable Voicemail'],
            enable_missed: task['Enable MissedCalls'],
            enable_fax: task['Enable Faxes'],
            enable_sms: task['Enable SMS'],
            vm_emails: task['Voicemail Emails'],
            fax_emails: task['Fax Emails'],
            sms_emails: task['SMS Emails'],
            missed_emails: task['MissedCall Emails']
        };

        let success = false;
        let attempt = 0;

        while (!success && attempt < 3) {
            attempt++;
            try {
                const resp = await fetch('/api/notifications/update-single', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                
                // Rate Limit Handler
                if (resp.status === 429) {
                    const errData = await resp.json();
                    const waitSecs = errData.retry_after || 60;
                    log(`Rate limit hit! Pausing for ${waitSecs} seconds...`, "error");
                    updateStatus(true, `Rate limited. Paused for ${waitSecs}s...`, Math.round(((i+1)/tasks.length)*100));
                    await new Promise(r => setTimeout(r, waitSecs * 1000));
                    continue;
                }

                const resJson = await resp.json();
                if (resJson.status === 'success') {
                    log(`✅ Updated ID ${extId}`, "success");
                } else {
                    log(`❌ Failed ID ${extId}: ${resJson.message}`, "error");
                }
                success = true;
            } catch (err) {
                log(`❌ Error ID ${extId}: ${err.message}`, "error");
                success = true; // Break out of retry loop on generic network crash
            }
        }
    }
    isRunning = false;
    updateStatus(false);
    log("Bulk update complete.");
}

document.getElementById('btn-stop').addEventListener('click', () => {
    isRunning = false;
    log("Stopping process...", "error");
});

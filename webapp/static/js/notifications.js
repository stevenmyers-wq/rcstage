let isNotificationAuditRunning = false;

document.getElementById('btn-notifications-run').addEventListener('click', async function() {
    const btnRun = document.getElementById('btn-notifications-run');
    const btnStop = document.getElementById('btn-notifications-stop');
    const statusBox = document.getElementById('notifications-status-box');
    const currentAction = document.getElementById('notifications-current-action');
    const progressBar = document.getElementById('notifications-progress-bar');
    const progressText = document.getElementById('notifications-progress-text');
    const logArea = document.getElementById('notifications-log');

    // 1. Reset UI
    isNotificationAuditRunning = true;
    btnRun.style.display = 'none';
    btnStop.style.display = 'inline-block';
    statusBox.style.display = 'block';
    progressBar.style.width = '0%';
    progressText.innerText = '0%';
    logArea.value = "--- Starting Notification Audit ---\n";

    try {
        // 2. Fetch the list of users first (Fast)
        currentAction.innerText = "Retrieving list of extensions...";
        const listResp = await fetch('/api/notifications/get-targets');
        const listData = await listResp.json();

        if (!listData.targets) throw new Error("Could not retrieve extension list.");
        
        const targets = listData.targets;
        const total = targets.length;
        logArea.value += `Found ${total} extensions. Starting checks...\n`;

        // 3. Process one by one (The Loop)
        for (let i = 0; i < total; i++) {
            if (!isNotificationAuditRunning) {
                logArea.value += "\n[!] Process stopped by user.\n";
                break;
            }

            const target = targets[i];

            // --- UPDATE STATUS TEXT (This is what you asked for) ---
            currentAction.innerText = `Retrieving ext notifications for ${target.name} (Ext ${target.ext})...`;
            
            // Update progress bar
            const percent = Math.round(((i + 1) / total) * 100);
            progressBar.style.width = `${percent}%`;
            progressText.innerText = `${percent}%`;

            // --- CALL BACKEND ---
            const checkResp = await fetch('/api/notifications/check-single', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: target.id })
            });
            const result = await checkResp.json();

            // --- LOG RESULT ---
            if (result.result === "MISSING_EMAIL") {
                logArea.value += `[WARN] Ext ${target.ext}: No email notification set.\n`;
            } else {
                logArea.value += `[OK] Ext ${target.ext}: Settings valid.\n`;
            }

            // Auto-scroll log to bottom
            logArea.scrollTop = logArea.scrollHeight;
        }

        currentAction.innerText = "Audit Complete.";
        currentAction.className = "text-success fw-bold";

    } catch (err) {
        logArea.value += `[ERROR] ${err.message}\n`;
        currentAction.innerText = "Error occurred.";
        currentAction.className = "text-danger fw-bold";
    } finally {
        isNotificationAuditRunning = false;
        btnRun.style.display = 'inline-block';
        btnStop.style.display = 'none';
    }
});

// Stop Button Handler
document.getElementById('btn-notifications-stop').addEventListener('click', function() {
    isNotificationAuditRunning = false;
    document.getElementById('notifications-current-action').innerText = "Stopping...";
});

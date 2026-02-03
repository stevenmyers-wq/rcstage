// webapp/static/js/live_events.js

document.addEventListener('DOMContentLoaded', () => {
    initLiveEventsListener();
});

let socket = null; // This will hold our WebSocket object
let activeSubscriptionId = null; // This will hold the ID of the current subscription
let allLogEntries = [];
let eventLogContainer;

function initLiveEventsListener() {
    if (document.getElementById('live-events-listener').hasAttribute('data-initialized')) return;

    // Attach event listeners to buttons
    document.getElementById('createSubscriptionBtn').addEventListener('click', startWebSocketConnection);
    document.getElementById('disconnectBtn').addEventListener('click', disconnect);
    document.getElementById('refreshSubscriptionsBtn').addEventListener('click', listActiveSubscriptions);
    document.getElementById('saveLogBtn').addEventListener('click', saveLogToFile);
    document.getElementById('clearLogBtn').addEventListener('click', clearLog);
    document.getElementById('logSearchInput').addEventListener('input', searchLog);
    document.getElementById('subscriptionType').addEventListener('change', () => {
        const selected = document.getElementById('subscriptionType').value;
        const extIdContainer = document.getElementById('extensionIdInputContainer');
        extIdContainer.style.display = (selected === 'extensionTelephony' || selected === 'extensionPresence') ? 'block' : 'none';
    });

    eventLogContainer = document.getElementById('eventLog');
    listActiveSubscriptions(); // Load any persistent WebHook subs
    document.getElementById('live-events-listener').setAttribute('data-initialized', 'true');
    window.addEventListener('beforeunload', () => { if (socket) { disconnect(); } });
}

function updateConnectionStatus(status, message) {
    const statusBadge = document.getElementById('connectionStatus');
    const statuses = {
        idle: { class: 'bg-secondary', text: 'Idle' },
        connecting: { class: 'bg-warning text-dark', text: 'Connecting...' },
        connected: { class: 'bg-success', text: 'Connected' },
        subscribing: { class: 'bg-info text-dark', text: 'Subscribing...' },
        error: { class: 'bg-danger', text: 'Error' },
        disconnecting: { class: 'bg-warning text-dark', text: 'Disconnecting...' },
        disconnected: { class: 'bg-secondary', text: 'Disconnected' },
    };
    const newStatus = statuses[status] || statuses['idle'];
    statusBadge.innerHTML = `<span class="badge ${newStatus.class}">${message || newStatus.text}</span>`;
}

async function startWebSocketConnection() {
    if (socket) {
        logMessage("A connection is already active. Please disconnect first.");
        return;
    }
    document.getElementById('createSubscriptionBtn').disabled = true;
    updateConnectionStatus('connecting');

    try {
        const response = await fetch('/api/live_events/wss-credentials', { method: 'POST' });
        const wssCredentials = await response.json();

        if (!response.ok) throw new Error(wssCredentials.error || 'Failed to get WSS credentials.');

        const fullWssUrl = `${wssCredentials.uri}?access_token=${wssCredentials.ws_access_token}`;
        socket = new WebSocket(fullWssUrl);

        socket.onopen = () => {
            //logMessage("--- WebSocket Connection Opened ---");
            //subscribeToEvents();
            logMessage("--- TESTING FORCED SIPDATA ---");
    const testMsg = [
        { "type": "ClientRequest", "messageId": "test-123", "method": "POST", "path": "/restapi/v1.0/subscription" },
        { 
          "deliveryMode": { "transportType": "WebSocket" }, 
          "eventFilters": ["/restapi/v1.0/account/~/telephony/sessions?sipData=true"] 
        }
    ];
    socket.send(JSON.stringify(testMsg));
        };

        socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            const timestamp = new Date().toISOString();
            const meta = data[0];
            const body = data[1];

            // Handle the three types of messages from RingCentral
            if (meta.type === "ConnectionDetails") {
                logMessage(`[${timestamp}] Connection confirmed. Sequence: ${body.wsc.sequence}`);
            } else if (meta.type === "ServerNotification" && body.subscriptionId) {
                // This is a REAL EVENT notification
                const logEntry = `[${timestamp}] EVENT \n${JSON.stringify(body, null, 2)}\n\n`;
                allLogEntries.push(logEntry);
                eventLogContainer.textContent += logEntry;
                eventLogContainer.scrollTop = eventLogContainer.scrollHeight;
            } else if (meta.type === "ClientRequest" && body.id) {
                // This is the CONFIRMATION that our subscription was created
                activeSubscriptionId = body.id; // Store the active subscription ID
                updateConnectionStatus('connected'); // Move from "Subscribing" to "Connected"
                logMessage(`--- Subscription Active (ID: ${activeSubscriptionId}) ---`);
                listActiveSubscriptions(); // Refresh the list to show the new sub
            } else {
                // Log any other message types for debugging
                logMessage(`[${timestamp}] UNKNOWN MESSAGE \n${JSON.stringify(data, null, 2)}\n\n`);
            }
        };

        socket.onerror = (error) => {
            console.error('WebSocket Error:', error);
            logMessage("--- WebSocket Error --- \nSee browser console for details.");
            updateConnectionStatus('error', 'Connection Failed');
            document.getElementById('createSubscriptionBtn').disabled = false;
        };

        socket.onclose = () => {
            logMessage("--- WebSocket Connection Closed ---");
            updateConnectionStatus('disconnected');
            activeSubscriptionId = null; // Clear the subscription ID
            document.getElementById('createSubscriptionBtn').disabled = false;
            document.getElementById('disconnectBtn').disabled = true;
            listActiveSubscriptions(); // Refresh the list to show it's gone
        };

        document.getElementById('disconnectBtn').disabled = false;

    } catch (error) {
        console.error('Connection Error:', error);
        updateConnectionStatus('error', error.message);
        document.getElementById('createSubscriptionBtn').disabled = false;
    }
}

function subscribeToEvents() {
    // ... (This function remains unchanged)
    const subType = document.getElementById('subscriptionType').value;
    const extId = document.getElementById('extensionId').value;
    let eventFilters = [];

    switch (subType) {
        case 'accountTelephony': eventFilters.push('/restapi/v1.0/account/~/telephony/sessions?sipData=true'); break;
        case 'extensionTelephony':
            if (!extId) { alert('Extension ID is required.'); return; }
            eventFilters.push(`/restapi/v1.0/account/~/extension/${extId}/telephony/sessions?sipData=true`); break;
        case 'accountPresence': eventFilters.push('/restapi/v1.0/account/~/presence?detailedTelephonyState=true'); break;
        case 'extensionPresence':
            if (!extId) { alert('Extension ID is required.'); return; }
            eventFilters.push(`/restapi/v1.0/account/~/extension/${extId}/presence?detailedTelephonyState=true`); break;
    }
    
    if (eventFilters.length === 0) return;

    const subscriptionMessage = [
        { "type": "ClientRequest", "messageId": crypto.randomUUID(), "method": "POST", "path": "/restapi/v1.0/subscription" },
        { "deliveryMode": { "transportType": "WebSocket" }, "eventFilters": eventFilters }
    ];

    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(subscriptionMessage));
        updateConnectionStatus('subscribing');
        logMessage(`--- Sending subscription request for: ${eventFilters.join(', ')} ---`);
    } else {
        logMessage("Cannot subscribe: WebSocket is not open.");
        updateConnectionStatus('error', 'Subscription Failed');
    }
}

async function disconnect() {
    updateConnectionStatus('disconnecting');
    if (activeSubscriptionId) {
        logMessage(`--- Deleting active subscription (ID: ${activeSubscriptionId}) ---`);
        try {
            // Attempt to delete the subscription from the server
            await deleteSubscriptionById(activeSubscriptionId, false); // false to suppress confirmation prompt
        } catch (error) {
            console.error("Failed to delete subscription on server, it may have already been cleaned up:", error);
        }
    }
    
    if (socket) {
        socket.close();
    }
}

function logMessage(message) {
    // ... (This function remains unchanged)
    const timestamp = new Date().toISOString();
    const logEntry = `[${timestamp}] \n${message}\n\n`;
    allLogEntries.push(logEntry);
    eventLogContainer.textContent += logEntry;
    eventLogContainer.scrollTop = eventLogContainer.scrollHeight;
}

// These helper functions remain unchanged but are now used more effectively.
async function listActiveSubscriptions() {
    // ... (This function remains unchanged)
    const tableBody = document.getElementById('activeSubscriptionsTable');
    tableBody.innerHTML = '<tr><td colspan="5">Loading...</td></tr>';

    try {
        const response = await fetch('/api/live_events/subscriptions');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Failed to fetch subscriptions.');

        if (data.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="5">No active persistent subscriptions found.</td></tr>';
            return;
        }

        tableBody.innerHTML = '';
        data.forEach(sub => {
            const row = `
                <tr>
                    <td><small>${sub.id}</small></td>
                    <td>${sub.deliveryMode.transportType}</td>
                    <td><small>${sub.eventFilters.join(', ')}</small></td>
                    <td>${sub.status}</td>
                    <td>
                        <button class="btn btn-danger btn-sm" onclick="deleteSubscriptionById('${sub.id}')">
                            <i class="bi bi-trash"></i>
                        </button>
                    </td>
                </tr>
            `;
            tableBody.innerHTML += row;
        });
    } catch (error) {
        console.error('List Subscriptions Error:', error);
        tableBody.innerHTML = `<tr><td colspan="5" class="text-danger">${error.message}</td></tr>`;
    }
}

window.deleteSubscriptionById = async function(subscriptionId, confirmFirst = true) {
    // ... (This function is slightly modified to allow skipping the confirm prompt)
    if (confirmFirst && !confirm(`Are you sure you want to delete subscription ${subscriptionId}?`)) {
        return;
    }

    try {
        const response = await fetch(`/api/live_events/subscriptions/${subscriptionId}`, { method: 'DELETE' });
        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.error || 'Failed to delete.');
        }
        listActiveSubscriptions(); // Refresh the list
    } catch (error) {
        if (confirmFirst) alert(`Error: ${error.message}`);
        else console.error(`Silent delete failed: ${error.message}`);
    }
};

function saveLogToFile() {
    // ... (This function remains unchanged)
    const text = allLogEntries.join('');
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
a.href = URL.createObjectURL(blob);
    a.download = `ringcentral-events-${new Date().toISOString()}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
}

function clearLog() {
    // ... (This function remains unchanged)
    allLogEntries = [];
    eventLogContainer.textContent = '';
}

function searchLog() {
    // ... (This function remains unchanged)
    const searchTerm = document.getElementById('logSearchInput').value.toLowerCase();
    if (!searchTerm) {
        eventLogContainer.textContent = allLogEntries.join('');
        return;
    }
    const filteredEntries = allLogEntries.filter(entry => entry.toLowerCase().includes(searchTerm));
    eventLogContainer.textContent = filteredEntries.join('');
}

// webapp/static/js/live_events.js
document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const createSubBtn = document.getElementById('createSubscriptionBtn');
    const disconnectBtn = document.getElementById('disconnectBtn');
    const connectionStatus = document.getElementById('connectionStatus');
    const eventLog = document.getElementById('eventLog');
    const clearLogBtn = document.getElementById('clearLogBtn');
    const subTypeSelect = document.getElementById('subscriptionType');
    const extInputContainer = document.getElementById('extensionIdInputContainer');
    const extInput = document.getElementById('extensionId');
    const saveLogBtn = document.getElementById('saveLogBtn');
    
    let webSocket = null;

    // Show/Hide Extension Input based on select dropdown
    subTypeSelect.addEventListener('change', (e) => {
        if (e.target.value.includes('extension')) {
            extInputContainer.style.display = 'block';
        } else {
            extInputContainer.style.display = 'none';
            extInput.value = ''; // Clear value when hidden
        }
    });

    // Logging helper
    function logEvent(message, type = 'info') {
        const time = new Date().toLocaleTimeString();
        let colorClass = 'text-green-400'; 
        
        if (type === 'error') colorClass = 'text-red-400';
        if (type === 'system') colorClass = 'text-blue-300';
        
        const logLine = `<span class="text-gray-500">[${time}]</span> <span class="${colorClass}">${message}</span>\n`;
        eventLog.innerHTML += logLine;
        eventLog.scrollTop = eventLog.scrollHeight; // Auto-scroll to bottom
    }

    // Save Log Helper
    saveLogBtn.addEventListener('click', () => {
        const textToSave = eventLog.innerText;
        const blob = new Blob([textToSave], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `rc_event_log_${new Date().toISOString().slice(0,10)}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    });

    // Clear Log Button
    clearLogBtn.addEventListener('click', () => {
        eventLog.innerHTML = '';
    });

    function generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    // Connect to WebSocket and Subscribe
    createSubBtn.addEventListener('click', async () => {
        const subType = subTypeSelect.value;
        const extId = extInput.value.trim();

        if (subType.includes('extension') && !extId) {
            logEvent('Error: Extension ID is required for this subscription type.', 'error');
            return;
        }

        logEvent('Fetching WSS credentials...', 'system');
        
        try {
            const response = await fetch('/api/live_events/wss-credentials', { method: 'POST' });
            if (response.status === 401) {
                logEvent('Error: Not authenticated with RingCentral.', 'error');
                return;
            }
            
            const data = await response.json();
            if (data.error) throw new Error(data.error);
            if (!data.uri || !data.ws_access_token) throw new Error('Incomplete WSS credentials returned from API.');

            const connectionUrl = `${data.uri}?access_token=${data.ws_access_token}`;

            logEvent(`Connecting to RingCentral WebSocket...`, 'system');
            webSocket = new WebSocket(connectionUrl);

            webSocket.onopen = () => {
                connectionStatus.innerHTML = '<span class="px-3 py-1.5 bg-green-200 text-green-800 rounded-full text-xs font-bold uppercase tracking-wider">Status: Connected</span>';
                logEvent('WebSocket connection successfully established!', 'system');
                
                createSubBtn.disabled = true;
                disconnectBtn.disabled = false;
                subTypeSelect.disabled = true;
                extInput.disabled = true;

                let eventFilter = '';
                if (subType === 'accountTelephony') {
                    eventFilter = '/restapi/v1.0/account/~/telephony/sessions?sipData=true';
                } else if (subType === 'extensionTelephony') {
                    eventFilter = `/restapi/v1.0/account/~/extension/${extId}/telephony/sessions?sipData=true`;
                } else if (subType === 'accountPresence') {
                    eventFilter = '/restapi/v1.0/account/~/presence?detailedTelephonyState=true&sipData=true';
                } else if (subType === 'extensionPresence') {
                    eventFilter = `/restapi/v1.0/account/~/extension/${extId}/presence?detailedTelephonyState=true&sipData=true`;
                }

                logEvent(`Creating subscription for: ${eventFilter}`, 'system');

                const requestPayload = [
                    {
                        "type": "ClientRequest",
                        "messageId": generateUUID(),
                        "method": "POST",
                        "path": "/restapi/v1.0/subscription"
                    },
                    {
                        "eventFilters": [eventFilter],
                        "deliveryMode": {
                            "transportType": "WebSocket"
                        }
                    }
                ];

                webSocket.send(JSON.stringify(requestPayload));
            };

            webSocket.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    
                    if (Array.isArray(payload) && payload[0] && payload[0].type === 'Heartbeat') {
                         return;
                    }
                    
                    if (Array.isArray(payload) && payload[0] && payload[0].type === 'ClientResponse') {
                        if (payload[0].status === 200 || payload[0].status === 201) {
                            logEvent('Subscription active! Listening for events...', 'system');
                        } else {
                            logEvent(`Subscription failed: ${JSON.stringify(payload[1])}`, 'error');
                        }
                        return;
                    }
                    
                    logEvent(JSON.stringify(payload, null, 2), 'info');
                } catch (e) {
                    logEvent(`Raw Message: ${event.data}`, 'info');
                }
            };

            webSocket.onerror = (error) => {
                logEvent('WebSocket encountered an error.', 'error');
                console.error('WS Error:', error);
            };

            webSocket.onclose = () => {
                connectionStatus.innerHTML = '<span class="px-3 py-1.5 bg-gray-200 text-gray-700 rounded-full text-xs font-bold uppercase tracking-wider">Status: Idle</span>';
                logEvent('WebSocket connection closed.', 'system');
                
                createSubBtn.disabled = false;
                disconnectBtn.disabled = true;
                subTypeSelect.disabled = false;
                extInput.disabled = false;
                webSocket = null;
            };

        } catch (error) {
            logEvent(`Connection failed: ${error.message}`, 'error');
        }
    });

    // Disconnect Button
    disconnectBtn.addEventListener('click', () => {
        if (webSocket) {
            logEvent('Closing connection...', 'system');
            webSocket.close();
        }
    });
});

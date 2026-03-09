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
        }
    });

    // Logging helper
    function logEvent(message, type = 'info') {
        const time = new Date().toLocaleTimeString();
        let colorClass = 'text-green-400'; // default info
        
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

    // Generate a simple UUID for message IDs
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

        // Validate extension ID if required
        if (subType.includes('extension') && !extId) {
            logEvent('Error: Extension ID is required for this subscription type.', 'error');
            return;
        }

        logEvent('Fetching WSS credentials...', 'system');
        
        try {
            // 1. Get WSS token from your backend
            const response = await fetch('/api/live_events/wss-credentials', { method: 'POST' });
            if (response.status === 401) {
                logEvent('Error: Not authenticated with RingCentral.', 'error');
                return;
            }
            
            const data = await response.json();
            if (data.error) throw new Error(data.error);
            if (!data.uri || !data.ws_access_token) throw new Error('Incomplete WSS credentials returned from API.');

            // 2. Construct the full URL by appending the token as a query parameter
            const connectionUrl = `${data.uri}?access_token=${data.ws_access_token}`;

            logEvent(`Connecting to RingCentral WebSocket...`, 'system');
            webSocket = new WebSocket(connectionUrl);

            webSocket.onopen = () => {
                connectionStatus.innerHTML = '<span class="px-2 py-1 bg-green-200 text-green-800 rounded text-xs font-semibold">Status: Connected</span>';
                logEvent('WebSocket connection successfully established!', 'system');
                
                createSubBtn.disabled = true;
                disconnectBtn.disabled = false;

                // 3. Determine the correct Event Filter based on UI selection
                // We append the required query parameters to explicitly request the SIP payload from RingCentral
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

                // 4. Send the ClientRequest to create the subscription over the socket
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
                    // Try to parse as JSON for pretty printing
                    const payload = JSON.parse(event.data);
                    
                    // Filter out the noisy heartbeat messages
                    if (Array.isArray(payload) && payload[0] && payload[0].type === 'Heartbeat') {
                         return;
                    }
                    
                    // Check if this is the confirmation of our subscription
                    if (Array.isArray(payload) && payload[0] && payload[0].type === 'ClientResponse') {
                        if (payload[0].status === 200 || payload[0].status === 201) {
                            logEvent('Subscription active! Listening for events...', 'system');
                        } else {
                            logEvent(`Subscription failed: ${JSON.stringify(payload[1])}`, 'error');
                        }
                        return;
                    }
                    
                    // Log actual live events
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
                connectionStatus.innerHTML = '<span class="px-2 py-1 bg-gray-200 text-gray-700 rounded text-xs font-semibold">Status: Disconnected</span>';
                logEvent('WebSocket connection closed.', 'system');
                
                createSubBtn.disabled = false;
                disconnectBtn.disabled = true;
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

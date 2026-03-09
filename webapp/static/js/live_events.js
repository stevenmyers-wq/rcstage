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
    
    let webSocket = null;

    // Show/Hide Extension Input based on select dropdown
    subTypeSelect.addEventListener('change', (e) => {
        if (e.target.value.includes('Specific')) {
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

    // Connect to WebSocket
    createSubBtn.addEventListener('click', async () => {
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
            if (!data.uri) throw new Error('No URI returned from API.');

            // 2. Establish native WebSocket connection
            logEvent(`Connecting to: ${data.uri.split('?')[0]}...`, 'system');
            webSocket = new WebSocket(data.uri);

            webSocket.onopen = () => {
                connectionStatus.innerHTML = '<span class="px-2 py-1 bg-green-200 text-green-800 rounded text-xs font-semibold">Status: Connected</span>';
                logEvent('WebSocket connection successfully established.', 'system');
                
                createSubBtn.disabled = true;
                disconnectBtn.disabled = false;

                // NOTE: In a production app, you would send a ClientRequest JSON payload 
                // over this WS connection right here to specify WHICH events you want to subscribe to.
                logEvent('Listening for events...', 'system');
            };

            webSocket.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    // Pretty-print the incoming JSON payload
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

    // Clear Log Button
    clearLogBtn.addEventListener('click', () => {
        eventLog.innerHTML = '';
    });
});

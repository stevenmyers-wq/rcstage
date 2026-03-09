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
    
    // SIP Visualizer Elements
    const callSelector = document.getElementById('callSelector');
    const mermaidContainer = document.getElementById('mermaidDiagram');

    let webSocket = null;
    
    // State Tracker for concurrent calls
    // Format: { "sessionId1": { caller: "...", callee: "...", events: [{from, to, msg}] } }
    const activeCalls = {};

    // Initialize Mermaid
    mermaid.initialize({ startOnLoad: false, theme: 'base', themeVariables: { primaryColor: '#f0f9ff', primaryTextColor: '#1f2937', primaryBorderColor: '#bae6fd', lineColor: '#94a3b8' }});

    // Toggle Extension Input
    subTypeSelect.addEventListener('change', (e) => {
        if (e.target.value.includes('extension')) {
            extInputContainer.style.display = 'block';
        } else {
            extInputContainer.style.display = 'none';
            extInput.value = ''; 
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
        eventLog.scrollTop = eventLog.scrollHeight; 
    }

    // Save/Clear Log Buttons
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

    clearLogBtn.addEventListener('click', () => { eventLog.innerHTML = ''; });

    function generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    // --- SIP PARSER & RENDERER LOGIC ---

    // Translate RC Status into a SIP Equivalent
    function mapStatusToSip(status) {
        switch(status) {
            case 'Setup': return 'INVITE';
            case 'Proceeding': return '180 Ringing';
            case 'Answered': return '200 OK';
            case 'Disconnected': return 'BYE';
            case 'Hold': return 'INVITE (Hold)';
            default: return status;
        }
    }

    function processTelephonyEvent(payload) {
        const body = payload.body;
        if (!body || !body.telephonySessionId || !body.parties) return;

        const sessionId = body.telephonySessionId;
        
        // Initialize call bucket if new
        if (!activeCalls[sessionId]) {
            activeCalls[sessionId] = {
                id: sessionId,
                events: [],
                caller: 'Unknown',
                callee: 'Unknown',
                lastUpdate: new Date()
            };
            
            // Add to UI dropdown
            const option = document.createElement('option');
            option.value = sessionId;
            option.text = `Session: ${sessionId.substring(0,8)}...`;
            callSelector.appendChild(option);
            
            // Auto-select if it's the first call
            if (callSelector.options.length === 2 && callSelector.value === "") {
                callSelector.value = sessionId;
            }
        }

        const callData = activeCalls[sessionId];
        callData.lastUpdate = new Date();

        // Parse parties to find direction and status
        body.parties.forEach(party => {
            let participantName = party.extensionId ? `Ext ${party.extensionId}` : (party.from?.phoneNumber || party.to?.phoneNumber || 'External');
            
            // Basic heuristic to assign caller/callee names on first pass
            if (party.direction === 'Inbound' && callData.caller === 'Unknown') {
                callData.caller = party.from?.phoneNumber || 'Caller';
                callData.callee = party.to?.phoneNumber || party.extensionId || 'System';
            } else if (party.direction === 'Outbound' && callData.caller === 'Unknown') {
                callData.caller = party.extensionId || 'Ext';
                callData.callee = party.to?.phoneNumber || 'Callee';
            }

            // Record the state change as an event
            if (party.status && party.status.code) {
                const sipMsg = mapStatusToSip(party.status.code);
                
                // Avoid logging duplicates (e.g. repeated Proceeding events)
                const lastEvent = callData.events[callData.events.length - 1];
                if (!lastEvent || lastEvent.msg !== sipMsg || lastEvent.participant !== participantName) {
                    callData.events.push({
                        participant: participantName,
                        direction: party.direction,
                        msg: sipMsg,
                        rawStatus: party.status.code
                    });
                }
            }
        });

        // Update dropdown text with actual caller/callee now that we know it
        const optionToUpdate = Array.from(callSelector.options).find(opt => opt.value === sessionId);
        if (optionToUpdate) {
            optionToUpdate.text = `${callData.caller} -> ${callData.callee}`;
        }

        // If this is the currently viewed call, re-render the diagram
        if (callSelector.value === sessionId) {
            renderMermaidDiagram(sessionId);
        }
    }

    async function renderMermaidDiagram(sessionId) {
        const callData = activeCalls[sessionId];
        if (!callData || callData.events.length === 0) {
            mermaidContainer.innerHTML = '<div class="text-gray-500">No event data parsed yet.</div>';
            return;
        }

        // Build Mermaid syntax
        let mermaidCode = `sequenceDiagram\n    autonumber\n    participant Caller as ${callData.caller}\n    participant RC as RingCentral\n    participant Callee as ${callData.callee}\n\n`;

        callData.events.forEach(ev => {
            // Logic to draw arrows based on direction and status
            if (ev.direction === 'Inbound') {
                if (ev.rawStatus === 'Setup') mermaidCode += `    Caller->>RC: ${ev.msg}\n`;
                if (ev.rawStatus === 'Proceeding') mermaidCode += `    RC-->>Caller: ${ev.msg}\n`;
                if (ev.rawStatus === 'Answered') mermaidCode += `    RC->>Caller: ${ev.msg}\n`;
                if (ev.rawStatus === 'Disconnected') mermaidCode += `    RC->>Caller: ${ev.msg}\n`;
            } else if (ev.direction === 'Outbound') {
                if (ev.rawStatus === 'Setup') mermaidCode += `    RC->>Callee: ${ev.msg}\n`;
                if (ev.rawStatus === 'Proceeding') mermaidCode += `    Callee-->>RC: ${ev.msg}\n`;
                if (ev.rawStatus === 'Answered') mermaidCode += `    Callee->>RC: ${ev.msg}\n`;
                if (ev.rawStatus === 'Disconnected') mermaidCode += `    Callee->>RC: ${ev.msg}\n`;
            }
        });

        try {
            // Render the diagram
            const { svg } = await mermaid.render(`mermaid-${Date.now()}`, mermaidCode);
            mermaidContainer.innerHTML = svg;
        } catch (error) {
            console.error("Mermaid parsing error:", error);
            mermaidContainer.innerHTML = '<div class="text-red-500">Error rendering diagram. See console.</div>';
        }
    }

    // Allow user to switch between concurrent calls
    callSelector.addEventListener('change', (e) => {
        if (e.target.value) {
            renderMermaidDiagram(e.target.value);
        } else {
            mermaidContainer.innerHTML = '<div class="text-gray-500">Select a call to view flow.</div>';
        }
    });

    // --- WEBSOCKET CONNECTION ---

    createSubBtn.addEventListener('click', async () => {
        const subType = subTypeSelect.value;
        const extId = extInput.value.trim();

        if (subType.includes('extension') && !extId) {
            logEvent('Error: Extension ID is required.', 'error');
            return;
        }

        logEvent('Fetching WSS credentials...', 'system');
        
        try {
            const response = await fetch('/api/live_events/wss-credentials', { method: 'POST' });
            if (response.status === 401) return logEvent('Error: Not authenticated.', 'error');
            
            const data = await response.json();
            if (data.error) throw new Error(data.error);

            const connectionUrl = `${data.uri}?access_token=${data.ws_access_token}`;
            logEvent(`Connecting to RingCentral WebSocket...`, 'system');
            
            webSocket = new WebSocket(connectionUrl);

            webSocket.onopen = () => {
                connectionStatus.innerHTML = '<span class="px-3 py-1.5 bg-green-200 text-green-800 rounded-full text-xs font-bold uppercase tracking-wider">Status: Connected</span>';
                logEvent('WebSocket connected!', 'system');
                
                createSubBtn.disabled = true;
                disconnectBtn.disabled = false;
                subTypeSelect.disabled = true;
                extInput.disabled = true;

                let eventFilter = '';
                if (subType === 'accountTelephony') eventFilter = '/restapi/v1.0/account/~/telephony/sessions?sipData=true';
                else if (subType === 'extensionTelephony') eventFilter = `/restapi/v1.0/account/~/extension/${extId}/telephony/sessions?sipData=true`;
                else if (subType === 'accountPresence') eventFilter = '/restapi/v1.0/account/~/presence?detailedTelephonyState=true&sipData=true';
                else if (subType === 'extensionPresence') eventFilter = `/restapi/v1.0/account/~/extension/${extId}/presence?detailedTelephonyState=true&sipData=true`;

                const requestPayload = [
                    { "type": "ClientRequest", "messageId": generateUUID(), "method": "POST", "path": "/restapi/v1.0/subscription" },
                    { "eventFilters": [eventFilter], "deliveryMode": { "transportType": "WebSocket" } }
                ];

                webSocket.send(JSON.stringify(requestPayload));
            };

            webSocket.onmessage = (event) => {
                try {
                    const payload = JSON.parse(event.data);
                    
                    if (Array.isArray(payload) && payload[0] && payload[0].type === 'Heartbeat') return;
                    
                    if (Array.isArray(payload) && payload[0] && payload[0].type === 'ClientResponse') {
                        if (payload[0].status === 200 || payload[0].status === 201) logEvent('Subscription active!', 'system');
                        else logEvent(`Subscription failed: ${JSON.stringify(payload[1])}`, 'error');
                        return;
                    }
                    
                    // Log raw JSON
                    logEvent(JSON.stringify(payload, null, 2), 'info');

                    // If it's a telephony event, parse it for the SIP Ladder
                    if (Array.isArray(payload) && payload[1] && payload[1].event && payload[1].event.includes('telephony/sessions')) {
                        processTelephonyEvent(payload[1]);
                    }

                } catch (e) {
                    logEvent(`Raw Message: ${event.data}`, 'info');
                }
            };

            webSocket.onerror = (error) => { logEvent('WebSocket error.', 'error'); };
            webSocket.onclose = () => {
                connectionStatus.innerHTML = '<span class="px-3 py-1.5 bg-gray-200 text-gray-700 rounded-full text-xs font-bold uppercase tracking-wider">Status: Idle</span>';
                logEvent('WebSocket closed.', 'system');
                
                createSubBtn.disabled = false;
                disconnectBtn.disabled = true;
                subTypeSelect.disabled = false;
                extInput.disabled = false;
                webSocket = null;
            };

        } catch (error) { logEvent(`Connection failed: ${error.message}`, 'error'); }
    });

    disconnectBtn.addEventListener('click', () => { if (webSocket) webSocket.close(); });
});

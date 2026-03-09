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
    const ladderSearchInput = document.getElementById('ladderSearchInput');
    const mermaidContainer = document.getElementById('mermaidDiagram');
    
    // SIP Modal Elements
    const sipModal = document.getElementById('sipModal');
    const closeSipModalBtn = document.getElementById('closeSipModalBtn');
    const sipModalTitle = document.getElementById('sipModalTitle');
    const sipModalContent = document.getElementById('sipModalContent');

    let webSocket = null;
    const activeCalls = {};

    mermaid.initialize({ startOnLoad: false, theme: 'base', themeVariables: { primaryColor: '#f0f9ff', primaryTextColor: '#1f2937', primaryBorderColor: '#bae6fd', lineColor: '#94a3b8' }});

    subTypeSelect.addEventListener('change', (e) => {
        if (e.target.value.includes('extension')) extInputContainer.style.display = 'block';
        else { extInputContainer.style.display = 'none'; extInput.value = ''; }
    });

    function logEvent(message, type = 'info') {
        const time = new Date().toLocaleTimeString();
        let colorClass = 'text-green-400'; 
        if (type === 'error') colorClass = 'text-red-400';
        if (type === 'system') colorClass = 'text-blue-300';
        eventLog.innerHTML += `<span class="text-gray-500">[${time}]</span> <span class="${colorClass}">${message}</span>\n`;
        eventLog.scrollTop = eventLog.scrollHeight; 
    }

    saveLogBtn.addEventListener('click', () => {
        const blob = new Blob([eventLog.innerText], { type: 'text/plain' });
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
    closeSipModalBtn.addEventListener('click', () => { sipModal.classList.add('hidden'); });

    function generateUUID() {
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
    }

    // --- SIP PARSER & RENDERER LOGIC ---

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

    function updateCallSelector() {
        const searchTerm = ladderSearchInput.value.toLowerCase();
        const currentValue = callSelector.value;
        
        callSelector.innerHTML = '<option value="">-- Select a call --</option>';
        let matchCount = 0;

        Object.values(activeCalls).forEach(call => {
            const searchString = `${call.id} ${call.caller} ${call.callee}`.toLowerCase();
            if (searchString.includes(searchTerm)) {
                const option = document.createElement('option');
                option.value = call.id;
                option.text = `${call.caller} -> ${call.callee}`;
                
                if (call.id === currentValue) {
                    option.selected = true;
                }
                callSelector.appendChild(option);
                matchCount++;
            }
        });

        if (Object.keys(activeCalls).length === 0) {
            callSelector.options[0].text = '-- No active calls --';
        } else if (matchCount === 0) {
            callSelector.options[0].text = '-- No matches found --';
        }

        if (matchCount === 1 && !callSelector.value) {
            callSelector.value = callSelector.options[1].value;
            renderMermaidDiagram(callSelector.value);
        }
    }

    function processTelephonyEvent(payload) {
        const body = payload.body;
        if (!body || !body.telephonySessionId || !body.parties) return;

        const sessionId = body.telephonySessionId;
        
        if (!activeCalls[sessionId]) {
            // Added masterSipData to correlate headers across the entire session lifecycle
            activeCalls[sessionId] = { id: sessionId, events: [], caller: 'Unknown', callee: 'Unknown', masterSipData: {} };
        }

        const callData = activeCalls[sessionId];

        body.parties.forEach(party => {
            let participantName = party.extensionId ? `Ext ${party.extensionId}` : (party.from?.phoneNumber || party.to?.phoneNumber || 'External');
            
            if (party.direction === 'Inbound' && callData.caller === 'Unknown') {
                callData.caller = party.from?.phoneNumber || 'Caller';
                callData.callee = party.to?.phoneNumber || party.extensionId || 'System';
            } else if (party.direction === 'Outbound' && callData.caller === 'Unknown') {
                callData.caller = party.extensionId || 'Ext';
                callData.callee = party.to?.phoneNumber || 'Callee';
            }

            // CORRELATION LOGIC: Learn any SIP data provided in this leg
            if (party.sipData && Object.keys(party.sipData).length > 0) {
                Object.assign(callData.masterSipData, party.sipData);
            }

            if (party.status && party.status.code) {
                const sipMsg = mapStatusToSip(party.status.code);
                
                const lastEvent = callData.events[callData.events.length - 1];
                if (!lastEvent || lastEvent.msg !== sipMsg || lastEvent.participant !== participantName) {
                    callData.events.push({
                        participant: participantName,
                        direction: party.direction,
                        msg: sipMsg,
                        rawStatus: party.status.code,
                        rawSipData: party.sipData || {},
                        // Take a snapshot of everything we know about this call's SIP headers so far
                        correlatedSipData: { ...callData.masterSipData } 
                    });
                }
            }
        });

        updateCallSelector();

        if (callSelector.value === sessionId) renderMermaidDiagram(sessionId);
    }

    function openSipModal(eventData, caller, callee) {
        sipModalTitle.textContent = `[${eventData.msg}] Details`;
        
        let content = `<span class="text-pink-400 font-bold">${eventData.msg}</span> <span class="text-orange-400">sip:${callee}@ringcentral.com</span> SIP/2.0\n`;
        content += `<span class="text-blue-300">Session-ID:</span> ${activeCalls[callSelector.value].id}\n`;
        
        // Use the correlated SIP data (gathered across the whole session) for the primary display
        const displaySip = Object.keys(eventData.correlatedSipData).length > 0 ? eventData.correlatedSipData : eventData.rawSipData;

        if (displaySip.callId) content += `<span class="text-blue-300">Call-ID:</span> ${displaySip.callId}\n`;
        if (displaySip.fromTag) content += `<span class="text-blue-300">From:</span> &lt;sip:${caller}&gt;;tag=${displaySip.fromTag}\n`;
        if (displaySip.toTag) content += `<span class="text-blue-300">To:</span> &lt;sip:${callee}&gt;;tag=${displaySip.toTag}\n`;
        
        content += `\n<span class="text-gray-400">--- RC Raw SIP Object (This Specific Event) ---</span>\n`;
        // Show exactly what RC sent for this specific packet, so you know if it was empty or not
        content += `<span class="text-green-200">${JSON.stringify(eventData.rawSipData, null, 2)}</span>`;

        sipModalContent.innerHTML = content;
        sipModal.classList.remove('hidden');
    }

    async function renderMermaidDiagram(sessionId) {
        const callData = activeCalls[sessionId];
        if (!callData || callData.events.length === 0) {
             mermaidContainer.innerHTML = '<div class="text-gray-500">Select a call to view flow.</div>';
             return;
        }

        let mermaidCode = `sequenceDiagram\n    autonumber\n    participant Caller as ${callData.caller}\n    participant RC as RingCentral\n    participant Callee as ${callData.callee}\n\n`;

        callData.events.forEach(ev => {
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
            const { svg } = await mermaid.render(`mermaid-${Date.now()}`, mermaidCode);
            mermaidContainer.innerHTML = svg;

            setTimeout(() => {
                const messageLabels = mermaidContainer.querySelectorAll('.messageText');
                messageLabels.forEach((label, index) => {
                    if (callData.events[index]) {
                        label.style.cursor = 'pointer';
                        label.style.fill = '#2563eb'; 
                        label.style.textDecoration = 'underline';
                        label.innerHTML += `<title>Click to view SIP details</title>`;
                        
                        label.addEventListener('click', () => {
                            openSipModal(callData.events[index], callData.caller, callData.callee);
                        });
                    }
                });
            }, 50); 

        } catch (error) {
            console.error("Mermaid parsing error:", error);
            mermaidContainer.innerHTML = '<div class="text-red-500">Error rendering diagram. See console.</div>';
        }
    }

    callSelector.addEventListener('change', (e) => {
        if (e.target.value) renderMermaidDiagram(e.target.value);
        else mermaidContainer.innerHTML = '<div class="text-gray-500">Select a call to view flow.</div>';
    });

    ladderSearchInput.addEventListener('input', () => {
        updateCallSelector();
        if (!callSelector.value) {
            mermaidContainer.innerHTML = '<div class="text-gray-500">Select a call to view flow.</div>';
        }
    });

    // --- WEBSOCKET CONNECTION ---
    createSubBtn.addEventListener('click', async () => {
        const subType = subTypeSelect.value;
        const extId = extInput.value.trim();

        if (subType.includes('extension') && !extId) return logEvent('Error: Extension ID is required.', 'error');

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
                    
                    logEvent(JSON.stringify(payload, null, 2), 'info');

                    if (Array.isArray(payload) && payload[1] && payload[1].event && payload[1].event.includes('telephony/sessions')) {
                        processTelephonyEvent(payload[1]);
                    }

                } catch (e) {
                    logEvent(`Raw Message: ${event.data}`, 'info');
                }
            };

            webSocket.onerror = () => { logEvent('WebSocket error.', 'error'); };
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

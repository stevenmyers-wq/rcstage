// --- GLOBAL WEBRTC VARIABLES ---
let webPhone = null;
let activeSession = null;
let audioCtx = null;
let virtualMic = null;

// --- 1. THE VIRTUAL MICROPHONE INTERCEPTOR ---
function setupVirtualMicrophone() {
    if (audioCtx) return; // Only set this up once
    
    const AudioContext = window.AudioContext || window.webkitAudioContext;
    audioCtx = new AudioContext();
    virtualMic = audioCtx.createMediaStreamDestination();
    
    // Monkey-patch the browser's getUserMedia to return our fake mic instead of the real one
    const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async (constraints) => {
        if (constraints && constraints.audio) {
            console.log("WebRTC requested mic. Supplying Virtual AI Microphone stream instead.");
            return virtualMic.stream;
        }
        return originalGetUserMedia(constraints);
    };
}

// --- 2. PIPING AUDIO INTO THE CALL ---
// This function is triggered by the purple "Send to Call" buttons in the chat UI
window.playTurnIntoCall = function(audioId) {
    const audioEl = document.getElementById(audioId);
    if (!audioEl) return;
    
    // Browsers sometimes suspend the audio context if it's idle; wake it up
    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
    }
    
    // We only want to wire the element into the graph the first time it's clicked
    if (!audioEl.isRouted) {
        const source = audioCtx.createMediaElementSource(audioEl);
        source.connect(virtualMic); // Route into the phone call
        // Note: We do NOT connect it to audioCtx.destination to prevent it playing aloud 
        // on your PC speakers, which would cause an echo while you wear your headset.
        audioEl.isRouted = true;
    }
    
    audioEl.play();
};
// --- 3. AUTO-PLAY SEQUENCER ---
window.totalTurns = 0; 
window.currentTurnIndex = 0;

window.startAutoDemo = function() {
    document.getElementById('auto-play-btn').disabled = true;
    document.getElementById('auto-play-btn').innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Demo Running...';
    window.currentTurnIndex = 0;
    playNextAutoTurn();
};

function playNextAutoTurn() {
    if (window.currentTurnIndex >= window.totalTurns) {
        console.log("Demo complete. Leaving call open.");
        document.getElementById('auto-play-btn').innerHTML = '<i class="fas fa-check-circle mr-2"></i>Sequence Complete';
        return; // We do NOT terminate the call, letting you hang up manually via RingEX
    }

    const audioEl = document.getElementById(`audio-turn-${window.currentTurnIndex}`);
    if (!audioEl) return;

    const isAgent = audioEl.dataset.speaker === 'agent';

    if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume();
    }

    if (!audioEl.isRouted) {
        const source = audioCtx.createMediaElementSource(audioEl);
        
        if (isAgent) {
            // AGENT AUDIO: Route to Mac Speakers -> BlackHole -> RingEX Mic
            source.connect(audioCtx.destination);
        } else {
            // CUSTOMER AUDIO: Route to Virtual Mic -> WebRTC Call -> RingEX Speaker
            source.connect(virtualMic);
        }
        audioEl.isRouted = true;
    }

    // Highlight the UI so you know who is talking
    document.querySelectorAll('.turn-container').forEach(el => el.classList.remove('ring-4', 'ring-purple-400', 'shadow-lg'));
    document.getElementById(`turn-container-${window.currentTurnIndex}`).classList.add('ring-4', 'ring-purple-400', 'shadow-lg');

    // When this audio finishes, wait 1 second (for natural pacing), then play the next one
    audioEl.onended = () => {
        window.currentTurnIndex++;
        setTimeout(playNextAutoTurn, 1000); 
    };

    audioEl.play();
}

document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const generateBtn = document.getElementById('generate-btn');
    const scenarioInput = document.getElementById('scenario-input');
    const scriptDisplay = document.getElementById('script-display');
    const loadingIndicator = document.getElementById('loading-indicator');
    const dialerSection = document.getElementById('dialer-section');
    
    // WebRTC Elements
    const callBtn = document.getElementById('call-btn');
    const hangupBtn = document.getElementById('hangup-btn');
    const dialTargetInput = document.getElementById('dial-target');
    const callStatus = document.getElementById('call-status');

    // --- PHASE 0: SCRIPT & AUDIO GENERATION ---
    if (generateBtn) {
        generateBtn.addEventListener('click', async () => {
            const scenario = scenarioInput.value.trim();
            const voicePrompt = document.getElementById('voice-prompt-input').value.trim() || 'Australian English';

            if (!scenario) return alert("Please enter a scenario.");

            generateBtn.disabled = true;
            generateBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Generating...';
            loadingIndicator.classList.remove('hidden');
            scriptDisplay.innerHTML = '';
            dialerSection.classList.add('hidden'); // Hide dialer until new script is ready

            try {
                // Step 1: Script
                const scriptRes = await fetch('/api/ai_demo_calls/generate-script', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scenario, voice_prompt: voicePrompt })
                });
                const scriptData = await scriptRes.json();
                if (scriptData.error) throw new Error(scriptData.error);

                // Step 2: Audio
                const audioRes = await fetch('/api/ai_demo_calls/generate-audio', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        script: scriptData.script,
                        template_id: `demo_${Date.now()}`,
                        voice_prompt: voicePrompt
                    })
                });
                const audioData = await audioRes.json();
                if (audioData.error) throw new Error(audioData.error);

                renderScriptAndAudio(audioData.files);
                dialerSection.classList.remove('hidden'); // Unlock the WebRTC Dialer

            } catch (error) {
                scriptDisplay.innerHTML = `<div class="p-4 bg-red-100 text-red-700 rounded-lg border border-red-200"><strong>Error:</strong> ${error.message}</div>`;
            } finally {
                generateBtn.disabled = false;
                generateBtn.innerHTML = '<i class="fas fa-magic mr-2"></i>Generate Content';
                loadingIndicator.classList.add('hidden');
            }
        });
    }

    // --- PHASE 1: WEBRTC CALLING LOGIC ---
    if (callBtn) {
        callBtn.addEventListener('click', async () => {
            const target = dialTargetInput.value.trim();
            if (!target) return alert("Please enter an extension or phone number to dial.");
            
            // Critical: AudioContext MUST be initialized during a user click event to bypass browser security
            setupVirtualMicrophone();
            if (audioCtx.state === 'suspended') await audioCtx.resume();

            callBtn.disabled = true;
            callStatus.innerText = "Status: Provisioning SIP...";
            callStatus.className = "mt-2 text-sm font-bold text-yellow-700 bg-yellow-100 p-2 rounded text-center border border-yellow-300";

            try {
                // 1. Get burner credentials from backend
                const res = await fetch('/api/ai_demo_calls/sip-provision', { method: 'POST' });
                const data = await res.json();
                if (data.error) throw new Error(data.error);

                callStatus.innerText = "Status: Loading WebRTC Engine...";
                
                callStatus.innerText = "Status: Loading WebRTC Engine...";
                const rcModule = await import('https://cdn.jsdelivr.net/npm/ringcentral-web-phone@0.8.2/+esm');
                
                // Safely extract the WebPhone class no matter how the CDN nested it
                const WebPhone = rcModule.WebPhone 
                              || (rcModule.default && rcModule.default.WebPhone)
                              || (rcModule.default && rcModule.default.default)
                              || rcModule.default;

                if (typeof WebPhone !== 'function') {
                    throw new Error("Failed to extract WebPhone class from module.");
                }

                callStatus.innerText = "Status: Registering WebPhone...";
                
                // 3. Initialize WebPhone (Notice it's just 'new WebPhone' now)
                webPhone = new WebPhone(data.sip_data, {
                    appKey: 'ai-demo-calls',
                    appName: 'AI Demo Call',
                    appVersion: '1.0.0',
                    uuid: 'demo-' + Date.now(),
                    logLevel: 0 // Keep console clean
                });

                let hasDialedThisSession = false; // Prevents the ghost calling bug
                // 4. Wait for registration, then dial
                webPhone.userAgent.on('registered', () => {
                    if (hasDialedThisSession) return; // If we already dialed, ignore future SIP refreshes
                    hasDialedThisSession = true;
                    
                    callStatus.innerText = `Status: Dialing ${target}...`;
                    
                    activeSession = webPhone.userAgent.invite(target, {
                        media: {
                            render: {
                                remote: document.getElementById('remote-audio'),
                                local: document.getElementById('local-audio')
                            }
                        }
                    });

                    // 5. When the Agent answers the phone
                    activeSession.on('accepted', () => {
                        callStatus.innerText = "Status: 🟢 Connected. Ready for Auto-Play.";
                        callStatus.className = "mt-2 text-sm font-bold text-green-700 bg-green-100 p-2 rounded text-center border border-green-300";
                        callBtn.classList.add('hidden');
                        hangupBtn.classList.remove('hidden');
                        
                        // Show the massive Auto-Play button
                        document.getElementById('auto-play-btn').classList.remove('hidden');
                    });

                    // 6. When the call is hung up
                    activeSession.on('terminated', () => {
                        callStatus.innerText = "Status: 🔴 Call Ended.";
                        callStatus.className = "mt-2 text-sm font-bold text-gray-700 bg-gray-100 p-2 rounded text-center border border-gray-300";
                        hangupBtn.classList.add('hidden');
                        callBtn.classList.remove('hidden');
                        document.getElementById('auto-play-btn').classList.add('hidden');
                        callBtn.disabled = false;
                        hasDialedThisSession = false; // Reset for the next call
                    });
                });

                webPhone.userAgent.on('registrationFailed', () => {
                    throw new Error("SIP Registration Failed");
                });

            } catch (err) {
                callStatus.innerText = `Status: ❌ Error - ${err.message}`;
                callStatus.className = "mt-2 text-sm font-bold text-red-700 bg-red-100 p-2 rounded text-center border border-red-300";
                callBtn.disabled = false;
            }
        });
    }

    if (hangupBtn) {
        hangupBtn.addEventListener('click', () => {
            if (activeSession) activeSession.terminate();
        });
    }

    // --- UI RENDERER ---
    function renderScriptAndAudio(turns) {
        let html = `
            <div class="flex items-center justify-between mb-2">
                <h3 class="text-lg font-bold text-gray-800">Demo Conversation</h3>
                <span class="text-xs text-gray-500">Only Customer turns have inject buttons</span>
            </div>
            <div class="bg-[#e5ddd5] p-4 rounded-xl h-[600px] overflow-y-auto flex flex-col space-y-4 shadow-inner">
        `;
        
        window.totalTurns = turns.length; // Save total turns for the auto-player

        turns.forEach(turn => {
            const isAgent = turn.speaker.toLowerCase() === 'agent';
            const alignmentClass = isAgent ? 'justify-end' : 'justify-start';
            const bubbleClass = isAgent ? 'bg-[#dcf8c6] rounded-br-none' : 'bg-white rounded-bl-none';    
            const senderName = isAgent ? 'Agent (BlackHole)' : 'Customer (WebRTC)';
            const nameColor = isAgent ? 'text-green-700' : 'text-blue-600';

            // Notice we added data-speaker and removed the manual purple buttons
            const audioBlock = turn.audio_url ? `
                <audio id="audio-turn-${turn.turn}" data-speaker="${turn.speaker.toLowerCase()}" controls src="${turn.audio_url}" class="h-8 w-full min-w-[200px] mt-2"></audio>
            ` : `<span class="text-red-600 text-xs font-semibold">Audio failed: ${turn.error}</span>`;

            // Added an ID to the wrapper so we can highlight it while playing
            html += `
                <div class="flex ${alignmentClass} w-full">
                    <div id="turn-container-${turn.turn}" class="p-3 rounded-xl shadow max-w-[85%] sm:max-w-[75%] flex flex-col ${bubbleClass} turn-container transition-all">
                        <span class="text-xs font-bold mb-1 ${nameColor}">${senderName}</span>
                        <p class="text-gray-800 text-sm md:text-base leading-snug">"${turn.text}"</p>
                        ${audioBlock}
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
        scriptDisplay.innerHTML = html;
    }
});

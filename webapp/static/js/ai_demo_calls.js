// --- INDEXEDDB UNLIMITED CACHE WRAPPER ---
// This replaces localStorage so we can store massive 3+ minute Base64 audio files without hitting 5MB quotas
const initDB = () => {
    return new Promise((resolve, reject) => {
        const request = indexedDB.open('DemoAppDB', 1);
        request.onupgradeneeded = (e) => {
            e.target.result.createObjectStore('demoCache');
        };
        request.onsuccess = (e) => resolve(e.target.result);
        request.onerror = (e) => reject(e.target.error);
    });
};

const saveToCache = async (data) => {
    const db = await initDB();
    return new Promise((resolve, reject) => {
        const tx = db.transaction('demoCache', 'readwrite');
        const req = tx.objectStore('demoCache').put(data, 'cachedDemoScript');
        req.onsuccess = () => resolve();
        req.onerror = (e) => reject(e.target.error);
    });
};

const loadFromCache = async () => {
    const db = await initDB();
    return new Promise((resolve) => {
        const tx = db.transaction('demoCache', 'readonly');
        const req = tx.objectStore('demoCache').get('cachedDemoScript');
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
    });
};

// --- GLOBAL WEBRTC VARIABLES ---
window.rcWebPhoneEngine = null; 
window.intentToDial = false;    
let activeSession = null;
let audioCtx = null;
let virtualMic = null;
let customerMixer = null; 

// --- 1. THE VIRTUAL MICROPHONE INTERCEPTOR ---
function setupVirtualMicrophone() {
    if (!audioCtx) {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AudioContext();
        customerMixer = audioCtx.createGain(); 
        
        const originalGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = async (constraints) => {
            if (constraints && constraints.audio) {
                console.log("WebRTC requested mic. Supplying fresh Virtual AI Microphone stream.");
                return virtualMic.stream;
            }
            return originalGetUserMedia(constraints);
        };
    }
    
    virtualMic = audioCtx.createMediaStreamDestination();
    customerMixer.disconnect();
    customerMixer.connect(virtualMic);
}

// --- 2. PIPING AUDIO INTO THE CALL (MANUAL BUTTONS) ---
window.playTurnIntoCall = function(audioId) {
    const audioEl = document.getElementById(audioId);
    if (!audioEl) return;
    
    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
    
    if (!audioEl.isRouted) {
        const source = audioCtx.createMediaElementSource(audioEl);
        source.connect(customerMixer); 
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
    
    if (window.currentTurnIndex >= window.totalTurns) {
        window.currentTurnIndex = 0;
    }
    playNextAutoTurn();
};

function playNextAutoTurn() {
    if (window.currentTurnIndex >= window.totalTurns) {
        console.log("Demo complete. Leaving call open.");
        const btn = document.getElementById('auto-play-btn');
        btn.innerHTML = '<i class="fas fa-redo mr-2"></i>Replay Sequence';
        btn.disabled = false;
        window.currentTurnIndex = 0; 
        return; 
    }

    const audioEl = document.getElementById(`audio-turn-${window.currentTurnIndex}`);
    if (!audioEl) return;

    const isAgent = audioEl.dataset.speaker === 'agent';

    if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();

    if (!audioEl.isRouted) {
        const source = audioCtx.createMediaElementSource(audioEl);
        
        if (isAgent) {
            source.connect(audioCtx.destination);
        } else {
            source.connect(customerMixer);
        }
        audioEl.isRouted = true;
    }

    document.querySelectorAll('.turn-container').forEach(el => el.classList.remove('ring-4', 'ring-purple-400', 'shadow-lg'));
    document.getElementById(`turn-container-${window.currentTurnIndex}`).classList.add('ring-4', 'ring-purple-400', 'shadow-lg');

    audioEl.onended = () => {
        window.currentTurnIndex++;
        setTimeout(playNextAutoTurn, 1000); 
    };

    audioEl.play();
}

document.addEventListener('DOMContentLoaded', () => {
    const generateBtn = document.getElementById('generate-btn');
    const scenarioInput = document.getElementById('scenario-input');
    const scriptDisplay = document.getElementById('script-display');
    const loadingIndicator = document.getElementById('loading-indicator');
    const dialerSection = document.getElementById('dialer-section');
    
    const callBtn = document.getElementById('call-btn');
    const hangupBtn = document.getElementById('hangup-btn');
    const dialTargetInput = document.getElementById('dial-target');
    const callStatus = document.getElementById('call-status');
    const dtmfKeypad = document.getElementById('dtmf-keypad');

    // --- DTMF KEYPAD LOGIC ---
    document.querySelectorAll('.dtmf-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const key = e.target.dataset.key;
            if (activeSession) {
                console.log(`Sending DTMF Tone: ${key}`);
                activeSession.dtmf(key); // This sends the tone down the active SIP line!
            }
        });
    });

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
            dialerSection.classList.add('hidden');

            try {
                const scriptRes = await fetch('/api/ai_demo_calls/generate-script', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ scenario, voice_prompt: voicePrompt })
                });
                const scriptData = await scriptRes.json();
                if (scriptData.error) throw new Error(scriptData.error);

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

                // MASSIVE CACHE FIX: Saving to IndexedDB
                await saveToCache(audioData.files);
                
                renderScriptAndAudio(audioData.files);
                dialerSection.classList.remove('hidden');

            } catch (error) {
                scriptDisplay.innerHTML = `<div class="p-4 bg-red-100 text-red-700 rounded-lg border border-red-200"><strong>Error:</strong> ${error.message}</div>`;
            } finally {
                generateBtn.disabled = false;
                generateBtn.innerHTML = '<i class="fas fa-magic mr-2"></i>Generate Content';
                loadingIndicator.classList.add('hidden');
            }
        });
    }

    function executeDial(targetNumber) {
        callStatus.innerText = `Status: Dialing ${targetNumber}...`;
        
        setupVirtualMicrophone();
        
        activeSession = window.rcWebPhoneEngine.userAgent.invite(targetNumber, {
            media: {
                render: {
                    remote: document.getElementById('remote-audio'),
                    local: document.getElementById('local-audio')
                }
            }
        });

        activeSession.on('accepted', () => {
            callStatus.innerText = "Status: 🟢 Connected. Ready for Auto-Play.";
            callStatus.className = "mt-2 text-sm font-bold text-green-700 bg-green-100 p-2 rounded text-center border border-green-300";
            callBtn.classList.add('hidden');
            hangupBtn.classList.remove('hidden');
            document.getElementById('auto-play-btn').classList.remove('hidden');
            document.getElementById('auto-play-btn').disabled = false;
            document.getElementById('auto-play-btn').innerHTML = '<i class="fas fa-play-circle mr-2"></i>Start Automated 2-Way Demo';
            
            // Show the DTMF Keypad
            if (dtmfKeypad) dtmfKeypad.classList.remove('hidden'); 
        });

        activeSession.on('terminated', () => {
            callStatus.innerText = "Status: 🔴 Call Ended.";
            callStatus.className = "mt-2 text-sm font-bold text-gray-700 bg-gray-100 p-2 rounded text-center border border-gray-300";
            hangupBtn.classList.add('hidden');
            callBtn.classList.remove('hidden');
            document.getElementById('auto-play-btn').classList.add('hidden');
            callBtn.disabled = false;
            activeSession = null;

            // Hide the DTMF Keypad
            if (dtmfKeypad) dtmfKeypad.classList.add('hidden');
        });
    }

    // --- PHASE 1: WEBRTC CALLING LOGIC ---
    if (callBtn) {
        callBtn.addEventListener('click', async () => {
            const target = dialTargetInput.value.trim();
            if (!target) return alert("Please enter an extension or phone number to dial.");
            
            if (!audioCtx) setupVirtualMicrophone();
            if (audioCtx.state === 'suspended') await audioCtx.resume();

            callBtn.disabled = true;
            callStatus.innerText = "Status: Provisioning SIP...";
            callStatus.className = "mt-2 text-sm font-bold text-yellow-700 bg-yellow-100 p-2 rounded text-center border border-yellow-300";

            window.intentToDial = true;

            try {
                if (!window.rcWebPhoneEngine) {
                    const regionSelect = document.getElementById('demo-region-select');
                    const region = regionSelect ? regionSelect.value : 'AU';

                    const res = await fetch('/api/ai_demo_calls/sip-provision', { 
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ region: region })
                    });
                    const data = await res.json();
                    if (data.error) throw new Error(data.error);

                    callStatus.innerText = "Status: Loading WebRTC Engine...";
                    const rcModule = await import('https://cdn.jsdelivr.net/npm/ringcentral-web-phone@0.8.2/+esm');
                    
                    const WebPhone = rcModule.WebPhone 
                                  || (rcModule.default && rcModule.default.WebPhone)
                                  || (rcModule.default && rcModule.default.default)
                                  || rcModule.default;

                    if (typeof WebPhone !== 'function') throw new Error("Failed to extract WebPhone class from module.");

                    callStatus.innerText = "Status: Registering WebPhone...";
                    
                    window.rcWebPhoneEngine = new WebPhone(data.sip_data, {
                        appKey: 'ai-demo-calls',
                        appName: 'AI Demo Call',
                        appVersion: '1.0.0',
                        uuid: 'demo-' + Date.now(),
                        logLevel: 0 
                    });

                    window.rcWebPhoneEngine.userAgent.on('registered', () => {
                        if (window.intentToDial) {
                            executeDial(target);
                            window.intentToDial = false; 
                        }
                    });

                    window.rcWebPhoneEngine.userAgent.on('registrationFailed', () => {
                        throw new Error("SIP Registration Failed");
                    });

                } else {
                    executeDial(target);
                    window.intentToDial = false;
                }

            } catch (err) {
                callStatus.innerText = `Status: ❌ Error - ${err.message}`;
                callStatus.className = "mt-2 text-sm font-bold text-red-700 bg-red-100 p-2 rounded text-center border border-red-300";
                callBtn.disabled = false;
                window.intentToDial = false;
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
        
        window.totalTurns = turns.length; 

        turns.forEach(turn => {
            const isAgent = turn.speaker.toLowerCase() === 'agent';
            const alignmentClass = isAgent ? 'justify-end' : 'justify-start';
            const bubbleClass = isAgent ? 'bg-[#dcf8c6] rounded-br-none' : 'bg-white rounded-bl-none';    
            const senderName = isAgent ? 'Agent (BlackHole)' : 'Customer (WebRTC)';
            const nameColor = isAgent ? 'text-green-700' : 'text-blue-600';

            const audioBlock = turn.audio_url ? `
                <audio id="audio-turn-${turn.turn}" data-speaker="${turn.speaker.toLowerCase()}" controls src="${turn.audio_url}" class="h-8 w-full min-w-[200px] mt-2"></audio>
            ` : `<span class="text-red-600 text-xs font-semibold">Audio failed: ${turn.error}</span>`;

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
        if(dialerSection) dialerSection.classList.remove('hidden');
    }

    // --- CACHE LOADER (INDEXEDDB) ---
    loadFromCache().then(cachedScript => {
        if (cachedScript) {
            console.log("Loaded script from IndexedDB cache - no waiting for Gemini!");
            renderScriptAndAudio(cachedScript);
        }
    });
});

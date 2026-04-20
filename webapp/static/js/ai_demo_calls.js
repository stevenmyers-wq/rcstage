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

// --- SEQUENCER STATE ---
window.totalTurns = 0;
window.currentTurnIndex = 0;
let sequencerRunning = false;
let sequencerAbortFlag = false;

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

// --- 3. PLAYBACK CONTROL BAR UPDATER ---
function updatePlaybackBar() {
    const progress = document.getElementById('playback-progress');
    if (progress) {
        progress.textContent = `Turn ${window.currentTurnIndex} of ${window.totalTurns}`;
    }
}

function setPlaybackBarRunning(running) {
    const btn = document.getElementById('playback-start-stop-btn');
    if (!btn) return;
    if (running) {
        btn.innerHTML = '<i class="fas fa-stop-circle mr-1"></i>Stop';
        btn.className = 'bg-red-500 hover:bg-red-600 text-white font-bold py-2 px-4 rounded transition text-sm';
    } else {
        btn.innerHTML = '<i class="fas fa-play-circle mr-1"></i>Start';
        btn.className = 'bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded transition text-sm';
    }
}

// Sync the sidebar auto-play button appearance too
function syncSidebarAutoPlayBtn(running) {
    const btn = document.getElementById('auto-play-btn');
    if (!btn) return;
    if (running) {
        btn.innerHTML = '<i class="fas fa-stop-circle mr-2"></i>Stop Demo';
        btn.onclick = stopAutoDemo;
    } else {
        btn.innerHTML = '<i class="fas fa-play-circle mr-2"></i>Start Automated 2-Way Demo';
        btn.onclick = startAutoDemo;
    }
    btn.disabled = false;
}

// --- 4. STOP THE SEQUENCER CLEANLY ---
window.stopAutoDemo = function() {
    sequencerAbortFlag = true;
    sequencerRunning = false;

    // Pause all audio elements immediately
    for (let i = 0; i < window.totalTurns; i++) {
        const audioEl = document.getElementById(`audio-turn-${i}`);
        if (audioEl) {
            audioEl.pause();
            audioEl.onended = null;
        }
    }

    // Remove active highlight from all turn containers
    document.querySelectorAll('.turn-container').forEach(el => {
        el.classList.remove('ring-4', 'ring-purple-400', 'shadow-lg');
    });

    setPlaybackBarRunning(false);
    syncSidebarAutoPlayBtn(false);
    updatePlaybackBar();
    console.log("Demo stopped.");
};

// --- 5. RESTART FROM THE BEGINNING ---
window.restartAutoDemo = function() {
    stopAutoDemo();
    window.currentTurnIndex = 0;
    updatePlaybackBar();
    // Small delay to let the stop settle before re-starting
    setTimeout(() => {
        sequencerAbortFlag = false;
        startAutoDemo();
    }, 200);
};

// --- 6. TOGGLE START/STOP ---
window.toggleAutoDemo = function() {
    if (sequencerRunning) {
        stopAutoDemo();
    } else {
        sequencerAbortFlag = false;
        startAutoDemo();
    }
};

// --- 7. AUTO-PLAY SEQUENCER ---
window.startAutoDemo = function() {
    if (sequencerRunning) return;

    sequencerAbortFlag = false;
    sequencerRunning = true;

    setPlaybackBarRunning(true);
    syncSidebarAutoPlayBtn(true);

    if (window.currentTurnIndex >= window.totalTurns) {
        window.currentTurnIndex = 0;
    }
    playNextAutoTurn();
};

function playNextAutoTurn() {
    // Check abort before each turn
    if (sequencerAbortFlag) {
        sequencerRunning = false;
        setPlaybackBarRunning(false);
        syncSidebarAutoPlayBtn(false);
        return;
    }

    if (window.currentTurnIndex >= window.totalTurns) {
        // Sequence complete
        sequencerRunning = false;
        setPlaybackBarRunning(false);
        syncSidebarAutoPlayBtn(false);

        const btn = document.getElementById('playback-start-stop-btn');
        if (btn) {
            btn.innerHTML = '<i class="fas fa-redo mr-1"></i>Replay';
            btn.className = 'bg-green-500 hover:bg-green-600 text-white font-bold py-2 px-4 rounded transition text-sm';
        }
        const sideBtn = document.getElementById('auto-play-btn');
        if (sideBtn) {
            sideBtn.innerHTML = '<i class="fas fa-redo mr-2"></i>Replay Sequence';
            sideBtn.onclick = startAutoDemo;
            sideBtn.disabled = false;
        }

        window.currentTurnIndex = 0;
        updatePlaybackBar();
        console.log("Demo sequence complete.");
        return;
    }

    const audioEl = document.getElementById(`audio-turn-${window.currentTurnIndex}`);
    if (!audioEl) {
        window.currentTurnIndex++;
        playNextAutoTurn();
        return;
    }

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

    // Highlight active turn
    document.querySelectorAll('.turn-container').forEach(el => {
        el.classList.remove('ring-4', 'ring-purple-400', 'shadow-lg');
    });
    const container = document.getElementById(`turn-container-${window.currentTurnIndex}`);
    if (container) {
        container.classList.add('ring-4', 'ring-purple-400', 'shadow-lg');
        container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // Update progress bar
    updatePlaybackBar();

    // Rewind in case this turn was played before (e.g. after a stop/restart)
    audioEl.currentTime = 0;

    audioEl.onended = () => {
        if (sequencerAbortFlag) return;
        window.currentTurnIndex++;
        updatePlaybackBar();
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
    const playbackControlBar = document.getElementById('playback-control-bar');

    // --- DTMF KEYPAD LOGIC ---
    document.querySelectorAll('.dtmf-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const key = e.target.dataset.key;
            if (activeSession) {
                console.log(`Sending DTMF Tone: ${key}`);
                activeSession.dtmf(key);
            }
        });
    });

    // --- PHASE 0: SCRIPT & AUDIO GENERATION ---
    if (generateBtn) {
        generateBtn.addEventListener('click', async () => {
            const scenario = scenarioInput.value.trim();
            const voicePrompt = document.getElementById('voice-prompt-select').value;
            const agentVoice = document.getElementById('agent-voice-select').value;
            const customerVoice = document.getElementById('customer-voice-select').value;

            if (!scenario) return alert("Please enter a scenario.");

            generateBtn.disabled = true;
            generateBtn.innerHTML = '<i class="fas fa-spinner fa-spin mr-2"></i>Generating...';
            loadingIndicator.classList.remove('hidden');
            scriptDisplay.innerHTML = '';
            dialerSection.classList.add('hidden');
            if (playbackControlBar) playbackControlBar.classList.add('hidden');

            // Stop any in-progress demo
            stopAutoDemo();
            window.currentTurnIndex = 0;

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
                        voice_prompt: voicePrompt,
                        agent_voice: agentVoice,
                        customer_voice: customerVoice
                    })
                });
                const audioData = await audioRes.json();
                if (audioData.error) throw new Error(audioData.error);

                // Save to IndexedDB cache
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

            // Show auto-play button in sidebar
            const autoPlayBtn = document.getElementById('auto-play-btn');
            if (autoPlayBtn) {
                autoPlayBtn.classList.remove('hidden');
                autoPlayBtn.disabled = false;
                autoPlayBtn.innerHTML = '<i class="fas fa-play-circle mr-2"></i>Start Automated 2-Way Demo';
                autoPlayBtn.onclick = startAutoDemo;
            }
            
            if (dtmfKeypad) dtmfKeypad.classList.remove('hidden'); 
        });

        activeSession.on('terminated', () => {
            callStatus.innerText = "Status: 🔴 Call Ended.";
            callStatus.className = "mt-2 text-sm font-bold text-gray-700 bg-gray-100 p-2 rounded text-center border border-gray-300";
            hangupBtn.classList.add('hidden');
            callBtn.classList.remove('hidden');

            const autoPlayBtn = document.getElementById('auto-play-btn');
            if (autoPlayBtn) autoPlayBtn.classList.add('hidden');

            callBtn.disabled = false;
            activeSession = null;

            if (dtmfKeypad) dtmfKeypad.classList.add('hidden');

            // Stop sequencer if still running when call drops
            if (sequencerRunning) stopAutoDemo();
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
                <span class="text-xs text-gray-500">${turns.length} turns generated</span>
            </div>
            <div class="bg-[#e5ddd5] p-4 rounded-xl h-[600px] overflow-y-auto flex flex-col space-y-4 shadow-inner" id="chat-scroll-area">
        `;
        
        window.totalTurns = turns.length;
        window.currentTurnIndex = 0;
        sequencerRunning = false;
        sequencerAbortFlag = false;

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

        // Show the sticky playback control bar
        if (playbackControlBar) {
            playbackControlBar.classList.remove('hidden');
            setPlaybackBarRunning(false);
            updatePlaybackBar();

            // Reset the start/stop button appearance cleanly
            const btn = document.getElementById('playback-start-stop-btn');
            if (btn) {
                btn.innerHTML = '<i class="fas fa-play-circle mr-1"></i>Start';
                btn.className = 'bg-blue-500 hover:bg-blue-600 text-white font-bold py-2 px-4 rounded transition text-sm';
                btn.onclick = toggleAutoDemo;
            }
        }

        if (dialerSection) dialerSection.classList.remove('hidden');
    }

    // --- CACHE LOADER (INDEXEDDB) ---
    loadFromCache().then(cachedScript => {
        if (cachedScript) {
            console.log("Loaded script from IndexedDB cache - no waiting for Gemini!");
            renderScriptAndAudio(cachedScript);
        }
    });
});

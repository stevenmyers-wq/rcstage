// webapp/static/js/visualiser.js

// FIX: Import the panzoom library as an ES Module.
import panzoom from 'https://cdn.jsdelivr.net/npm/@panzoom/panzoom@4.5.1/dist/panzoom.min.js';

// --- DOM Elements ---
const visualizeBtn = document.getElementById('visualize-button');
const searchInput = document.getElementById('visualiser-search-input');
const resultsContainer = document.getElementById('search-results-container');
const outputDiv = document.getElementById('call-flow-diagram');
const logContainer = document.getElementById('api-log-container');
const exportControls = document.getElementById('export-controls');
const savePdfBtn = document.getElementById('save-pdf-btn');
const zoomControls = document.getElementById('zoom-controls');

let selectedTargetId = null;
let searchTimeout = null;
let panzoomInstance = null;

// --- Helper Functions ---
function displayApiLog(logData) {
    if (!logContainer) return;
    logContainer.innerHTML = '';

    if (!logData || logData.length === 0) {
        logContainer.innerHTML = '<div class="text-gray-500">No API calls recorded.</div>';
        return;
    }

    logData.forEach(entry => {
        const line = document.createElement('div');
        const statusClass = entry.status === 'SUCCESS' ? 'text-green-400' : 'text-red-400';
        const statusIcon = entry.status === 'SUCCESS' ? '✅' : '❌';

        let detail = `(${entry.duration}): ${entry.endpoint}`;
        if (entry.status !== 'SUCCESS') {
            detail = `**FAIL**: ${entry.endpoint}<br> &nbsp; &nbsp; <span class="text-yellow-400">Detail: ${entry.detail}</span>`;
        }

        line.innerHTML = `<span class="${statusClass}">${statusIcon} ${entry.code} ${entry.method}</span> ${detail}`;
        logContainer.appendChild(line);
    });

    logContainer.scrollTop = logContainer.scrollHeight;
}

function showMessage(message, isError) {
    console.log(isError ? 'Error:' : 'Success:', message);
}

// --- Pan/Zoom Functions (Updated for the new library) ---
function initializePanZoom() {
    const svgElement = outputDiv.querySelector('svg');

    if (!svgElement) {
        console.warn('No SVG element found for pan/zoom initialization');
        return;
    }
    
    // Destroy existing instance if any
    if (panzoomInstance) {
        panzoomInstance.destroy();
    }
    
    // Initialize panzoom on the SVG element
    panzoomInstance = panzoom(svgElement, {
        maxScale: 5,
        minScale: 0.1,
        contain: 'outside'
    });
    
    // Add wheel zoom support to the container
    outputDiv.addEventListener('wheel', function(event) {
        if (!panzoomInstance) return;
        event.preventDefault();
        panzoomInstance.zoomWithWheel(event);
    }, { passive: false });
    
    // Show zoom controls
    zoomControls.style.display = 'flex';
    outputDiv.classList.add('pan-enabled');
    
    // Set up zoom and pan control buttons
    const panDistance = 100;
    document.getElementById('zoom-in-btn').onclick = () => panzoomInstance.zoomIn();
    document.getElementById('zoom-out-btn').onclick = () => panzoomInstance.zoomOut();
    document.getElementById('pan-up-btn').onclick = () => panzoomInstance.pan(0, -panDistance, { relative: true });
    document.getElementById('pan-down-btn').onclick = () => panzoomInstance.pan(0, panDistance, { relative: true });
    document.getElementById('pan-left-btn').onclick = () => panzoomInstance.pan(-panDistance, 0, { relative: true });
    document.getElementById('pan-right-btn').onclick = () => panzoomInstance.pan(panDistance, 0, { relative: true });
    document.getElementById('zoom-reset-btn').onclick = () => panzoomInstance.reset();
    document.getElementById('fit-btn').onclick = () => panzoomInstance.reset(); // Fit is the same as reset in this context
}


// --- Core Logic (No changes needed here) ---
function handleSearchInput() {
    clearTimeout(searchTimeout);
    const query = searchInput.value;

    selectedTargetId = null;
    visualizeBtn.disabled = true;

    if (query.length < 3) {
        resultsContainer.classList.add('hidden');
        resultsContainer.innerHTML = '';
        return;
    }

    resultsContainer.innerHTML = '<div class="p-3 text-gray-500">Searching...</div>';
    resultsContainer.classList.remove('hidden');

    searchTimeout = setTimeout(async () => {
        try {
            const response = await fetch(`/api/rc/visualiser/search?query=${encodeURIComponent(query)}`);
            if (!response.ok) throw new Error('Search request failed.');

            const data = await response.json();
            if (data.status === 'success') {
                displaySearchResults(data.results);
            } else {
                throw new Error(data.message || 'Failed to get search results.');
            }
        } catch (error) {
            resultsContainer.innerHTML = `<div class="p-3 text-red-500">${error.message}</div>`;
        }
    }, 300);
}

function displaySearchResults(results) {
    resultsContainer.innerHTML = '';

    if (results.length === 0) {
        resultsContainer.innerHTML = '<div class="p-3 text-gray-500">No results found.</div>';
        return;
    }

    results.forEach(item => {
        const resultItem = document.createElement('div');
        resultItem.className = 'p-3 hover:bg-gray-100 cursor-pointer border-b border-gray-200';
        resultItem.innerHTML = `<div class="font-semibold">${item.name}</div><div class="text-xs text-gray-500">${item.type}</div>`;
        resultItem.addEventListener('click', () => selectSearchResult(item));
        resultsContainer.appendChild(resultItem);
    });
}

function selectSearchResult(item) {
    searchInput.value = item.name;
    selectedTargetId = item.id;
    visualizeBtn.disabled = false;
    resultsContainer.classList.add('hidden');
    resultsContainer.innerHTML = '';
}

async function handleVisualize() {
    if (!selectedTargetId) {
        showMessage('Please select a valid item from the search results.', true);
        return;
    }
    
    // Clean up existing pan/zoom instance
    if (panzoomInstance) {
        panzoomInstance.destroy();
        panzoomInstance = null;
    }
    
    outputDiv.classList.remove('pan-enabled');
    outputDiv.innerHTML = `<div class="p-8 text-gray-500 flex items-center justify-center h-full">
        <svg class="animate-spin h-8 w-8 text-purple-600 mr-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        Generating call flow for ${searchInput.value}...
    </div>`;
    
    logContainer.innerHTML = '<div class="text-gray-500">Executing API calls...</div>';
    exportControls.style.display = 'none';
    zoomControls.style.display = 'none';
    visualizeBtn.disabled = true;
    
    try {
        const response = await fetch(`/api/rc/trace-flow/${selectedTargetId}`);
        const data = await response.json();
        
        displayApiLog(data.api_log || []);
        
        if (data.status === 'success' && data.mermaid_graph) {
            outputDiv.innerHTML = '';
            
            const mermaidContainer = document.createElement('div');
            mermaidContainer.className = 'mermaid';
            mermaidContainer.textContent = data.mermaid_graph;
            outputDiv.appendChild(mermaidContainer);
            
            await mermaid.run();
            
            setTimeout(() => {
                initializePanZoom();
            }, 150);
            
            exportControls.style.display = 'flex';
            showMessage('Call flow visualization complete!', false);
        } else {
            throw new Error(data.message || 'Failed to generate flow.');
        }
    } catch (error) {
        outputDiv.innerHTML = `<div class="p-8 text-red-500">${error.message}</div>`;
        showMessage(error.message, true);
    } finally {
        visualizeBtn.disabled = false;
    }
}

function handleSavePdf() {
    const diagramElement = outputDiv.querySelector('svg');
    if (!diagramElement) {
        showMessage('Could not find diagram to save.', true);
        return;
    }
    
    if (panzoomInstance) {
        panzoomInstance.reset();
    }
    
    html2canvas(diagramElement, { 
        scale: 2,
        backgroundColor: 'white'
    }).then(canvas => {
        const imgData = canvas.toDataURL('image/jpeg', 0.95);
        const { jsPDF } = window.jspdf;
        
        const pdfWidth = canvas.width;
        const pdfHeight = canvas.height;
        const orientation = pdfWidth > pdfHeight ? 'l' : 'p';
        
        const pdf = new jsPDF(orientation, 'px', [pdfWidth, pdfHeight]);
        pdf.addImage(imgData, 'JPEG', 0, 0, pdfWidth, pdfHeight);
        pdf.save(`call-flow-${searchInput.value.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.pdf`);
        
        showMessage('PDF saved successfully!', false);
    }).catch(error => {
        console.error('Error generating PDF:', error);
        showMessage('Error generating PDF', true);
    });
}

// --- Event Listeners and Initialization ---
(function() {
    if (!visualizeBtn) return;
    
    visualizeBtn.addEventListener('click', handleVisualize);
    savePdfBtn.addEventListener('click', handleSavePdf);
    searchInput.addEventListener('input', handleSearchInput);
    
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !resultsContainer.contains(e.target)) {
            resultsContainer.classList.add('hidden');
        }
    });
    
    fetch('/api/rc/status').then(res => res.json()).then(data => {
        if (data.status !== 'connected') {
            searchInput.disabled = true;
            searchInput.placeholder = "Connect to RingCentral to enable search...";
        }
    }).catch(err => {
        console.error('Failed to check RC connection status:', err);
    });
})();

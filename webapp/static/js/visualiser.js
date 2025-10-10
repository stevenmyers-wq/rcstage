// webapp/static/js/visualiser.js
// --- DOM Elements ---
const visualizeBtn = document.getElementById('visualize-button');
const searchInput = document.getElementById('visualiser-search-input');
const resultsContainer = document.getElementById('search-results-container');
const outputDiv = document.getElementById('call-flow-diagram');
const logContainer = document.getElementById('api-log-container');
const exportControls = document.getElementById('export-controls');
const savePdfBtn = document.getElementById('save-pdf-btn');

let selectedTargetId = null;
let searchTimeout = null;

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

// --- Core Logic ---
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
    }, 300); // Debounce API calls by 300ms
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

    outputDiv.innerHTML = `<div class="p-8 text-gray-500 flex items-center justify-center h-full"><svg class="animate-spin h-8 w-8 text-purple-600 mr-3" viewBox="0 0 24 24"></svg> Generating call flow for ${searchInput.value}...</div>`;
    logContainer.innerHTML = '<div class="text-gray-500">Executing API calls...</div>';
    exportControls.style.display = 'none';
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
    const diagramElement = outputDiv.querySelector('.mermaid svg');
    if (!diagramElement) {
        showMessage('Could not find diagram to save.', true);
        return;
    }
    const originalBg = diagramElement.style.backgroundColor;
    diagramElement.style.backgroundColor = 'white';
    
    html2canvas(diagramElement, { scale: 3 }).then(canvas => {
        diagramElement.style.backgroundColor = originalBg;
        const imgData = canvas.toDataURL('image/jpeg', 0.95);
        const { jsPDF } = window.jspdf;
        const pdfWidth = canvas.width;
        const pdfHeight = canvas.height;
        const orientation = pdfWidth > pdfHeight ? 'l' : 'p';
        const pdf = new jsPDF(orientation, 'px', [pdfWidth, pdfHeight]);
        pdf.addImage(imgData, 'JPEG', 0, 0, pdfWidth, pdfHeight);
        pdf.save(`call-flow-${searchInput.value.replace(/[^a-z0-9]/gi, '_').toLowerCase()}.pdf`);
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
    });
})();



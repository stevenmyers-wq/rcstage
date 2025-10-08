// webapp/static/js/visualiser.js

function displayApiLog(logData) {
    const logContainer = document.getElementById('api-log-container');
    if (!logContainer) return;

    logContainer.innerHTML = ''; 
    logData.forEach(entry => {
        const line = document.createElement('div');
        let statusClass, statusIcon;

        if (entry.status === 'SUCCESS') {
            statusClass = 'text-green-400';
            statusIcon = '✅';
            line.innerHTML = `<span class="${statusClass}">${statusIcon} ${entry.code} ${entry.method}</span> (${entry.duration}): ${entry.endpoint}`;
        } else {
            statusClass = 'text-red-400';
            statusIcon = '❌';
            line.innerHTML = `<span class="${statusClass}">${statusIcon} ${entry.code} ${entry.method}</span> **FAIL**: ${entry.endpoint}<br> &nbsp; &nbsp; <span class="text-yellow-400">Detail: ${entry.detail}</span>`;
        }
        logContainer.appendChild(line);
    });

    if (logData.length === 0) {
        logContainer.innerHTML = '<div class="text-gray-500">No API calls recorded.</div>';
    }
    logContainer.scrollTop = logContainer.scrollHeight; 
}

async function fetchAndPopulateNumbers() {
    const select = document.getElementById('phone-number-select');
    select.innerHTML = '<option value="">-- Loading numbers... --</option>';

    try {
        const response = await fetch('/api/rc/phone-numbers');
        
        if (!response.ok) {
            const errorData = await response.json();
            const errorMessage = errorData.message || `HTTP Error ${response.status}: Failed to fetch data.`;
            
            select.innerHTML = `<option value="">-- ${errorMessage} --</option>`;
            showMessage(errorMessage, true);
            return; 
        }

        const data = await response.json();
        
        if (data.status === 'success' && data.numbers) {
             window.allPhoneNumbers = data.numbers; 
             filterAndDisplayNumbers(); 
             if (document.getElementById('visualize-button')) {
                 document.getElementById('visualize-button').disabled = false;
             }
        } else {
             select.innerHTML = `<option value="">-- ${data.message || 'Unknown data format'} --</option>`;
             showMessage(data.message || 'Failed to populate numbers.', true);
        }
    } catch (error) {
        console.error('Network or parsing error:', error);
        select.innerHTML = '<option value="">-- Network Error --</option>';
        showMessage('Network Error: Could not reach the API server or failed parsing.', true);
    }
}

function filterAndDisplayNumbers() {
      const select = document.getElementById('phone-number-select');
      const searchTerm = document.getElementById('phone-search').value.toLowerCase();
      select.innerHTML = '<option value="">-- Select a number from the list --</option>';

      if (!window.allPhoneNumbers || window.allPhoneNumbers.length === 0) {
         select.innerHTML = '<option value="">-- No numbers available --</option>';
         return;
      }

      const filteredNumbers = window.allPhoneNumbers.filter(num => {
           const search = searchTerm.toLowerCase();
           return num.number.includes(search) || 
                  num.usage.toLowerCase().includes(search) || 
                  num.name.toLowerCase().includes(search);
      });

      filteredNumbers.forEach(num => {
           const option = document.createElement('option');
           option.value = num.id; 
           
           let displayName = num.name;
           if (!displayName.includes(num.usage)) {
             displayName += ` [${num.usage}]`;
           }
           
           option.textContent = `${displayName} - ${num.number}`;
           select.appendChild(option);
      });
}

function createNodeElement(nodeData, isBranchRoot = false) {
    const nodeElement = document.createElement('div');
    nodeElement.className = `flow-node ${nodeData.classList}`;
    
    let htmlContent = `<div class="title"><b>${nodeData.name}</b></div>`;
    if (nodeData.details && nodeData.details.length > 0) {
        htmlContent += `<ul class="detail-list list-none p-0">`;
        nodeData.details.forEach(detail => {
            htmlContent += `<li class="mt-1">${detail}</li>`;
        });
        htmlContent += `</ul>`;
    }
    nodeElement.innerHTML = htmlContent;

    if (isBranchRoot) {
        const button = document.createElement('button');
        button.className = 'toggle-button';
        button.innerText = '−';
        button.onclick = (e) => {
            e.stopPropagation();
            const branchContainer = nodeElement.closest('.branch-container');
            const childrenToToggle = Array.from(branchContainer.children)
                .filter(child => child.classList.contains('arrow-down') || child.classList.contains('branch-wrapper'));
            
            childrenToToggle.forEach(child => child.classList.toggle('hidden'));
            const isHidden = childrenToToggle[0] && childrenToToggle[0].classList.contains('hidden');
            button.innerText = isHidden ? '+' : '−';
        };
        nodeElement.appendChild(button);
    }
    return nodeElement;
}

function createArrowElement() {
    const arrow = document.createElement('div');
    arrow.className = 'arrow-down';
    return arrow;
}

function renderHtmlFlow(apiFlowData) {
    const mainContainer = document.getElementById('call-flow-diagram');
    mainContainer.innerHTML = '';
    mainContainer.classList.remove('justify-center', 'items-center', 'text-gray-400');
    mainContainer.classList.add('flow-container');

    function buildBranch(dataArray, parentElement) {
        if (!dataArray || dataArray.length === 0) return;

        const node = dataArray[0];
        const children = dataArray.slice(1);
        const nodeData = {
            name: node.name,
            details: node.details,
            classList: 'main-flow node-' + (node.type || 'endpoint')
        };
        
        const hasSideContent = (node.rules && node.rules.length > 0) || (node.members && node.members.length > 0);
        const hasSubBranches = node.branches && node.branches.length > 0;
        const isBranchRoot = hasSubBranches;

        const branchContainer = document.createElement('div');
        branchContainer.className = 'branch-container';

        if (hasSideContent) {
            const sepContainer = document.createElement('div');
            sepContainer.className = 'flow-step-separator';
            
            const leftColumn = document.createElement('div');
            leftColumn.className = 'flow-side-column justify-self-end mr-6';
            if (node.rules && node.rules.length > 0) {
                leftColumn.appendChild(createNodeElement({ name: 'Info / Rules', details: node.rules, classList: 'side-flow node-rule' }));
            }
            
            const centerColumn = document.createElement('div');
            centerColumn.className = 'flow-center-column';
            centerColumn.appendChild(createNodeElement(nodeData, isBranchRoot));

            const rightColumn = document.createElement('div');
            rightColumn.className = 'flow-side-column ml-6';
            if (node.members && node.members.length > 0) {
                rightColumn.appendChild(createNodeElement({ name: node.members_name || 'Queue Members', details: node.members, classList: 'side-flow node-member' }));
            }
            
            sepContainer.appendChild(leftColumn);
            sepContainer.appendChild(centerColumn);
            sepContainer.appendChild(rightColumn);
            branchContainer.appendChild(sepContainer);
        } else {
            branchContainer.appendChild(createNodeElement(nodeData, isBranchRoot));
        }
        
        parentElement.appendChild(branchContainer);

        if (hasSubBranches) {
            const branchWrapper = document.createElement('div');
            branchWrapper.className = 'branch-wrapper w-full';
            node.branches.forEach(branch => {
                branchWrapper.appendChild(createArrowElement());
                buildBranch(branch, branchWrapper);
            });
            branchContainer.appendChild(branchWrapper);
        } else if (children.length > 0) {
            branchContainer.appendChild(createArrowElement());
            buildBranch(children, branchContainer);
        }
    }

    buildBranch(apiFlowData, mainContainer);
}

function handleVisualizeCallFlow() {
    const selectElement = document.getElementById('phone-number-select');
    if (!selectElement) {
         showMessage('Error: Phone number select element not found.', true);
         return;
    }

    const extId = selectElement.value;
    const container = document.getElementById('call-flow-diagram');
    const logContainer = document.getElementById('api-log-container');
    const selectedOption = selectElement.options[selectElement.selectedIndex];
    const selectedText = selectedOption ? selectedOption.textContent : extId;

    if (!extId || extId.startsWith('mock')) {
        showMessage('Please select a valid phone number, not a mock or empty entry.', true);
        return;
    }
    
    logContainer.innerHTML = '<div class="text-gray-500">Executing API calls...</div>';
    container.innerHTML = `<div class="p-8 text-gray-500 flex items-center justify-center h-full"><svg class="animate-spin h-8 w-8 text-purple-600 mr-3" viewBox="0 0 24 24"></svg> Generating flow for ${selectedText}...</div>`;
    
    fetch(`/api/rc/trace-flow/${extId}?phoneNumber=${encodeURIComponent(selectedText)}`)
    .then(response => response.json())
    .then(data => {
        displayApiLog(data.api_log || []);
        
        if (data.status === 'success' && data.flow_data) {
            renderHtmlFlow(data.flow_data);
            showMessage('Call flow visualization complete!', false);

        } else {
            container.innerHTML = `<div class="p-8 text-red-500">${data.message || 'Failed to generate flow. Check API Log for details.'}</div>`;
            showMessage(data.message || 'Call flow generation failed. Check debug log.', true);
        }
    })
    .catch(error => {
        container.innerHTML = '<div class="p-8 text-red-500">Network error during flow tracing.</div>';
        showMessage('Network error during flow tracing. Check console for details.', true);
        console.error('Call flow trace error:', error);
    });
}

// --- Event Listeners and Initialization for the VISUALISER TAB ---
(function() {
    // This self-invoking function ensures the code runs as soon as the script is loaded
    // and correctly scopes the variables.
    const visualizeButton = document.getElementById('visualize-button');
    const phoneSearchInput = document.getElementById('phone-search');

    if (phoneSearchInput) {
        phoneSearchInput.addEventListener('input', filterAndDisplayNumbers);
    }

    if (visualizeButton) {
        visualizeButton.addEventListener('click', handleVisualizeCallFlow);
    }
    
    // Check RC connection status before populating numbers
    fetch('/api/rc/status').then(res => res.json()).then(data => {
        const visualizeButton = document.getElementById('visualize-button');
        if (data.status === 'connected') {
            if (visualizeButton) visualizeButton.disabled = false;
            fetchAndPopulateNumbers();
        } else {
            if (visualizeButton) visualizeButton.disabled = true;
            document.getElementById('phone-number-select').innerHTML = '<option value="">-- Connect RingCentral first --</option>';
        }
    });
})();
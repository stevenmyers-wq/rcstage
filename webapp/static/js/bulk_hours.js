// webapp/static/js/bulk_hours.js
document.addEventListener('DOMContentLoaded', () => {
    // --- Element References ---
    const fetchBtns = {
        sitesHours: document.getElementById('fetch-sites-hours-btn'),
        queuesHours: document.getElementById('fetch-queues-hours-btn'),
        sitesRules: document.getElementById('fetch-sites-rules-btn'),
        queuesRules: document.getElementById('fetch-queues-rules-btn'),
    };
    const downloadCsvBtn = document.getElementById('download-csv-btn');
    const csvFileInput = document.getElementById('csv-file-input');
    const applyFromFileBtn = document.getElementById('apply-from-file-btn');
    const applyInAppChangesBtn = document.getElementById('apply-in-app-changes-btn');
    const tableContainer = document.getElementById('data-table-container');
    const statusContainer = document.getElementById('status-container');
    const statusTitle = document.getElementById('status-title');
    const statusBox = document.getElementById('status-box');
    const spinner = document.getElementById('spinner');
    const bulkEditContainer = document.getElementById('bulk-edit-container');
    const daysSelect = document.getElementById('days-select');
    const hoursInput = document.getElementById('hours-input');
    const applyBulkEditBtn = document.getElementById('apply-bulk-edit-btn');
    const filterContainer = document.getElementById('filter-container');
    const filterInput = document.getElementById('filter-input');

    let currentData = [];
    let currentDataType = ''; // 'Hours' or 'Rules'
    let headers = [];

    // --- Utility Functions ---
    const showStatus = (title, message) => {
        statusContainer.classList.remove('hidden');
        statusTitle.textContent = title;
        statusBox.textContent = message;
    };

    const toggleLoading = (isLoading) => {
        spinner.classList.toggle('hidden', !isLoading);
        Object.values(fetchBtns).forEach(btn => btn.disabled = isLoading);
        [downloadCsvBtn, applyFromFileBtn, applyInAppChangesBtn, applyBulkEditBtn].forEach(btn => btn.disabled = isLoading);
    };

    const renderTable = (data, dataType) => {
        tableContainer.innerHTML = '';
        bulkEditContainer.classList.toggle('hidden', dataType !== 'Hours');
        filterContainer.classList.toggle('hidden', !data || data.length === 0);
        filterInput.value = '';

        if (!data || data.length === 0) {
            tableContainer.innerHTML = '<p class="text-center text-gray-500">No data to display.</p>';
            return;
        }

        // --- CHANGE 1: Define a fixed order for Rules headers. ---
        if (dataType === 'Hours') {
            headers = ['EntityType', 'EntityID', 'EntityName', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
        } else if (dataType === 'Rules') {
            headers = ['EntityType', 'EntityName', 'RuleName', 'Enabled', 'Action', 'RuleID', 'EntityID', 'ScheduleType', 'ScheduleDetails', 'CallAction', 'ActionTarget'];
        } else {
            headers = Object.keys(data[0]);
        }

        const table = document.createElement('table');
        table.className = 'min-w-full divide-y divide-gray-200 border';
        
        const thead = table.createTHead();
        const headerRow = thead.insertRow();
        thead.className = 'bg-gray-50';

        if (dataType === 'Hours') {
            headerRow.insertCell(); // Placeholder for checkbox column
        }

        headers.forEach((key, index) => {
            const th = document.createElement('th');
            th.className = 'px-4 py-2 text-left text-xs font-medium text-gray-500 uppercase cursor-pointer hover:bg-gray-200';
            th.textContent = key;
            th.dataset.columnIndex = dataType === 'Hours' ? index + 1 : index; // Account for checkbox column
            th.dataset.sortDirection = 'asc';
            th.addEventListener('click', () => sortTable(th));
            headerRow.appendChild(th);
        });

        const tbody = table.createTBody();
        tbody.className = 'bg-white divide-y divide-gray-200';
        data.forEach((rowData, index) => {
            const row = tbody.insertRow();
            row.dataset.index = index;

            if (dataType === 'Hours') {
                const cellCheck = row.insertCell();
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.className = 'row-checkbox';
                checkbox.addEventListener('change', (e) => row.classList.toggle('selected-row', e.target.checked));
                cellCheck.appendChild(checkbox);
            }
            
            headers.forEach(header => {
                const cell = row.insertCell();
                cell.className = 'px-4 py-2 whitespace-nowrap text-sm text-gray-700';
                cell.dataset.header = header;
                cell.textContent = rowData[header];
            });
        });
        tableContainer.appendChild(table);
    };
    
    // --- NEW: Filter and Sort Functions ---
    const filterTable = () => {
        const searchTerm = filterInput.value.toLowerCase();
        const tableRows = tableContainer.querySelectorAll('tbody tr');
        tableRows.forEach(row => {
            const rowText = row.textContent.toLowerCase();
            row.style.display = rowText.includes(searchTerm) ? '' : 'none';
        });
    };

    const sortTable = (th) => {
        const table = th.closest('table');
        const tbody = table.querySelector('tbody');
        const columnIndex = parseInt(th.dataset.columnIndex);
        const direction = th.dataset.sortDirection === 'asc' ? 1 : -1;

        const rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort((rowA, rowB) => {
            const cellA = rowA.cells[columnIndex].textContent.trim();
            const cellB = rowB.cells[columnIndex].textContent.trim();
            return cellA.localeCompare(cellB, undefined, {numeric: true}) * direction;
        });

        tbody.innerHTML = '';
        rows.forEach(row => tbody.appendChild(row));

        // Flip direction for next click
        th.dataset.sortDirection = direction === 1 ? 'desc' : 'asc';
    };

    // --- Core Logic ---
    const handleFetch = async (dataType, entityType) => {
        toggleLoading(true);
        currentDataType = dataType;
        const entityName = entityType.charAt(0).toUpperCase() + entityType.slice(1);
        showStatus(`Fetching ${entityName} ${dataType}...`, 'Requesting data from API...');
        tableContainer.innerHTML = '';
        [downloadCsvBtn, applyInAppChangesBtn, applyFromFileBtn].forEach(b => b.disabled = true);
        
        try {
            const endpoint = `/api/bulk_hours/${dataType.toLowerCase()}/${entityType}`;
            const response = await fetch(endpoint);
            if (!response.ok) {
                let errorMsg = `HTTP error! Status: ${response.status} ${response.statusText}`;
                try { const errorJson = await response.json(); errorMsg = errorJson.error || JSON.stringify(errorJson); } catch (e) {}
                throw new Error(errorMsg);
            }
            
            const data = await response.json();
            currentData = data;
            renderTable(data, dataType);
            showStatus(`Fetch Complete`, `Loaded ${data.length} ${entityName} ${dataType} records.`);
            downloadCsvBtn.disabled = data.length === 0;
            applyInAppChangesBtn.disabled = data.length === 0;
        } catch (error) {
            showStatus('Fetch Error', error.message);
        } finally {
            toggleLoading(false);
        }
    };

    const handleApply = async (records) => {
        if (!records || records.length === 0) {
            showStatus('Info', 'No records were selected to apply.');
            return;
        }
        toggleLoading(true);
        showStatus('Applying to RingCentral...', `Sending ${records.length} records...`);
        
        try {
            const response = await fetch('/api/bulk_hours/upload', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ dataType: currentDataType, records: records })
            });
            const results = await response.json();
            if (!response.ok) throw new Error(results.error || 'Unknown upload error.');
            
            const success = results.filter(r => r.status === 'success').length;
            const errors = results.length - success;
            const log = results.map(r => `[${r.status.toUpperCase()}] ${r.name}: ${r.message}`).join('\n');
            showStatus(`Apply Complete (Success: ${success}, Failed: ${errors})`, log);

            if (success > 0) {
                tableContainer.innerHTML = '<p class="text-center font-semibold text-green-700">Changes applied successfully. Please fetch data again to see the results.</p>';
                bulkEditContainer.classList.add('hidden');
                filterContainer.classList.add('hidden');
            }

        } catch (error) {
            showStatus('Apply Failed', error.message);
        } finally {
            toggleLoading(false);
        }
    };

    // --- Event Listeners ---
    filterInput.addEventListener('keyup', filterTable);
    fetchBtns.sitesHours.addEventListener('click', () => handleFetch('Hours', 'sites'));
    fetchBtns.queuesHours.addEventListener('click', () => handleFetch('Hours', 'queues'));
    fetchBtns.sitesRules.addEventListener('click', () => handleFetch('Rules', 'sites'));
    fetchBtns.queuesRules.addEventListener('click', () => handleFetch('Rules', 'queues'));
    
    applyInAppChangesBtn.addEventListener('click', () => {
        const selectedRows = document.querySelectorAll('.row-checkbox:checked');
        if (selectedRows.length === 0) {
            alert("Please select at least one row with a checkbox before applying changes.");
            return;
        }
        const recordsToApply = Array.from(selectedRows).map(checkbox => {
            const rowIndex = checkbox.closest('tr').dataset.index;
            return currentData[rowIndex];
        });
        handleApply(recordsToApply);
    });

    downloadCsvBtn.addEventListener('click', () => {
        if (currentData.length === 0) return;
        const csvContent = [
            headers.join(','),
            ...currentData.map(row => headers.map(h => `"${String(row[h]).replace(/"/g, '""')}"`).join(','))
        ].join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `RC_${currentDataType}_${new Date().toISOString().slice(0,10)}.csv`;
        link.click();
    });

    applyBulkEditBtn.addEventListener('click', () => {
        const daysToChange = {
            weekdays: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
            weekends: ['Saturday', 'Sunday']
        }[daysSelect.value] || [daysSelect.value];

        const selectedRows = document.querySelectorAll('.row-checkbox:checked');
        if (selectedRows.length === 0) {
            alert("Please select at least one row to edit.");
            return;
        }

        selectedRows.forEach(checkbox => {
            const row = checkbox.closest('tr');
            const rowIndex = row.dataset.index;
            
            daysToChange.forEach(day => {
                currentData[rowIndex][day] = hoursInput.value.trim();
                const cell = row.querySelector(`td[data-header="${day}"]`);
                if (cell) cell.textContent = hoursInput.value.trim();
            });
            row.classList.add('modified-row');
            checkbox.checked = false;
            row.classList.remove('selected-row');
        });
        
        showStatus('Edits Applied', `${selectedRows.length} rows were modified. Select them again and click "Apply In-App Changes..." to save.`);
    });
    
    csvFileInput.addEventListener('change', () => {
        applyFromFileBtn.disabled = !csvFileInput.files.length;
    });

    applyFromFileBtn.addEventListener('click', () => {
        const file = csvFileInput.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (event) => {
            const lines = event.target.result.trim().split('\n');
            const headers = lines[0].split(',').map(h => h.trim().replace(/"/g, ''));
            const records = lines.slice(1).map(line => {
                const values = line.match(/(".*?"|[^",]+)(?=\s*,|\s*$)/g) || [];
                return headers.reduce((obj, h, i) => {
                    obj[h] = (values[i] || '').replace(/"/g, '').trim();
                    return obj;
                }, {});
            });
            handleApply(records);
        };
        reader.readAsText(file);
        csvFileInput.value = '';
        applyFromFileBtn.disabled = true;
    });
});

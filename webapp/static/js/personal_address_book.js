// webapp/static/js/personal_address_book.js
document.addEventListener('DOMContentLoaded', () => {
    // --- STATE ---
    let allUsers = [];
    let allContacts = [];
    let selectedUserIds = new Set();
    let pollingInterval; // To hold the interval for progress checking

    // Downloadable result listing for the most recent upload.
    let pabResultRows = null;
    let pabUploadCtx = { userIds: [], action: '' };
    // Shared with the server during a write so /cancel can flag this run.
    let pabStopTaskId = null;
    const pabStopBtn = document.getElementById('pab-stop-btn');
    if (pabStopBtn) {
        pabStopBtn.addEventListener('click', async () => {
            if (!pabStopTaskId) return;
            pabStopBtn.disabled = true;
            pabStopBtn.textContent = 'Stopping...';
            try {
                await fetch(`/api/pab/cancel?task_id=${pabStopTaskId}`, { method: 'POST' });
            } catch (e) { /* server loop also polls the flag */ }
        });
    }
    function pabShowStop(show) {
        if (!pabStopBtn) return;
        pabStopBtn.classList.toggle('hidden', !show);
        pabStopBtn.disabled = false;
        pabStopBtn.textContent = '■ Stop';
    }
    UCResults.attachButton(document.getElementById('pab-result-btn'), () => pabResultRows, 'Address_Book_Results', 'Results');
    function showPabResults(rows) {
        pabResultRows = (rows && rows.length) ? rows : null;
        document.getElementById('pab-result-btn').classList.toggle('hidden', !pabResultRows);
    }

    // --- DOM ELEMENTS ---
    const fetchUsersBtn = document.getElementById('pab-fetch-users-btn');
    const getContactsBtn = document.getElementById('pab-get-contacts-btn');
    const userLoader = document.getElementById('pab-user-loader');
    const userTbody = document.getElementById('pab-user-list-tbody');
    const contactsSection = document.getElementById('pab-contacts-section');
    const contactLoader = document.getElementById('pab-contact-loader');
    const contactLoaderText = document.getElementById('pab-contact-loader-text');
    const contactTbody = document.getElementById('pab-contact-list-tbody');
    const selectAllUsersCheckbox = document.getElementById('pab-select-all-users');
    const selectAllContactsCheckbox = document.getElementById('pab-select-all-contacts');
    const deleteContactsBtn = document.getElementById('pab-delete-contacts-btn');
    const exportCsvBtn = document.getElementById('pab-export-csv-btn');
    const uploadCsvBtn = document.getElementById('pab-upload-csv-btn');
    const csvFileInput = document.getElementById('pab-csv-upload-input');
    const uploadActionSelect = document.getElementById('pab-upload-action');
    const filterName = document.getElementById('pab-filter-name');
    const filterExt = document.getElementById('pab-filter-ext');
    const filterDept = document.getElementById('pab-filter-dept');
    const filterSite = document.getElementById('pab-filter-site');

    // --- RENDER FUNCTIONS ---
    const renderUsers = (users) => {
        userTbody.innerHTML = '';
        users.forEach(user => {
            const isChecked = selectedUserIds.has(String(user.id));
            const row = `
                <tr>
                    <td><input type="checkbox" class="pab-user-checkbox" data-user-id="${user.id}" ${isChecked ? 'checked' : ''}></td>
                    <td>${user.contact.firstName || ''} ${user.contact.lastName || ''}</td>
                    <td>${user.extensionNumber || 'N/A'}</td>
                    <td>${user.contact.department || 'N/A'}</td>
                    <td>${user.site?.name || 'N/A'}</td>
                    <td>${user.contact.email || 'N/A'}</td>
                </tr>
            `;
            userTbody.insertAdjacentHTML('beforeend', row);
        });
    };

    const renderContacts = (contacts) => {
        contactTbody.innerHTML = '';
        contactsSection.style.display = 'block';
        contacts.forEach((contactGroup, index) => {
            const contact = contactGroup.contactData;
            const userNames = contactGroup.users.map(u => allUsers.find(au => au.id == u.userId)?.contact?.firstName + ' ' + allUsers.find(au => au.id == u.userId)?.contact?.lastName || u.userId).join(', ');
            const row = `
                <tr>
                    <td><input type="checkbox" class="pab-contact-checkbox" data-contact-index="${index}"></td>
                    <td>${contact.firstName || ''}</td>
                    <td>${contact.lastName || ''}</td>
                    <td>${contact.businessPhone || ''}</td>
                    <td>${contact.homePhone || ''}</td>
                    <td>${contact.mobilePhone || ''}</td>
                    <td>${contact.email || ''}</td>
                    <td><span class="badge bg-secondary" title="${userNames}">${contactGroup.users.length} user(s)</span></td>
                </tr>
            `;
            contactTbody.insertAdjacentHTML('beforeend', row);
        });
    };
    
    // --- EVENT LISTENERS ---
    const applyUserFilter = () => {
        const nameFilter = filterName.value.toLowerCase();
        const extFilter = filterExt.value.toLowerCase();
        const deptFilter = filterDept.value.toLowerCase();
        const siteFilter = filterSite.value.toLowerCase();
        const filteredUsers = allUsers.filter(user => {
            const fullName = `${user.contact.firstName || ''} ${user.contact.lastName || ''}`.toLowerCase();
            const extension = (user.extensionNumber || '').toLowerCase();
            const department = (user.contact.department || '').toLowerCase(); // This was the broken line
            const site = (user.site?.name || '').toLowerCase();
            return fullName.includes(nameFilter) && extension.includes(extFilter) && department.includes(deptFilter) && site.includes(siteFilter);
        });
        renderUsers(filteredUsers);
    };

    [filterName, filterExt, filterDept, filterSite].forEach(input => input.addEventListener('input', applyUserFilter));

    fetchUsersBtn.addEventListener('click', async () => {
        userLoader.style.display = 'block';
        userTbody.innerHTML = '';
        contactsSection.style.display = 'none';
        selectedUserIds.clear();
        getContactsBtn.disabled = true;
        try {
            const response = await fetch('/api/pab/users');
            if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
            allUsers = await response.json();
            renderUsers(allUsers);
        } catch (error) {
            console.error("Error fetching users:", error);
        } finally {
            userLoader.style.display = 'none';
        }
    });

    userTbody.addEventListener('change', (e) => {
        if (e.target.classList.contains('pab-user-checkbox')) {
            const userId = e.target.dataset.userId;
            if (e.target.checked) {
                selectedUserIds.add(userId);
            } else {
                selectedUserIds.delete(userId);
            }
        }
        getContactsBtn.disabled = selectedUserIds.size === 0;
    });

    selectAllUsersCheckbox.addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        const visibleUserCheckboxes = userTbody.querySelectorAll('.pab-user-checkbox');
        visibleUserCheckboxes.forEach(cb => {
            cb.checked = isChecked;
            const userId = cb.dataset.userId;
            if (isChecked) {
                selectedUserIds.add(userId);
            } else {
                selectedUserIds.delete(userId);
            }
        });
        getContactsBtn.disabled = selectedUserIds.size === 0;
    });

    getContactsBtn.addEventListener('click', async () => {
        const userIdsArray = Array.from(selectedUserIds);
        if (userIdsArray.length === 0) return;
        contactLoader.style.display = 'block';
        contactLoaderText.textContent = 'Fetching contacts... This may take a moment.';
        contactTbody.innerHTML = '';
        contactsSection.style.display = 'block';
        try {
            const response = await fetch('/api/pab/contacts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ userIds: userIdsArray })
            });
            if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
            allContacts = await response.json();
            renderContacts(allContacts);
        } catch (error) {
            console.error("Error fetching contacts:", error);
        } finally {
            contactLoader.style.display = 'none';
        }
    });
    
    contactTbody.addEventListener('change', () => {
        const selectedContacts = document.querySelectorAll('.pab-contact-checkbox:checked').length;
        deleteContactsBtn.disabled = selectedContacts === 0;
        selectAllContactsCheckbox.checked = selectedContacts > 0 && selectedContacts === document.querySelectorAll('.pab-contact-checkbox').length;
    });

    selectAllContactsCheckbox.addEventListener('change', (e) => {
        document.querySelectorAll('.pab-contact-checkbox').forEach(cb => cb.checked = e.target.checked);
        deleteContactsBtn.disabled = !e.target.checked;
    });
    
    deleteContactsBtn.addEventListener('click', async () => {
        const selectedContactIndexes = Array.from(document.querySelectorAll('.pab-contact-checkbox:checked')).map(cb => parseInt(cb.dataset.contactIndex));
        if (selectedContactIndexes.length === 0 || !confirm(`Delete ${selectedContactIndexes.length} contact(s)?`)) return;

        const contactsToDelete = selectedContactIndexes.map(index => allContacts[index]);
        contactLoader.style.display = 'block';
        contactLoaderText.textContent = 'Deleting contacts...';
        pabStopTaskId = `pab_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        pabShowStop(true);
        try {
            const response = await fetch('/api/pab/contacts/delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ contacts: contactsToDelete, stop_task_id: pabStopTaskId })
            });
            if (!response.ok) throw new Error('Failed to delete contacts.');
            getContactsBtn.click();
        } catch (error) {
            console.error("Error deleting contacts:", error);
        } finally {
            pabStopTaskId = null;
            pabShowStop(false);
            contactLoader.style.display = 'none';
        }
    });

    exportCsvBtn.addEventListener('click', () => {
        if (allContacts.length === 0) return;
        const headers = ['firstName', 'lastName', 'email', 'businessPhone', 'homePhone', 'mobilePhone', 'company'];
        let csvContent = "data:text/csv;charset=utf-8," + headers.join(',') + '\n';
        allContacts.forEach(contactGroup => {
            const contact = contactGroup.contactData;
            const row = headers.map(header => `"${contact[header] || ''}"`).join(',');
            csvContent += row + '\n';
        });
        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", "unique_contacts_export.csv");
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });

    // Build a per-user result listing for the RC bulk-add task. If RC returns a
    // per-record array, reflect it; otherwise fall back to one row per targeted
    // user carrying the final task status.
    function buildTaskResultRows(data, taskStatus) {
        const records = Array.isArray(data.records) ? data.records
                      : (Array.isArray(data.errors) ? data.errors : null);
        if (records) {
            return records.map(r => ({
                'User ID': r.extensionId || r.userId || '',
                'Action': pabUploadCtx.action,
                'Status': r.status || taskStatus,
                'Detail': (r.errors ? JSON.stringify(r.errors) : (r.message || ''))
            }));
        }
        return pabUploadCtx.userIds.map(uid => ({
            'User ID': uid,
            'Action': pabUploadCtx.action,
            'Status': taskStatus,
            'Detail': data.message || ''
        }));
    }

    // --- NEW: Polling function for checking task status ---
    function pollTaskStatus(taskId) {
        const progressContainer = document.getElementById('pab-progress-container');
        const progressBar = document.getElementById('pab-progress-bar');
        const progressLabel = document.getElementById('pab-progress-label');

        pollingInterval = setInterval(async () => {
            try {
                const response = await fetch(`/api/pab/contacts/task-status/${taskId}`);
                const data = await response.json();

                if (!response.ok) throw new Error(data.error || 'Failed to fetch task status.');

                const progress = data.progress || 0;
                progressBar.style.width = `${progress}%`;
                progressBar.textContent = `${progress}%`;
                progressBar.setAttribute('aria-valuenow', progress);
                progressLabel.textContent = `Processing... Status: ${data.status}`;
                
                if (data.status === 'Completed') {
                    clearInterval(pollingInterval);
                    progressLabel.textContent = 'Upload complete! Refreshing...';
                    progressBar.classList.remove('progress-bar-animated', 'bg-info');
                    progressBar.classList.add('bg-success');
                    showPabResults(buildTaskResultRows(data, 'Completed'));

                    setTimeout(() => {
                        progressContainer.style.display = 'none';
                        getContactsBtn.click();
                    }, 1500);

                } else if (data.status === 'Failed') {
                    clearInterval(pollingInterval);
                    progressLabel.textContent = `Upload failed: ${data.message || 'Check server logs.'}`;
                    progressBar.classList.remove('progress-bar-animated', 'bg-info');
                    progressBar.classList.add('bg-danger');
                    showPabResults(buildTaskResultRows(data, 'Failed'));
                }
            } catch (error) {
                console.error('Polling error:', error);
                clearInterval(pollingInterval);
                progressLabel.textContent = `Error checking status: ${error.message}`;
                progressBar.classList.add('bg-danger');
            }
        }, 2500);
    }

    // --- REPLACED: uploadCsvBtn event listener with new progress bar logic ---
    uploadCsvBtn.addEventListener('click', () => {
        if (pollingInterval) clearInterval(pollingInterval); // Clear any old timers
        const file = csvFileInput.files[0];
        if (!file) return;
        const userIdsToUpdate = Array.from(selectedUserIds);
        if (userIdsToUpdate.length === 0) return;

        const reader = new FileReader();
        reader.onload = async (event) => {
            const csv = event.target.result;
            const lines = csv.split(/\r\n|\n/).filter(line => line.trim() !== '');
            if (lines.length < 2) return;
            
            const headers = lines[0].split(',').map(h => h.trim().replace(/"/g, ''));
            const contacts = lines.slice(1).map(line => {
                // Regex to handle commas inside quoted fields
                const values = line.match(/(".*?"|[^",]+)(?=\s*,|\s*$)/g) || [];
                const rowValues = values.map(v => v.trim().replace(/"/g, ''));
                let contact = {};
                headers.forEach((header, index) => {
                    if (rowValues[index]) contact[header] = rowValues[index];
                });
                return contact;
            });

            const action = uploadActionSelect.value;
            if (!confirm(`Are you sure you want to '${action}' contacts for ${userIdsToUpdate.length} user(s)?`)) return;
            
            const progressContainer = document.getElementById('pab-progress-container');
            const progressBar = document.getElementById('pab-progress-bar');
            const progressLabel = document.getElementById('pab-progress-label');
            
            // Reset and show progress bar
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            progressBar.classList.remove('bg-success', 'bg-danger');
            progressBar.classList.add('progress-bar-animated', 'bg-info');
            progressLabel.textContent = `Initiating '${action}' operation...`;
            pabUploadCtx = { userIds: userIdsToUpdate, action };
            showPabResults(null);
            progressContainer.style.display = 'block';
            // 'add' is a single RC bulk task (not item-by-item on our side), so
            // only offer Stop for the item-by-item remove/update paths.
            pabStopTaskId = `pab_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
            pabShowStop(action !== 'add');

            try {
                const response = await fetch('/api/pab/contacts/upload', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ userIds: userIdsToUpdate, contacts, action, stop_task_id: pabStopTaskId })
                });
                const result = await response.json();
                if (!response.ok || result.error) throw new Error(result.error || `Failed to start ${action} process.`);
                
                if (result.task_id) {
                    progressLabel.textContent = 'Task started. Monitoring progress...';
                    progressBar.style.width = '5%';
                    progressBar.textContent = '5%';
                    pollTaskStatus(result.task_id);
                } else {
                    progressLabel.textContent = result.status || 'Operation complete.';
                    progressBar.style.width = '100%';
                    progressBar.classList.add('bg-success');

                    // 'remove'/'sync' return a per-contact details array.
                    if (Array.isArray(result.details)) {
                        showPabResults(result.details.map(d => ({
                            'User ID': d.userId || '',
                            'Contact ID': d.contactId || '',
                            'Action': action,
                            'Status': d.status === 'success' ? 'Success' : (d.status || 'Unknown'),
                            'Detail': d.message || ''
                        })));
                    } else {
                        showPabResults(pabUploadCtx.userIds.map(uid => ({
                            'User ID': uid, 'Action': action, 'Status': 'Completed', 'Detail': result.status || ''
                        })));
                    }
                }
            } catch (error) {
                console.error('Upload error:', error);
                progressLabel.textContent = `Error: ${error.message}`;
                progressBar.classList.add('bg-danger');
            } finally {
                // For the 'add' path the RC task keeps polling; the stop button
                // was never shown there. For remove/update the work is done here.
                if (action !== 'add') { pabStopTaskId = null; pabShowStop(false); }
            }
        };
        reader.readAsText(file);
    });
});

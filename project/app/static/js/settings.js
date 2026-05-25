document.addEventListener('DOMContentLoaded', () => {
    loadDatabases();

    document.getElementById('add-db-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const data = {
            name: document.getElementById('db-name').value,
            file_path: document.getElementById('db-path').value,
            role: document.getElementById('db-role').value
        };

        try {
            await API.post('/datasources/', data);
            alert('Database added successfully');
            e.target.reset();
            loadDatabases();
        } catch (error) {
            alert('Error adding database: ' + error.message);
        }
    });

    const scanFolderForm = document.getElementById('scan-folder-form');
    if (scanFolderForm) {
        scanFolderForm.addEventListener('submit', handleFolderScanSubmit);
    }

    startDatasourceListPolling();
});

function startDatasourceListPolling() {
    setInterval(() => {
        if (document.hidden) return;
        loadDatabases();
    }, 5000);
}

function showScanResult(message, isError = false) {
    const resultEl = document.getElementById('db-scan-result');
    if (!resultEl) return;

    resultEl.style.display = 'block';
    resultEl.innerText = message;
    resultEl.style.color = isError ? 'var(--danger)' : 'var(--text-primary)';
    resultEl.style.background = isError ? 'rgba(242, 54, 69, 0.12)' : 'rgba(41, 98, 255, 0.12)';
    resultEl.style.border = `1px solid ${isError ? 'rgba(242, 54, 69, 0.25)' : 'rgba(41, 98, 255, 0.25)'}`;
}

async function handleFolderScanSubmit(e) {
    e.preventDefault();

    const folderPath = document.getElementById('db-scan-folder').value.trim();
    const role = document.getElementById('db-scan-role').value;
    const recursive = document.getElementById('db-scan-recursive').checked;
    const autoScan = document.getElementById('db-scan-auto-schema').checked;
    const submitBtn = e.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerText;

    if (!folderPath) {
        showScanResult('Please enter a folder path to scan.', true);
        return;
    }

    submitBtn.disabled = true;
    submitBtn.innerText = 'Scanning...';
    showScanResult('Scanning folder for SQLite files...');

    try {
        const result = await API.post('/datasources/scan-folder', {
            folder_path: folderPath,
            role: role,
            recursive: recursive,
            auto_scan: autoScan
        });

        const summary = [
            `Found ${result.discovered} SQLite files`,
            `Imported ${result.imported}`,
            `Skipped ${result.skipped}`,
            autoScan ? `Schema scanned ${result.scanned}` : 'Schema scan skipped'
        ].join(' | ');

        showScanResult(result.scan_errors && result.scan_errors.length
            ? `${summary} | ${result.scan_errors.length} scan errors`
            : summary);

        await loadDatabases();
    } catch (error) {
        showScanResult('Error scanning folder: ' + error.message, true);
    } finally {
        submitBtn.disabled = false;
        submitBtn.innerText = originalText;
    }
}

async function loadDatabases() {
    try {
        const dbs = await API.get('/datasources/');
        const tbody = document.getElementById('db-list');
        tbody.innerHTML = '';

        dbs.sort((a, b) => a.name.localeCompare(b.name));

        dbs.forEach(db => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td style="padding: 0.5rem;">${db.name}<br><small style="color:#666">${db.role}</small></td>
                <td style="padding: 0.5rem;"><code>${db.file_path}</code></td>
                <td style="padding: 0.5rem;">
                    ${db.last_scanned_at ? `<small>Scanned: ${new Date(db.last_scanned_at).toLocaleString()}</small>` : '<small>Not scanned</small>'}
                </td>
                <td style="padding: 0.5rem;">
                    <button class="btn" onclick="scanDatabase(${db.id})" style="font-size: 0.8rem; padding: 0.3rem 0.6rem;">Scan Schema</button>
                    <button class="btn btn-danger" onclick="deleteDatabase(${db.id})" style="font-size: 0.8rem; padding: 0.3rem 0.6rem; margin-left: 0.5rem;">Delete</button>
                </td>
            `;
            tr.style.borderBottom = '1px solid var(--border-color)';
            tbody.appendChild(tr);
        });
    } catch (error) {
        console.error('Failed to load databases', error);
    }
}

async function scanDatabase(id) {
    try {
        const result = await API.post(`/datasources/${id}/scan`);
        alert(`Scanned successfully: Found ${result.tables} tables and ${result.columns} columns.`);
        loadDatabases();
    } catch (error) {
        alert('Error scanning database: ' + error.message);
    }
}

async function deleteDatabase(id) {
    if (!confirm('Are you sure you want to delete this database registration?')) return;
    try {
        await API.delete(`/datasources/${id}`);
        loadDatabases();
    } catch (error) {
        alert('Error deleting database: ' + error.message);
    }
}

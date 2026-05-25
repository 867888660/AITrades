let grid;
let currentDashboardId = null;
let dbSchemaCache = {}; // for main chart modal
let allDatabases = []; // Global cache for DBs
let settingsDbSchemas = {}; // db_id -> schema cache for settings drawer
let activeCharts = {};
let activeWidgetId = null;
let currentSettingsChartConfig = null;
let activePaneId = null;
let datasourceSignature = '';
let datasourcePollInFlight = false;

document.addEventListener('DOMContentLoaded', async () => {
    // Initialize GridStack
    grid = GridStack.init({
        cellHeight: 80,
        margin: 16, // Use space-4 roughly
        handle: '.chart-header',
        column: 12,
        animate: true
    });

    // Resize ECharts on GridStack resize
    grid.on('resizestop', function(event, el) {
        const widgetId = el.getAttribute('gs-id');
        const chartMap = activeCharts[widgetId];
        if (chartMap) {
            Object.values(chartMap).forEach(chart => {
                if (chart) chart.resize();
            });
        }
    });

    // Handle Active Chart Selection
    document.getElementById('workspace').addEventListener('click', (e) => {
        const item = e.target.closest('.grid-stack-item');
        if (item) {
            setActiveWidget(item.getAttribute('gs-id'));
        }
    });

    await loadDashboards();
    
    // Fetch all databases initially
    try {
        await refreshAllDatabases(true);
    } catch(e) {
        console.error("Failed to load databases", e);
    }

    document.getElementById('dashboard-select').addEventListener('change', async (e) => {
        currentDashboardId = e.target.value;
        if (currentDashboardId) {
            document.getElementById('btn-add-chart').style.display = 'inline-flex';
            await loadDashboardLayout();
        } else {
            document.getElementById('btn-add-chart').style.display = 'none';
            grid.removeAll();
            updateSidebarList();
        }
    });

    document.getElementById('chart-form').addEventListener('submit', handleChartSubmit);
    
    // Live preview settings form
    const settingsForm = document.getElementById('settings-form');
    if (settingsForm) {
        settingsForm.addEventListener('input', debounce(previewSettings, 300));
        settingsForm.addEventListener('change', debounce(previewSettings, 100)); // for selects and color picker
        settingsForm.addEventListener('submit', saveSettings);
    }

    startDatasourcePolling();
});

function debounce(func, wait) {
    let timeout;
    return function(...args) {
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(this, args), wait);
    };
}

async function refreshAllDatabases(force = false) {
    if (!force && allDatabases.length > 0) return allDatabases;
    allDatabases = await API.get('/datasources/');
    datasourceSignature = buildDatasourceSignature(allDatabases);
    return allDatabases;
}

function populateDatabaseSelect(selectEl, databases, placeholder = 'Select DB...') {
    if (!selectEl) return;
    selectEl.innerHTML = `<option value="">${placeholder}</option>`;
    databases.forEach(db => {
        selectEl.innerHTML += `<option value="${db.id}">${db.name}</option>`;
    });
}

function buildDatasourceSignature(databases) {
    return (databases || [])
        .map(db => `${db.id}:${db.updated_at || ''}:${db.last_scanned_at || ''}`)
        .sort()
        .join('|');
}

function startDatasourcePolling() {
    setInterval(async () => {
        if (document.hidden || datasourcePollInFlight) return;
        datasourcePollInFlight = true;
        try {
            const latestDatabases = await API.get('/datasources/');
            const nextSignature = buildDatasourceSignature(latestDatabases);
            if (nextSignature !== datasourceSignature) {
                allDatabases = latestDatabases;
                datasourceSignature = nextSignature;
                await refreshDashboardWidgets();
            }
        } catch (e) {
            console.error('Failed to auto-refresh datasource state', e);
        } finally {
            datasourcePollInFlight = false;
        }
    }, 5000);
}

async function refreshDashboardWidgets() {
    if (!currentDashboardId || !grid || !grid.engine || !grid.engine.nodes) return;
    const widgetNodes = [...grid.engine.nodes];
    for (const node of widgetNodes) {
        const el = node.el;
        if (!el) continue;
        const widgetId = node.id;
        const chartId = el.getAttribute('data-chart-id');
        if (widgetId && chartId) {
            await refreshWidget(widgetId, chartId);
        }
    }
}

async function loadDashboards() {
    try {
        const dashboards = await API.get('/dashboards/');
        const select = document.getElementById('dashboard-select');
        select.innerHTML = '<option value="">Select Dashboard...</option>';
        dashboards.forEach(d => {
            select.innerHTML += `<option value="${d.id}">${d.name}</option>`;
        });
    } catch (e) {
        console.error(e);
    }
}

async function createNewDashboard() {
    const name = prompt("Enter dashboard name:");
    if (!name) return;
    try {
        const res = await API.post('/dashboards/', { name });
        await loadDashboards();
        document.getElementById('dashboard-select').value = res.id;
        document.getElementById('dashboard-select').dispatchEvent(new Event('change'));
    } catch (e) {
        alert("Error: " + e.message);
    }
}

async function loadDashboardLayout() {
    try {
        const layout = await API.get(`/dashboards/${currentDashboardId}/layout`);
        grid.removeAll();
        activeCharts = {};
        
        for (const item of layout) {
            await addWidgetToGrid(item);
        }
        updateSidebarList();
    } catch (e) {
        console.error("Failed to load layout", e);
    }
}

async function saveLayout() {
    if (!currentDashboardId) return;
    try {
        const layout = grid.engine.nodes.map(n => ({
            id: n.id,
            x: n.x,
            y: n.y,
            w: n.w,
            h: n.h
        }));
        await API.put(`/dashboards/${currentDashboardId}/layout`, layout);
        alert('Layout saved successfully');
    } catch (e) {
        alert("Error saving layout: " + e.message);
    }
}

// Sidebar logic
function updateSidebarList() {
    const list = document.getElementById('sidebar-chart-list');
    list.innerHTML = '';
    
    const nodes = grid.engine.nodes;
    if (nodes.length === 0) {
        list.innerHTML = `<li style="color: var(--text-muted); padding: var(--space-3); font-size: 12px; text-align: center;">No charts yet</li>`;
        return;
    }
    
    // Sort by y position
    const sortedNodes = [...nodes].sort((a, b) => a.y - b.y);
    
    sortedNodes.forEach(node => {
        const el = node.el;
        if (!el) return;
        const title = el.querySelector('.chart-title').innerText;
        const li = document.createElement('li');
        li.className = 'sidebar-item';
        if (node.id === activeWidgetId) {
            li.classList.add('active');
        }
        li.innerHTML = `<span>${title}</span> <span style="font-size: 10px; color: var(--text-muted);">ID: ${node.id.split('-')[1]}</span>`;
        li.onclick = () => {
            setActiveWidget(node.id);
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        };
        list.appendChild(li);
    });
}

async function setActiveWidget(widgetId) {
    if (activeWidgetId === widgetId) return;
    activeWidgetId = widgetId;
    
    // Update classes in DOM
    document.querySelectorAll('.grid-stack-item').forEach(item => {
        item.classList.toggle('active', item.getAttribute('gs-id') === widgetId);
    });
    
    updateSidebarList();
    
    // Open drawer and load settings
    const el = document.querySelector(`[gs-id="${widgetId}"]`);
    if (el) {
        const chartId = el.getAttribute('data-chart-id');
        if (chartId) {
            await openSettingsDrawer(widgetId, chartId);
        }
    }
}

// Chart Modal Functions
function showChartModal() {
    const modal = document.getElementById('chart-modal');
    modal.classList.add('show');
    
    // Refresh on open so newly imported databases are immediately available.
    refreshAllDatabases(true)
        .then(dbs => populateDatabaseSelect(document.getElementById('chart-db'), dbs))
        .catch(e => console.error(e));
}

function hideChartModal() {
    document.getElementById('chart-modal').classList.remove('show');
    document.getElementById('chart-form').reset();
}

async function loadTables() {
    const dbId = document.getElementById('chart-db').value;
    if (!dbId) return;
    
    try {
        const schema = await API.get(`/schema/${dbId}`);
        dbSchemaCache = schema;
        const sel = document.getElementById('chart-table');
        sel.innerHTML = '<option value="">Select Table...</option>';
        Object.keys(schema).forEach(table => {
            sel.innerHTML += `<option value="${table}">${table}</option>`;
        });
    } catch(e) {
        console.error(e);
    }
}

function loadColumns() {
    const table = document.getElementById('chart-table').value;
    if (!table || !dbSchemaCache[table]) return;
    
    const cols = dbSchemaCache[table];
    const timeSel = document.getElementById('chart-time-col');
    const valSel = document.getElementById('chart-val-cols');
    
    timeSel.innerHTML = '<option value="">-- No X Axis (Index) --</option>';
    valSel.innerHTML = '';
    
    cols.forEach(col => {
        const name = col.column_name;
        if (col.is_time_candidate) {
            timeSel.innerHTML = `<option value="${name}" selected>${name}</option>` + timeSel.innerHTML;
        } else {
            timeSel.innerHTML += `<option value="${name}">${name}</option>`;
        }
        
        if (col.is_numeric_candidate) {
            valSel.innerHTML = `<option value="${name}">${name}</option>` + valSel.innerHTML;
        } else {
            valSel.innerHTML += `<option value="${name}">${name}</option>`;
        }
    });
}

async function handleChartSubmit(e) {
    e.preventDefault();
    const valSel = document.getElementById('chart-val-cols');
    const selectedVals = Array.from(valSel.selectedOptions).map(opt => opt.value);
    
    if (selectedVals.length === 0) {
        alert("Please select at least one value column");
        return;
    }
    
    const config = {
        name: document.getElementById('chart-name').value,
        db_id: document.getElementById('chart-db').value,
        table_name: document.getElementById('chart-table').value,
        time_column: document.getElementById('chart-time-col').value,
        panes: [
            {
                id: "main",
                name: "Main Pane",
                kind: "main",
                height: 360,
                series: selectedVals.map((v, i) => ({
                    column: v,
                    label: v,
                    yAxisIndex: 0,
                    type: 'line',
                    null_mode: 'carry_forward'
                }))
            }
        ],
        query: {
            limit: 5000,
            downsample: { enabled: true, max_points: 1000 }
        }
    };
    
    try {
        const res = await API.post('/chart/', config);
        const widgetKey = `widget-${res.id}-${Date.now()}`;
        
        // Force full width (12 columns)
        await API.post(`/dashboards/${currentDashboardId}/widgets`, {
            chart_id: res.id,
            widget_key: widgetKey,
            w: 12, h: 6
        });
        
        hideChartModal();
        
        await addWidgetToGrid({
            id: widgetKey,
            chart_id: res.id,
            x: 0, y: 1000, // push to bottom
            w: 12, h: 6
        });
        
        updateSidebarList();
        setActiveWidget(widgetKey);
    } catch(e) {
        alert("Error saving chart: " + e.message);
    }
}

async function addWidgetToGrid(item) {
    let chartId;
    if (item.chart_id) {
        chartId = item.chart_id;
    } else {
        const parts = item.id.split('-');
        if (parts.length >= 2) chartId = parts[1];
    }
    
    if (!chartId) return;

    const widgetId = item.id;
    const containerId = `chart-container-${widgetId}`;
    
    // Force w=12 for a terminal workspace feel
    const w = 12;
    const h = item.h || 6;

    const html = `
        <div class="grid-stack-item" gs-id="${widgetId}" data-chart-id="${chartId}" gs-x="${item.x||0}" gs-y="${item.y||0}" gs-w="${w}" gs-h="${h}">
            <div class="grid-stack-item-content">
                <div class="chart-card workspace-card">
                    <div class="chart-header workspace-header">
                        <div class="chart-title-area">
                            <span class="chart-title">Loading...</span>
                            <span class="chart-subtitle"></span>
                        </div>
                        <div class="chart-actions">
                            <button class="icon-btn" onclick="addPane('${widgetId}'); event.stopPropagation();" title="Add Pane">+Pane</button>
                            <button class="icon-btn" onclick="openSettingsDrawer('${widgetId}', ${chartId}); event.stopPropagation();" title="Settings">⚙️</button>
                            <button class="icon-btn" onclick="refreshWidget('${widgetId}', ${chartId}); event.stopPropagation();" title="Refresh">🔄</button>
                            <button class="icon-btn" onclick="removeWidget(this); event.stopPropagation();" style="color:var(--danger);" title="Remove">🗑️</button>
                        </div>
                    </div>
                    <div class="workspace-body" style="flex: 1; width: 100%; min-height: 0; display: flex; flex-direction: column; overflow-y: auto;" id="${containerId}"></div>
                </div>
            </div>
        </div>
    `;
    
    const node = grid.addWidget(html);
    const el = node.el || node;
    
    try {
        await loadAndRenderChart(widgetId, chartId, el, containerId);
    } catch(e) {
        console.error(e);
        const body = el.querySelector('.workspace-body');
        if (body) body.innerText = "Error loading chart data.";
    }
}

function ensurePanes(chartConfig) {
    if (chartConfig.panes && chartConfig.panes.length) return chartConfig.panes;

    return [
        {
            id: "main",
            name: "Main Pane",
            kind: "main",
            height: 360,
            series: chartConfig.series || []
        }
    ];
}

function getPaneById(config, paneId) {
    const panes = ensurePanes(config);
    return panes.find(p => p.id === paneId);
}

function getActivePane() {
    if (!currentSettingsChartConfig) return null;
    const panes = ensurePanes(currentSettingsChartConfig);
    return panes.find(p => p.id === activePaneId) || panes[0] || null;
}

async function loadAndRenderChart(widgetId, chartId, el, containerId, customConfig = null) {
    const chartConfig = customConfig || await API.get(`/chart/${chartId}`);
    el.querySelector('.chart-title').innerText = chartConfig.name;
    el.querySelector('.chart-subtitle').innerText = `${chartConfig.table_name}`;
    
    const panes = ensurePanes(chartConfig);
    const workspaceBody = document.getElementById(containerId);
    
    // Dispose old charts before clearing HTML to prevent detached DOM memory leaks and rendering issues
    if (activeCharts[widgetId]) {
        Object.values(activeCharts[widgetId]).forEach(chart => {
            if (chart && typeof chart.dispose === 'function') {
                chart.dispose();
            }
        });
    }
    activeCharts[widgetId] = {};

    workspaceBody.innerHTML = "";

    for (let i = 0; i < panes.length; i++) {
        const pane = panes[i];
        const paneId = `pane-${widgetId}-${pane.id || i}`;
        
        const paneHtml = `
            <div class="pane-card ${pane.kind}-pane" style="display: flex; flex-direction: column; ${pane.kind === 'main' ? 'flex: 1;' : `height: ${pane.height || 160}px;`} border-bottom: 1px solid var(--border-subtle);">
                <div class="pane-header" style="padding: 2px var(--space-2); font-size: 11px; color: var(--text-secondary); background: var(--bg-panel-alt); display: flex; justify-content: space-between; align-items: center;">
                    <span>${pane.name || 'Pane'}</span>
                    <div style="display:flex; gap:6px;">
                        <button class="icon-btn" onclick="editPane('${widgetId}', '${pane.id}'); event.stopPropagation();" title="Edit Pane">⚙️</button>
                        ${pane.kind !== 'main' ? `<button class="icon-btn" onclick="deletePane('${widgetId}', '${pane.id}'); event.stopPropagation();" style="color:var(--danger);" title="Delete Pane">✖</button>` : ''}
                    </div>
                </div>
                <div class="pane-chart" id="${paneId}" style="flex: 1; min-height: 0;"></div>
            </div>
        `;
        workspaceBody.insertAdjacentHTML('beforeend', paneHtml);

        let data = { series_data: [], rows: [], multi_series: false };
        if (pane.series && pane.series.length > 0) {
            try {
                data = await API.post('/chart/query', {
                    db_id: chartConfig.db_id,
                    table_name: chartConfig.table_name,
                    time_column: chartConfig.time_column,
                    series: pane.series,
                    filters: [],
                    order: "asc",
                    limit: 5000,
                    downsample: { enabled: true, max_points: 800 }
                });
            } catch (e) {
                console.error("Error fetching data for pane:", e);
            }
        }
        
        // Use setTimeout to ensure DOM is fully rendered before ECharts init
        setTimeout(() => {
            renderEChart(paneId, pane, data, chartConfig, widgetId);
        }, 0);
    }
}

async function refreshWidget(widgetId, chartId) {
    const el = document.querySelector(`[gs-id="${widgetId}"]`);
    if (!el) return;
    const containerId = el.querySelector('.workspace-body').id;
    const chartMap = activeCharts[widgetId];
    if (chartMap) {
        Object.values(chartMap).forEach(chart => {
            if (chart && typeof chart.showLoading === 'function') {
                try { chart.showLoading({ color: '#2962ff', maskColor: 'rgba(19,23,34,0.8)' }); } catch (e) {}
            }
        });
    }
    
    try {
        await loadAndRenderChart(widgetId, chartId, el, containerId);
    } finally {
        const newChartMap = activeCharts[widgetId];
        if (newChartMap) {
            Object.values(newChartMap).forEach(chart => {
                if (chart && typeof chart.hideLoading === 'function') {
                    try { chart.hideLoading(); } catch (e) {}
                }
            });
        }
    }
}

async function addPane(widgetId) {
    const el = document.querySelector(`[gs-id="${widgetId}"]`);
    if (!el) return;
    const chartId = el.getAttribute('data-chart-id');

    try {
        const chartConfig = await API.get(`/chart/${chartId}`);
        const panes = ensurePanes(chartConfig);

        panes.push({
            id: `aux_${Date.now()}`,
            name: `Aux Pane ${panes.length}`,
            kind: "aux",
            height: 160,
            series: []
        });

        const payload = {
            name: chartConfig.name,
            db_id: chartConfig.db_id,
            table_name: chartConfig.table_name,
            time_column: chartConfig.time_column,
            series: chartConfig.series || [],
            panes: panes
        };

        await API.put(`/chart/${chartId}`, payload);
        await refreshWidget(widgetId, chartId);

        if (activeWidgetId === widgetId) {
            const newPane = panes[panes.length - 1];
            await openSettingsDrawer(widgetId, chartId, newPane.id);
        }
    } catch(e) {
        console.error(e);
        alert("Error adding pane: " + e.message);
    }
}

async function editPane(widgetId, paneId) {
    activePaneId = paneId;
    const el = document.querySelector(`[gs-id="${widgetId}"]`);
    if (!el) return;
    const chartId = el.getAttribute('data-chart-id');
    await openSettingsDrawer(widgetId, chartId, paneId);
}

async function deletePane(widgetId, paneId) {
    const el = document.querySelector(`[gs-id="${widgetId}"]`);
    if (!el) return;

    const chartId = el.getAttribute('data-chart-id');

    try {
        const chartConfig = await API.get(`/chart/${chartId}`);
        const panes = ensurePanes(chartConfig);
        const pane = panes.find(p => p.id === paneId);

        if (!pane || pane.kind === 'main') return;

        chartConfig.panes = panes.filter(p => p.id !== paneId);

        await API.put(`/chart/${chartId}`, {
            name: chartConfig.name,
            db_id: chartConfig.db_id,
            table_name: chartConfig.table_name,
            time_column: chartConfig.time_column,
            series: chartConfig.series || [],
            panes: chartConfig.panes
        });

        await refreshWidget(widgetId, chartId);

        if (activeWidgetId === widgetId) {
            await openSettingsDrawer(widgetId, chartId);
        }
    } catch (e) {
        console.error(e);
        alert("Error deleting pane: " + e.message);
    }
}

function normalizeSeries(values, nullMode) {
    let lastValid = null;
    return values.map(v => {
        // Robust check for missing values including string "null", "NaN", None, empty strings, and actual JS NaN
        const isMissing = v === null || v === undefined || v === 'null' || v === 'None' || v === 'NaN' || v === '' || (typeof v === 'number' && isNaN(v));
        
        if (isMissing) {
            if (nullMode === 'carry_forward') return lastValid;
            if (nullMode === 'zero_fill') return 0;
            return null; // For break mode
        }
        
        const numVal = Number(v);
        lastValid = numVal;
        return numVal;
    });
}

function normalizeMultiSeriesData(sData, nullMode, hasTime) {
    let lastValid = null;
    return sData.map((point, index) => {
        let time = hasTime ? point[0] : `Row ${index + 1}`;
        let v = hasTime ? point[1] : point[0];
        
        const isMissing = v === null || v === undefined || v === 'null' || v === 'None' || v === 'NaN' || v === '' || (typeof v === 'number' && isNaN(v));
        
        if (isMissing) {
            if (nullMode === 'carry_forward') v = lastValid;
            else if (nullMode === 'zero_fill') v = 0;
            else v = null;
        } else {
            v = Number(v);
            lastValid = v;
        }
        return hasTime ? [time, v] : v;
    });
}

function renderEChart(containerId, paneConfig, data, chartConfig, widgetId) {
    let paneKey = paneConfig.id || containerId;
    if (!activeCharts[widgetId]) {
        activeCharts[widgetId] = {};
    }
    
    let chart = activeCharts[widgetId][paneKey];
    if (!chart) {
        chart = echarts.init(document.getElementById(containerId));
        activeCharts[widgetId][paneKey] = chart;
    }
    
    const hasTime = !!chartConfig.time_column;
    let times = [];
    if (!data.multi_series && !hasTime) {
        times = (data.rows || []).map((r, i) => `Row ${i + 1}`);
    } else if (data.multi_series && !hasTime) {
        // Find longest series to generate times array
        let maxLen = 0;
        (data.series_data || []).forEach(sd => { if (sd.length > maxLen) maxLen = sd.length; });
        for (let i = 0; i < maxLen; i++) times.push(`Row ${i + 1}`);
    }
    
    const yAxes = [];
    const seriesData = (paneConfig.series || []).map((s, i) => {
        let sData;
        
        if (data.multi_series) {
            sData = normalizeMultiSeriesData((data.series_data || [])[i] || [], s.null_mode || 'carry_forward', hasTime);
        } else {
            const offset = hasTime ? 1 : 0;
            let rawData = (data.rows || []).map(r => r[i + offset]);
            rawData = normalizeSeries(rawData, s.null_mode || 'carry_forward');
            sData = hasTime ? (data.rows || []).map((r, idx) => [r[0], rawData[idx]]) : rawData;
        }
        
        const yAxisIndex = s.yAxisIndex || 0;
        const position = s.yAxisPosition || (yAxisIndex === 0 ? 'left' : 'right');
        
        if (!yAxes[yAxisIndex]) {
            yAxes[yAxisIndex] = {
                type: 'value',
                position: position,
                alignTicks: true,
                splitLine: { 
                    show: yAxisIndex === 0,
                    lineStyle: { color: '#2a2e39', type: 'dashed' }
                },
                axisLabel: { color: '#8b929e', fontSize: 11 }
            };
        }
        
        if (s.max_value !== null && s.max_value !== undefined && s.max_value !== '') {
            yAxes[yAxisIndex].max = s.max_value;
        }

        return {
            name: s.label || s.column,
            type: s.type || 'line',
            showSymbol: false,
            data: sData,
            yAxisIndex: yAxisIndex,
            itemStyle: s.color ? { color: s.color } : undefined,
            lineStyle: s.color ? { color: s.color, width: 2 } : { width: 2 },
            connectNulls: true, // We handle nulls explicitly, but allow connecting them
            step: s.null_mode === 'carry_forward' && s.type !== 'bar' ? 'end' : undefined // Optional: use step lines for carry forward to look exactly like trading platforms
        };
    });
    
    if (yAxes.length === 0) yAxes.push({ 
        type: 'value', position: 'left', 
        splitLine: { lineStyle: { color: '#2a2e39', type: 'dashed' } },
        axisLabel: { color: '#8b929e', fontSize: 11 }
    });

    const option = {
        backgroundColor: 'transparent',
        color: ['#2962ff', '#089981', '#f23645', '#e6a438', '#9c27b0', '#00bcd4', '#e91e63'], // beautiful default trading palette
        tooltip: { 
            trigger: 'axis',
            backgroundColor: '#1e222d',
            borderColor: '#363a45',
            textStyle: { color: '#d1d4dc', fontSize: 12 },
            axisPointer: { type: 'cross', lineStyle: { color: '#5d606b', type: 'dashed' } }
        },
        legend: { 
            top: 5, 
            left: 10,
            textStyle: { color: '#d1d4dc', fontSize: 12 },
            icon: 'circle'
        },
        grid: { left: '2%', right: '2%', bottom: '15%', top: '40px', containLabel: true },
        xAxis: {
            type: hasTime ? 'time' : 'category',
            data: hasTime ? undefined : times,
            splitLine: { show: false },
            axisLabel: { color: '#8b929e', fontSize: 11 },
            axisLine: { lineStyle: { color: '#363a45' } },
            axisTick: { lineStyle: { color: '#363a45' } }
        },
        yAxis: yAxes,
        dataZoom: [
            { type: 'inside', xAxisIndex: 0 },
            { 
                type: 'slider', 
                xAxisIndex: 0,
                height: 24,
                bottom: 5,
                borderColor: '#2a2e39',
                backgroundColor: '#131722',
                fillerColor: 'rgba(41, 98, 255, 0.15)',
                textStyle: { color: '#8b929e' },
                handleStyle: { color: '#8b929e', borderColor: '#8b929e' },
                dataBackground: {
                    lineStyle: { color: '#2a2e39', width: 1 },
                    areaStyle: { color: '#2a2e39', opacity: 0.2 }
                }
            }
        ],
        series: seriesData
    };
    
    chart.setOption(option, true);
}

async function removeWidget(btn) {
    if(!confirm('Remove this chart?')) return;
    const item = btn.closest('.grid-stack-item');
    const widgetId = item.getAttribute('gs-id');
    
    try {
        await API.delete(`/dashboards/${currentDashboardId}/widgets/${widgetId}`);
        grid.removeWidget(item);
        delete activeCharts[widgetId];
        
        if (activeWidgetId === widgetId) {
            closeSettingsDrawer();
            activeWidgetId = null;
        }
        updateSidebarList();
    } catch(e) {
        alert("Error removing widget: " + e.message);
    }
}

async function fetchSchemaForDb(dbId) {
    if (!dbId) return null;
    if (settingsDbSchemas[dbId]) return settingsDbSchemas[dbId];
    try {
        const schema = await API.get(`/schema/${dbId}`);
        settingsDbSchemas[dbId] = schema;
        return schema;
    } catch(e) {
        console.error(e);
        return null;
    }
}

async function loadSettingsTables() {
    const dbId = document.getElementById('settings-chart-db').value;
    const tableSel = document.getElementById('settings-chart-table');
    const timeSel = document.getElementById('settings-chart-time-col');
    
    tableSel.innerHTML = '<option value="">Select Table...</option>';
    timeSel.innerHTML = '<option value="">-- No X Axis (Index) --</option>';

    if (!dbId) return;
    
    const schema = await fetchSchemaForDb(dbId);
    if (schema) {
        Object.keys(schema).forEach(table => {
            tableSel.innerHTML += `<option value="${table}">${table}</option>`;
        });
    }
}

function loadSettingsColumns() {
    const table = document.getElementById('settings-chart-table').value;
    const dbId = document.getElementById('settings-chart-db').value;
    const timeSel = document.getElementById('settings-chart-time-col');
    
    timeSel.innerHTML = '<option value="">-- No X Axis (Index) --</option>';
    
    if (!table || !dbId || !settingsDbSchemas[dbId] || !settingsDbSchemas[dbId][table]) {
        return;
    }
    
    const cols = settingsDbSchemas[dbId][table];
    
    let timeHtml = '<option value="">-- No X Axis (Index) --</option>';
    
    cols.forEach(col => {
        timeHtml += `<option value="${col.column_name}">${col.column_name}</option>`;
    });
    
    timeSel.innerHTML = timeHtml;
}

async function handleSeriesDbChange(selectEl, seriesIndex) {
    const block = selectEl.closest('.series-setting-block');
    const dbId = selectEl.value;
    const tableSel = block.querySelector('.s-table');
    const colSel = block.querySelector('.s-col');
    
    tableSel.innerHTML = '<option value="">Select Table...</option>';
    colSel.innerHTML = '<option value="">Select Column...</option>';
    
    if (!dbId) {
        previewSettings();
        return;
    }
    
    const schema = await fetchSchemaForDb(dbId);
    if (schema) {
        Object.keys(schema).forEach(table => {
            tableSel.innerHTML += `<option value="${table}">${table}</option>`;
        });
    }
    previewSettings();
}

function handleSeriesTableChange(selectEl, seriesIndex) {
    const block = selectEl.closest('.series-setting-block');
    const dbId = block.querySelector('.s-db').value;
    const table = selectEl.value;
    const colSel = block.querySelector('.s-col');
    
    colSel.innerHTML = '<option value="">Select Column...</option>';
    
    if (!table || !dbId || !settingsDbSchemas[dbId] || !settingsDbSchemas[dbId][table]) {
        previewSettings();
        return;
    }
    
    const cols = settingsDbSchemas[dbId][table];
    cols.forEach(col => {
        colSel.innerHTML += `<option value="${col.column_name}">${col.column_name}</option>`;
    });
    previewSettings();
}

function renderSeriesSettingBlock(s, i) {
    let dbOptions = '<option value="">Inherit Chart DB</option>';
    allDatabases.forEach(db => {
        const selected = db.id == s.db_id ? 'selected' : '';
        dbOptions += `<option value="${db.id}" ${selected}>${db.name}</option>`;
    });

    let tableOptions = '<option value="">Inherit Chart Table</option>';
    let colOptions = '<option value="">Select Column...</option>';
    
    // We will populate tableOptions and colOptions dynamically after rendering
    // if db_id and table_name are set in the series.

    return `
        <div class="series-setting-block" data-index="${i}">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:var(--space-2);">
                <h4><span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:${s.color||'var(--accent)'};"></span> Series ${i+1}</h4>
                <button type="button" class="icon-btn" onclick="removeSeriesFromSettings(${i})" style="color:var(--danger);" title="Remove Series">✖</button>
            </div>
            <div class="series-setting-grid">
                <div class="form-group compact grid-col-full">
                    <label>Override Data Source (Optional)</label>
                    <select class="s-db" onchange="handleSeriesDbChange(this, ${i})">${dbOptions}</select>
                </div>
                <div class="form-group compact grid-col-full">
                    <label>Override Table (Optional)</label>
                    <select class="s-table" onchange="handleSeriesTableChange(this, ${i})">${tableOptions}</select>
                </div>
                <div class="form-group compact grid-col-full">
                    <label>Data Column</label>
                    <select class="s-col" required>${colOptions}</select>
                </div>
                <div class="form-group compact grid-col-full">
                    <label>Label</label>
                    <input type="text" class="s-label" value="${s.label || s.column || ''}">
                </div>
                <div class="form-group compact">
                    <label>Type</label>
                    <select class="s-type">
                        <option value="line" ${s.type === 'line' || !s.type ? 'selected' : ''}>Line</option>
                        <option value="bar" ${s.type === 'bar' ? 'selected' : ''}>Bar</option>
                        <option value="scatter" ${s.type === 'scatter' ? 'selected' : ''}>Scatter</option>
                    </select>
                </div>
                <div class="form-group compact">
                    <label>Color</label>
                    <div class="color-picker-wrap">
                        <input type="color" class="s-color" value="${s.color || '#2962ff'}">
                        <span style="font-size:12px;color:var(--text-secondary);">${s.color || '#2962ff'}</span>
                    </div>
                </div>
                <div class="form-group compact">
                    <label>Y-Axis</label>
                    <select class="s-yaxis">
                        <option value="0" ${(!s.yAxisIndex || s.yAxisIndex == 0) ? 'selected' : ''}>Left (Main)</option>
                        <option value="1" ${(s.yAxisIndex == 1) ? 'selected' : ''}>Right (Secondary)</option>
                    </select>
                </div>
                <div class="form-group compact">
                    <label>Null Mode</label>
                    <select class="s-null">
                        <option value="carry_forward" ${(!s.null_mode || s.null_mode === 'carry_forward') ? 'selected' : ''}>Carry Forward</option>
                        <option value="zero_fill" ${s.null_mode === 'zero_fill' ? 'selected' : ''}>Zero Fill</option>
                        <option value="break" ${s.null_mode === 'break' ? 'selected' : ''}>Break (Null)</option>
                    </select>
                </div>
                <div class="form-group compact grid-col-full">
                    <label>Y-Axis Ceiling (Max Value)</label>
                    <input type="number" class="s-max" placeholder="Auto" value="${s.max_value || ''}" step="any">
                </div>
            </div>
        </div>
    `;
}

function syncCurrentSettingsConfigFromDrawer() {
    if (!currentSettingsChartConfig || !activePaneId) return;

    const updatedConfig = collectSettingsFromDrawer();
    if (updatedConfig) {
        currentSettingsChartConfig = updatedConfig;
    }
}

function renderPaneList() {
    const paneList = document.getElementById('settings-pane-list');
    if (!paneList || !currentSettingsChartConfig) return;

    paneList.innerHTML = '';
    const panes = ensurePanes(currentSettingsChartConfig);

    panes.forEach(pane => {
        const item = document.createElement('div');
        const isActive = pane.id === activePaneId;
        item.className = 'pane-list-item' + (isActive ? ' active' : '');
        item.style.cssText = `display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border:1px solid ${isActive ? 'var(--accent)' : 'var(--border-subtle)'};border-radius:6px;margin-bottom:6px;cursor:pointer;background:${isActive ? 'rgba(41, 98, 255, 0.08)' : 'transparent'};`;
        item.innerHTML = `
            <span>${pane.name || 'Pane'}</span>
            <div style="display:flex;gap:6px;">
                <button type="button" class="icon-btn" onclick="switchPane('${pane.id}'); event.stopPropagation();" title="Edit Pane">⚙️</button>
                ${pane.kind !== 'main' ? `<button type="button" class="icon-btn" style="color:var(--danger);" onclick="deletePaneFromDrawer('${pane.id}'); event.stopPropagation();" title="Delete Pane">✖</button>` : ''}
            </div>
        `;
        item.onclick = () => switchPane(pane.id);
        paneList.appendChild(item);
    });
}

async function renderActivePaneSettings() {
    const activePane = getActivePane();
    const container = document.getElementById('settings-series-container');
    const paneNameInput = document.getElementById('settings-pane-name');
    const paneHeightInput = document.getElementById('settings-pane-height');

    if (!container) return;

    container.innerHTML = '';

    if (!activePane) {
        if (paneNameInput) paneNameInput.value = '';
        if (paneHeightInput) paneHeightInput.value = '';
        return;
    }

    if (paneNameInput) paneNameInput.value = activePane.name || '';
    if (paneHeightInput) paneHeightInput.value = activePane.height || 160;

    (activePane.series || []).forEach((s, i) => {
        container.innerHTML += renderSeriesSettingBlock(s, i);
    });

    const blocks = document.querySelectorAll('.series-setting-block');
    for (let i = 0; i < blocks.length; i++) {
        await populateSeriesDropdowns(blocks[i], activePane.series[i]);
    }
}

async function populateSeriesDropdowns(block, s) {
    const dbId = s.db_id;
    const tableName = s.table_name;
    const colName = s.column;
    
    const tableSel = block.querySelector('.s-table');
    const colSel = block.querySelector('.s-col');
    
    // Default fallback to inherit if not set
    let activeDbId = dbId || document.getElementById('settings-chart-db').value;
    let activeTable = tableName || document.getElementById('settings-chart-table').value;
    
    if (activeDbId) {
        const schema = await fetchSchemaForDb(activeDbId);
        if (schema) {
            tableSel.innerHTML = dbId ? '<option value="">Select Table...</option>' : '<option value="">Inherit Chart Table</option>';
            Object.keys(schema).forEach(t => {
                const selected = t === tableName ? 'selected' : '';
                tableSel.innerHTML += `<option value="${t}" ${selected}>${t}</option>`;
            });
            
            if (activeTable && schema[activeTable]) {
                const cols = schema[activeTable];
                colSel.innerHTML = '<option value="">Select Column...</option>';
                cols.forEach(c => {
                    const selected = c.column_name === colName ? 'selected' : '';
                    colSel.innerHTML += `<option value="${c.column_name}" ${selected}>${c.column_name}</option>`;
                });
            }
        }
    }
}

async function addSeriesToSettings() {
    const activePane = getActivePane();
    if (!activePane) return;

    const newSeries = {
        db_id: '',
        table_name: '',
        column: '',
        label: 'New Series',
        type: 'line',
        color: '#2962ff',
        yAxisIndex: 0,
        null_mode: 'carry_forward',
        max_value: ''
    };

    if (!activePane.series) activePane.series = [];
    activePane.series.push(newSeries);

    await switchPane(activePane.id, false);
    previewSettings();
}

async function removeSeriesFromSettings(index) {
    const activePane = getActivePane();
    if (!activePane || !activePane.series) return;

    activePane.series.splice(index, 1);
    await switchPane(activePane.id, false);
    previewSettings();
}

async function switchPane(paneId, syncCurrent = true) {
    if (!currentSettingsChartConfig) return;

    if (syncCurrent) {
        syncCurrentSettingsConfigFromDrawer();
    }
    activePaneId = paneId;
    renderPaneList();
    await renderActivePaneSettings();
}

async function deletePaneFromDrawer(paneId) {
    if (!currentSettingsChartConfig) return;

    syncCurrentSettingsConfigFromDrawer();
    const panes = ensurePanes(currentSettingsChartConfig);
    const pane = panes.find(p => p.id === paneId);

    if (!pane || pane.kind === 'main') return;

    currentSettingsChartConfig.panes = panes.filter(p => p.id !== paneId);
    activePaneId = currentSettingsChartConfig.panes[0] ? currentSettingsChartConfig.panes[0].id : null;

    renderPaneList();
    await renderActivePaneSettings();
    previewSettings();
}

async function openSettingsDrawer(widgetId, chartId, paneId = null) {
    if (activeWidgetId !== widgetId) {
        activeWidgetId = widgetId;
        document.querySelectorAll('.grid-stack-item').forEach(item => {
            item.classList.toggle('active', item.getAttribute('gs-id') === widgetId);
        });
        updateSidebarList();
    }

    document.getElementById('settings-drawer').classList.remove('closed');
    document.getElementById('drawer-empty-state').style.display = 'none';
    document.getElementById('settings-form').style.display = 'block';
    
    document.getElementById('settings-widget-id').value = widgetId;
    document.getElementById('settings-chart-id').value = chartId;
    
    try {
        await refreshAllDatabases(true);
        currentSettingsChartConfig = await API.get(`/chart/${chartId}`);
        currentSettingsChartConfig.panes = ensurePanes(currentSettingsChartConfig);

        if (!paneId) {
            activePaneId = currentSettingsChartConfig.panes[0].id;
        } else {
            activePaneId = paneId;
        }
        
        document.getElementById('settings-chart-name').value = currentSettingsChartConfig.name;
        
        // Load DBs and select
        const dbSel = document.getElementById('settings-chart-db');
        populateDatabaseSelect(dbSel, allDatabases);
        dbSel.value = currentSettingsChartConfig.db_id;

        // Load Tables
        if (currentSettingsChartConfig.db_id) {
            const schema = await fetchSchemaForDb(currentSettingsChartConfig.db_id);
            if (schema) {
                const tableSel = document.getElementById('settings-chart-table');
                tableSel.innerHTML = '<option value="">Select Table...</option>';
                Object.keys(schema).forEach(table => {
                    tableSel.innerHTML += `<option value="${table}">${table}</option>`;
                });
                tableSel.value = currentSettingsChartConfig.table_name;
            }
        }

        // Load Columns
        if (currentSettingsChartConfig.table_name && settingsDbSchemas[currentSettingsChartConfig.db_id] && settingsDbSchemas[currentSettingsChartConfig.db_id][currentSettingsChartConfig.table_name]) {
            const cols = settingsDbSchemas[currentSettingsChartConfig.db_id][currentSettingsChartConfig.table_name];
            const timeSel = document.getElementById('settings-chart-time-col');
            timeSel.innerHTML = '<option value="">-- No X Axis (Index) --</option>';
            cols.forEach(col => {
                timeSel.innerHTML += `<option value="${col.column_name}">${col.column_name}</option>`;
            });
            timeSel.value = currentSettingsChartConfig.time_column || "";
        }

        renderPaneList();
        await renderActivePaneSettings();
    } catch (e) {
        console.error('Failed to load chart settings:', e);
    }
}

function closeSettingsDrawer() {
    document.getElementById('settings-drawer').classList.add('closed');
    document.getElementById('drawer-empty-state').style.display = 'block';
    document.getElementById('settings-form').style.display = 'none';
    currentSettingsChartConfig = null;
    activePaneId = null;
    activeWidgetId = null;
    document.querySelectorAll('.grid-stack-item').forEach(item => item.classList.remove('active'));
    updateSidebarList();
}

function collectSettingsFromDrawer() {
    if (!currentSettingsChartConfig) return null;
    
    const config = JSON.parse(JSON.stringify(currentSettingsChartConfig)); // deep clone
    config.name = document.getElementById('settings-chart-name').value;
    config.db_id = document.getElementById('settings-chart-db').value;
    config.table_name = document.getElementById('settings-chart-table').value;
    config.time_column = document.getElementById('settings-chart-time-col').value;

    const activePane = config.panes.find(p => p.id === activePaneId);
    if (!activePane) return config;

    const paneNameInput = document.getElementById('settings-pane-name');
    const paneHeightInput = document.getElementById('settings-pane-height');

    if (paneNameInput) activePane.name = paneNameInput.value;
    if (paneHeightInput) activePane.height = Number(paneHeightInput.value || 160);

    const blocks = document.querySelectorAll('.series-setting-block');
    activePane.series = [];
    config.series = [];

    blocks.forEach((b) => {
        const column = b.querySelector('.s-col').value;
        const colorVal = b.querySelector('.s-color').value;
        const maxVal = b.querySelector('.s-max').value;
        
        b.querySelector('.color-picker-wrap span').innerText = colorVal;
        b.querySelector('h4 span').style.background = colorVal;
        
        activePane.series.push({
            db_id: b.querySelector('.s-db').value || null,
            table_name: b.querySelector('.s-table').value || null,
            column: column,
            label: b.querySelector('.s-label').value,
            type: b.querySelector('.s-type').value,
            color: colorVal,
            yAxisIndex: parseInt(b.querySelector('.s-yaxis').value),
            yAxisPosition: parseInt(b.querySelector('.s-yaxis').value) === 0 ? 'left' : 'right',
            null_mode: b.querySelector('.s-null').value,
            max_value: maxVal !== '' ? Number(maxVal) : null
        });
    });
    
    return config;
}

async function previewSettings() {
    if (!currentSettingsChartConfig || !activeWidgetId) return;
    
    const newConfig = collectSettingsFromDrawer();
    if (!newConfig) return;
    
    const el = document.querySelector(`[gs-id="${activeWidgetId}"]`);
    if (el) {
        const workspaceBody = el.querySelector('.workspace-body');
        const containerId = workspaceBody ? workspaceBody.id : el.querySelector('.chart-body').id;

        // Re-render whole widget with custom config
        await loadAndRenderChart(activeWidgetId, null, el, containerId, newConfig);
    }
}

async function saveSettings(e) {
    if (e) e.preventDefault();
    if (!currentSettingsChartConfig) return;
    
    const chartId = document.getElementById('settings-chart-id').value;
    const newConfig = collectSettingsFromDrawer();
    
    try {
        await API.put(`/chart/${chartId}`, {
            name: newConfig.name,
            db_id: newConfig.db_id,
            table_name: newConfig.table_name,
            time_column: newConfig.time_column,
            series: [],
            panes: newConfig.panes
        });
        currentSettingsChartConfig = newConfig;
        renderPaneList();
        updateSidebarList(); // Title might have changed
        // Show success indicator
        const btn = document.querySelector('#settings-form button[type="submit"]');
        const origText = btn.innerText;
        btn.innerText = 'Saved!';
        btn.style.backgroundColor = 'var(--accent-yes)';
        setTimeout(() => {
            btn.innerText = origText;
            btn.style.backgroundColor = '';
        }, 1500);
    } catch (err) {
        alert("Error saving settings: " + err.message);
    }
}

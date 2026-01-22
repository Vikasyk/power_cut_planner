// API Configuration
const API_BASE_URL = 'http://localhost:5000/api';

// Global state
let state = {
    feeders: {},
    areas: {},
    schedule: [],
    maintenance: []
};

let chart = null;
let networkGraph = null;

// Test API connection on load
async function testAPIConnection() {
    try {
        const response = await fetch('http://localhost:5000/api/health', {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        if (response.ok) {
            console.log('✓ API Connection OK');
            return true;
        }
    } catch (e) {
        console.error('✗ API Connection Failed:', e);
        showNotification('⚠️ Warning: Backend API not responding', 'warning');
        return false;
    }
}

// ========== TAB NAVIGATION ==========
function switchTab(tabName) {
    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(tab => {
        tab.classList.remove('active');
    });

    // Remove active from all nav buttons
    document.querySelectorAll('.nav-btn').forEach(btn => {
        btn.classList.remove('active');
    });

    // Show selected tab
    document.getElementById(tabName).classList.add('active');

    // Add active to clicked button
    event.target.classList.add('active');

    // Load data for specific tabs
    if (tabName === 'dashboard') {
        loadDashboard();
    } else if (tabName === 'feeders') {
        loadFeeders();
    } else if (tabName === 'areas') {
        loadAreas();
    } else if (tabName === 'network') {
        setTimeout(generateNetworkGraph, 300);
    } else if (tabName === 'maintenance') {
        loadAreas().then(() => {
            populateMaintenanceAreaSelect();
            loadMaintenance();
        });
    }
}

// ========== DASHBOARD ==========
async function loadDashboard() {
    try {
        const response = await fetch(`${API_BASE_URL}/dashboard`);
        const text = await response.text();

        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('Failed to parse dashboard response:', e);
            return;
        }

        // Update system status
        document.getElementById('totalDemand').textContent = `${data.total_demand} kW`;
        document.getElementById('availablePower').textContent = `${data.available_power} kW`;
        document.getElementById('currentHour').textContent = `${data.current_hour}:00`;

        // Update substations
        let substationHTML = '';
        for (let subId in data.substations) {
            substationHTML += `
                <div class="list-item">
                    <span>${data.substations[subId].name}</span>
                    <span class="badge">#${subId}</span>
                </div>
            `;
        }
        document.getElementById('substationList').innerHTML = substationHTML || '<p>No substations</p>';

        // Update priority distribution
        let priorityHTML = '';
        const priorities = ['P1 (Critical)', 'P2 (High)', 'P3 (Medium)', 'P4 (Low)'];
        const priorityClasses = ['p1', 'p2', 'p3', 'p4'];

        data.priority_areas.forEach((count, idx) => {
            priorityHTML += `
                <div class="priority-item ${priorityClasses[idx]}">
                    <strong>${priorities[idx]}</strong>: ${count} areas
                </div>
            `;
        });
        document.getElementById('priorityChart').innerHTML = priorityHTML;

    } catch (error) {
        console.error('Error loading dashboard:', error);
    }
}

function updateLoadChart(hourlyDemand) {
    const ctx = document.getElementById('loadChart').getContext('2d');

    if (chart) {
        chart.destroy();
    }

    chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: Array.from({ length: 24 }, (_, i) => `${i}:00`),
            datasets: [
                {
                    label: 'Load Demand (kW)',
                    data: hourlyDemand,
                    borderColor: '#2196F3',
                    backgroundColor: 'rgba(33, 150, 243, 0.1)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: '#2196F3'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    display: true,
                    position: 'top'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'Power (kW)'
                    }
                }
            }
        }
    });
}

// ========== FEEDERS MANAGEMENT ==========
async function addFeeder() {
    const feederName = document.getElementById('feederName').value.trim();

    if (!feederName) {
        showNotification('Please enter feeder name', 'warning');
        return;
    }

    try {
        console.log('Adding feeder:', feederName);
        console.log('API URL:', `${API_BASE_URL}/feeders`);

        const response = await fetch(`${API_BASE_URL}/feeders`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                name: feederName,
                capacity_kw: 1000
            })
        });

        console.log('Response status:', response.status);
        console.log('Response OK:', response.ok);
        console.log('Content-Type:', response.headers.get('content-type'));

        const text = await response.text();
        console.log('Raw response text:', text);
        console.log('Text length:', text.length);

        if (!text || text.length === 0) {
            showNotification('Error: Empty response from server', 'error');
            return;
        }

        let data;
        try {
            data = JSON.parse(text);
            console.log('Parsed data:', data);
        } catch (e) {
            console.error('JSON parse error:', e);
            console.error('Failed to parse:', text.substring(0, 200));
            showNotification('Error: Server returned invalid response - ' + text.substring(0, 50), 'error');
            return;
        }

        if (response.ok) {
            showNotification('✓ Feeder added successfully!', 'success');
            document.getElementById('feederName').value = '';
            await loadFeeders();
            await loadAreaFeederDropdown();
        } else {
            showNotification('Error: ' + (data.error || 'Failed to add feeder'), 'error');
        }
    } catch (error) {
        console.error('Error adding feeder:', error);
        showNotification('Error: ' + error.message, 'error');
    }
}

async function loadFeeders() {
    try {
        const response = await fetch(`${API_BASE_URL}/feeders`);
        const text = await response.text();

        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('Failed to parse feeders response:', e);
            document.getElementById('feedersList').innerHTML = '<p>Error loading feeders</p>';
            return;
        }

        state.feeders = data.feeders;

        let html = '';
        for (let feederId in data.feeders) {
            const feeder = data.feeders[feederId];
            const areaCount = data.areas_per_feeder[feederId] || 0;
            const totalLoad = data.load_per_feeder[feederId] || 0;

            html += `
                <div class="feeder-card">
                    <h4>📌 ${feeder.name}</h4>
                    <div class="feeder-card-info">
                        <span>
                            <span class="label">Capacity</span>
                            <span class="value">${feeder.capacity_kw} kW</span>
                        </span>
                        <span>
                            <span class="label">Current Load</span>
                            <span class="value">${totalLoad.toFixed(1)} kW</span>
                        </span>
                        <span>
                            <span class="label">Areas</span>
                            <span class="value">${areaCount}</span>
                        </span>
                    </div>
                    <div class="feeder-card-actions">
                        <button class="btn btn-danger btn-small" onclick="deleteFeeder(${feederId})">Delete</button>
                    </div>
                </div>
            `;
        }

        document.getElementById('feedersList').innerHTML = html || '<p>No feeders added yet. Create one to get started!</p>';

    } catch (error) {
        console.error('Error loading feeders:', error);
        document.getElementById('feedersList').innerHTML = '<p>Error: Cannot connect to server</p>';
    }
}

async function loadAreaFeederDropdown() {
    try {
        const response = await fetch(`${API_BASE_URL}/feeders`);
        const text = await response.text();

        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('Failed to parse dropdown response:', e);
            document.getElementById('areaFeeder').innerHTML = '<option value="">Error loading feeders</option>';
            return;
        }

        let html = '<option value="">-- Select a Feeder --</option>';
        for (let feederId in data.feeders) {
            html += `<option value="${feederId}">${data.feeders[feederId].name}</option>`;
        }

        document.getElementById('areaFeeder').innerHTML = html;

    } catch (error) {
        console.error('Error loading feeder dropdown:', error);
        document.getElementById('areaFeeder').innerHTML = '<option value="">Error loading feeders</option>';
    }
}

async function deleteFeeder(feederId) {
    if (!confirm('Delete this feeder and all its areas?')) return;

    try {
        const response = await fetch(`${API_BASE_URL}/feeders/${feederId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            showNotification('Feeder deleted', 'success');
            loadFeeders();
            loadAreaFeederDropdown();
            loadAreas();
        }
    } catch (error) {
        console.error('Error deleting feeder:', error);
        showNotification('Error deleting feeder', 'error');
    }
}

// ========== AREAS MANAGEMENT ==========
async function addArea() {
    const feederId = document.getElementById('areaFeeder').value;
    const areaName = document.getElementById('areaName').value.trim();

    if (!feederId) {
        showNotification('Please select a Feeder first', 'warning');
        return;
    }

    if (!areaName) {
        showNotification('Please enter Area name', 'warning');
        return;
    }

    const area = {
        feeder_id: parseInt(feederId),
        name: areaName,
        load_kw: parseFloat(document.getElementById('areaLoad').value) || 0,
        population: parseInt(document.getElementById('areaPopulation').value) || 0,
        hospitals: parseInt(document.getElementById('areaHospitals').value) || 0,
        emergency_services: parseInt(document.getElementById('areaEmergency').value) || 0,
        research_centers: parseInt(document.getElementById('areaResearch').value) || 0,
        schools: parseInt(document.getElementById('areaSchools').value) || 0
    };

    try {
        const response = await fetch(`${API_BASE_URL}/areas`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(area)
        });

        if (response.ok) {
            const result = await response.json();
            showNotification('Area added successfully!', 'success');

            // Clear form
            document.getElementById('areaName').value = '';
            document.getElementById('areaLoad').value = '';
            document.getElementById('areaPopulation').value = '';
            document.getElementById('areaHospitals').value = '';
            document.getElementById('areaEmergency').value = '';
            document.getElementById('areaResearch').value = '';
            document.getElementById('areaSchools').value = '';
            document.getElementById('areaFeeder').value = '';

            loadAreas();
            loadFeeders();
        }
    } catch (error) {
        console.error('Error adding area:', error);
        showNotification('Error adding area', 'error');
    }
}

async function loadAreas() {
    try {
        const response = await fetch(`${API_BASE_URL}/areas`);
        const text = await response.text();

        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('Failed to parse areas response:', e);
            document.getElementById('areasTableBody').innerHTML = '<tr><td colspan="7">Error loading areas</td></tr>';
            return;
        }

        state.areas = data.areas;

        let html = '';
        for (let areaId in data.areas) {
            const area = data.areas[areaId];
            const feederName = data.feeder_names[area.feeder_id] || 'Unknown';
            html += `
                <tr>
                    <td>${areaId}</td>
                    <td>${area.name}</td>
                    <td>${feederName}</td>
                    <td>${area.load_kw}</td>
                    <td>Priority ${area.priority}</td>
                    <td>${area.population}</td>
                    <td>
                        <button onclick="deleteArea(${areaId})" class="btn btn-danger btn-small">Delete</button>
                    </td>
                </tr>
            `;
        }

        document.getElementById('areasTableBody').innerHTML = html || '<tr><td colspan="7">No areas</td></tr>';

        // Update schedule info
        updateScheduleInfo();

    } catch (error) {
        console.error('Error loading areas:', error);
        document.getElementById('areasTableBody').innerHTML = '<tr><td colspan="7">Error loading areas</td></tr>';
    }
}

async function deleteArea(areaId) {
    if (!confirm('Delete this area?')) return;

    try {
        const response = await fetch(`${API_BASE_URL}/areas/${areaId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            showNotification('Area deleted', 'success');
            loadAreas();
            updateScheduleInfo();
        }
    } catch (error) {
        console.error('Error deleting area:', error);
        showNotification('Error deleting area', 'error');
    }
}

function updateScheduleInfo() {
    // Calculate total demand if all areas are ON
    let totalDemand = 0;
    for (let areaId in state.areas) {
        totalDemand += state.areas[areaId].load_kw || 0;
    }

    // Calculate daily energy (24 hours)
    const dailyEnergy = totalDemand * 24;

    // Update display
    document.getElementById('hourlyDemandInfo').textContent =
        `Hourly demand = ${totalDemand.toFixed(1)} kW`;
    document.getElementById('dailyEnergyInfo').textContent =
        `Daily energy needed = ${dailyEnergy.toFixed(1)} kWh`;
}

// ========== SCHEDULE GENERATION ==========
async function generateSchedule() {
    const availablePower = parseFloat(document.getElementById('availablePowerInput').value) || 0;

    try {
        const response = await fetch(`${API_BASE_URL}/schedule/generate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                available_power: availablePower
            })
        });

        const data = await response.json();
        state.schedule = data.schedule;

        displaySchedule();
        showNotification('Schedule generated successfully!', 'success');

    } catch (error) {
        console.error('Error generating schedule:', error);
        showNotification('Error generating schedule', 'error');
    }
}

function displaySchedule() {
    // If no schedule, show empty message
    if (!state.schedule || state.schedule.length === 0) {
        document.getElementById('scheduleGrid').innerHTML = '<p style="padding: 20px; text-align: center; color: #999;">No schedule generated yet.</p>';
        return;
    }

    // Create table format similar to Streamlit
    let html = `
        <table class="schedule-table">
            <thead>
                <tr>
                    <th>Hour</th>
                    <th>Start Time</th>
                    <th>End Time</th>
                    <th>Area ID</th>
                    <th>Area Name</th>
                    <th>Feeder Name</th>
                    <th>Priority</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
    `;

    // Group by hour and show all cut areas for that hour
    let currentHour = -1;
    let hourHasData = false;

    state.schedule.forEach((schedule_item, idx) => {
        const hour = schedule_item.hour || idx;

        // If this is a cut hour with affected areas
        if (schedule_item.areas && schedule_item.areas.length > 0) {
            schedule_item.areas.forEach(area_id => {
                // Get area from loaded areas data
                const area = state.areas[area_id];
                const areaName = area ? area.name : `Area ${area_id}`;
                const feederName = area && state.feeders[area.feeder_id] ? state.feeders[area.feeder_id].name : 'Unknown';
                const priority = area ? `P${area.priority}` : 'P?';
                const startTime = schedule_item.start_time;
                const endTime = schedule_item.end_time;

                html += `
                    <tr class="cut-row">
                        <td>${hour}</td>
                        <td>${startTime}</td>
                        <td>${endTime}</td>
                        <td>${area_id}</td>
                        <td><strong>${areaName}</strong></td>
                        <td>${feederName}</td>
                        <td>${priority}</td>
                        <td><span class="badge badge-cut">⚠️ CUT</span></td>
                    </tr>
                `;
            });
        }
    });

    html += `
            </tbody>
        </table>
    `;

    document.getElementById('scheduleGrid').innerHTML = html;
}

// ========== NETWORK GRAPH ==========
async function generateNetworkGraph() {
    try {
        const response = await fetch(`${API_BASE_URL}/network/graph`);
        const data = await response.json();

        const canvas = document.getElementById('networkCanvas');
        if (!canvas) return;

        const ctx = canvas.getContext('2d');
        ctx.canvas.width = ctx.canvas.offsetWidth;
        ctx.canvas.height = ctx.canvas.offsetHeight;

        const width = ctx.canvas.width;
        const height = ctx.canvas.height;

        // Clear canvas
        ctx.fillStyle = '#f5f5f5';
        ctx.fillRect(0, 0, width, height);

        const feeders = data.feeders;
        const areas = data.areas;

        // Plant
        const plantX = width / 2;
        const plantY = 40;
        ctx.fillStyle = '#1976D2';
        ctx.fillRect(plantX - 30, plantY - 20, 60, 40);
        ctx.fillStyle = '#fff';
        ctx.font = '14px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText('PLANT', plantX, plantY);

        // Feeders
        const feedersArray = Object.entries(feeders);
        const feederCount = feedersArray.length;
        const feederY = 120;
        const feederSpacing = width / (feederCount + 1);
        const feederPositions = {};

        feedersArray.forEach((entry, idx) => {
            const feederX = feederSpacing * (idx + 1);
            feederPositions[entry[0]] = { x: feederX, y: feederY };

            // Connection Plant to Feeder
            ctx.strokeStyle = '#999';
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(plantX, plantY + 20);
            ctx.lineTo(feederX, feederY - 15);
            ctx.stroke();

            // Feeder box
            ctx.fillStyle = '#64B5F6';
            ctx.fillRect(feederX - 35, feederY - 15, 70, 30);
            ctx.fillStyle = '#fff';
            ctx.font = '10px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(entry[1].name, feederX, feederY);
        });

        // Areas
        const areaY = height - 60;
        const areasArray = Object.entries(areas);
        const areaSpacing = width / (areasArray.length + 1);

        areasArray.forEach((entry, idx) => {
            const area = entry[1];
            const areaX = areaSpacing * (idx + 1);

            // Connection Feeder to Area
            const feederPos = feederPositions[area.feeder_id];
            ctx.strokeStyle = '#ccc';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(feederPos.x, feederPos.y + 15);
            ctx.lineTo(areaX, areaY - 30);
            ctx.stroke();

            // Priority color
            const colors = { 1: '#F44336', 2: '#FF9800', 3: '#2196F3', 4: '#4CAF50' };
            ctx.fillStyle = colors[area.priority] || '#999';
            ctx.fillRect(areaX - 40, areaY - 30, 80, 60);

            // Label
            ctx.fillStyle = '#fff';
            ctx.font = '9px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(area.name, areaX, areaY - 12);
            ctx.font = '8px Arial';
            ctx.fillText('P' + area.priority, areaX, areaY + 2);
            ctx.fillText(area.load_kw + ' kW', areaX, areaY + 12);
        });

    } catch (error) {
        console.error('Error generating network graph:', error);
        showNotification('Error loading network graph', 'error');
    }
}

// ========== MAINTENANCE ==========
async function addMaintenance() {
    const areaId = parseInt(document.getElementById('maintenanceArea').value);
    const issue = document.getElementById('maintenanceIssue').value.trim();

    if (!areaId || areaId === 0) {
        showNotification('Please select an area', 'error');
        return;
    }

    if (!issue) {
        showNotification('Please enter an issue description', 'error');
        return;
    }

    const task = {
        area_id: areaId,
        issue: issue
    };

    try {
        const response = await fetch(`${API_BASE_URL}/maintenance`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(task)
        });

        if (response.ok) {
            document.getElementById('maintenanceArea').value = '';
            document.getElementById('maintenanceIssue').value = '';
            showNotification('Maintenance task added', 'success');
            loadMaintenance();
        } else {
            const error = await response.json();
            showNotification(error.error || 'Error adding task', 'error');
        }
    } catch (error) {
        console.error('Error adding maintenance:', error);
        showNotification('Error adding maintenance task', 'error');
    }
}

async function loadMaintenance() {
    try {
        await loadAreas();  // Ensure areas are loaded first

        const response = await fetch(`${API_BASE_URL}/maintenance`);
        const data = await response.json();

        state.maintenance = data.queue;

        let html = '';

        if (!data.queue || data.queue.length === 0) {
            html = '<p>No maintenance tasks</p>';
        } else {
            data.queue.forEach((task, idx) => {
                const priorityColor = task.area_priority === 1 ? '#D32F2F' :
                    task.area_priority === 2 ? '#F57C00' :
                        task.area_priority === 3 ? '#FBC02D' : '#4CAF50';

                const priorityLabel = `P${task.area_priority}`;
                const resolvedClass = task.resolved ? 'maintenance-resolved' : '';

                html += `
                    <div class="maintenance-item ${resolvedClass}" style="border-left: 5px solid ${priorityColor}">
                        <div class="maintenance-content">
                            <div class="maintenance-header">
                                <strong>${task.area_name}</strong>
                                <span class="priority-badge" style="background-color: ${priorityColor};">${priorityLabel}</span>
                            </div>
                            <p class="maintenance-issue">${task.issue}</p>
                            <p class="maintenance-time">${new Date(task.timestamp).toLocaleString()}</p>
                        </div>
                        <div class="maintenance-actions">
                            <label class="checkbox-container">
                                <input type="checkbox" ${task.resolved ? 'checked' : ''} onchange="resolveMaintenance(${task.id})">
                                <span class="checkbox-label">Resolved</span>
                            </label>
                        </div>
                    </div>
                `;
            });
        }

        document.getElementById('maintenanceQueue').innerHTML = html;

    } catch (error) {
        console.error('Error loading maintenance:', error);
    }
}

async function resolveMaintenance(taskId) {
    try {
        const response = await fetch(`${API_BASE_URL}/maintenance/${taskId}/resolve`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            showNotification('Task marked as resolved', 'success');
            loadMaintenance();
        }
    } catch (error) {
        console.error('Error resolving task:', error);
        showNotification('Error updating task', 'error');
    }
}

function populateMaintenanceAreaSelect() {
    const select = document.getElementById('maintenanceArea');
    select.innerHTML = '<option value="">-- Select an Area --</option>';

    // Sort areas by priority (P1 first)
    const sortedAreas = Object.values(state.areas).sort((a, b) => a.priority - b.priority);

    sortedAreas.forEach(area => {
        const option = document.createElement('option');
        option.value = area.id;
        option.textContent = `${area.name} (P${area.priority}) - ${area.load_kw} kW`;
        select.appendChild(option);
    });
}

// ========== UTILITIES ==========
function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    const bgColor = type === 'success' ? '#4CAF50' :
        type === 'error' ? '#F44336' :
            type === 'warning' ? '#FF9800' : '#2196F3';

    notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        background: ${bgColor};
        color: white;
        border-radius: 6px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        z-index: 1000;
        animation: slideIn 0.3s ease;
    `;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}

// ========== INITIALIZATION ==========
document.addEventListener('DOMContentLoaded', async () => {
    // Test API connection first
    await testAPIConnection();

    // Update time
    setInterval(() => {
        const now = new Date();
        const hour = String(now.getHours()).padStart(2, '0');
        const minute = String(now.getMinutes()).padStart(2, '0');
        const timeElement = document.getElementById('currentHour');
        if (timeElement) {
            timeElement.textContent = `${hour}:${minute}`;
        }
    }, 1000);

    // Initial load
    loadDashboard();
    loadFeeders();
    loadAreaFeederDropdown();
    loadAreas();
});

// Add animation styles
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(400px);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes slideOut {
        from {
            transform: translateX(0);
            opacity: 1;
        }
        to {
            transform: translateX(400px);
            opacity: 0;
        }
    }
    
    .badge {
        background: #2196F3;
        color: white;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.85rem;
        font-weight: 600;
    }
`;
document.head.appendChild(style);

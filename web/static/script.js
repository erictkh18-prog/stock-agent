// Use same-origin in production and localhost only for local file testing.
const API_URL = window.location.origin.startsWith('http')
    ? window.location.origin
    : 'http://localhost:8000';

let scoreChart = null;

function setButtonState(buttonEl, isBusy, busyText, idleText) {
    if (!buttonEl) return;
    buttonEl.disabled = isBusy;
    buttonEl.textContent = isBusy ? busyText : idleText;
}

async function parseApiError(response, fallbackMessage) {
    try {
        const payload = await response.json();
        return payload?.detail || fallbackMessage;
    } catch (_) {
        return fallbackMessage;
    }
}

// ============ ANALYZE SINGLE STOCK ============
async function analyzeStock(buttonEl = null) {
    const symbol = document.getElementById('singleSymbol').value.trim().toUpperCase();
    if (!symbol) {
        alert('Please enter a stock symbol');
        return;
    }

    const btn = buttonEl || document.getElementById('analyzeBtn');

    try {
        setButtonState(btn, true, 'Analyzing...', 'Analyze');

        const response = await fetch(`${API_URL}/analyze/${symbol}`);
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Stock not found');
            throw new Error(errorMessage);
        }
        
        const data = await response.json();
        displaySingleResult(data);
        
        setButtonState(btn, false, 'Analyzing...', 'Analyze');
    } catch (error) {
        alert(`Error: ${error.message}`);
        setButtonState(btn, false, 'Analyzing...', 'Analyze');
    }
}

function displaySingleResult(data) {
    const resultDiv = document.getElementById('singleResult');
    
    // Update header
    document.getElementById('resultSymbol').textContent = `${data.symbol} - ${data.name}`;
    document.getElementById('resultPrice').textContent = `$${data.current_price.toFixed(2)}`;
    
    // Recommendation status
    const statusEl = document.getElementById('resultStatus');
    statusEl.textContent = data.recommendation;
    statusEl.className = `status ${data.recommendation.toLowerCase()}`;
    
    // Metrics
    document.getElementById('metricPE').textContent = data.fundamental?.pe_ratio || '—';
    document.getElementById('metricEPS').textContent = data.fundamental?.eps || '—';
    document.getElementById('metricDividend').textContent = data.fundamental?.dividend_yield 
        ? `${(data.fundamental.dividend_yield * 100).toFixed(2)}%` 
        : '—';
    document.getElementById('metricTrend').textContent = data.technical?.trend || '—';
    
    // Score bars
    if (data.fundamental) {
        updateScoreBar('fundamental', data.fundamental.score);
    }
    if (data.technical) {
        updateScoreBar('technical', data.technical.score);
    }
    if (data.sentiment) {
        updateScoreBar('sentiment', data.sentiment.score);
    }
    
    // Gauge chart
    drawGaugeChart(data.overall_score);
    
    // Recommendation box
    const recBox = document.getElementById('recommendationBox');
    const recommendation = data.recommendation.toLowerCase();
    const confidence = (data.confidence * 100).toFixed(0);
    recBox.className = `recommendation ${recommendation}`;
    recBox.innerHTML = `<span style="font-size: 1.5em; margin-right: 10px;">
        ${recommendation === 'buy' ? '✅' : recommendation === 'hold' ? '⏸️' : '❌'}
    </span>
    ${data.recommendation} | Confidence: ${confidence}%`;
    
    resultDiv.classList.remove('hidden');
}

function updateScoreBar(type, score) {
    const bar = document.getElementById(`${type}Bar`);
    const scoreEl = document.getElementById(`${type}Score`);
    bar.style.width = `${Math.min(score, 100)}%`;
    scoreEl.textContent = `${score.toFixed(1)}/100`;
}

function drawGaugeChart(score) {
    const ctx = document.getElementById('scoreChart');
    
    if (scoreChart) {
        scoreChart.destroy();
    }
    
    // Determine color
    let color;
    if (score >= 70) color = '#28a745';
    else if (score >= 50) color = '#ffc107';
    else color = '#dc3545';
    
    scoreChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            datasets: [{
                data: [score, 100 - score],
                backgroundColor: [color, '#e0e0e0'],
                borderColor: ['white', 'white'],
                borderWidth: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: { display: false },
                tooltip: { enabled: false }
            }
        },
        plugins: [{
            id: 'textCenter',
            beforeDatasetsDraw(chart) {
                const { width, height, ctx } = chart;
                ctx.restore();
                const fontSize = (height / 200).toFixed(2);
                ctx.font = `bold ${fontSize}em sans-serif`;
                ctx.textBaseline = 'middle';
                ctx.fillStyle = '#333';
                const text = `${score.toFixed(1)}`;
                const textX = Math.round((width - ctx.measureText(text).width) / 2);
                const textY = height / 2;
                ctx.fillText(text, textX, textY);
                ctx.save();
            }
        }]
    });
}

// ============ SCREEN MULTIPLE STOCKS ============
async function screenStocks(buttonEl = null) {
    const input = document.getElementById('multipleSymbols').value.trim();
    if (!input) {
        alert('Please enter stock symbols');
        return;
    }

    const symbols = [...new Set(input.split(',').map(s => s.trim().toUpperCase()).filter(Boolean))];
    if (!symbols.length) {
        alert('Please enter at least one valid stock symbol');
        return;
    }

    const btn = buttonEl || document.getElementById('screenBtn');

    try {
        setButtonState(btn, true, 'Screening...', 'Screen');

        const params = new URLSearchParams();
        symbols.forEach(sym => params.append('symbols', sym));
        params.append('top_n', '20');

        const response = await fetch(`${API_URL}/screen?${params}`);
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Screening failed');
            throw new Error(errorMessage);
        }
        
        const data = await response.json();
        displayScreenResults(data?.results || data, 'screenerTable', 'screenResult');
        
        setButtonState(btn, false, 'Screening...', 'Screen');
    } catch (error) {
        alert(`Error: ${error.message}`);
        setButtonState(btn, false, 'Screening...', 'Screen');
    }
}

function displayScreenResults(results, tableContainerId = 'screenerTable', resultContainerId = 'screenResult') {
    const resultDiv = document.getElementById(resultContainerId);
    const tableDiv = document.getElementById(tableContainerId);

    if (!Array.isArray(results) || !results.length) {
        tableDiv.innerHTML = '<p>No results found for the selected symbols and filters.</p>';
        resultDiv.classList.remove('hidden');
        return;
    }
    
    let html = `
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Price</th>
                    <th>Score</th>
                    <th>Recommendation</th>
                    <th>P/E Ratio</th>
                    <th>Dividend</th>
                    <th>Trend</th>
                </tr>
            </thead>
            <tbody>
    `;
    
    results.forEach(stock => {
        const recommendation = stock.recommendation || 'HOLD';
        const recClass = recommendation.toLowerCase();
        html += `
            <tr>
                <td class="symbol">${stock.symbol}</td>
                <td>$${stock.current_price?.toFixed(2) || '—'}</td>
                <td><strong>${stock.overall_score?.toFixed(1) || '—'}/100</strong></td>
                <td><span class="status ${recClass}">${recommendation}</span></td>
                <td>${stock.fundamental?.pe_ratio || '—'}</td>
                <td>${stock.fundamental?.dividend_yield 
                    ? `${(stock.fundamental.dividend_yield * 100).toFixed(2)}%` 
                    : '—'}</td>
                <td>${stock.technical?.trend || '—'}</td>
            </tr>
        `;
    });
    
    html += `
            </tbody>
        </table>
    `;
    
    tableDiv.innerHTML = html;
    resultDiv.classList.remove('hidden');
}

// ============ TOP PERFORMERS ============
async function fetchTopPerformers(buttonEl = null) {
    const btn = buttonEl || document.getElementById('topBtn');

    try {
        setButtonState(btn, true, 'Loading...', 'Show Top 10');

        const response = await fetch(`${API_URL}/fetch-top-performers?top_n=10`);
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not fetch top performers');
            throw new Error(errorMessage);
        }
        
        const data = await response.json();
        displayScreenResults(data?.results || data, 'topPerformersTable', 'topResult');
        
        setButtonState(btn, false, 'Loading...', 'Show Top 10');
    } catch (error) {
        alert(`Error: ${error.message}`);
        setButtonState(btn, false, 'Loading...', 'Show Top 10');
    }
}

// ============ ENTER KEY SUPPORT ============
document.addEventListener('DOMContentLoaded', () => {
    const analyzeBtn = document.getElementById('analyzeBtn');
    const screenBtn = document.getElementById('screenBtn');

    document.getElementById('singleSymbol')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') analyzeStock(analyzeBtn);
    });

    document.getElementById('multipleSymbols')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') screenStocks(screenBtn);
    });
});

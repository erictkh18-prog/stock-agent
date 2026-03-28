// Use same-origin in production and localhost only for local file testing.
const API_URL = window.location.origin.startsWith('http')
    ? window.location.origin
    : 'http://localhost:8000';

let scoreChart = null;

// ============ MOBILE MENU TOGGLE ============
document.addEventListener('DOMContentLoaded', function() {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');
    
    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function() {
            sidebar.classList.toggle('open');
        });
        
        // Close sidebar when clicking on a link
        sidebar.querySelectorAll('.sidebar-item').forEach(link => {
            link.addEventListener('click', function() {
                sidebar.classList.remove('open');
            });
        });
        
        // Close sidebar when clicking outside
        document.addEventListener('click', function(e) {
            if (!sidebar.contains(e.target) && !sidebarToggle.contains(e.target)) {
                sidebar.classList.remove('open');
            }
        });
    }
});

// Scroll to section helper
function scrollToSection(sectionId) {
    const section = document.getElementById(sectionId);
    if (section) {
        setTimeout(() => {
            section.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 100);
    }
}

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
        ${escapeHtml(data.recommendation)} | Confidence: ${confidence}%`;
    
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

        const response = await fetch(`${API_URL}/screen-async?${params}`, { method: 'POST' });
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

// ============ US MARKET SCANNER ============
async function scanUsMarket(buttonEl = null) {
    const btn = buttonEl || document.getElementById('marketScanBtn');
    const universe = document.getElementById('marketUniverse')?.value || 'sp500';
    const sector = document.getElementById('marketSector')?.value || 'all';
    const minScoreInput = Number(document.getElementById('marketMinScore')?.value);
    const topNInput = Number(document.getElementById('marketTopN')?.value);
    const maxSymbolsInput = Number(document.getElementById('marketMaxSymbols')?.value);
    const minScore = Number.isFinite(minScoreInput) ? Math.min(100, Math.max(0, minScoreInput)) : 65;
    const topN = Number.isFinite(topNInput) ? Math.min(100, Math.max(1, Math.floor(topNInput))) : 20;
    const maxSymbols = Math.min(800, Math.max(5, Math.floor(maxSymbolsInput || 10)));
    const progressEl = document.getElementById('marketScanMeta');

    try {
        setButtonState(btn, true, 'Scanning...', 'Scan US Market');

        const params = new URLSearchParams();
        params.append('universe', universe);
        if (sector && sector !== 'all') {
            params.append('sector', sector);
        }
        params.append('min_overall_score', String(minScore));
        params.append('top_n', String(topN));
        params.append('max_symbols', String(maxSymbols));

        const progressSector = sector === 'all' ? 'all sectors' : sector;
        if (progressEl) {
            progressEl.textContent = `Scanning up to ${maxSymbols} symbols from ${universe} (${progressSector}). This may take 10-60 seconds...`;
        }
        document.getElementById('marketScanResult')?.classList.remove('hidden');

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 90000);
        const response = await fetch(`${API_URL}/scan-us-market?${params}`, { signal: controller.signal });
        clearTimeout(timeoutId);

        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not scan US market');
            throw new Error(errorMessage);
        }
        
        const data = await response.json();

        const scannedCount = data?.scanned_count ?? 'N/A';
        const filteredCount = data?.filtered_count ?? 'N/A';
        const universeLabel = universe === 'sp500'
            ? 'S&P 500'
            : (universe === 'nasdaq100' ? 'Nasdaq-100' : 'Combined');
        const selectedSector = data?.sector && data.sector !== 'all' ? data.sector : 'all sectors';
        const metaEl = document.getElementById('marketScanMeta');
        if (metaEl) {
            metaEl.textContent = `Scanned ${scannedCount} symbols from ${universeLabel} (${selectedSector}); ${filteredCount} passed filters.`;
        }

        displayScreenResults(data?.results || [], 'marketScanTable', 'marketScanResult');
        
        setButtonState(btn, false, 'Scanning...', 'Scan US Market');
    } catch (error) {
        const message = error?.name === 'AbortError'
            ? 'Scan timed out after 90 seconds. Try reducing Max Symbols.'
            : `Error: ${error.message}`;

        if (progressEl) {
            progressEl.textContent = message;
        }
        alert(message);
        setButtonState(btn, false, 'Scanning...', 'Scan US Market');
    }
}

// ============ STOCK RECOMMENDATION ============
let recommendScanJobId = null;
let recommendScanPollHandle = null;

function _setRecommendationScanUiState(isScanning) {
    const scanBtn = document.getElementById('recommendScanBtn');
    const stopBtn = document.getElementById('recommendStopBtn');
    if (scanBtn) {
        setButtonState(scanBtn, isScanning, 'Scanning...', 'Scan US Market');
    }
    if (stopBtn) {
        stopBtn.disabled = !isScanning;
    }
}

function _stopRecommendationPolling() {
    if (recommendScanPollHandle) {
        clearInterval(recommendScanPollHandle);
        recommendScanPollHandle = null;
    }
}

async function _pollRecommendationScanJob() {
    if (!recommendScanJobId) {
        return;
    }

    const progressEl = document.getElementById('recommendMeta');

    try {
        const response = await fetch(`${API_URL}/stock-recommendations/scan/${encodeURIComponent(recommendScanJobId)}`);
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not fetch recommendation scan status');
            throw new Error(errorMessage);
        }

        const data = await response.json();
        const scanned = Number(data?.scanned_count || 0);
        const total = Number(data?.total_symbols || 0);
        const found = Number(data?.found_count || 0);
        const status = String(data?.status || 'running').toLowerCase();
        const statusText = data?.message || `Scanning ${scanned}/${total} symbols, found ${found}.`;

        if (progressEl) {
            progressEl.textContent = statusText;
        }
        displayRecommendationResults(data?.results || []);

        if (status === 'completed' || status === 'stopped' || status === 'error') {
            _stopRecommendationPolling();
            recommendScanJobId = null;
            _setRecommendationScanUiState(false);
        }
    } catch (error) {
        _stopRecommendationPolling();
        recommendScanJobId = null;
        _setRecommendationScanUiState(false);
        if (progressEl) {
            progressEl.textContent = `Error: ${error.message}`;
        }
        alert(`Error: ${error.message}`);
    }
}

async function scanStockRecommendations(buttonEl = null) {
    const btn = buttonEl || document.getElementById('recommendScanBtn');
    const universe = document.getElementById('recommendUniverse')?.value || 'sp500';
    const sector = document.getElementById('recommendSector')?.value || 'all';
    const durationInput = Number(document.getElementById('recommendDurationDays')?.value);
    const targetPctInput = Number(document.getElementById('recommendTargetPct')?.value);

    const durationDays = Number.isFinite(durationInput) ? Math.min(365, Math.max(1, Math.floor(durationInput))) : 30;
    const targetPercentage = Number.isFinite(targetPctInput) ? Math.min(100, Math.max(1, targetPctInput)) : 8;
    const progressEl = document.getElementById('recommendMeta');

    try {
        _stopRecommendationPolling();
        recommendScanJobId = null;
        _setRecommendationScanUiState(true);

        const params = new URLSearchParams();
        params.append('universe', universe);
        if (sector && sector !== 'all') {
            params.append('sector', sector);
        }
        params.append('duration_days', String(durationDays));
        params.append('target_percentage', String(targetPercentage));

        if (progressEl) {
            progressEl.textContent = `Started scan for stocks targeting ${targetPercentage}% in ${durationDays} days. Searching...`;
        }
        document.getElementById('recommendResult')?.classList.remove('hidden');

        const response = await fetch(`${API_URL}/stock-recommendations/scan/start?${params}`, {
            method: 'POST',
        });
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not start recommendation scan');
            throw new Error(errorMessage);
        }

        const data = await response.json();
        recommendScanJobId = data?.job_id || null;

        if (!recommendScanJobId) {
            displayRecommendationResults(data?.results || []);
            if (progressEl) {
                progressEl.textContent = data?.message || 'No symbols available for this selection.';
            }
            _setRecommendationScanUiState(false);
            return;
        }

        if (progressEl) {
            progressEl.textContent = data?.message || 'Scan started.';
        }
        displayRecommendationResults(data?.results || []);
        await _pollRecommendationScanJob();
        recommendScanPollHandle = setInterval(_pollRecommendationScanJob, 2000);
    } catch (error) {
        const message = `Error: ${error.message}`;
        if (progressEl) {
            progressEl.textContent = message;
        }
        alert(message);
        _stopRecommendationPolling();
        recommendScanJobId = null;
        _setRecommendationScanUiState(false);
    }
}

async function stopStockRecommendationScan(buttonEl = null) {
    const btn = buttonEl || document.getElementById('recommendStopBtn');
    if (!recommendScanJobId) {
        return;
    }

    const progressEl = document.getElementById('recommendMeta');
    try {
        setButtonState(btn, true, 'Stopping...', 'Stop Scan');
        const response = await fetch(`${API_URL}/stock-recommendations/scan/${encodeURIComponent(recommendScanJobId)}/stop`, {
            method: 'POST',
        });
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not stop recommendation scan');
            throw new Error(errorMessage);
        }

        if (progressEl) {
            progressEl.textContent = 'Stop requested. Finishing current batch...';
        }
        await _pollRecommendationScanJob();
    } catch (error) {
        if (progressEl) {
            progressEl.textContent = `Error: ${error.message}`;
        }
        alert(`Error: ${error.message}`);
    } finally {
        setButtonState(btn, false, 'Stopping...', 'Stop Scan');
    }
}

function displayRecommendationResults(results) {
    const resultDiv = document.getElementById('recommendResult');
    const tableDiv = document.getElementById('recommendTable');

    if (!Array.isArray(results) || !results.length) {
        tableDiv.innerHTML = '<p>No recommendations matched your target. Try lower % target or longer duration.</p>';
        resultDiv.classList.remove('hidden');
        return;
    }

    let html = `
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Price</th>
                    <th>Projected Upside</th>
                    <th>Learning Adj</th>
                    <th>Target Price</th>
                    <th>Stop Loss</th>
                    <th>Why Recommended</th>
                </tr>
            </thead>
            <tbody>
    `;

    results.forEach((stock) => {
        const symbol = String(stock.symbol || '').toUpperCase();
        const currentPrice = Number(stock.current_price || 0);
        const targetPrice = Number(stock.target_price || 0);
        const stopLossPrice = Number(stock.stop_loss_price || 0);
        html += `
            <tr>
                <td class="symbol">${escapeHtml(symbol)}</td>
                <td>$${currentPrice.toFixed(2)}</td>
                <td>${Number(stock.adjusted_upside_pct ?? stock.expected_upside_pct ?? 0).toFixed(2)}%</td>
                <td>${Number(stock.learning_adjustment || 0).toFixed(2)}%</td>
                <td>$${targetPrice.toFixed(2)}</td>
                <td>$${stopLossPrice.toFixed(2)} (${Number(stock.stop_loss_pct || 0).toFixed(1)}%)</td>
                <td>${escapeHtml(stock.reason || 'Model indicates this stock has favorable risk/reward for your target.')}</td>
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

// ============ TRADE OUTCOME TRACKER ============
// ============ PAPER TRADING ============
async function triggerAutoBuy(buttonEl) {
    const btn = buttonEl || document.getElementById('autoBuyBtn');
    try {
        setButtonState(btn, true, 'Scanning…', '▶ Trigger Auto-Buy Now');
        const response = await fetch(`${API_URL}/paper-trading/auto-buy`, { method: 'POST' });
        if (!response.ok) {
            const msg = await parseApiError(response, 'Auto-buy failed');
            throw new Error(msg);
        }
        const data = await response.json();
        const meta = document.getElementById('paperTradingMeta');
        if (meta) {
            if (data.status === 'ok') {
                meta.textContent = `Opened: ${data.position.symbol} × ${data.position.shares} shares @ $${Number(data.position.entry_price).toFixed(2)} | Target $${Number(data.position.target_price).toFixed(2)} | Stop $${Number(data.position.stop_loss_price).toFixed(2)}`;
            } else {
                meta.textContent = data.message || 'No BUY found.';
            }
        }
        await loadPaperTrading();
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        setButtonState(btn, false, 'Scanning…', '▶ Trigger Auto-Buy Now');
    }
}

async function checkAndClosePositions(buttonEl) {
    const btn = buttonEl || document.getElementById('checkPositionsBtn');
    try {
        setButtonState(btn, true, 'Checking…', '🔄 Check & Close Positions');
        const response = await fetch(`${API_URL}/paper-trading/check-positions`, { method: 'POST' });
        if (!response.ok) {
            const msg = await parseApiError(response, 'Check failed');
            throw new Error(msg);
        }
        const data = await response.json();
        const meta = document.getElementById('paperTradingMeta');
        if (meta) {
            meta.textContent = data.closed_count > 0
                ? `Closed ${data.closed_count} position(s): ${data.closed.map(t => `${t.symbol} (${t.exit_reason} ${Number(t.return_pct).toFixed(2)}%)`).join(', ')}`
                : 'No positions closed — none hit target, stop, or expiry.';
        }
        await loadPaperTrading();
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        setButtonState(btn, false, 'Checking…', '🔄 Check & Close Positions');
    }
}

async function manualClosePosition(positionId, symbol, buttonEl) {
    if (!confirm(`Close ${symbol} position now at current market price?`)) return;
    buttonEl.disabled = true;
    buttonEl.textContent = '…';
    try {
        const response = await fetch(`${API_URL}/paper-trading/positions/${encodeURIComponent(positionId)}/close`, { method: 'POST' });
        if (!response.ok) {
            const msg = await parseApiError(response, 'Close failed');
            throw new Error(msg);
        }
        const data = await response.json();
        const meta = document.getElementById('paperTradingMeta');
        if (meta) {
            meta.textContent = `Manually closed ${data.trade.symbol} @ $${Number(data.trade.exit_price).toFixed(2)} | Return: ${Number(data.trade.return_pct).toFixed(2)}%`;
        }
        await loadPaperTrading();
    } catch (error) {
        alert(`Error: ${error.message}`);
        buttonEl.disabled = false;
        buttonEl.textContent = 'Close';
    }
}

async function loadPaperTrading() {
    const openTable = document.getElementById('openPositionsTable');
    const closedTable = document.getElementById('closedTradesTable');
    const closedMeta = document.getElementById('closedTradesMeta');
    const meta = document.getElementById('paperTradingMeta');
    if (!openTable) return;

    try {
        const [posRes, tradesRes] = await Promise.all([
            fetch(`${API_URL}/paper-trading/positions`),
            fetch(`${API_URL}/paper-trading/trades?limit=50`),
        ]);

        // ── Open positions ──
        if (posRes.ok) {
            const posData = await posRes.json();
            const positions = posData.positions || [];
            if (!positions.length) {
                openTable.innerHTML = '<p>No open positions.</p>';
            } else {
                let html = `<table>
                    <thead><tr>
                        <th>Symbol</th><th>Shares</th><th>Entry $</th>
                        <th>Target $</th><th>Stop $</th>
                        <th>Current $</th><th>P&amp;L</th><th>Days Left</th><th></th>
                    </tr></thead><tbody>`;
                positions.forEach((p) => {
                    const pnlClass = (p.unrealized_pnl || 0) >= 0 ? 'style="color:green"' : 'style="color:red"';
                    html += `<tr>
                        <td class="symbol">${escapeHtml(p.symbol)}</td>
                        <td>${p.shares}</td>
                        <td>$${Number(p.entry_price).toFixed(2)}</td>
                        <td>$${Number(p.target_price).toFixed(2)}</td>
                        <td>$${Number(p.stop_loss_price).toFixed(2)}</td>
                        <td>${p.current_price != null ? `$${Number(p.current_price).toFixed(2)}` : '—'}</td>
                        <td ${pnlClass}>${p.unrealized_pnl != null ? `$${Number(p.unrealized_pnl).toFixed(2)} (${Number(p.unrealized_pct).toFixed(2)}%)` : '—'}</td>
                        <td>${p.days_remaining}</td>
                        <td><button class="mini-btn" onclick="manualClosePosition('${escapeHtml(p.id)}','${escapeHtml(p.symbol)}',this)">Close</button></td>
                    </tr>`;
                });
                html += '</tbody></table>';
                openTable.innerHTML = html;
            }
            if (meta) meta.textContent = `${positions.length} open position(s).`;
        }

        // ── Closed trades ──
        if (tradesRes.ok) {
            const tradesData = await tradesRes.json();
            const summary = tradesData.summary || {};
            const trades = tradesData.trades || [];

            if (closedMeta) {
                closedMeta.textContent = `Total: ${summary.total || 0} | Wins: ${summary.target_hits || 0} | Losses: ${summary.stop_hits || 0} | Win rate: ${Number(summary.win_rate_pct || 0).toFixed(1)}% | Avg return: ${Number(summary.average_return_pct || 0).toFixed(2)}% | Total P&L: $${Number(summary.total_pnl || 0).toFixed(2)}`;
            }

            if (!trades.length) {
                if (closedTable) closedTable.innerHTML = '<p>No closed trades yet.</p>';
            } else {
                let html = `<table>
                    <thead><tr>
                        <th>Closed</th><th>Symbol</th><th>Reason</th>
                        <th>Entry $</th><th>Exit $</th><th>Return</th><th>P&amp;L</th>
                    </tr></thead><tbody>`;
                trades.forEach((t) => {
                    const retClass = (t.return_pct || 0) >= 0 ? 'style="color:green"' : 'style="color:red"';
                    html += `<tr>
                        <td>${escapeHtml((t.closed_at || '').replace('T', ' ').slice(0, 16))}</td>
                        <td class="symbol">${escapeHtml(t.symbol || '')}</td>
                        <td>${escapeHtml(t.exit_reason || '')}</td>
                        <td>$${Number(t.entry_price || 0).toFixed(2)}</td>
                        <td>$${Number(t.exit_price || 0).toFixed(2)}</td>
                        <td ${retClass}>${Number(t.return_pct || 0).toFixed(2)}%</td>
                        <td ${retClass}>$${Number(t.pnl || 0).toFixed(2)}</td>
                    </tr>`;
                });
                html += '</tbody></table>';
                if (closedTable) closedTable.innerHTML = html;
            }
        }
    } catch (error) {
        if (meta) meta.textContent = `Error loading paper trading data: ${error.message}`;
    }
}

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

// ============ ENTER KEY SUPPORT ============
document.addEventListener('DOMContentLoaded', () => {
    const analyzeBtn = document.getElementById('analyzeBtn');
    const screenBtn = document.getElementById('screenBtn');
    const marketScanBtn = document.getElementById('marketScanBtn');
    const recommendScanBtn = document.getElementById('recommendScanBtn');
    const recommendStopBtn = document.getElementById('recommendStopBtn');

    document.getElementById('singleSymbol')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') analyzeStock(analyzeBtn);
    });

    document.getElementById('multipleSymbols')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') screenStocks(screenBtn);
    });

    marketScanBtn?.addEventListener('click', () => {
        scanUsMarket(marketScanBtn);
    });

    recommendScanBtn?.addEventListener('click', () => {
        scanStockRecommendations(recommendScanBtn);
    });

    recommendStopBtn?.addEventListener('click', () => {
        stopStockRecommendationScan(recommendStopBtn);
    });

    document.getElementById('autoBuyBtn')?.addEventListener('click', (e) => {
        triggerAutoBuy(e.currentTarget);
    });
    document.getElementById('checkPositionsBtn')?.addEventListener('click', (e) => {
        checkAndClosePositions(e.currentTarget);
    });
    document.getElementById('refreshPositionsBtn')?.addEventListener('click', () => {
        loadPaperTrading();
    });

    loadPaperTrading();
});

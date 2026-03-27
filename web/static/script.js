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
async function scanStockRecommendations(buttonEl = null) {
    const btn = buttonEl || document.getElementById('recommendScanBtn');
    const universe = document.getElementById('recommendUniverse')?.value || 'sp500';
    const sector = document.getElementById('recommendSector')?.value || 'all';
    const minScoreInput = Number(document.getElementById('recommendMinScore')?.value);
    const topNInput = Number(document.getElementById('recommendTopN')?.value);
    const maxSymbolsInput = Number(document.getElementById('recommendMaxSymbols')?.value);
    const durationInput = Number(document.getElementById('recommendDurationDays')?.value);
    const targetPctInput = Number(document.getElementById('recommendTargetPct')?.value);

    const minScore = Number.isFinite(minScoreInput) ? Math.min(100, Math.max(0, minScoreInput)) : 65;
    const topN = Number.isFinite(topNInput) ? Math.min(50, Math.max(1, Math.floor(topNInput))) : 10;
    const maxSymbols = Math.min(800, Math.max(5, Math.floor(maxSymbolsInput || 10)));
    const durationDays = Number.isFinite(durationInput) ? Math.min(365, Math.max(1, Math.floor(durationInput))) : 30;
    const targetPercentage = Number.isFinite(targetPctInput) ? Math.min(100, Math.max(1, targetPctInput)) : 8;
    const progressEl = document.getElementById('recommendMeta');

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
        params.append('duration_days', String(durationDays));
        params.append('target_percentage', String(targetPercentage));

        if (progressEl) {
            progressEl.textContent = `Scanning up to ${maxSymbols} symbols to find stocks targeting ${targetPercentage}% upside in ${durationDays} days...`;
        }
        document.getElementById('recommendResult')?.classList.remove('hidden');

        const response = await fetch(`${API_URL}/stock-recommendations?${params}`);
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not fetch stock recommendations');
            throw new Error(errorMessage);
        }

        const data = await response.json();
        if (progressEl) {
            const learning = data?.learning || {};
            const learningText = learning.total_tracked_outcomes
                ? ` Learning: ${learning.total_tracked_outcomes} tracked outcomes | win rate ${Number(learning.win_rate_pct || 0).toFixed(1)}% | avg return ${Number(learning.average_return_pct || 0).toFixed(2)}%.`
                : '';
            progressEl.textContent = (data?.summary || 'Recommendation scan complete.') + learningText;
        }

        displayRecommendationResults(data?.results || []);
        setButtonState(btn, false, 'Scanning...', 'Scan US Market');
    } catch (error) {
        const message = `Error: ${error.message}`;
        if (progressEl) {
            progressEl.textContent = message;
        }
        alert(message);
        setButtonState(btn, false, 'Scanning...', 'Scan US Market');
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
                    <th>Outcome Log</th>
                </tr>
            </thead>
            <tbody>
    `;

    results.forEach((stock) => {
        const symbol = String(stock.symbol || '').toUpperCase();
        const currentPrice = Number(stock.current_price || 0);
        const targetPrice = Number(stock.target_price || 0);
        const stopLossPrice = Number(stock.stop_loss_price || 0);
        const durationDays = Number(document.getElementById('recommendDurationDays')?.value || 30);
        const targetPercentage = Number(document.getElementById('recommendTargetPct')?.value || 8);

        html += `
            <tr>
                <td class="symbol">${escapeHtml(symbol)}</td>
                <td>$${currentPrice.toFixed(2)}</td>
                <td>${Number(stock.adjusted_upside_pct ?? stock.expected_upside_pct ?? 0).toFixed(2)}%</td>
                <td>${Number(stock.learning_adjustment || 0).toFixed(2)}%</td>
                <td>$${targetPrice.toFixed(2)}</td>
                <td>$${stopLossPrice.toFixed(2)} (${Number(stock.stop_loss_pct || 0).toFixed(1)}%)</td>
                <td>${escapeHtml(stock.reason || 'Model indicates this stock has favorable risk/reward for your target.')}</td>
                <td>
                    <div class="quick-outcome-actions">
                        <button class="mini-btn mini-btn-target quick-log-btn" type="button"
                            data-symbol="${escapeHtml(symbol)}"
                            data-outcome="target_hit"
                            data-entry="${currentPrice.toFixed(4)}"
                            data-exit="${targetPrice.toFixed(4)}"
                            data-target="${targetPrice.toFixed(4)}"
                            data-stop="${stopLossPrice.toFixed(4)}"
                            data-duration="${durationDays}"
                            data-target-pct="${targetPercentage}">Target</button>
                        <button class="mini-btn mini-btn-stop quick-log-btn" type="button"
                            data-symbol="${escapeHtml(symbol)}"
                            data-outcome="stop_hit"
                            data-entry="${currentPrice.toFixed(4)}"
                            data-exit="${stopLossPrice.toFixed(4)}"
                            data-target="${targetPrice.toFixed(4)}"
                            data-stop="${stopLossPrice.toFixed(4)}"
                            data-duration="${durationDays}"
                            data-target-pct="${targetPercentage}">Stop</button>
                        <button class="mini-btn mini-btn-timeout quick-log-btn" type="button"
                            data-symbol="${escapeHtml(symbol)}"
                            data-outcome="timeout"
                            data-entry="${currentPrice.toFixed(4)}"
                            data-target="${targetPrice.toFixed(4)}"
                            data-stop="${stopLossPrice.toFixed(4)}"
                            data-duration="${durationDays}"
                            data-target-pct="${targetPercentage}">Timeout</button>
                    </div>
                </td>
            </tr>
        `;
    });

    html += `
            </tbody>
        </table>
    `;

    tableDiv.innerHTML = html;
    resultDiv.classList.remove('hidden');

    tableDiv.querySelectorAll('.quick-log-btn').forEach((button) => {
        button.addEventListener('click', async () => {
            const payload = {
                symbol: button.dataset.symbol,
                outcome: button.dataset.outcome,
                entry_price: Number(button.dataset.entry),
                exit_price: button.dataset.exit ? Number(button.dataset.exit) : null,
                target_price: button.dataset.target ? Number(button.dataset.target) : null,
                stop_loss_price: button.dataset.stop ? Number(button.dataset.stop) : null,
                duration_days: button.dataset.duration ? Number(button.dataset.duration) : null,
                target_percentage: button.dataset.targetPct ? Number(button.dataset.targetPct) : null,
            };
            await quickLogRecommendationOutcome(payload, button);
        });
    });
}

async function quickLogRecommendationOutcome(payload, buttonEl) {
    const originalText = buttonEl.textContent;
    buttonEl.disabled = true;
    buttonEl.textContent = '...';

    try {
        const response = await fetch(`${API_URL}/trade-outcomes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not log outcome');
            throw new Error(errorMessage);
        }

        const result = await response.json();
        const meta = document.getElementById('outcomeTrackerMeta');
        if (meta) {
            meta.textContent = `Logged ${result.record.symbol} (${result.record.outcome}) with return ${Number(result.record.return_pct || 0).toFixed(2)}%.`;
        }
        await loadTradeOutcomeHistory();

        buttonEl.textContent = 'Logged';
    } catch (error) {
        alert(`Error: ${error.message}`);
        buttonEl.disabled = false;
        buttonEl.textContent = originalText;
    }
}

// ============ TRADE OUTCOME TRACKER ============
async function logTradeOutcome(buttonEl = null) {
    const btn = buttonEl || document.getElementById('logOutcomeBtn');
    const symbol = (document.getElementById('outcomeSymbol')?.value || '').trim().toUpperCase();
    const outcome = document.getElementById('outcomeType')?.value || 'manual_close';
    const entryPrice = Number(document.getElementById('outcomeEntryPrice')?.value);
    const exitPriceRaw = document.getElementById('outcomeExitPrice')?.value;
    const targetPriceRaw = document.getElementById('outcomeTargetPrice')?.value;
    const stopPriceRaw = document.getElementById('outcomeStopPrice')?.value;

    if (!symbol) {
        alert('Please enter a symbol.');
        return;
    }
    if (!Number.isFinite(entryPrice) || entryPrice <= 0) {
        alert('Please provide a valid entry price.');
        return;
    }

    const payload = {
        symbol,
        outcome,
        entry_price: entryPrice,
        exit_price: exitPriceRaw ? Number(exitPriceRaw) : null,
        target_price: targetPriceRaw ? Number(targetPriceRaw) : null,
        stop_loss_price: stopPriceRaw ? Number(stopPriceRaw) : null,
    };

    try {
        setButtonState(btn, true, 'Logging...', 'Log Outcome');
        const response = await fetch(`${API_URL}/trade-outcomes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not log trade outcome');
            throw new Error(errorMessage);
        }

        const result = await response.json();
        const meta = document.getElementById('outcomeTrackerMeta');
        if (meta) {
            meta.textContent = `Logged ${result.record.symbol} (${result.record.outcome}) with return ${Number(result.record.return_pct || 0).toFixed(2)}%.`;
        }
        await loadTradeOutcomeHistory();

        document.getElementById('outcomeSymbol').value = '';
        document.getElementById('outcomeEntryPrice').value = '';
        document.getElementById('outcomeExitPrice').value = '';
        document.getElementById('outcomeTargetPrice').value = '';
        document.getElementById('outcomeStopPrice').value = '';
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        setButtonState(btn, false, 'Logging...', 'Log Outcome');
    }
}

async function loadTradeOutcomeHistory() {
    const resultDiv = document.getElementById('outcomeResult');
    const tableDiv = document.getElementById('outcomeTable');
    const meta = document.getElementById('outcomeTrackerMeta');
    if (!resultDiv || !tableDiv || !meta) {
        return;
    }

    try {
        const response = await fetch(`${API_URL}/trade-outcomes?limit=50`);
        if (!response.ok) {
            throw new Error('Could not load trade outcomes');
        }

        const data = await response.json();
        const summary = data.summary || {};
        meta.textContent = `Tracked: ${summary.total || 0} trades | Win rate: ${Number(summary.win_rate_pct || 0).toFixed(1)}% | Avg return: ${Number(summary.average_return_pct || 0).toFixed(2)}%`;

        const records = data.records || [];
        if (!records.length) {
            tableDiv.innerHTML = '<p>No outcomes logged yet.</p>';
            resultDiv.classList.remove('hidden');
            return;
        }

        let html = `
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Outcome</th>
                        <th>Entry</th>
                        <th>Exit</th>
                        <th>Return</th>
                    </tr>
                </thead>
                <tbody>
        `;

        records.forEach((record) => {
            html += `
                <tr>
                    <td>${escapeHtml((record.recorded_at || '').replace('T', ' ').slice(0, 19))}</td>
                    <td class="symbol">${escapeHtml(record.symbol || '')}</td>
                    <td>${escapeHtml(record.outcome || '')}</td>
                    <td>$${Number(record.entry_price || 0).toFixed(2)}</td>
                    <td>${record.exit_price != null ? `$${Number(record.exit_price).toFixed(2)}` : '—'}</td>
                    <td>${Number(record.return_pct || 0).toFixed(2)}%</td>
                </tr>
            `;
        });

        html += `</tbody></table>`;
        tableDiv.innerHTML = html;
        resultDiv.classList.remove('hidden');
    } catch (error) {
        meta.textContent = `Error loading outcomes: ${error.message}`;
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
    const logOutcomeBtn = document.getElementById('logOutcomeBtn');

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

    logOutcomeBtn?.addEventListener('click', () => {
        logTradeOutcome(logOutcomeBtn);
    });

    loadTradeOutcomeHistory();
});

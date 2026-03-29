// Use same-origin in production and localhost only for local file testing.
const API_URL = window.location.origin.startsWith('http')
    ? window.location.origin
    : 'http://localhost:8000';

let scoreChart = null;

// ============ TOAST NOTIFICATIONS ============
function showToast(message, type = 'info', durationMs = 4500) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const iconMap = { success: '✅', error: '❌', info: 'ℹ️', warning: '⚠️' };
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'alert');
    toast.innerHTML = `<span class="toast-icon" aria-hidden="true">${iconMap[type] || 'ℹ️'}</span><span class="toast-body">${escapeHtml(message)}</span>`;

    container.appendChild(toast);

    const remove = () => {
        toast.classList.add('toast-hiding');
        toast.addEventListener('animationend', () => toast.remove(), { once: true });
    };
    const timerId = setTimeout(remove, durationMs);
    toast.addEventListener('click', () => { clearTimeout(timerId); remove(); });
}

// Update screen-reader live region
function announceToSR(message) {
    const el = document.getElementById('live-status');
    if (el) {
        el.textContent = '';
        // force re-read by toggle
        setTimeout(() => { el.textContent = message; }, 50);
    }
}

// ============ SCORE INTERPRETATION ============
function scoreInterpretation(score) {
    if (score >= 70) return { label: 'Strong', cls: 'bullish' };
    if (score >= 50) return { label: 'Neutral', cls: 'neutral' };
    return { label: 'Weak', cls: 'bearish' };
}

function setScoreInterpBadge(id, score) {
    const el = document.getElementById(id);
    if (!el) return;
    const { label, cls } = scoreInterpretation(score);
    el.textContent = label;
    el.className = `score-interpretation ${cls}`;
}

// ============ INLINE VALIDATION ============
function showFieldError(inputId, message) {
    const input = document.getElementById(inputId);
    const errEl = document.getElementById(`${inputId}-error`);
    if (input) input.classList.add('invalid');
    if (errEl) {
        errEl.textContent = message;
        errEl.classList.add('visible');
    }
}

function clearFieldError(inputId) {
    const input = document.getElementById(inputId);
    const errEl = document.getElementById(`${inputId}-error`);
    if (input) input.classList.remove('invalid');
    if (errEl) {
        errEl.textContent = '';
        errEl.classList.remove('visible');
    }
}

// ============ MOBILE MENU TOGGLE ============
document.addEventListener('DOMContentLoaded', function() {
    const sidebarToggle = document.getElementById('sidebarToggle');
    const sidebar = document.getElementById('sidebar');

    if (sidebarToggle && sidebar) {
        sidebarToggle.addEventListener('click', function() {
            const isOpen = sidebar.classList.toggle('open');
            sidebarToggle.setAttribute('aria-expanded', String(isOpen));
        });

        // Close sidebar when clicking on a link
        sidebar.querySelectorAll('.sidebar-item').forEach(link => {
            link.addEventListener('click', function() {
                sidebar.classList.remove('open');
                sidebarToggle.setAttribute('aria-expanded', 'false');
            });
        });

        // Close sidebar when clicking outside
        document.addEventListener('click', function(e) {
            if (!sidebar.contains(e.target) && !sidebarToggle.contains(e.target)) {
                sidebar.classList.remove('open');
                sidebarToggle.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // Mark active sidebar item based on current hash or scroll position
    function updateActiveSidebarItem() {
        const hash = window.location.hash;
        document.querySelectorAll('.sidebar-item').forEach(item => {
            item.classList.remove('active');
            if (hash && item.getAttribute('href') === hash) {
                item.classList.add('active');
            }
        });
    }
    window.addEventListener('hashchange', updateActiveSidebarItem);
    updateActiveSidebarItem();
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
    clearFieldError('singleSymbol');

    if (!symbol) {
        showFieldError('singleSymbol', 'Please enter a stock symbol (e.g. AAPL)');
        document.getElementById('singleSymbol').focus();
        return;
    }

    const btn = buttonEl || document.getElementById('analyzeBtn');

    try {
        setButtonState(btn, true, 'Analyzing…', 'Analyze');
        announceToSR(`Analyzing ${symbol}…`);

        const response = await fetch(`${API_URL}/analyze/${symbol}`);
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Stock not found');
            throw new Error(errorMessage);
        }
        
        const data = await response.json();
        displaySingleResult(data);
        announceToSR(`Analysis complete for ${symbol}: ${data.recommendation}`);
        
        setButtonState(btn, false, 'Analyzing…', 'Analyze');
    } catch (error) {
        showToast(`Analysis failed: ${error.message}`, 'error');
        setButtonState(btn, false, 'Analyzing…', 'Analyze');
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
    
    // Score bars with interpretation badges
    if (data.fundamental) {
        updateScoreBar('fundamental', data.fundamental.score);
        setScoreInterpBadge('fundamentalInterp', data.fundamental.score);
    }
    if (data.technical) {
        updateScoreBar('technical', data.technical.score);
        setScoreInterpBadge('technicalInterp', data.technical.score);
    }
    if (data.sentiment) {
        updateScoreBar('sentiment', data.sentiment.score);
        setScoreInterpBadge('sentimentInterp', data.sentiment.score);
    }
    
    // Gauge chart
    drawGaugeChart(data.overall_score);
    
    // Recommendation box
    const recBox = document.getElementById('recommendationBox');
    const recommendation = data.recommendation.toLowerCase();
    const confidence = (data.confidence * 100).toFixed(0);
    recBox.className = `recommendation ${recommendation}`;
    recBox.innerHTML = `<span style="font-size: 1.5em; margin-right: 10px;" aria-hidden="true">
        ${recommendation === 'buy' ? '✅' : recommendation === 'hold' ? '⏸️' : '❌'}
    </span>
        ${escapeHtml(data.recommendation)} | Confidence: ${confidence}%`;
    
    resultDiv.classList.remove('hidden');
}

function updateScoreBar(type, score) {
    const bar = document.getElementById(`${type}Bar`);
    const scoreEl = document.getElementById(`${type}Score`);
    const barBg = bar?.parentElement;
    const pct = Math.min(score, 100);
    bar.style.width = `${pct}%`;
    if (barBg) barBg.setAttribute('aria-valuenow', String(pct));
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
    clearFieldError('multipleSymbols');

    if (!input) {
        showFieldError('multipleSymbols', 'Enter one or more symbols (e.g. AAPL, MSFT)');
        document.getElementById('multipleSymbols').focus();
        return;
    }

    const symbols = [...new Set(input.split(',').map(s => s.trim().toUpperCase()).filter(Boolean))];
    if (!symbols.length) {
        showFieldError('multipleSymbols', 'Please enter at least one valid stock symbol');
        return;
    }

    const btn = buttonEl || document.getElementById('screenBtn');

    try {
        setButtonState(btn, true, 'Screening…', 'Screen Stocks');
        announceToSR(`Screening ${symbols.length} stock(s)…`);

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
        announceToSR(`Screening complete. ${(data?.results || data)?.length || 0} result(s) shown.`);
        
        setButtonState(btn, false, 'Screening…', 'Screen Stocks');
    } catch (error) {
        showToast(`Screening failed: ${error.message}`, 'error');
        setButtonState(btn, false, 'Screening…', 'Screen Stocks');
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
        setButtonState(btn, true, 'Scanning…', 'Scan Market');

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
        announceToSR(`Market scan complete. ${filteredCount} stock(s) passed filters.`);
        
        setButtonState(btn, false, 'Scanning…', 'Scan Market');
    } catch (error) {
        const message = error?.name === 'AbortError'
            ? 'Scan timed out after 90 seconds. Try reducing Max Symbols.'
            : `Scan failed: ${error.message}`;

        if (progressEl) {
            progressEl.textContent = message;
        }
        showToast(message, error?.name === 'AbortError' ? 'warning' : 'error');
        setButtonState(btn, false, 'Scanning…', 'Scan Market');
    }
}

// ============ STOCK RECOMMENDATION ============
let recommendScanJobId = null;
let recommendScanPollHandle = null;

function _setRecommendationScanUiState(isScanning) {
    const scanBtn = document.getElementById('recommendScanBtn');
    const stopBtn = document.getElementById('recommendStopBtn');
    if (scanBtn) {
        setButtonState(scanBtn, isScanning, 'Scanning…', 'Scan for Buys');
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
        showToast(`Recommendation scan failed: ${error.message}`, 'error');
    }
}

async function scanStockRecommendations(buttonEl = null) {
    const btn = buttonEl || document.getElementById('recommendScanBtn');
    const universe = document.getElementById('recommendUniverse')?.value || 'sp500';
    const sector = document.getElementById('recommendSector')?.value || 'all';
    const durationRaw = (document.getElementById('recommendDurationDays')?.value || '').trim();
    const targetRaw = (document.getElementById('recommendTargetPct')?.value || '').trim();

    const durationInput = Number(durationRaw);
    const targetPctInput = Number(targetRaw);
    const hasDuration = durationRaw !== '' && Number.isFinite(durationInput);
    const hasTarget = targetRaw !== '' && Number.isFinite(targetPctInput);

    const durationDays = hasDuration
        ? Math.min(365, Math.max(1, Math.floor(durationInput)))
        : null;
    const targetPercentage = hasTarget
        ? Math.min(100, Math.max(1, targetPctInput))
        : null;
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
        if (durationDays !== null) {
            params.append('duration_days', String(durationDays));
        }
        if (targetPercentage !== null) {
            params.append('target_percentage', String(targetPercentage));
        }

        if (progressEl) {
            if (targetPercentage !== null && durationDays !== null) {
                progressEl.textContent = `Started scan for BUY uptrend stocks targeting ${targetPercentage}% in ${durationDays} days...`;
            } else if (targetPercentage !== null) {
                progressEl.textContent = `Started scan for BUY uptrend stocks targeting ${targetPercentage}%...`;
            } else {
                progressEl.textContent = 'Started scan for BUY uptrend stocks...';
            }
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
        const message = `Scan failed: ${error.message}`;
        if (progressEl) {
            progressEl.textContent = message;
        }
        showToast(message, 'error');
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
        setButtonState(btn, true, 'Stopping…', 'Stop Scan');
        const response = await fetch(`${API_URL}/stock-recommendations/scan/${encodeURIComponent(recommendScanJobId)}/stop`, {
            method: 'POST',
        });
        if (!response.ok) {
            const errorMessage = await parseApiError(response, 'Could not stop recommendation scan');
            throw new Error(errorMessage);
        }

        if (progressEl) {
            progressEl.textContent = 'Stop requested. Finishing current batch…';
        }
        await _pollRecommendationScanJob();
    } catch (error) {
        if (progressEl) {
            progressEl.textContent = `Error: ${error.message}`;
        }
        showToast(`Could not stop scan: ${error.message}`, 'error');
    } finally {
        setButtonState(btn, false, 'Stopping…', 'Stop Scan');
    }
}

function displayRecommendationResults(results) {
    const resultDiv = document.getElementById('recommendResult');
    const tableDiv = document.getElementById('recommendTable');

    if (!Array.isArray(results) || !results.length) {
        tableDiv.innerHTML = '<p>No BUY uptrend recommendations found for the current selection.</p>';
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
                    <th>Target / Duration</th>
                    <th>Stop Loss</th>
                    <th>Technical Reason</th>
                    <th>Layman Explanation</th>
                </tr>
            </thead>
            <tbody>
    `;

    results.forEach((stock) => {
        const symbol = String(stock.symbol || '').toUpperCase();
        const symbolUrl = `https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}`;
        const currentPrice = Number(stock.current_price || 0);
        const targetPrice = Number(stock.target_price || 0);
        const stopLossPrice = Number(stock.stop_loss_price || 0);
        const durationDays = stock.target_duration_days != null ? Number(stock.target_duration_days) : null;
        const durationText = Number.isFinite(durationDays)
            ? `${durationDays} day${durationDays === 1 ? '' : 's'}`
            : 'No fixed duration';
        const technicalReason = String(stock.technical_reason || '').trim();
        const laymanReason = String(stock.layman_reason || stock.reason || '').trim();

        html += `
            <tr>
                <td class="symbol"><a href="${symbolUrl}" target="_blank" rel="noopener noreferrer">${escapeHtml(symbol)}</a></td>
                <td>$${currentPrice.toFixed(2)}</td>
                <td>${Number(stock.adjusted_upside_pct ?? stock.expected_upside_pct ?? 0).toFixed(2)}%</td>
                <td>${Number(stock.learning_adjustment || 0).toFixed(2)}%</td>
                <td>$${targetPrice.toFixed(2)}<br><span class="description">${escapeHtml(durationText)}</span></td>
                <td>$${stopLossPrice.toFixed(2)} (${Number(stock.stop_loss_pct || 0).toFixed(1)}%)</td>
                <td>${escapeHtml(technicalReason || 'Trend and score alignment indicate favorable technical setup.')}</td>
                <td>${escapeHtml(laymanReason || 'Model indicates this stock has favorable risk/reward right now.')}</td>
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
        setButtonState(btn, true, 'Scanning…', '▶ Open Paper Positions Now');
        announceToSR('Scanning for BUY candidates…');
        const response = await fetch(`${API_URL}/paper-trading/auto-buy`, { method: 'POST' });
        if (!response.ok) {
            const msg = await parseApiError(response, 'Auto-buy failed');
            throw new Error(msg);
        }
        const data = await response.json();
        const meta = document.getElementById('paperTradingMeta');
        if (meta) {
            if (data.status === 'ok') {
                const opened = Array.isArray(data.opened_positions) ? data.opened_positions : [];
                const openedList = opened.map(p => `${p.symbol} × ${p.shares}`).join(', ');
                const skipped = Array.isArray(data.skipped_existing_symbols) && data.skipped_existing_symbols.length
                    ? ` Skipped existing: ${data.skipped_existing_symbols.join(', ')}.`
                    : '';
                const summary = `Opened ${Number(data.opened_count || opened.length)} position(s): ${openedList}.${skipped}`;
                meta.textContent = summary;
                showToast(summary, 'success');
            } else if (data.status === 'no_new_positions') {
                const skipped = Array.isArray(data.skipped_existing_symbols) && data.skipped_existing_symbols.length
                    ? ` Existing open symbols: ${data.skipped_existing_symbols.join(', ')}.`
                    : '';
                const msg = `${data.message || 'No new positions opened.'}${skipped}`;
                meta.textContent = msg;
                showToast(msg, 'info');
            } else {
                const msg = data.message || 'No BUY candidates found.';
                meta.textContent = msg;
                showToast(msg, 'info');
            }
        }
        await loadPaperTrading();
    } catch (error) {
        showToast(`Auto-buy failed: ${error.message}`, 'error');
    } finally {
        setButtonState(btn, false, 'Scanning…', '▶ Open Paper Positions Now');
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
            if (data.closed_count > 0) {
                const summary = `Closed ${data.closed_count} position(s): ${data.closed.map(t => `${t.symbol} (${t.exit_reason} ${Number(t.return_pct).toFixed(2)}%)`).join(', ')}`;
                meta.textContent = summary;
                showToast(summary, 'success');
            } else {
                const msg = 'No positions closed — none hit target, stop-loss, or expiry.';
                meta.textContent = msg;
                showToast(msg, 'info');
            }
        }
        await loadPaperTrading();
    } catch (error) {
        showToast(`Check positions failed: ${error.message}`, 'error');
    } finally {
        setButtonState(btn, false, 'Checking…', '🔄 Check & Close Positions');
    }
}

async function loadPaperTrading() {
    const openTable = document.getElementById('openPositionsTable');
    const closedTable = document.getElementById('closedTradesTable');
    const closedMeta = document.getElementById('closedTradesMeta');
    const meta = document.getElementById('paperTradingMeta');
    if (!openTable) return;

    try {
        const [posRes, tradesRes, storageRes] = await Promise.all([
            fetch(`${API_URL}/paper-trading/positions`),
            fetch(`${API_URL}/paper-trading/trades?limit=50`),
            fetch(`${API_URL}/paper-trading/storage-status`),
        ]);

        let storageSummary = 'Storage status unavailable.';
        if (storageRes.ok) {
            const storageData = await storageRes.json();
            if (storageData.healthy && storageData.mode === 'postgres') {
                storageSummary = 'Storage: Postgres connected.';
            } else if (storageData.healthy && storageData.mode === 'json-local') {
                storageSummary = 'Storage: Local JSON (non-persistent across deploys).';
            } else if (storageData.mode === 'postgres-error') {
                storageSummary = 'Storage: Postgres error detected.';
            }
        }

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
                        <th>Current $</th><th>P&L</th><th>Days Left</th>
                    </tr></thead><tbody>`;
                positions.forEach((p) => {
                    const pnlCls = (p.unrealized_pnl || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
                    html += `<tr>
                        <td class="symbol">${escapeHtml(p.symbol)}</td>
                        <td>${p.shares}</td>
                        <td>$${Number(p.entry_price).toFixed(2)}</td>
                        <td>$${Number(p.target_price).toFixed(2)}</td>
                        <td>$${Number(p.stop_loss_price).toFixed(2)}</td>
                        <td>${p.current_price != null ? `$${Number(p.current_price).toFixed(2)}` : '—'}</td>
                        <td class="${pnlCls}">${p.unrealized_pnl != null ? `$${Number(p.unrealized_pnl).toFixed(2)} (${Number(p.unrealized_pct).toFixed(2)}%)` : '—'}</td>
                        <td>${p.days_remaining}</td>
                    </tr>`;
                });
                html += '</tbody></table>';
                openTable.innerHTML = html;
            }
            if (meta) meta.textContent = `${positions.length} open position(s). ${storageSummary}`;
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
                    const retCls = (t.return_pct || 0) >= 0 ? 'pnl-positive' : 'pnl-negative';
                    html += `<tr>
                        <td>${escapeHtml((t.closed_at || '').replace('T', ' ').slice(0, 16))}</td>
                        <td class="symbol">${escapeHtml(t.symbol || '')}</td>
                        <td>${escapeHtml(t.exit_reason || '')}</td>
                        <td>$${Number(t.entry_price || 0).toFixed(2)}</td>
                        <td>$${Number(t.exit_price || 0).toFixed(2)}</td>
                        <td class="${retCls}">${Number(t.return_pct || 0).toFixed(2)}%</td>
                        <td class="${retCls}">$${Number(t.pnl || 0).toFixed(2)}</td>
                    </tr>`;
                });
                html += '</tbody></table>';
                if (closedTable) closedTable.innerHTML = html;
            }
        }
    } catch (error) {
        if (meta) meta.textContent = `Error loading paper trading data: ${error.message}`;
        showToast(`Could not load positions: ${error.message}`, 'error');
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

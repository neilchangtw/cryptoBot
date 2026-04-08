/* CryptoBot Dashboard — 前端邏輯 */

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// State
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
const S = {
    mode: localStorage.getItem('cb_mode') || 'paper',
    tab: 'status',
    trades: [],
    chartReady: false,
    mainChart: null,
    candleSeries: null,
    ema20Series: null,
    gkChart: null,
    gkSeries: null,
    equityChart: null,
    dailyChart: null,
    sortCol: 'entry_ts',
    sortAsc: false,
    filters: { direction: '', sub: '', win: '', exit: '' },
    priceLines: [],
};

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Helpers
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function api(url) {
    const sep = url.includes('?') ? '&' : '?';
    const resp = await fetch(`${url}${sep}mode=${S.mode}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
}

function $(id) { return document.getElementById(id); }
function pnlClass(v) { return v > 0 ? 'pnl-pos' : v < 0 ? 'pnl-neg' : ''; }
function pnlStr(v) { return v == null ? '-' : (v > 0 ? '+' : '') + v.toFixed(2); }
function fmtTime(s) { return s ? s.replace('T', ' ').substring(0, 16) : '-'; }

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Mode Switch
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function switchMode(mode) {
    S.mode = mode;
    localStorage.setItem('cb_mode', mode);
    document.querySelectorAll('.mode-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === mode));
    resetCountdown();
    loadCurrentTab();
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab Switch
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function switchTab(tab) {
    S.tab = tab;
    document.querySelectorAll('.tab-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.tab-content').forEach(c =>
        c.classList.toggle('active', c.id === `tab-${tab}`));
    resetCountdown();
    loadCurrentTab();
}

function loadCurrentTab() {
    if (S.tab === 'status') loadStatus();
    if (S.tab === 'chart') loadChart();
    if (S.tab === 'trades') loadTrades();
    if (S.tab === 'analytics') loadAnalytics();
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 1: Status
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function loadStatus() {
    try {
        const d = await api('/api/status');
        renderStatusCards(d);
        renderGK(d);
        renderHealth(d.health);
    } catch (e) {
        $('status-cards').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function renderStatusCards(d) {
    const pnl = d.today_pnl || 0;
    const pos = d.positions;
    $('status-cards').innerHTML = `
        <div class="card">
            <div class="card-label">帳戶餘額 (Balance)</div>
            <div class="card-value">$${(d.account_balance||0).toLocaleString('en',{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
            <div class="card-sub">Bar #${d.bar_counter} | ${d.last_bar_time || '-'}</div>
        </div>
        <div class="card">
            <div class="card-label">持倉 (Positions)</div>
            <div class="card-value ${pos.total>0?'gold':''}">
                ${pos.total > 0 ? `L:${pos.long_count} S:${pos.short_count}` : '空手'}
            </div>
            <div class="card-sub">${pos.details.map(p=>`${p.sub_strategy} ${p.side} @$${(p.entry_price||0).toFixed(2)}`).join(' | ') || '無持倉'}</div>
        </div>
        <div class="card">
            <div class="card-label">今日損益 (Today P&L)</div>
            <div class="card-value ${pnl>0?'green':pnl<0?'red':''}">${pnlStr(pnl)}</div>
            <div class="card-sub">開 ${d.today_trades||0} 筆 | W:${d.today_wins||0} L:${d.today_losses||0}</div>
        </div>
        <div class="card">
            <div class="card-label">最新價格 (Price)</div>
            <div class="card-value">${d.last_close ? '$'+d.last_close.toFixed(2) : '-'}</div>
            <div class="card-sub">ETHUSDT 1h</div>
        </div>
    `;
}

function renderGK(d) {
    const gk = d.gk_pctile;
    let color = '#8888a0', label = '無資料 (No Data)', bg = '#8888a0';
    if (gk != null) {
        if (gk < 30) { color = 'var(--green)'; label = '壓縮中 (Compressed)'; bg = 'var(--green)'; }
        else if (gk < 50) { color = 'var(--gold)'; label = '蓄勢中 (Building Up)'; bg = 'var(--gold)'; }
        else { color = 'var(--text-dim)'; label = '正常波動 (Normal)'; bg = 'var(--text-dim)'; }
    }
    const rangeItems = [
        { range: '0 – 30', label: '壓縮區 (Compression)', desc: '波動率極低，突破信號可觸發 (L 策略進場條件)', cls: 'gk-range-green' },
        { range: '30 – 40', label: '低波動 (Low Vol)', desc: '做空策略可觸發 (S1/S2/S4 門檻)', cls: 'gk-range-gold' },
        { range: '40 – 50', label: '過渡區 (Transition)', desc: '波動回升中，暫無信號', cls: 'gk-range-dim' },
        { range: '50 – 70', label: '正常區 (Normal)', desc: '一般波動水平', cls: 'gk-range-dim' },
        { range: '70 – 100', label: '擴張區 (Expansion)', desc: '高波動，突破正在進行', cls: 'gk-range-blue' },
    ];
    const rangeHtml = rangeItems.map(r => `
        <div class="gk-range-item ${r.cls}${gk != null && isInRange(gk, r.range) ? ' gk-range-active' : ''}">
            <span class="gk-range-num">${r.range}</span>
            <span class="gk-range-label">${r.label}</span>
            <span class="gk-range-desc">${r.desc}</span>
        </div>`).join('');

    $('gk-section').innerHTML = `
        <div class="gk-panel">
            <div class="gk-label">GK 壓縮指數 (Compression Index)</div>
            <div class="gk-value" style="color:${color}">${gk != null ? gk.toFixed(1) : '-'}</div>
            <div class="gk-label">${label}</div>
            <div class="gk-bar"><div class="gk-bar-fill" style="width:${gk||0}%;background:${bg}"></div></div>
        </div>
        <div class="gk-explain">
            <div class="gk-explain-title">GK 指數區間說明 (Range Guide)</div>
            ${rangeHtml}
        </div>
    `;
}

function isInRange(val, rangeStr) {
    const parts = rangeStr.split('–').map(s => parseInt(s.trim()));
    return val >= parts[0] && (parts[1] === 100 ? val <= 100 : val < parts[1]);
}

// 健康檢查翻譯表
const HEALTH_NAME_MAP = {
    'Monthly trade count': '月交易量 (Monthly Trades)',
    'SafeNet trigger rate': '安全網觸發率 (SafeNet Rate)',
    '24-48h hold WR': '24-48h 持倉勝率 (Hold WR)',
    'Avg hold time': '平均持倉時間 (Avg Hold)',
    'MFE/MAE ratio': '順逆行比 (MFE/MAE)',
    'Total PnL': '總損益 (Total PnL)',
    'Profit Factor': '盈利因子 (Profit Factor)',
    'Max Drawdown': '最大回撤 (Max DD)',
};
const HEALTH_DESC_MAP = {
    'Monthly trade count': '每月平倉交易筆數，正常範圍 10-30 筆',
    'SafeNet trigger rate': '安全網（-5.5% 止損）觸發比率，低於 15% 為正常',
    '24-48h hold WR': '持倉 24-48 小時的交易勝率，目標 ≥70%',
    'Avg hold time': '平均每筆交易持倉時間，目標 ≥18 小時',
    'MFE/MAE ratio': '最大順行 vs 最大逆行比值，>1.5 表示趨勢捕捉良好',
    'Total PnL': '期間內已平倉交易的總損益',
    'Profit Factor': '總獲利 / 總虧損，≥1.5 為健康',
    'Max Drawdown': '期間內最大回撤金額，目標 > -$500',
};
const HEALTH_STATUS_MAP = {
    'OK': '正常 (OK)',
    'WARNING': '注意 (Warning)',
    'ALERT': '警報 (Alert)',
};

function renderHealth(h) {
    if (!h || !h.checks) {
        $('health-section').innerHTML = '';
        return;
    }
    const overall = (h.overall || 'UNKNOWN').toUpperCase();
    const cls = overall === 'NORMAL' ? 'status-normal' : overall === 'WARNING' ? 'status-warning' : overall === 'PAUSE' ? 'status-pause' : 'badge-unknown';
    const overallLabel = overall === 'NORMAL' ? '正常 (Normal)' : overall === 'WARNING' ? '警告 (Warning)' : overall === 'PAUSE' ? '暫停 (Pause)' : overall;
    let html = `<div class="health-header">
        <span class="health-title">策略健康度 (Health Check)</span>
        <span class="status-badge ${cls}">${overallLabel}</span>
    </div>`;
    html += '<div class="health-grid">';
    for (const c of (h.checks || [])) {
        const st = (c.status || '').toUpperCase();
        const bcls = st === 'OK' ? 'badge-ok' : st === 'WARNING' ? 'badge-warn' : st === 'ALERT' ? 'badge-alert' : 'badge-unknown';
        const name = HEALTH_NAME_MAP[c.name] || c.name || '';
        const desc = HEALTH_DESC_MAP[c.name] || c.detail || '';
        const stLabel = HEALTH_STATUS_MAP[st] || st;
        html += `<div class="health-item">
            <div class="health-info">
                <span class="health-name">${name} <span style="color:var(--text-dim)">${c.value != null ? c.value : ''}</span></span>
                <span class="health-desc">${desc}</span>
            </div>
            <span class="health-badge ${bcls}">${stLabel}</span>
        </div>`;
    }
    html += '</div>';
    $('health-section').innerHTML = html;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 2: Chart
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function loadChart() {
    try {
        const [kd, td, st] = await Promise.all([
            fetch('/api/klines?limit=1500').then(r => r.json()),
            api('/api/trades'),
            api('/api/status'),
        ]);
        if (!S.chartReady) initCharts();
        S.trades = td.trades || [];
        updateChartData(kd, S.trades);
        updatePositionLines(st.positions ? st.positions.details : []);
    } catch (e) {
        $('main-chart').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function initCharts() {
    const mc = $('main-chart');
    const gc = $('gk-chart');
    mc.innerHTML = '';
    gc.innerHTML = '';

    const chartOpts = {
        layout: { background: { color: '#1a1a2e' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    };

    S.mainChart = LightweightCharts.createChart(mc, { ...chartOpts, width: mc.clientWidth, height: 480 });
    S.candleSeries = S.mainChart.addCandlestickSeries({
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });
    S.ema20Series = S.mainChart.addLineSeries({ color: '#f0b90b', lineWidth: 2, title: 'EMA20' });

    S.gkChart = LightweightCharts.createChart(gc, { ...chartOpts, width: gc.clientWidth, height: 120 });
    S.gkSeries = S.gkChart.addHistogramSeries({ color: '#5b86e5', title: 'GK 百分位 (Pctile)' });

    // Sync time scales
    let syncing = false;
    S.mainChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncing || !range) return;
        syncing = true;
        S.gkChart.timeScale().setVisibleLogicalRange(range);
        syncing = false;
    });
    S.gkChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (syncing || !range) return;
        syncing = true;
        S.mainChart.timeScale().setVisibleLogicalRange(range);
        syncing = false;
    });

    // Resize
    const ro = new ResizeObserver(() => {
        S.mainChart.applyOptions({ width: mc.clientWidth });
        S.gkChart.applyOptions({ width: gc.clientWidth });
    });
    ro.observe(mc);

    // 點擊標記顯示交易詳情
    S.mainChart.subscribeClick(param => {
        const tooltip = $('trade-tooltip');
        if (!param.time || !S.trades.length) { tooltip.classList.remove('show'); return; }

        // 找最接近點擊時間的交易
        const clickTime = param.time;
        let best = null, bestDist = Infinity;
        for (const t of S.trades) {
            for (const ts of [t.entry_ts, t.exit_ts]) {
                if (ts > 0 && Math.abs(ts - clickTime) < bestDist) {
                    bestDist = Math.abs(ts - clickTime);
                    best = t;
                }
            }
        }
        // 容差：3 根 bar (3h)
        if (!best || bestDist > 3600 * 3) { tooltip.classList.remove('show'); return; }

        const t = best;
        const isLong = (t.direction || '').toUpperCase() === 'LONG';
        const dirLabel = isLong ? '做多 (Long)' : '做空 (Short)';
        const dirCls = isLong ? 'pnl-pos' : 'pnl-neg';
        tooltip.innerHTML = `
            <div style="margin-bottom:6px"><b class="${dirCls}">${dirLabel}</b> <span style="color:var(--text-dim)">${t.sub_strategy||''}</span></div>
            <div>進場：${fmtTime(t.entry_time_utc8)} @ $${Number(t.entry_price||0).toFixed(2)}</div>
            ${t.exit_price ? `<div>出場：${fmtTime(t.exit_time_utc8)} @ $${Number(t.exit_price).toFixed(2)}</div>` : '<div>狀態：持倉中</div>'}
            ${t.exit_type ? `<div>原因：${t.exit_type}</div>` : ''}
            ${t.net_pnl_usd != null ? `<div>損益：<span class="${pnlClass(t.net_pnl_usd)}">${pnlStr(t.net_pnl_usd)} (${t.net_pnl_pct!=null?t.net_pnl_pct.toFixed(1)+'%':''})</span></div>` : ''}
            ${t.hold_bars != null ? `<div>持倉：${t.hold_bars}h</div>` : ''}
        `;
        // 定位 tooltip
        const rect = mc.getBoundingClientRect();
        let x = param.point.x + rect.left + 12;
        let y = param.point.y + rect.top - 20;
        if (x + 220 > window.innerWidth) x = param.point.x + rect.left - 230;
        if (y + 150 > window.innerHeight) y = window.innerHeight - 160;
        tooltip.style.left = x + 'px';
        tooltip.style.top = y + 'px';
        tooltip.classList.add('show');
    });

    // 點擊空白處關閉 tooltip
    document.addEventListener('click', e => {
        if (!e.target.closest('.chart-container') && !e.target.closest('.trade-tooltip')) {
            $('trade-tooltip').classList.remove('show');
        }
    });

    S.chartReady = true;
}

function updateChartData(kd, trades) {
    S.candleSeries.setData(kd.candles || []);
    S.ema20Series.setData(kd.ema20 || []);
    S.gkSeries.setData((kd.gk_pctile || []).map(g => ({
        time: g.time,
        value: g.value,
        color: g.value < 30 ? '#26a69a' : g.value < 50 ? '#f0b90b' : '#5b86e5',
    })));

    // Trade markers — 圓點標記（無文字）
    // 多單進場: 綠色 | 多單出場: 金色 | 空單進場: 紅色 | 空單出場: 紫色
    const markers = [];
    for (const t of trades) {
        const isLong = (t.direction || '').toUpperCase() === 'LONG';

        if (t.entry_ts > 0) {
            markers.push({
                time: t.entry_ts,
                position: isLong ? 'belowBar' : 'aboveBar',
                color: isLong ? '#26a69a' : '#ef5350',
                shape: 'circle',
                text: '',
            });
        }
        if (t.exit_ts > 0 && t.exit_type) {
            markers.push({
                time: t.exit_ts,
                position: isLong ? 'aboveBar' : 'belowBar',
                color: isLong ? '#f0b90b' : '#b39ddb',
                shape: 'circle',
                text: '',
            });
        }
    }
    markers.sort((a, b) => a.time - b.time);
    S.candleSeries.setMarkers(markers);

    // 自動顯示最新 K 線，右邊留 5 根空間
    S.mainChart.timeScale().applyOptions({ rightOffset: 5 });
    S.mainChart.timeScale().scrollToRealTime();
}

// 持倉水平價格線（已停用，會遮住 K 線）
function updatePositionLines(positions) {}

function scrollChartTo(ts) {
    switchTab('chart');
    setTimeout(() => {
        if (S.mainChart) {
            S.mainChart.timeScale().scrollToPosition(-5, false);
            // Find the bar closest to ts and center
            S.mainChart.timeScale().setVisibleRange({
                from: ts - 3600 * 48,
                to: ts + 3600 * 48,
            });
        }
    }, 300);
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 3: Trades Table
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function loadTrades() {
    try {
        const td = await api('/api/trades');
        S.trades = td.trades || [];
        renderFilters();
        renderTradesTable();
    } catch (e) {
        $('trades-table-wrap').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function renderFilters() {
    $('trade-filters').innerHTML = `
        <select onchange="S.filters.direction=this.value;renderTradesTable()">
            <option value="">全部方向 (All)</option>
            <option value="LONG">做多 (Long)</option>
            <option value="SHORT">做空 (Short)</option>
        </select>
        <select onchange="S.filters.sub=this.value;renderTradesTable()">
            <option value="">全部策略 (All)</option>
            <option value="L">L</option>
            <option value="S1">S1</option><option value="S2">S2</option>
            <option value="S3">S3</option><option value="S4">S4</option>
        </select>
        <select onchange="S.filters.win=this.value;renderTradesTable()">
            <option value="">勝負 (W/L)</option>
            <option value="WIN">贏 (Win)</option>
            <option value="LOSS">虧 (Loss)</option>
        </select>
        <select onchange="S.filters.exit=this.value;renderTradesTable()">
            <option value="">出場原因 (Exit)</option>
            <option value="Trail">追蹤止盈 (Trail)</option>
            <option value="SafeNet">安全網 (SafeNet)</option>
            <option value="EarlyStop">提前止損 (EarlyStop)</option>
            <option value="TP">止盈 (TP)</option>
            <option value="MaxHold">時間止損 (MaxHold)</option>
        </select>
    `;
}

function renderTradesTable() {
    let data = [...S.trades];

    // Filter
    const f = S.filters;
    if (f.direction) data = data.filter(t => t.direction === f.direction);
    if (f.sub) data = data.filter(t => t.sub_strategy === f.sub);
    if (f.win) data = data.filter(t => t.win_loss === f.win);
    if (f.exit) data = data.filter(t => t.exit_type === f.exit);

    // Sort
    data.sort((a, b) => {
        let va = a[S.sortCol], vb = b[S.sortCol];
        if (va == null) va = '';
        if (vb == null) vb = '';
        if (typeof va === 'string') return S.sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        return S.sortAsc ? va - vb : vb - va;
    });

    const sortIcon = (col) => S.sortCol === col ? (S.sortAsc ? ' ▲' : ' ▼') : '';

    let html = `<table><thead><tr>
        <th onclick="sortBy('trade_number')">#${sortIcon('trade_number')}</th>
        <th onclick="sortBy('entry_time_utc8')">進場時間 (Entry)${sortIcon('entry_time_utc8')}</th>
        <th onclick="sortBy('direction')">方向 (Dir)${sortIcon('direction')}</th>
        <th onclick="sortBy('sub_strategy')">策略 (Strat)${sortIcon('sub_strategy')}</th>
        <th onclick="sortBy('entry_price')">進場價 (Entry$)${sortIcon('entry_price')}</th>
        <th onclick="sortBy('exit_price')">出場價 (Exit$)${sortIcon('exit_price')}</th>
        <th onclick="sortBy('exit_type')">出場原因 (Exit)${sortIcon('exit_type')}</th>
        <th onclick="sortBy('net_pnl_usd')">損益 $ (PnL)${sortIcon('net_pnl_usd')}</th>
        <th onclick="sortBy('net_pnl_pct')">損益 % (PnL)${sortIcon('net_pnl_pct')}</th>
        <th onclick="sortBy('hold_bars')">持倉時數 (Hold h)${sortIcon('hold_bars')}</th>
        <th onclick="sortBy('max_adverse_excursion_pct')">最大逆行 (MAE%)${sortIcon('max_adverse_excursion_pct')}</th>
        <th onclick="sortBy('max_favorable_excursion_pct')">最大順行 (MFE%)${sortIcon('max_favorable_excursion_pct')}</th>
    </tr></thead><tbody>`;

    for (const t of data) {
        const dirCls = (t.direction||'') === 'LONG' ? 'dir-long' : 'dir-short';
        const pCls = pnlClass(t.net_pnl_usd);
        html += `<tr class="clickable" onclick="scrollChartTo(${t.entry_ts||0})">
            <td>${t.trade_number||''}</td>
            <td>${fmtTime(t.entry_time_utc8)}</td>
            <td class="${dirCls}">${t.direction||''}</td>
            <td>${t.sub_strategy||''}</td>
            <td>${t.entry_price ? '$'+Number(t.entry_price).toFixed(2) : '-'}</td>
            <td>${t.exit_price ? '$'+Number(t.exit_price).toFixed(2) : '-'}</td>
            <td>${t.exit_type||'-'}</td>
            <td class="${pCls}">${pnlStr(t.net_pnl_usd)}</td>
            <td class="${pCls}">${t.net_pnl_pct!=null ? t.net_pnl_pct.toFixed(1)+'%' : '-'}</td>
            <td>${t.hold_bars!=null ? t.hold_bars : '-'}</td>
            <td>${t.max_adverse_excursion_pct!=null ? t.max_adverse_excursion_pct.toFixed(1)+'%' : '-'}</td>
            <td>${t.max_favorable_excursion_pct!=null ? t.max_favorable_excursion_pct.toFixed(1)+'%' : '-'}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    $('trades-table-wrap').innerHTML = html;
}

function sortBy(col) {
    if (S.sortCol === col) S.sortAsc = !S.sortAsc;
    else { S.sortCol = col; S.sortAsc = true; }
    renderTradesTable();
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 4: Analytics
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function loadAnalytics() {
    try {
        const [an, dl] = await Promise.all([
            api('/api/analytics'),
            api('/api/daily'),
        ]);
        renderAnalyticsCards(an);
        renderEquityCurve(an.cumulative_equity || []);
        renderDailyChart(dl.daily || []);
        renderExitDist(an.exit_distribution || {});
        renderStratCompare(an.strategy_comparison || {});
    } catch (e) {
        $('analytics-cards').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function renderAnalyticsCards(an) {
    $('analytics-cards').innerHTML = `
        <div class="card">
            <div class="card-label">總損益 (Total P&L)</div>
            <div class="card-value ${an.total_pnl>0?'green':an.total_pnl<0?'red':''}">${pnlStr(an.total_pnl)}</div>
        </div>
        <div class="card">
            <div class="card-label">勝率 (Win Rate)</div>
            <div class="card-value">${an.win_rate.toFixed(1)}%</div>
        </div>
        <div class="card">
            <div class="card-label">盈利因子 (Profit Factor)</div>
            <div class="card-value">${an.profit_factor.toFixed(2)}</div>
        </div>
        <div class="card">
            <div class="card-label">平均持倉 (Avg Hold)</div>
            <div class="card-value">${an.avg_hold_bars.toFixed(1)}h</div>
        </div>
        <div class="card">
            <div class="card-label">總交易 (Total Trades)</div>
            <div class="card-value">${an.total_trades}</div>
        </div>
    `;
}

function renderEquityCurve(data) {
    const el = $('equity-chart');
    el.innerHTML = '';
    if (data.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth, height: 250,
        layout: { background: { color: '#1a1a2e' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        timeScale: { borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    });
    const series = chart.addAreaSeries({
        topColor: 'rgba(38,166,154,0.4)', bottomColor: 'rgba(38,166,154,0.0)',
        lineColor: '#26a69a', lineWidth: 2,
    });
    series.setData(data);
    S.equityChart = chart;
    new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
}

function renderDailyChart(daily) {
    const el = $('daily-chart');
    el.innerHTML = '';
    if (daily.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth, height: 250,
        layout: { background: { color: '#1a1a2e' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        timeScale: { borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    });
    const series = chart.addHistogramSeries();
    const data = daily.filter(d => d.date && d.net_pnl != null).map(d => ({
        time: d.date,
        value: d.net_pnl || 0,
        color: (d.net_pnl || 0) >= 0 ? '#26a69a' : '#ef5350',
    }));
    series.setData(data);
    S.dailyChart = chart;
    new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);
}

function renderExitDist(dist) {
    const el = $('exit-dist');
    const entries = Object.entries(dist);
    if (entries.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    const total = entries.reduce((s, [, v]) => s + v, 0);
    const colors = { Trail: '#26a69a', TP: '#5b86e5', MaxHold: '#f0b90b', SafeNet: '#ef5350', EarlyStop: '#ff9800' };

    let html = '';
    for (const [name, count] of entries.sort((a, b) => b[1] - a[1])) {
        const pct = (count / total * 100).toFixed(0);
        const c = colors[name] || '#888';
        html += `<div class="dist-bar-row">
            <span class="dist-bar-label">${name}</span>
            <div class="dist-bar-track">
                <div class="dist-bar-fill" style="width:${pct}%;background:${c}">${count} (${pct}%)</div>
            </div>
        </div>`;
    }
    el.innerHTML = html;
}

function renderStratCompare(comp) {
    const el = $('strat-compare');
    const keys = Object.keys(comp);
    if (keys.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    let html = `<table class="strat-table"><thead><tr>
        <th>策略 (Strategy)</th><th>筆數 (Trades)</th><th>勝率 (Win Rate)</th><th>總損益 (Total PnL)</th><th>均損益 (Avg PnL)</th>
    </tr></thead><tbody>`;
    for (const k of keys) {
        const s = comp[k];
        html += `<tr>
            <td><b>${k}</b></td>
            <td>${s.trades}</td>
            <td>${s.win_rate}%</td>
            <td class="${pnlClass(s.pnl)}">${pnlStr(s.pnl)}</td>
            <td class="${pnlClass(s.avg_pnl)}">${pnlStr(s.avg_pnl)}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    el.innerHTML = html;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Auto-refresh + Countdown Timer
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
const REFRESH_INTERVAL = 60; // seconds
let refreshCountdown = REFRESH_INTERVAL;

let lastRefreshTime = null;

function updateTimerDisplay() {
    const el = $('refresh-timer');
    if (el) {
        const timeStr = lastRefreshTime ? lastRefreshTime.toLocaleTimeString('zh-TW', {hour12: false}) : '--:--:--';
        el.innerHTML = `上次更新 ${timeStr} | 刷新 <span class="timer-sec">${refreshCountdown}s</span>`;
    }
}

function resetCountdown() {
    refreshCountdown = REFRESH_INTERVAL;
    updateTimerDisplay();
}

function onRefreshTick() {
    refreshCountdown--;
    if (refreshCountdown <= 0) {
        refreshCountdown = REFRESH_INTERVAL;
        lastRefreshTime = new Date();
        loadCurrentTab();
    }
    updateTimerDisplay();
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Init
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(function init() {
    // Restore mode
    document.querySelectorAll('.mode-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === S.mode));
    loadStatus();
    updateTimerDisplay();
    setInterval(onRefreshTick, 1000);
})();

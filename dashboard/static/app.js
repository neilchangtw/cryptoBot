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
    slopeSeries: null,        // SMA200 斜率（主圖左軸）
    slopeData: [],            // 給 autoscaleInfoProvider 引用
    fundingChart: null,       // 資金費率副圖（取代原本斜率副圖位置）
    fundingSeries: null,
    fundingData: [],
    chartRO: null,            // ResizeObserver（避免重複建立）
    volumeSeries: null,
    klineCache: null,        // 最近一次 /api/klines 結果（toggle 重繪用）
    tradeMarkers: [],         // 交易進出場 markers（合併到 candleSeries）
    candidateMarkers: [],     // 進場候選 markers（依 toggle 開關決定是否合併）
    chartLayers: {            // 顯示開關狀態（從 localStorage 讀）
        ema20: true, volume: true, candidates: true, positions: true, funding: true, gk: true, slope: true,
    },
    chartRange: '1m',         // 當前選取的範圍鈕
    chartUserZoomed: false,   // 使用者手動 zoom 後不再 auto-scroll
    equityChart: null,
    equitySeries: null,
    equityRO: null,
    dailyChart: null,
    dailySeries: null,
    dailyRO: null,
    sortCol: 'entry_ts',
    sortAsc: false,
    filters: { direction: '', sub: '', win: '', exit: '' },
    tradePage: 0,
    tradePageSize: 20,
    priceLines: [],
    tradeLines: [],   // 持倉期間進場價格線 series
    logFile: 'system',
    // Backtest
    btResult: null,
    btRunning: false,
    btInited: false,
    btEquityChart: null,
    btEquitySeries: null,
    btEquityRO: null,
    btMonthlyChart: null,
    btMonthlySeries: null,
    btMonthlyRO: null,
    btSortCol: 'no',
    btSortAsc: true,
    btPage: 0,
    btPageSize: 20,
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

function relativeTime(dtStr) {
    if (!dtStr) return '';
    try {
        const dt = new Date(dtStr.replace(' ', 'T'));
        const now = new Date();
        const diffMs = now - dt;
        const mins = Math.floor(diffMs / 60000);
        if (mins < 1) return '剛剛';
        if (mins < 60) return `${mins} 分鐘前`;
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return `${hrs} 小時前`;
        return `${Math.floor(hrs / 24)} 天前`;
    } catch { return ''; }
}

function setConnStatus(online) {
    const dot = $('conn-dot');
    if (dot) {
        dot.className = 'conn-dot ' + (online ? 'conn-online' : 'conn-offline');
        dot.title = online ? 'API 連線正常' : 'API 連線失敗';
        // 更新成功時閃一下
        if (online) {
            dot.classList.add('conn-flash');
            setTimeout(() => dot.classList.remove('conn-flash'), 600);
        }
    }
}

// 數值更新動畫：給所有 .card-value 加 pop 效果
function triggerValuePop() {
    document.querySelectorAll('.card-value').forEach(el => {
        el.classList.add('updated');
        setTimeout(() => el.classList.remove('updated'), 400);
    });
}

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
    // Mode 變動 → 重連 WS 帶新 mode
    closeStatusWS();
    if (S.tab === 'status' || S.tab === 'chart') openStatusWS();
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
    // status 與 chart 都需要 status WS（chart 浮動面板用）；其他 tab 關 WS 省資源
    if (tab === 'status' || tab === 'chart') openStatusWS();
    else closeStatusWS();
}

function loadCurrentTab() {
    if (S.tab === 'status') loadStatus();
    if (S.tab === 'chart') loadChart();
    if (S.tab === 'trades') loadTrades();
    if (S.tab === 'analytics') loadAnalytics();
    if (S.tab === 'logs') loadLogs();
    if (S.tab === 'backtest') loadBacktest();
    if (S.tab === 'guide') loadGuide();
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 1: Status
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function loadStatus() {
    try {
        const d = await api('/api/status');
        setConnStatus(true);
        renderStatusCards(d);
        renderGK(d);
        renderEntryConditions(d.entry_conditions, d.positions, d.cooldowns);
        renderRecentTrades(d.recent_trades || [], d.positions);
        renderBreakers(d.breakers);
        renderHealth(d.health);
        triggerValuePop();
    } catch (e) {
        setConnStatus(false);
        $('status-cards').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function renderStatusCards(d) {
    const pnl = d.today_pnl || 0;
    const pos = d.positions;
    const relTime = relativeTime(d.last_bar_time);
    const barTimeStr = d.last_bar_time ? `${d.last_bar_time}${relTime ? ' (' + relTime + ')' : ''}` : '-';

    // 持倉列表 + 均價計算
    let posDetail = '';
    let avgPriceHtml = '';
    if (pos.details.length > 0) {
        // 分 L/S 計算均價
        const longs = pos.details.filter(p => p.sub_strategy === 'L');
        const shorts = pos.details.filter(p => (p.sub_strategy||'').startsWith('S'));
        const avgPrice = (arr) => arr.length ? arr.reduce((s, p) => s + (p.entry_price||0), 0) / arr.length : 0;
        const parts = [];
        if (longs.length > 0) parts.push(`<span class="dir-long">L 均價 $${avgPrice(longs).toFixed(2)}</span>`);
        if (shorts.length > 0) parts.push(`<span class="dir-short">S 均價 $${avgPrice(shorts).toFixed(2)}</span>`);
        avgPriceHtml = `<div class="card-sub" style="margin-top:6px">${parts.join(' | ')}</div>`;

        posDetail = '<div class="pos-list">' + pos.details.map(p => {
            const dirCls = p.sub_strategy === 'L' ? 'dir-long' : 'dir-short';
            return `<div class="pos-row">
                <span class="${dirCls}">${p.sub_strategy||''}</span>
                <span>$${(p.entry_price||0).toFixed(2)}</span>
                <span>${p.bars_held||0}h</span>
            </div>`;
        }).join('') + '</div>';
    } else {
        posDetail = '<div class="card-sub">無持倉</div>';
    }

    $('status-cards').innerHTML = `
        <div class="card">
            <div class="card-label">帳戶餘額 (Balance)</div>
            <div class="card-value">$${(d.account_balance||0).toLocaleString('en',{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
            <div class="card-sub">Bar #${d.bar_counter} | ${barTimeStr}</div>
        </div>
        <div class="card">
            <div class="card-label">持倉 (Positions)</div>
            <div class="card-value ${pos.total>0?'gold':''}">
                ${pos.total > 0 ? `L:${pos.long_count} S:${pos.short_count}` : '空手'}
            </div>
            ${avgPriceHtml}
            ${posDetail}
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
            ${renderUnrealizedSummary(pos.details, d.last_close)}
        </div>
        ${renderRegimeCard(d.regime)}
    `;
}

function renderRegimeCard(rg) {
    if (!rg) return '';
    const labelColors = {
        'UP': 'var(--red)', 'DOWN': 'var(--green)', 'SIDE': 'var(--gold)',
        'MILD_UP': 'var(--green)', 'WARMUP': 'var(--text-dim)'
    };
    const clr = labelColors[rg.label] || 'var(--text)';
    const slopeStr = rg.slope_pct != null ? (rg.slope_pct >= 0 ? '+' : '') + rg.slope_pct.toFixed(2) + '%' : '-';
    // Badge 只在被擋時亮紅
    const lBadge = rg.block_l ? `<span class="regime-badge regime-block">L 擋</span>` : `<span class="regime-badge regime-ok">L ✓</span>`;
    const sBadge = rg.block_s ? `<span class="regime-badge regime-block">S 擋</span>` : `<span class="regime-badge regime-ok">S ✓</span>`;
    // 距離門檻解說
    let dist = '';
    if (rg.slope_pct != null) {
        const u = rg.dist_to_up, s = rg.dist_to_side;
        const uStr = u >= 0 ? `距 +4.5% 還有 ${u.toFixed(2)}%` : `已越 +4.5%（${(-u).toFixed(2)}%）`;
        const sStr = s >= 0 ? `離 ±1% 還有 ${s.toFixed(2)}%` : `進 ±1%（${(-s).toFixed(2)}%）`;
        dist = `<div class="card-sub" style="font-size:11px;color:var(--text-dim);margin-top:4px">${uStr}<br>${sStr}</div>`;
    }
    return `
        <div class="card">
            <div class="card-label">市場狀態 (Regime · SMA200 斜率)</div>
            <div class="card-value" style="color:${clr};font-size:1.5rem">${rg.label}</div>
            <div class="card-sub">斜率 ${slopeStr} | ${rg.desc}</div>
            <div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">${lBadge}${sBadge}</div>
            ${dist}
        </div>
    `;
}

function renderUnrealizedSummary(details, lastClose) {
    if (!details || details.length === 0) return '';
    let totalUnr = 0;
    let lines = [];
    let markPrice = null;

    for (const p of details) {
        const ep = p.entry_price || 0;
        if (ep <= 0) continue;
        const sub = p.sub_strategy || '';
        const unrPnl = p.unrealized_pnl;
        if (unrPnl == null) continue;
        totalUnr += unrPnl;
        if (p.mark_price) markPrice = p.mark_price;

        const mp = p.mark_price || lastClose || 0;
        let unrPct;
        if (sub === 'L') {
            unrPct = mp > 0 ? (mp - ep) / ep * 100 : 0;
        } else {
            unrPct = mp > 0 ? (ep - mp) / ep * 100 : 0;
        }
        const label = sub === 'L' ? 'L' : 'S';
        const cls = unrPnl >= 0 ? 'pnl-pos' : 'pnl-neg';
        lines.push(`<span class="${cls}">${label}: ${unrPct >= 0 ? '+' : ''}${unrPct.toFixed(2)}% ($${unrPnl >= 0 ? '+' : ''}${unrPnl.toFixed(2)})</span>`);
    }

    if (lines.length === 0) return '';

    const totalCls = totalUnr >= 0 ? 'pnl-pos' : 'pnl-neg';
    const mpNote = markPrice ? `<div style="margin-top:3px;color:var(--text-dim);font-size:11px">Mark: $${markPrice.toFixed(2)}</div>` : '';
    return `<div style="margin-top:8px;padding-top:6px;border-top:1px solid var(--border);font-size:12px">
        <div style="margin-bottom:2px">未實現損益 (Unrealized)</div>
        <div class="${totalCls}" style="font-size:16px;font-weight:700">$${totalUnr >= 0 ? '+' : ''}${totalUnr.toFixed(2)}</div>
        <div style="margin-top:2px;color:var(--text-dim)">${lines.join(' | ')}</div>
        ${mpNote}
    </div>`;
}

function renderGK(d) {
    const gk = d.gk_pctile;
    const gkS = d.gk_pctile_s;
    let color = '#8888a0', label = '無資料 (No Data)', barZone = '', valZone = '', panelZone = '';
    if (gk != null) {
        if (gk < 25) {
            color = 'var(--green)'; label = '觸發區 — 心跳加速 (Trigger Zone)';
            barZone = 'gk-zone-trigger'; valZone = 'gk-val-trigger'; panelZone = 'gk-panel-trigger';
        } else if (gk < 35) {
            color = '#06b6d4'; label = '待命區 — 蓄勢待發 (Ready Zone)';
            barZone = 'gk-zone-ready'; valZone = 'gk-val-ready'; panelZone = 'gk-panel-ready';
        } else if (gk < 50) {
            color = 'var(--gold)'; label = '蓄勢中 — 緩慢脈動 (Building Up)';
            barZone = 'gk-zone-building'; valZone = 'gk-val-building'; panelZone = 'gk-panel-building';
        } else {
            color = 'var(--text-dim)'; label = '正常波動 — 靜息 (Normal)';
            barZone = 'gk-zone-normal'; valZone = 'gk-val-normal'; panelZone = '';
        }
    }
    // L/S 雙 GK 值顯示
    const gkLStr = gk != null ? gk.toFixed(1) : '-';
    const gkSStr = gkS != null ? gkS.toFixed(1) : '-';
    let gkLColor = 'var(--text-dim)', gkSColor = 'var(--text-dim)';
    if (gk != null) gkLColor = gk < 25 ? 'var(--green)' : gk < 35 ? '#06b6d4' : gk < 50 ? 'var(--gold)' : 'var(--text-dim)';
    if (gkS != null) gkSColor = gkS < 25 ? 'var(--green)' : gkS < 35 ? '#06b6d4' : gkS < 50 ? 'var(--gold)' : 'var(--text-dim)';
    $('gk-section').innerHTML = `
        <div class="gk-panel ${panelZone}">
            <div class="gk-label">GK 壓縮指數 (Compression Index)</div>
            <div class="gk-dual-values">
                <div class="gk-dual-item">
                    <span class="gk-dual-label">L (5/20)</span>
                    <span class="gk-value ${valZone}" style="color:${gkLColor};font-size:1.6rem">${gkLStr}</span>
                </div>
                <div class="gk-dual-sep"></div>
                <div class="gk-dual-item">
                    <span class="gk-dual-label">S (10/30)</span>
                    <span class="gk-value" style="color:${gkSColor};font-size:1.6rem">${gkSStr}</span>
                </div>
            </div>
            <div class="gk-label">${label}</div>
            <div class="gk-bar-wrap">
                <div class="gk-bar"><div class="gk-bar-fill ${barZone}" style="width:${gk||0}%"></div></div>
                <div class="gk-bar-ticks">
                    <div class="gk-tick" style="left:25%;background:rgba(34,197,94,0.5)"></div>
                    <div class="gk-tick-label" style="left:25%" data-zone="trigger">25</div>
                    <div class="gk-tick" style="left:35%;background:rgba(6,182,212,0.5)"></div>
                    <div class="gk-tick-label" style="left:35%" data-zone="ready">35</div>
                    <div class="gk-tick" style="left:50%;background:rgba(234,179,8,0.4)"></div>
                    <div class="gk-tick-label" style="left:50%" data-zone="building">50</div>
                </div>
            </div>
        </div>
    `;
}

function isInRange(val, rangeStr) {
    const parts = rangeStr.split('–').map(s => parseInt(s.trim()));
    return val >= parts[0] && (parts[1] === 100 ? val <= 100 : val < parts[1]);
}

function sessionTimeStr(side) {
    const now = new Date();
    const h = now.getHours();
    const d = now.getDay();    // 0=Sun
    const blockH = [0, 1, 2, 12];
    // L: block Sat,Sun | S: block Mon,Sat,Sun
    const blockD = side === 'S' ? [0, 1, 6] : [0, 6];
    const inBlock = blockH.includes(h) || blockD.includes(d);
    const dayNames = ['日','一','二','三','四','五','六'];
    if (inBlock) {
        return `${dayNames[d]} ${h}:00（封鎖中）`;
    }
    return side === 'S' ? '二~五 3-11,13-23' : '二~六 3-11,13-23';
}

function renderEntryConditions(ec, positions, cooldowns) {
    const el = $('entry-conditions');
    if (!el) return;
    if (!ec) { el.innerHTML = ''; return; }

    function condRow(icon, pass, label, valueStr) {
        const cls = pass ? 'cond-pass' : 'cond-fail';
        const ic = pass ? '✓' : '✗';
        return `<div class="entry-cond">
            <span class="cond-icon ${cls}">${ic}</span>
            <span class="cond-label">${label}</span>
            <span class="cond-value">${valueStr || ''}</span>
        </div>`;
    }

    // 冷卻倒數 Badge（連虧 24 bar 優先，再來 L=6 bar / S=8 bar）
    function cooldownBadge(side) {
        if (!cooldowns) return '';
        const consec = cooldowns.consec_loss;
        if (consec && consec.active) {
            return `<div class="cd-badge cd-alert" title="連虧 4 筆觸發 24 bar 冷卻，L+S 皆停">
                <div class="cd-head">
                    <span class="cd-icon">🚫</span>
                    <span class="cd-label">連虧冷卻中（L+S 皆停）</span>
                    <span class="cd-remain">剩 ${consec.remaining} / ${consec.total} bar（約 ${consec.remaining}h）</span>
                </div>
                <div class="cd-track"><div class="cd-fill" style="width:${Math.round((consec.total - consec.remaining) / consec.total * 100)}%;background:var(--red)"></div></div>
            </div>`;
        }
        const cd = cooldowns[side];
        if (!cd || !cd.active) return '';
        const pct = Math.round((cd.passed / cd.total) * 100);
        return `<div class="cd-badge" title="出場後冷卻中，避免反覆進出同一 setup">
            <div class="cd-head">
                <span class="cd-icon">⏱</span>
                <span class="cd-label">進場冷卻中</span>
                <span class="cd-remain">剩 ${cd.remaining} / ${cd.total} bar（約 ${cd.remaining}h）</span>
            </div>
            <div class="cd-track"><div class="cd-fill" style="width:${pct}%"></div></div>
        </div>`;
    }

    // L 條件面板
    const lc = ec.L ? ec.L.conditions : {};
    const lPassed = ec.L ? ec.L.passed : 0;
    const lTotal = ec.L ? ec.L.total : 4;
    const lPct = Math.round(lPassed / lTotal * 100);
    const lColor = lPct >= 100 ? 'var(--green)' : lPct >= 50 ? 'var(--gold)' : 'var(--red)';

    let lHtml = `<div class="entry-panel">
        <div class="entry-panel-title">
            <span>L 做多進場條件</span>
            <span class="entry-progress" style="color:${lColor}">${lPassed}/${lTotal}</span>
        </div>`;
    lHtml += cooldownBadge('L');
    if (lc.gk) lHtml += condRow('', lc.gk.pass, 'GK < 25（壓縮）', lc.gk.value != null ? lc.gk.value.toFixed(1) : '-');
    if (lc.breakout) lHtml += condRow('', lc.breakout.pass, '向上突破 15bar', '');
    if (lc.session) lHtml += condRow('', lc.session.pass, '時段允許', sessionTimeStr('L'));
    if (lc.regime) lHtml += condRow('', lc.regime.pass, '非強多頭 (slope≤+4.5%)', lc.regime.value != null ? (lc.regime.value * 100).toFixed(2) + '%' : '-');
    lHtml += `<div class="entry-bar"><div class="entry-bar-fill" style="width:${lPct}%;background:${lColor}"></div></div>`;
    lHtml += '</div>';

    // S 條件面板
    const sc = ec.S ? ec.S.conditions : {};
    const sPassed = ec.S ? ec.S.passed : 0;
    const sTotal = ec.S ? ec.S.total : 4;
    const sPct = Math.round(sPassed / sTotal * 100);
    const sColor = sPct >= 100 ? 'var(--green)' : sPct >= 50 ? 'var(--gold)' : 'var(--red)';

    let sHtml = `<div class="entry-panel">
        <div class="entry-panel-title">
            <span>S 做空進場條件</span>
            <span class="entry-progress" style="color:${sColor}">${sPassed}/${sTotal}</span>
        </div>`;
    sHtml += cooldownBadge('S');
    if (sc.gk) sHtml += condRow('', sc.gk.pass, 'GK < 35（壓縮）', sc.gk.value != null ? sc.gk.value.toFixed(1) : '-');
    if (sc.breakout) sHtml += condRow('', sc.breakout.pass, '向下突破 15bar', '');
    if (sc.session) sHtml += condRow('', sc.session.pass, '時段允許', sessionTimeStr('S'));
    if (sc.regime) sHtml += condRow('', sc.regime.pass, '非橫盤 (|slope|≥1%)', sc.regime.value != null ? (sc.regime.value * 100).toFixed(2) + '%' : '-');
    sHtml += `<div class="entry-bar"><div class="entry-bar-fill" style="width:${sPct}%;background:${sColor}"></div></div>`;
    sHtml += '</div>';

    // GK 指數說明（原有的 explain）
    const gkHtml = renderGKExplainPanel(ec.L && ec.L.conditions.gk ? ec.L.conditions.gk.value : null);

    el.innerHTML = `<div class="entry-grid">${lHtml}${sHtml}${gkHtml}</div>`;
}

function renderGKExplainPanel(gk) {
    const rangeItems = [
        { range: '0 – 25', label: '觸發區', desc: 'L 進場 — 心跳急促', cls: 'gk-range-green' },
        { range: '25 – 35', label: '待命區', desc: 'S 進場 — 脈搏穩定', cls: 'gk-range-cyan' },
        { range: '35 – 50', label: '蓄勢區', desc: '尚未壓縮 — 緩慢脈動', cls: 'gk-range-gold' },
        { range: '50 – 70', label: '正常區', desc: '一般波動 — 靜息', cls: 'gk-range-dim' },
        { range: '70 – 100', label: '擴張區', desc: '高波動', cls: 'gk-range-blue' },
    ];
    const rows = rangeItems.map(r => `
        <div class="gk-range-item ${r.cls}${gk != null && isInRange(gk, r.range) ? ' gk-range-active' : ''}">
            <span class="gk-range-num">${r.range}</span>
            <span class="gk-range-label">${r.label}</span>
            <span class="gk-range-desc">${r.desc}</span>
        </div>`).join('');
    return `<div class="entry-panel">
        <div class="entry-panel-title"><span>GK 指數區間</span></div>
        ${rows}
    </div>`;
}

function exitBarHtml(label, pct, clr, desc) {
    // pct: 0~100, 越高=越接近平倉
    const rounded = Math.round(pct);
    return `<div class="exit-item">
        <span>${label}</span>
        <span style="color:var(--text-dim)">${desc}</span>
        <div class="exit-bar-track" data-tooltip="${rounded}% 接近觸發"><div class="exit-bar-fill" style="width:${pct}%;background:${clr}"></div></div>
    </div>`;
}

function renderExitProgress(ep, sub) {
    if (!ep) return '';
    let items = '';
    const unr = ep.unrealized_pct;
    const unrCls = unr >= 0 ? 'pnl-pos' : 'pnl-neg';
    items += `<div class="exit-item"><span>未實現: <b class="${unrCls}">${unr >= 0 ? '+' : ''}${unr}%</b></span></div>`;

    if (sub === 'L') {
        // L: SafeNet -3.5%
        const sn = ep.safenet;
        if (sn) {
            const lossAmt = Math.max(0, -sn.current);
            const pct = Math.min(100, lossAmt / 3.5 * 100);
            const clr = pct > 70 ? 'var(--red)' : pct > 40 ? 'var(--gold)' : 'var(--green)';
            const safeLabel = pct < 30 ? '安全' : pct < 70 ? '注意' : '危險';
            items += exitBarHtml('安全網 -3.5%', pct, clr, `已用 ${lossAmt.toFixed(1)}% / 3.5%（${safeLabel}）`);
        }
        // L: TP +3.5%
        const tp = ep.tp;
        if (tp) {
            const profit = Math.max(0, tp.current);
            const pct = Math.min(100, profit / 3.5 * 100);
            const clr = pct > 70 ? 'var(--green)' : pct > 40 ? 'var(--gold)' : 'var(--text-dim)';
            const label = pct >= 100 ? '即將止盈！' : `已賺 ${profit.toFixed(2)}% / 3.5%`;
            items += exitBarHtml('止盈 +3.5%', pct, clr, label);
        }
        // L: MFE Trailing（V14 新增）
        const mft = ep.mfe_trail;
        if (mft) {
            const mfe = mft.running_mfe;
            const armed = mfe >= mft.act;
            const pct = Math.min(100, mfe / mft.act * 100);
            const clr = armed ? 'var(--green)' : 'var(--text-dim)';
            const label = armed ? `MFE ${mfe.toFixed(2)}% 已啟動（回吐 ${mft.dd}% 出場）` : `MFE ${mfe.toFixed(2)}% / ${mft.act}%`;
            items += exitBarHtml('MFE 追蹤', pct, clr, label);
        }
        // L: MaxHold (5 or 6 bar)
        const mh = ep.max_hold;
        if (mh) {
            const th = mh.threshold;
            const pct = Math.min(100, mh.bars_held / th * 100);
            const clr = pct > 80 ? 'var(--red)' : pct > 50 ? 'var(--gold)' : 'var(--text-dim)';
            items += exitBarHtml(`時間止損 ${th}h`, pct, clr, `${mh.bars_held}/${th}h（剩 ${mh.remaining}h）`);
        }
    } else {
        // S: TP -2.0%
        const tp = ep.tp;
        if (tp) {
            const profit = Math.max(0, tp.current);
            const pct = Math.min(100, profit / 2.0 * 100);
            const clr = pct > 70 ? 'var(--green)' : pct > 40 ? 'var(--gold)' : 'var(--text-dim)';
            const label = pct >= 100 ? '即將止盈！' : `已賺 ${profit.toFixed(2)}% / 2.0%`;
            items += exitBarHtml('止盈 -2.0%', pct, clr, label);
        }
        // S: SafeNet +4.0%
        const sn = ep.safenet;
        if (sn) {
            const lossAmt = Math.max(0, sn.current);
            const pct = Math.min(100, lossAmt / 4.0 * 100);
            const clr = pct > 70 ? 'var(--red)' : pct > 40 ? 'var(--gold)' : 'var(--green)';
            const safeLabel = pct < 30 ? '安全' : pct < 70 ? '注意' : '危險';
            items += exitBarHtml('安全網 +4.0%', pct, clr, `已虧 ${lossAmt.toFixed(1)}% / 4.0%（${safeLabel}）`);
        }
        // S: MaxHold 10 bar
        const mh = ep.max_hold;
        if (mh) {
            const pct = Math.min(100, mh.bars_held / 10 * 100);
            const clr = pct > 80 ? 'var(--red)' : pct > 50 ? 'var(--gold)' : 'var(--text-dim)';
            items += exitBarHtml('時間止損 10h', pct, clr, `${mh.bars_held}/10h（剩 ${mh.remaining}h）`);
        }
    }
    return `<div class="exit-progress">${items}</div>`;
}

function renderRecentTrades(trades, positions) {
    const el = $('recent-section');
    if (!el) return;

    // 先渲染持倉出場進度
    const posDetails = positions ? positions.details || [] : [];
    let exitHtml = '';
    if (posDetails.length > 0) {
        const posWithExit = posDetails.filter(p => p.exit_progress);
        if (posWithExit.length > 0) {
            let posRows = '';
            for (const p of posWithExit) {
                const dirCls = p.sub_strategy === 'L' ? 'dir-long' : 'dir-short';
                posRows += `<div style="margin-bottom:8px">
                    <span class="${dirCls}" style="font-weight:600">${p.sub_strategy}</span>
                    <span style="color:var(--text-dim)">@ $${(p.entry_price||0).toFixed(2)} | ${p.bars_held||0}h</span>
                    ${renderExitProgress(p.exit_progress, p.sub_strategy)}
                </div>`;
            }
            exitHtml = `<div class="recent-section" style="margin-bottom:12px">
                <div class="recent-title">持倉平倉進度 (Exit Progress)</div>
                ${posRows}
            </div>`;
        }
    }

    // 再渲染最近交易
    let tradeHtml = '';
    if (!trades || trades.length === 0) {
        tradeHtml = `<div class="recent-section"><div class="recent-title">最近交易 (Recent Trades)</div><div style="color:var(--text-dim);font-size:13px">尚無交易記錄</div></div>`;
    } else {
        let rows = '';
        for (const t of trades) {
            const dirCls = (t.direction||'') === 'LONG' ? 'dir-long' : 'dir-short';
            const dirLabel = (t.direction||'') === 'LONG' ? '多' : '空';
            const pCls = pnlClass(t.net_pnl_usd);
            rows += `<tr>
                <td>${t.trade_number||''}</td>
                <td class="${dirCls}">${dirLabel} ${t.sub_strategy||''}</td>
                <td>${fmtTime(t.entry_time_utc8)}</td>
                <td>${t.exit_type||'<span style="color:var(--gold)">持倉中</span>'}</td>
                <td class="${pCls}">${t.net_pnl_usd != null ? pnlStr(t.net_pnl_usd) : '-'}</td>
                <td>${t.hold_bars != null ? t.hold_bars + 'h' : '-'}</td>
            </tr>`;
        }
        tradeHtml = `<div class="recent-section">
            <div class="recent-title">最近交易 (Recent Trades)</div>
            <table class="recent-table"><thead><tr>
                <th>#</th><th>方向</th><th>進場時間</th><th>出場原因</th><th>損益</th><th>持倉</th>
            </tr></thead><tbody>${rows}</tbody></table>
        </div>`;
    }

    el.innerHTML = exitHtml + tradeHtml;
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
    'SafeNet trigger rate': '安全網（L -3.5% / S +4.0%）觸發比率，低於 15% 為正常',
    '24-48h hold WR': '長持倉交易勝率（L 6h / S 10h MaxHold）',
    'Avg hold time': '平均每筆交易持倉時間',
    'MFE/MAE ratio': '最大順行 vs 最大逆行比值，>1.5 表示趨勢捕捉良好',
    'Total PnL': '期間內已平倉交易的總損益',
    'Profit Factor': '總獲利 / 總虧損，≥1.5 為健康',
    'Max Drawdown': '期間內最大回撤金額',
};
const HEALTH_STATUS_MAP = {
    'OK': '正常 (OK)',
    'WARNING': '注意 (Warning)',
    'ALERT': '警報 (Alert)',
};

function renderBreakers(bk) {
    const el = $('breakers-section');
    if (!el) return;
    if (!bk) { el.innerHTML = ''; return; }

    // 依使用率決定顏色：<40% 綠 / 40-70% 黃 / >=70% 橘 / 100% 紅
    function barColor(pct, triggered) {
        if (triggered) return 'var(--red)';
        if (pct >= 70) return '#ff9800';
        if (pct >= 40) return 'var(--gold)';
        return 'var(--green)';
    }
    function bar(label, used, cap, unit, pct, triggered, note) {
        const clr = barColor(pct, triggered);
        const status = triggered ? '<span style="color:var(--red);font-weight:600">🚫 已觸發</span>'
                     : pct >= 70 ? '<span style="color:#ff9800">⚠ 接近</span>'
                     : '<span style="color:var(--green)">✓ 安全</span>';
        return `<div class="bk-item">
            <div class="bk-head">
                <span class="bk-label">${label}</span>
                <span class="bk-status">${status}</span>
            </div>
            <div class="bk-bar-track"><div class="bk-bar-fill" style="width:${pct}%;background:${clr}"></div></div>
            <div class="bk-meta">${used}${unit} / ${cap}${unit}（${pct.toFixed(0)}%）${note ? '｜' + note : ''}</div>
        </div>`;
    }

    const d = bk.daily, mL = bk.monthly_l, mS = bk.monthly_s, cs = bk.consec;
    const pausedBadge = bk.paused ? '<span class="bk-paused">⏸ 已暫停</span>' : '';

    let html = `<div class="breakers-wrap">
        <div class="breakers-title">
            <span>風控熔斷 (Circuit Breakers)</span>${pausedBadge}
        </div>
        <div class="breakers-grid">`;
    html += bar('日虧 -$200', d.loss_used, 200, '$', d.used_pct, d.triggered, `今日 PnL ${d.pnl>=0?'+':''}$${d.pnl}`);
    html += bar('月虧 L -$75', mL.loss_used, 75, '$', mL.used_pct, mL.triggered, `PnL ${mL.pnl>=0?'+':''}$${mL.pnl}`);
    html += bar('月虧 S -$150', mS.loss_used, 150, '$', mS.used_pct, mS.triggered, `PnL ${mS.pnl>=0?'+':''}$${mS.pnl}`);
    html += bar('L 月度進場額度', mL.entries, mL.entry_cap, '筆', mL.entry_pct, mL.entries >= mL.entry_cap, '');
    html += bar('S 月度進場額度', mS.entries, mS.entry_cap, '筆', mS.entry_pct, mS.entries >= mS.entry_cap, '');
    const cdNote = cs.cooldown_bars_remain > 0 ? `冷卻剩 ${cs.cooldown_bars_remain} bar` : '';
    html += bar('連虧計數', cs.value, 4, '筆', cs.used_pct, cs.triggered, cdNote);
    html += '</div></div>';
    el.innerHTML = html;
}

function renderHealth(h) {
    // 改為與「風控熔斷」一致的進度條樣式（bk-* 系列）
    // 進度條語意：越接近觸發（ALERT）條越長越紅，與風控熔斷的「使用率」概念對齊
    //   OK      → 30% 綠   （安全）
    //   WARNING → 65% 橘黃 （接近）
    //   ALERT   → 100% 紅  （已觸發）
    if (!h || !h.checks) {
        $('health-section').innerHTML = '';
        return;
    }
    const overall = (h.overall || 'UNKNOWN').toUpperCase();
    const overallLabel = overall === 'NORMAL' ? '正常' : overall === 'WARNING' ? '警告' : overall === 'PAUSE' ? '暫停' : overall;
    const overallCls = overall === 'NORMAL' ? 'bk-ok' : overall === 'WARNING' ? 'bk-warn' : 'bk-alert';
    const overallBadge = `<span class="bk-paused ${overallCls}">${overallLabel}</span>`;

    // 單筆檢查渲染：複用風控熔斷的 .bk-item / .bk-bar-track / .bk-bar-fill / .bk-meta
    function healthBar(c) {
        const st = (c.status || '').toUpperCase();
        // 狀態 → 顏色 + 填充比例 + 中文狀態標籤
        let pct, clr, statusHtml;
        if (st === 'OK') {
            pct = 30; clr = 'var(--green)';
            statusHtml = '<span style="color:var(--green)">✓ 正常</span>';
        } else if (st === 'WARNING') {
            pct = 65; clr = '#ff9800';
            statusHtml = '<span style="color:#ff9800">⚠ 注意</span>';
        } else if (st === 'ALERT') {
            pct = 100; clr = 'var(--red)';
            statusHtml = '<span style="color:var(--red);font-weight:600">🚫 警報</span>';
        } else {
            pct = 0; clr = 'var(--text-dim)';
            statusHtml = '<span style="color:var(--text-dim)">未知</span>';
        }

        const name = HEALTH_NAME_MAP[c.name] || c.name || '';
        const desc = HEALTH_DESC_MAP[c.name] || c.detail || '';
        // meta 行：當前值 + 門檻 + detail，用全形分隔線對齊風控熔斷
        const val = c.value != null ? c.value : '-';
        const thr = c.threshold ? `門檻 ${c.threshold}` : '';
        const metaParts = [`當前 ${val}`, thr, desc].filter(x => x);
        const meta = metaParts.join('｜');

        return `<div class="bk-item">
            <div class="bk-head">
                <span class="bk-label">${name}</span>
                <span class="bk-status">${statusHtml}</span>
            </div>
            <div class="bk-bar-track"><div class="bk-bar-fill" style="width:${pct}%;background:${clr}"></div></div>
            <div class="bk-meta">${meta}</div>
        </div>`;
    }

    let html = `<div class="breakers-wrap">
        <div class="breakers-title">
            <span>策略健康度 (Health Check)</span>${overallBadge}
        </div>
        <div class="breakers-grid">`;
    for (const c of (h.checks || [])) {
        html += healthBar(c);
    }
    html += '</div></div>';
    $('health-section').innerHTML = html;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 2: Chart
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function loadChart() {
    try {
        const [kd, td, sd] = await Promise.all([
            fetch('/api/klines?limit=1500').then(r => r.json()),
            api('/api/trades'),
            api('/api/status').catch(() => null),
        ]);
        setConnStatus(true);
        if (!S.chartReady) initCharts();
        S.trades = td.trades || [];
        updateChartData(kd, S.trades);
        if (sd) updateChartStatusPanel(sd);
    } catch (e) {
        setConnStatus(false);
        $('main-chart').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function initCharts() {
    const mc = $('main-chart');
    const gc = $('gk-chart');
    const sc = $('slope-chart');
    mc.innerHTML = '';
    gc.innerHTML = '';
    if (sc) sc.innerHTML = '';

    // 載入 toggle 狀態
    try {
        const saved = JSON.parse(localStorage.getItem('cb_chart_layers') || 'null');
        if (saved) Object.assign(S.chartLayers, saved);
    } catch (e) {}
    document.querySelectorAll('.chart-toggle input[data-layer]').forEach(inp => {
        inp.checked = !!S.chartLayers[inp.dataset.layer];
    });

    const baseOpts = {
        layout: { background: { color: '#000000' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#2B2B43', minimumWidth: 80 },
    };

    // 主圖（左軸給資金費率用，預設顯示）
    S.mainChart = LightweightCharts.createChart(mc, {
        ...baseOpts, width: mc.clientWidth, height: mc.clientHeight || 480,
        timeScale: { visible: false, borderColor: '#2B2B43' },
        leftPriceScale: { visible: false, borderColor: '#2B2B43' },
    });
    S.candleSeries = S.mainChart.addCandlestickSeries({
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });
    // 成交量 histogram（疊在主圖下方 25% 區域）
    S.volumeSeries = S.mainChart.addHistogramSeries({
        priceFormat: { type: 'volume' },
        priceScaleId: 'vol',
        title: 'Volume',
    });
    S.mainChart.priceScale('vol').applyOptions({
        scaleMargins: { top: 0.78, bottom: 0 },
        visible: false,
    });
    S.ema20Series = S.mainChart.addLineSeries({ color: '#f0b90b', lineWidth: 2, title: 'EMA20' });
    // SMA200 斜率（左側軸；含 L/S block 門檻線；預設顯示）
    S.slopeSeries = S.mainChart.addLineSeries({
        color: '#ab47bc', lineWidth: 2, title: 'SMA200 斜率 %',
        priceScaleId: 'left',
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        autoscaleInfoProvider: (orig) => {
            const data = S.slopeData || [];
            if (!data.length) return orig();
            let mn = Infinity, mx = -Infinity;
            for (const p of data) {
                if (p.value < mn) mn = p.value;
                if (p.value > mx) mx = p.value;
            }
            // 確保門檻線（+4.5 / ±1）也在視窗內
            mn = Math.min(mn, -1.5);
            mx = Math.max(mx, 5.0);
            const span = Math.max(mx - mn, 1.5);
            const pad = span * 0.10;
            return { priceRange: { minValue: mn - pad, maxValue: mx + pad } };
        },
    });
    S.slopeSeries.createPriceLine({ price: 4.5, color: '#ef5350', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'L block' });
    S.slopeSeries.createPriceLine({ price: 1.0, color: '#f0b90b', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'S block ↑' });
    S.slopeSeries.createPriceLine({ price: -1.0, color: '#f0b90b', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'S block ↓' });
    S.slopeSeries.createPriceLine({ price: 0, color: '#555', lineWidth: 1, lineStyle: 3, axisLabelVisible: false });
    // L/S 進場候選 bar markers：合併到 candleSeries（避免獨立 series 拉壞 price scale + markers 擠在單點）

    // GK 副圖
    S.gkChart = LightweightCharts.createChart(gc, {
        ...baseOpts, width: gc.clientWidth, height: gc.clientHeight || 130,
        timeScale: { visible: false, borderColor: '#2B2B43' },
    });
    S.gkSeries = S.gkChart.addHistogramSeries({ color: '#5b86e5', title: 'GK 百分位 (Pctile)' });

    // 資金費率副圖（取代原本的斜率副圖位置）
    if (sc) {
        S.fundingChart = LightweightCharts.createChart(sc, {
            ...baseOpts, width: sc.clientWidth, height: sc.clientHeight || 130,
            timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#2B2B43' },
            rightPriceScale: {
                borderColor: '#2B2B43', minimumWidth: 80,
                scaleMargins: { top: 0.15, bottom: 0.15 },
            },
        });
        S.fundingSeries = S.fundingChart.addHistogramSeries({
            base: 0,
            priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
            title: '資金費率%',
            autoscaleInfoProvider: (orig) => {
                const data = S.fundingData || [];
                if (!data.length) return orig();
                let mn = Infinity, mx = -Infinity;
                for (const p of data) {
                    if (p.value < mn) mn = p.value;
                    if (p.value > mx) mx = p.value;
                }
                // 強制 0 軸對稱可見
                mn = Math.min(mn, 0);
                mx = Math.max(mx, 0);
                const span = Math.max(mx - mn, 0.02);
                const pad = span * 0.20;
                return { priceRange: { minValue: mn - pad, maxValue: mx + pad } };
            },
        });
        S.fundingSeries.createPriceLine({ price: 0, color: '#555', lineWidth: 1, lineStyle: 3, axisLabelVisible: false });
    }

    // Sync time scales + 標記使用者手動 zoom（暫停 auto-scroll）
    let syncing = false;
    const syncFrom = (from, targets) => {
        from.timeScale().subscribeVisibleLogicalRangeChange(range => {
            if (syncing || !range) return;
            syncing = true;
            for (const t of targets) t.timeScale().setVisibleLogicalRange(range);
            syncing = false;
            // 偵測使用者主動操作（drag/wheel）→ 暫停 auto-scroll
            if (S._allowZoomDetect) S.chartUserZoomed = true;
        });
    };
    const charts = [S.mainChart, S.gkChart];
    if (S.fundingChart) charts.push(S.fundingChart);
    for (const c of charts) {
        syncFrom(c, charts.filter(x => x !== c));
    }

    // Resize：同時觀察三個容器，跳過 clientHeight=0（tab 切換 display:none 時）
    // 避免「切回圖表分頁高度被退化成 fallback」造成 layout 跑掉
    if (S.chartRO) { try { S.chartRO.disconnect(); } catch (e) {} }
    S.chartRO = new ResizeObserver(() => {
        const mh = mc.clientHeight, mw = mc.clientWidth;
        if (mh > 0 && mw > 0) S.mainChart.applyOptions({ width: mw, height: mh });
        const gh = gc.clientHeight, gw = gc.clientWidth;
        if (gh > 0 && gw > 0) S.gkChart.applyOptions({ width: gw, height: gh });
        if (S.fundingChart && sc) {
            const sh = sc.clientHeight, sw = sc.clientWidth;
            if (sh > 0 && sw > 0) S.fundingChart.applyOptions({ width: sw, height: sh });
        }
    });
    S.chartRO.observe(mc);
    S.chartRO.observe(gc);
    if (sc) S.chartRO.observe(sc);

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
        // 信號指標
        const sigLines = [];
        if (t.gk_pctile_at_entry != null) sigLines.push(`GK: ${Number(t.gk_pctile_at_entry).toFixed(1)}`);
        if (t.ema20_at_entry != null) sigLines.push(`EMA20: $${Number(t.ema20_at_entry).toFixed(2)}`);
        if (t.ema20_distance_pct != null) sigLines.push(`EMA距離: ${Number(t.ema20_distance_pct).toFixed(2)}%`);
        if (t.breakout_strength_pct != null) sigLines.push(`突破力: ${Number(t.breakout_strength_pct).toFixed(2)}%`);
        const sigHtml = sigLines.length > 0
            ? `<div style="margin-top:4px;padding-top:4px;border-top:1px solid var(--border);color:var(--text-dim);font-size:11px">
                <div style="color:var(--gold);font-weight:600;margin-bottom:2px">進場信號</div>
                ${sigLines.join(' | ')}
               </div>` : '';
        tooltip.innerHTML = `
            <div style="margin-bottom:6px"><b class="${dirCls}">${dirLabel}</b> <span style="color:var(--text-dim)">${t.sub_strategy||''}</span></div>
            <div>進場：${fmtTime(t.entry_time_utc8)} @ $${Number(t.entry_price||0).toFixed(2)}</div>
            ${t.exit_price ? `<div>出場：${fmtTime(t.exit_time_utc8)} @ $${Number(t.exit_price).toFixed(2)}</div>` : '<div>狀態：<span style="color:var(--gold)">持倉中</span></div>'}
            ${t.exit_type ? `<div>原因：${t.exit_type}</div>` : ''}
            ${t.net_pnl_usd != null ? `<div>損益：<span class="${pnlClass(t.net_pnl_usd)}">${pnlStr(t.net_pnl_usd)} (${t.net_pnl_pct!=null?t.net_pnl_pct.toFixed(1)+'%':''})</span></div>` : ''}
            ${t.hold_bars != null ? `<div>持倉：${t.hold_bars}h</div>` : ''}
            ${sigHtml}
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

    // ── Crosshair 即時讀數（OHLC + GK + slope + EMA20 距離）──
    const readout = $('chart-readout');
    if (readout) {
        S.mainChart.subscribeCrosshairMove(param => {
            if (!param.time || !S.klineCache) { readout.classList.remove('show'); return; }
            const cache = S.klineCache;
            const candle = (cache.candles || []).find(c => c.time === param.time);
            if (!candle) { readout.classList.remove('show'); return; }
            const ema = (cache.ema20 || []).find(e => e.time === param.time);
            const gk  = (cache.gk_pctile || []).find(g => g.time === param.time);
            const gks = (cache.gk_pctile_s || []).find(g => g.time === param.time);
            const slp = (cache.sma_slope || []).find(s => s.time === param.time);
            const vol = (cache.volume || []).find(v => v.time === param.time);
            const fr  = (cache.funding_rate || []).find(f => f.time === param.time);
            const dirCls = candle.close >= candle.open ? 'pnl-pos' : 'pnl-neg';
            const emaDist = (ema && ema.value > 0) ? ((candle.close - ema.value) / ema.value * 100).toFixed(2) : null;
            const fmtT = (ts) => {
                const d = new Date(ts * 1000);
                return d.toISOString().slice(5, 16).replace('T', ' ');
            };
            readout.innerHTML = `
                <div class="ro-row"><span class="ro-label">時間</span><span>${fmtT(candle.time)}</span></div>
                <div class="ro-row"><span class="ro-label">開</span><span>${candle.open.toFixed(2)}</span></div>
                <div class="ro-row"><span class="ro-label">高</span><span>${candle.high.toFixed(2)}</span></div>
                <div class="ro-row"><span class="ro-label">低</span><span>${candle.low.toFixed(2)}</span></div>
                <div class="ro-row"><span class="ro-label">收</span><span class="${dirCls}">${candle.close.toFixed(2)}</span></div>
                ${vol != null ? `<div class="ro-row"><span class="ro-label">成交量</span><span>${vol.value.toFixed(0)}</span></div>` : ''}
                ${ema ? `<div class="ro-row"><span class="ro-label">EMA20</span><span>${ema.value.toFixed(2)} (${emaDist!=null?(emaDist>=0?'+':'')+emaDist+'%':'-'})</span></div>` : ''}
                ${gk != null ? `<div class="ro-row"><span class="ro-label">GK 多</span><span>${gk.value.toFixed(1)}</span></div>` : ''}
                ${gks != null ? `<div class="ro-row"><span class="ro-label">GK 空</span><span>${gks.value.toFixed(1)}</span></div>` : ''}
                ${slp != null ? `<div class="ro-row"><span class="ro-label">斜率</span><span>${(slp.value>=0?'+':'')+slp.value.toFixed(2)}%</span></div>` : ''}
                ${fr  != null ? `<div class="ro-row"><span class="ro-label">資金費</span><span class="${fr.value>=0?'pnl-pos':'pnl-neg'}">${(fr.value>=0?'+':'')+fr.value.toFixed(4)}%</span></div>` : ''}
            `;
            readout.classList.add('show');
        });
    }

    // ── 範圍快速鈕 ──
    document.querySelectorAll('.range-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            S.chartRange = btn.dataset.range;
            S.chartUserZoomed = false;  // 主動點 = 重置 zoom 鎖
            applyChartRange();
        });
    });

    // ── 顯示開關 ──
    document.querySelectorAll('.chart-toggle input[data-layer]').forEach(inp => {
        inp.addEventListener('change', () => {
            S.chartLayers[inp.dataset.layer] = inp.checked;
            try { localStorage.setItem('cb_chart_layers', JSON.stringify(S.chartLayers)); } catch (e) {}
            applyChartLayers();
        });
    });

    applyChartLayers();
    // 初始化完成後才開始偵測 user zoom（避免初次 setData 被誤判）
    setTimeout(() => { S._allowZoomDetect = true; }, 500);

    S.chartReady = true;
}

// 套用顯示開關（hide/show series + 副圖容器）
function applyChartLayers() {
    if (!S.chartReady && !S.mainChart) return;
    const L = S.chartLayers;
    if (S.ema20Series) S.ema20Series.applyOptions({ visible: L.ema20 });
    if (S.volumeSeries) S.volumeSeries.applyOptions({ visible: L.volume });
    // 斜率（主圖左軸）：toggle 同時控制 series 可見與左軸顯示
    if (S.slopeSeries) {
        S.slopeSeries.applyOptions({ visible: L.slope });
        if (S.mainChart) S.mainChart.priceScale('left').applyOptions({ visible: L.slope });
    }
    // 候選 markers toggle：重組 markers 合集（候選 + 交易），交給 candleSeries
    setCombinedMarkers();
    // 副圖隱藏：DOM display:none，會觸發 ResizeObserver 跳過更新
    const gc = $('gk-chart'), sc = $('slope-chart');
    if (gc) gc.classList.toggle('hidden', !L.gk);
    if (sc) sc.classList.toggle('hidden', !L.funding);  // 副圖 2 = 資金費率
    // 持倉線：重繪
    if (S.klineCache && S.trades) {
        for (const ls of S.tradeLines) S.mainChart.removeSeries(ls);
        S.tradeLines = [];
        if (L.positions) renderPositionLines(S.klineCache.candles || [], S.trades);
    }
}

// 套用範圍鈕（依當前資料時間軸 setVisibleRange）
function applyChartRange() {
    if (!S.mainChart || !S.klineCache) return;
    const candles = S.klineCache.candles || [];
    if (candles.length === 0) return;
    const last = candles[candles.length - 1].time;
    const HOUR = 3600;
    let from;
    switch (S.chartRange) {
        case '1d':  from = last - 24 * HOUR; break;
        case '1w':  from = last - 7 * 24 * HOUR; break;
        case '1m':  from = last - 30 * 24 * HOUR; break;
        case '3m':  from = last - 90 * 24 * HOUR; break;
        default:    from = candles[0].time;
    }
    S.mainChart.timeScale().setVisibleRange({ from, to: last + 5 * HOUR });
}

function updateChartData(kd, trades) {
    const candles = kd.candles || [];
    S.klineCache = kd;  // crosshair tooltip + range/toggle 重繪用

    // 保留使用者當前 zoom（updateData 不該打斷）
    const prevRange = S.chartUserZoomed
        ? S.mainChart.timeScale().getVisibleLogicalRange()
        : null;

    S.candleSeries.setData(candles);
    S.ema20Series.setData(kd.ema20 || []);

    // 成交量 histogram
    if (S.volumeSeries) {
        S.volumeSeries.setData(kd.volume || []);
    }
    // 資金費率（底部副圖，histogram，正綠負紅）
    if (S.fundingSeries) {
        const raw = kd.funding_rate || [];
        S.fundingData = raw;
        const colored = raw.map(p => ({
            time: p.time,
            value: p.value,
            color: p.value >= 0 ? 'rgba(38,166,154,0.85)' : 'rgba(239,83,80,0.85)',
        }));
        S.fundingSeries.setData(colored);
    }

    // 用 candles 的時間建立完整時間集合，GK 沒值的 bar 填 0（保持時間對齊）
    const gkMap = {};
    for (const g of (kd.gk_pctile || [])) { gkMap[g.time] = g.value; }
    const gkFull = candles.map(c => {
        const v = gkMap[c.time];
        return {
            time: c.time,
            value: v != null ? v : 0,
            color: v != null ? (v < 30 ? '#26a69a' : v < 50 ? '#f0b90b' : '#5b86e5') : 'rgba(0,0,0,0)',
        };
    });
    S.gkSeries.setData(gkFull);

    // SMA200 斜率（移到主圖左軸）
    if (S.slopeSeries && kd.sma_slope) {
        S.slopeData = kd.sma_slope;
        S.slopeSeries.setData(kd.sma_slope);
    }

    // ── 候選 bar markers + Trade markers 合併到 candleSeries ──
    // 只有 candleSeries 帶完整 candle 時間集合，外掛 series 會把 markers 擠到單點
    const candleTimes = new Set(candles.map(c => c.time));
    S.candidateMarkers = [];
    for (const c of (kd.candidates_l || [])) {
        if (!candleTimes.has(c.time)) continue;
        S.candidateMarkers.push({
            time: c.time, position: 'belowBar', color: '#26a69a', shape: 'circle', text: '',
        });
    }
    for (const c of (kd.candidates_s || [])) {
        if (!candleTimes.has(c.time)) continue;
        S.candidateMarkers.push({
            time: c.time, position: 'aboveBar', color: '#ef5350', shape: 'circle', text: '',
        });
    }

    // ── 清除舊的持倉線並重繪 ──
    for (const ls of S.tradeLines) S.mainChart.removeSeries(ls);
    S.tradeLines = [];

    // ── Trade markers + 候選 markers 合併送 candleSeries ──
    S.tradeMarkers = renderTradeMarkers(trades);
    setCombinedMarkers();

    // 持倉期間進場價格線（依 toggle 開關）
    if (S.chartLayers.positions) renderPositionLines(candles, trades);

    // ── Zoom 記憶：若使用者已 zoom 過 → 還原；否則才 scroll-to-realtime ──
    if (prevRange) {
        S.mainChart.timeScale().setVisibleLogicalRange(prevRange);
    } else {
        S.mainChart.timeScale().applyOptions({ rightOffset: 5 });
        // 若有當前 range 鈕選擇，套用 range；否則預設 1M（避免顯示 1500 根太密）
        if (S.chartRange && S.chartRange !== 'all') {
            applyChartRange();
        } else {
            S.mainChart.timeScale().scrollToRealTime();
        }
    }
}

// 把候選 markers + trade markers 合併排序後送 candleSeries
// 候選依 toggle 開關決定是否納入；trade markers 永遠顯示
function setCombinedMarkers() {
    if (!S.candleSeries) return;
    const trade = S.tradeMarkers || [];
    const cand = S.chartLayers.candidates ? (S.candidateMarkers || []) : [];
    const merged = [...cand, ...trade].sort((a, b) => a.time - b.time);
    S.candleSeries.setMarkers(merged);
}

function renderTradeMarkers(trades) {
    const markers = [];
    const COLOR_L = '#42a5f5', COLOR_S = '#ff9800';
    for (const t of trades) {
        const isLong = (t.direction || '').toUpperCase() === 'LONG';
        const sub = t.sub_strategy || (isLong ? 'L' : 'S');
        const dirColor = isLong ? COLOR_L : COLOR_S;
        const isClosed = t.exit_ts > 0 && t.exit_type;
        const pnl = t.net_pnl_usd;
        if (t.entry_ts > 0) {
            markers.push({
                time: t.entry_ts,
                position: isLong ? 'belowBar' : 'aboveBar',
                color: dirColor, shape: isLong ? 'arrowUp' : 'arrowDown', text: sub,
            });
        }
        if (isClosed) {
            const pnlText = pnl != null ? (pnl >= 0 ? '+' : '') + pnl.toFixed(1) : '';
            markers.push({
                time: t.exit_ts,
                position: isLong ? 'aboveBar' : 'belowBar',
                color: dirColor, shape: 'circle', text: pnlText,
            });
        }
    }
    markers.sort((a, b) => a.time - b.time);
    return markers;
}

function renderPositionLines(candles, trades) {
    const lastCandleTime = candles.length > 0 ? candles[candles.length - 1].time : 0;
    for (const t of trades) {
        const isLong = (t.direction || '').toUpperCase() === 'LONG';
        const isClosed = t.exit_ts > 0 && t.exit_type;
        const ep = t.entry_price;
        if (!(t.entry_ts > 0 && ep > 0)) continue;
        const endTs = isClosed ? t.exit_ts : lastCandleTime;
        if (!(endTs > 0)) continue;
        const lineData = [];
        for (const c of candles) {
            if (c.time >= t.entry_ts && c.time <= endTs) {
                lineData.push({ time: c.time, value: ep });
            }
        }
        if (lineData.length < 2) continue;
        const lineColor = !isClosed ? '#f0b90bcc' : (isLong ? '#42a5f5aa' : '#ff9800aa');
        const ls = S.mainChart.addLineSeries({
            color: lineColor, lineWidth: 2, lineStyle: 2,
            crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
        });
        ls.setData(lineData);
        S.tradeLines.push(ls);
    }
}

// ── Chart 浮動狀態面板（regime / GK / 持倉出場進度）──
function updateChartStatusPanel(d) {
    const el = $('chart-status-panel');
    if (!el || !d) return;
    const rg = d.regime || {};
    const gkL = d.gk_pctile, gkS = d.gk_pctile_s;
    const lc = d.last_close;
    const pos = (d.positions && d.positions.details) || [];

    const slopeStr = rg.slope_pct != null
        ? (rg.slope_pct >= 0 ? '+' : '') + rg.slope_pct.toFixed(2) + '%'
        : '-';
    const regTag = `<span class="csp-tag ${rg.label || 'NA'}">${rg.label || 'NA'}</span>`;
    const lBlock = rg.block_l ? '<span class="pnl-neg">L 擋</span>' : '<span class="pnl-pos">L ✓</span>';
    const sBlock = rg.block_s ? '<span class="pnl-neg">S 擋</span>' : '<span class="pnl-pos">S ✓</span>';

    const gkLClr = gkL == null ? 'var(--text-dim)'
        : gkL < 25 ? 'var(--green)' : gkL < 35 ? '#06b6d4' : gkL < 50 ? 'var(--gold)' : 'var(--text-dim)';
    const gkSClr = gkS == null ? 'var(--text-dim)'
        : gkS < 25 ? 'var(--green)' : gkS < 35 ? '#06b6d4' : gkS < 50 ? 'var(--gold)' : 'var(--text-dim)';
    const gkLStr = gkL != null ? gkL.toFixed(1) : '-';
    const gkSStr = gkS != null ? gkS.toFixed(1) : '-';

    let posHtml = '';
    if (pos.length > 0) {
        const lines = pos.map(p => {
            const sub = p.sub_strategy || '';
            const isL = sub === 'L';
            const dirCls = isL ? 'dir-long' : 'dir-short';
            const ep = p.entry_price || 0;
            const mark = p.mark_price || lc || 0;
            const unrPct = ep > 0 && mark > 0
                ? (isL ? (mark - ep) / ep * 100 : (ep - mark) / ep * 100)
                : 0;
            const unrCls = unrPct >= 0 ? 'pnl-pos' : 'pnl-neg';
            const ep_str = `<span class="${dirCls}">${sub}</span> $${ep.toFixed(2)} · ${p.bars_held || 0}h`;
            const pct_str = `<span class="${unrCls}">${unrPct >= 0 ? '+' : ''}${unrPct.toFixed(2)}%</span>`;

            // Exit progress 短摘要：剩餘 MH bars / 距 TP / 距 SafeNet
            let exitStr = '';
            const ep2 = p.exit_progress;
            if (ep2) {
                const parts = [];
                if (ep2.max_hold) parts.push(`MH剩${ep2.max_hold.remaining}/${ep2.max_hold.threshold}`);
                if (ep2.tp) {
                    const td = ep2.tp.distance;
                    parts.push(`TP差${td >= 0 ? td.toFixed(2) : '已到'}%`);
                }
                if (ep2.mfe_trail && ep2.mfe_trail.running_mfe > 0) {
                    parts.push(`MFE${ep2.mfe_trail.running_mfe.toFixed(2)}%`);
                }
                exitStr = `<div class="csp-row" style="font-size:10px;color:var(--text-dim)">${parts.join(' · ')}</div>`;
            }

            return `<div class="csp-row">${ep_str}${pct_str}</div>${exitStr}`;
        });
        posHtml = `
            <div class="csp-divider"></div>
            <div class="csp-row"><span class="csp-label">持倉</span><span>${pos.length} 筆</span></div>
            ${lines.join('')}
        `;
    }

    el.innerHTML = `
        <div class="csp-row"><span class="csp-label">Regime</span>${regTag}</div>
        <div class="csp-row"><span class="csp-label">Slope</span><span>${slopeStr}</span></div>
        <div class="csp-row"><span class="csp-label">Gate</span><span>${lBlock} | ${sBlock}</span></div>
        <div class="csp-divider"></div>
        <div class="csp-row"><span class="csp-label">GK_L (5/20)</span><span style="color:${gkLClr}">${gkLStr}</span></div>
        <div class="csp-row"><span class="csp-label">GK_S (10/30)</span><span style="color:${gkSClr}">${gkSStr}</span></div>
        ${posHtml}
    `;
}

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
        setConnStatus(true);
        S.trades = td.trades || [];
        renderFilters();
        renderTradesTable();
    } catch (e) {
        setConnStatus(false);
        $('trades-table-wrap').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function renderFilters() {
    const f = S.filters;
    const sel = (val, target) => val === target ? 'selected' : '';
    $('trade-filters').innerHTML = `
        <select onchange="S.filters.direction=this.value;renderTradesTable()">
            <option value="" ${sel(f.direction,'')}>全部方向 (All)</option>
            <option value="LONG" ${sel(f.direction,'LONG')}>做多 (Long)</option>
            <option value="SHORT" ${sel(f.direction,'SHORT')}>做空 (Short)</option>
        </select>
        <select onchange="S.filters.sub=this.value;renderTradesTable()">
            <option value="" ${sel(f.sub,'')}>全部策略 (All)</option>
            <option value="L" ${sel(f.sub,'L')}>L 做多</option>
            <option value="S" ${sel(f.sub,'S')}>S 做空</option>
        </select>
        <select onchange="S.filters.win=this.value;renderTradesTable()">
            <option value="" ${sel(f.win,'')}>勝負 (W/L)</option>
            <option value="WIN" ${sel(f.win,'WIN')}>贏 (Win)</option>
            <option value="LOSS" ${sel(f.win,'LOSS')}>虧 (Loss)</option>
        </select>
        <select onchange="S.filters.exit=this.value;renderTradesTable()">
            <option value="" ${sel(f.exit,'')}>出場原因 (Exit)</option>
            <option value="TP" ${sel(f.exit,'TP')}>止盈 (TP)</option>
            <option value="MFE-trail" ${sel(f.exit,'MFE-trail')}>浮盈回吐 (MFE-trail)</option>
            <option value="MaxHold" ${sel(f.exit,'MaxHold')}>時間止損 (MaxHold)</option>
            <option value="MH-ext" ${sel(f.exit,'MH-ext')}>延長賽 (MH-ext)</option>
            <option value="BE" ${sel(f.exit,'BE')}>平保 (BE)</option>
            <option value="SafeNet" ${sel(f.exit,'SafeNet')}>安全網 (SafeNet)</option>
        </select>
        <button class="export-btn" onclick="exportTradesCSV()">匯出 CSV</button>
    `;
}

function exportTradesCSV() {
    if (!S.trades || S.trades.length === 0) return;
    const cols = ['trade_number','entry_time_utc8','direction','sub_strategy','entry_price','exit_price','exit_type','net_pnl_usd','net_pnl_pct','hold_bars','gk_pctile_at_entry'];
    const header = cols.join(',');
    const rows = S.trades.map(t => cols.map(c => t[c] != null ? t[c] : '').join(','));
    const csv = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `trades_${S.mode}_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
}

function regimeTag(rg, slopePct) {
    if (!rg) return '-';
    const colors = { 'UP':'#ef5350', 'DOWN':'#26a69a', 'SIDE':'#f0b90b', 'MILD_UP':'#42a5f5' };
    const c = colors[rg] || '#888';
    const slopeStr = slopePct != null ? (slopePct >= 0 ? '+' : '') + slopePct.toFixed(2) + '%' : '';
    return `<span style="color:${c};font-weight:600">${rg}</span> <span style="color:var(--text-dim);font-size:11px">${slopeStr}</span>`;
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

    // Pagination
    const total = data.length;
    const ps = S.tradePageSize;
    const maxPage = Math.max(0, Math.ceil(total / ps) - 1);
    if (S.tradePage > maxPage) S.tradePage = maxPage;
    const pageData = data.slice(S.tradePage * ps, (S.tradePage + 1) * ps);

    const sortIcon = (col) => S.sortCol === col ? (S.sortAsc ? ' ▲' : ' ▼') : '';

    let html = `<table><thead><tr>
        <th onclick="sortBy('trade_number')">#${sortIcon('trade_number')}</th>
        <th onclick="sortBy('entry_time_utc8')">進場時間${sortIcon('entry_time_utc8')}</th>
        <th onclick="sortBy('direction')">方向${sortIcon('direction')}</th>
        <th onclick="sortBy('sub_strategy')">策略${sortIcon('sub_strategy')}</th>
        <th onclick="sortBy('entry_price')">進場價${sortIcon('entry_price')}</th>
        <th onclick="sortBy('exit_price')">出場價${sortIcon('exit_price')}</th>
        <th onclick="sortBy('exit_type')">出場原因${sortIcon('exit_type')}</th>
        <th onclick="sortBy('net_pnl_usd')">損益 $${sortIcon('net_pnl_usd')}</th>
        <th onclick="sortBy('net_pnl_pct')">損益 %${sortIcon('net_pnl_pct')}</th>
        <th onclick="sortBy('hold_bars')">持倉h${sortIcon('hold_bars')}</th>
        <th onclick="sortBy('entry_regime')">進場Regime${sortIcon('entry_regime')}</th>
        <th onclick="sortBy('gk_pctile_at_entry')">GK${sortIcon('gk_pctile_at_entry')}</th>
        <th onclick="sortBy('ema20_distance_pct')">EMA%${sortIcon('ema20_distance_pct')}</th>
        <th onclick="sortBy('breakout_strength_pct')">突破%${sortIcon('breakout_strength_pct')}</th>
        <th onclick="sortBy('max_adverse_excursion_pct')">MAE%${sortIcon('max_adverse_excursion_pct')}</th>
        <th onclick="sortBy('max_favorable_excursion_pct')">MFE%${sortIcon('max_favorable_excursion_pct')}</th>
    </tr></thead><tbody>`;

    for (const t of pageData) {
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
            <td>${regimeTag(t.entry_regime, t.entry_slope_pct)}</td>
            <td>${t.gk_pctile_at_entry!=null ? Number(t.gk_pctile_at_entry).toFixed(1) : '-'}</td>
            <td>${t.ema20_distance_pct!=null ? Number(t.ema20_distance_pct).toFixed(2)+'%' : '-'}</td>
            <td>${t.breakout_strength_pct!=null ? Number(t.breakout_strength_pct).toFixed(2)+'%' : '-'}</td>
            <td>${t.max_adverse_excursion_pct!=null ? t.max_adverse_excursion_pct.toFixed(1)+'%' : '-'}</td>
            <td>${t.max_favorable_excursion_pct!=null ? t.max_favorable_excursion_pct.toFixed(1)+'%' : '-'}</td>
        </tr>`;
    }
    html += '</tbody></table>';

    // 分頁控制
    if (total > ps) {
        html += `<div class="pagination">
            <button onclick="tradePageGo(0)" ${S.tradePage===0?'disabled':''}>«</button>
            <button onclick="tradePageGo(${S.tradePage-1})" ${S.tradePage===0?'disabled':''}>‹</button>
            <span>${S.tradePage+1} / ${maxPage+1}（共 ${total} 筆）</span>
            <button onclick="tradePageGo(${S.tradePage+1})" ${S.tradePage>=maxPage?'disabled':''}>›</button>
            <button onclick="tradePageGo(${maxPage})" ${S.tradePage>=maxPage?'disabled':''}>»</button>
        </div>`;
    }
    $('trades-table-wrap').innerHTML = html;
}

function tradePageGo(page) {
    S.tradePage = Math.max(0, page);
    renderTradesTable();
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
        const an = await api('/api/analytics');
        setConnStatus(true);
        renderAnalyticsCards(an);
        renderEquityCurve(an.cumulative_equity || []);
        renderDailyChart(an.daily_pnl || []);
        renderExitDist(an.exit_distribution || {});
        renderStratCompare(an.strategy_comparison || {});
        renderRegimeCompare(an.regime_performance || {});
    } catch (e) {
        setConnStatus(false);
        $('analytics-cards').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

// 進場 Regime 說明（collapsible）：讓沒看過研究文件的使用者也能理解
// UP / MILD_UP / DOWN / SIDE 四種分類的意義 + V25-D 出場參數
function regimeExplainHTML() {
    return `
    <details class="regime-explain" style="margin-bottom:12px">
        <summary style="cursor:pointer;color:var(--text);font-size:12px;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px">
            ℹ 什麼是「進場 Regime」？（點此展開說明）
        </summary>
        <div style="padding:10px 12px;margin-top:6px;background:var(--bg);border:1px solid var(--border);border-radius:6px;font-size:12px;color:var(--text-dim);line-height:1.7">
            <b style="color:var(--text)">定義</b>：進場當下 ETH 的市場狀態分類，依 <b>SMA200 的 100-bar 相對斜率</b>分成 4 類。每類有不同的進場准入 + 出場參數（V14+R / V25-D）。<br><br>

            <div class="table-scroll-wrap">
            <table class="strat-table" style="margin:6px 0">
                <thead><tr>
                    <th>Regime</th><th>斜率範圍</th><th>市場狀態</th>
                    <th>可進場</th><th>L_TP</th><th>L_MH</th><th>S_MH</th>
                </tr></thead>
                <tbody>
                    <tr><td><b>UP</b></td><td>slope &gt; +4.5%</td><td>強多頭</td>
                        <td style="color:var(--gold)">只 S</td><td>-</td><td>-</td><td><b style="color:var(--green)">8</b></td></tr>
                    <tr><td><b>MILD_UP</b></td><td>0 &lt; slope ≤ +4.5%</td><td>溫和多頭</td>
                        <td>L + S</td><td>3.5%</td><td><b style="color:var(--green)">7</b></td><td>10</td></tr>
                    <tr><td><b>DOWN</b></td><td>slope &lt; -1.0%</td><td>下跌</td>
                        <td>L + S</td><td><b style="color:var(--green)">4.0%</b></td><td>6</td><td>10</td></tr>
                    <tr><td><b>SIDE</b></td><td>|slope| &lt; 1.0%</td><td>橫盤</td>
                        <td style="color:var(--gold)">只 L</td><td>3.5%</td><td>6</td><td>-</td></tr>
                </tbody>
            </table>
            </div>

            <b style="color:var(--text)">斜率公式</b>：<code>slope = (SMA200 − SMA200.shift(100)) / SMA200.shift(100)</code><br>
            意義：200-bar 均線在過去 100 bar（約 4 天）漲了幾 %。例如 slope=+3% 代表 4 天內均線漲 3% → MILD_UP。<br><br>

            <b style="color:var(--text)">為什麼要分？</b><br>
            • <b>V23 研究</b>：V14 L 在 UP regime 會虧、S 在 SIDE regime 會虧 → 加 <b>R gate</b> 擋掉（UP 擋 L、SIDE 擋 S）<br>
            • <b>V25-D 研究</b>：不同 regime 下最佳出場參數不同，綠色粗體是 V25-D 相對 V14 baseline 的調整（回測 PnL +3.1%、MDD -10.5%）<br><br>

            <details style="margin:4px 0 10px 0">
                <summary style="cursor:pointer;color:var(--text);font-size:12px">為什麼「強多頭（UP）」只能做空？（反直覺解釋）</summary>
                <div style="padding:8px 10px;margin-top:6px;background:var(--bg-card);border-left:2px solid var(--gold);border-radius:4px;font-size:11px;line-height:1.7">
                    <b style="color:var(--text)">關鍵理解</b>：V14 L 不是普通做多，是「<b>GK 壓縮 &lt; 25 + 15-bar 新高突破</b>」——<b>賭低波動壓縮後往上爆</b>。<br><br>

                    <b style="color:var(--text)">V14 L 在強多頭為何虧？</b><br>
                    ETH 已經漲一大波後出現 GK 壓縮，通常是這兩種情境：<br>
                    1. <b>高檔整理（distribution）</b>→ 壓縮後往下<br>
                    2. <b>爆拉頂部（blow-off top）</b>→ 突破當下就是局部頂<br>
                    兩者 V14 L 進場都容易被反轉打掉。V23 回測 UP regime 下 V14 L <b>OOS 淨虧</b>，故 R gate 擋掉。<br><br>

                    <b style="color:var(--text)">V14 S 在強多頭為何仍有效？</b><br>
                    • 強多頭的<b>回檔又快又猛</b>（FOMO 耗盡後獲利了結）<br>
                    • S 的 TP 只設 2%，急殺中很容易打到<br>
                    • GK 壓縮後的下破常是「最後一次甩轎」→ 2% 快殺很常見<br><br>

                    <b style="color:var(--text)">一句話總結</b>：<br>
                    「強多頭只能 S」≠「強多頭適合做空」，而是<b>「V14 L 的突破訊號在強多頭環境下特別容易變頂部騙線」</b>。
                    V14 S 只是順便在這個環境仍有正 edge，所以保留。
                </div>
            </details>


            <b style="color:var(--text)">怎麼讀下方分組績效？</b><br>
            • 各 regime 的實盤績效分開統計，可觀察哪個 regime 最賺/最虧<br>
            • V14+R 部署後，UP 的「L 筆數」應 = 0、SIDE 的「S 筆數」應 = 0；若不為 0 通常是部署前舊交易
        </div>
    </details>`;
}

function renderRegimeCompare(perf) {
    const el = $('regime-compare');
    if (!el) return;
    const order = ['UP', 'MILD_UP', 'DOWN', 'SIDE'];
    const descMap = {
        'UP':      ['強多頭 slope>+4.5%', 'L 理論被擋，僅 S 可進'],
        'MILD_UP': ['溫和多頭 0<slope≤+4.5%', 'L+S 皆可進'],
        'DOWN':    ['下跌 slope<0 且 |slope|≥1%', 'L+S 皆可進'],
        'SIDE':    ['橫盤 |slope|<1%', 'S 理論被擋，僅 L 可進'],
    };
    const keys = order.filter(k => perf[k]);
    // 說明區永遠顯示（即使沒資料也讓使用者能看懂），分組表依資料有無動態顯示
    let html = regimeExplainHTML();
    if (keys.length === 0) {
        html += '<div class="loading">資料不足 — 需有帶 sma_slope 的 bar_snapshots.csv 才能分組</div>';
        el.innerHTML = html;
        return;
    }
    html += `<div class="table-scroll-wrap"><table class="strat-table"><thead><tr>
        <th>Regime</th><th>說明</th><th>筆數</th><th>L/S</th>
        <th>勝率</th><th>總損益</th><th>均損益</th><th>平均斜率</th>
    </tr></thead><tbody>`;
    for (const k of keys) {
        const r = perf[k];
        const desc = descMap[k] ? descMap[k][0] : '';
        html += `<tr>
            <td><b>${k}</b></td>
            <td style="color:var(--text-dim);font-size:12px">${desc}</td>
            <td>${r.trades}</td>
            <td>L${r.l_trades} / S${r.s_trades}</td>
            <td>${r.win_rate}%</td>
            <td class="${pnlClass(r.pnl)}">${pnlStr(r.pnl)}</td>
            <td class="${pnlClass(r.avg_pnl)}">${pnlStr(r.avg_pnl)}</td>
            <td>${r.avg_slope_pct >= 0 ? '+' : ''}${r.avg_slope_pct.toFixed(2)}%</td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    // 提示：UP regime 若有 L 筆數 >0 = 舊 V14 資料（當時無 R gate），新交易該欄應為 0
    html += `<div style="margin-top:8px;color:var(--text-dim);font-size:11px">
        註：V14+R 部署後，UP 新 L 進場應 = 0、SIDE 新 S 進場應 = 0；若不為 0 可能是部署前的舊交易或 WARMUP 期。
    </div>`;
    el.innerHTML = html;
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
    if (data.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    // 已有 chart → 只更新資料
    if (S.equityChart && S.equitySeries) {
        S.equitySeries.setData(data);
        return;
    }

    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth, height: 250,
        layout: { background: { color: '#000000' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        timeScale: { borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    });
    S.equitySeries = chart.addAreaSeries({
        topColor: 'rgba(38,166,154,0.4)', bottomColor: 'rgba(38,166,154,0.0)',
        lineColor: '#26a69a', lineWidth: 2,
    });
    S.equitySeries.setData(data);
    S.equityChart = chart;
    if (S.equityRO) S.equityRO.disconnect();
    S.equityRO = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    S.equityRO.observe(el);
}

function renderDailyChart(daily) {
    const el = $('daily-chart');
    const data = daily.filter(d => d.time && d.value != null).map(d => ({
        time: d.time,
        value: d.value,
        color: d.value >= 0 ? '#26a69a' : '#ef5350',
    }));
    if (data.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    // 已有 chart → 只更新資料
    if (S.dailyChart && S.dailySeries) {
        S.dailySeries.setData(data);
        return;
    }

    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth, height: 250,
        layout: { background: { color: '#000000' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        timeScale: { borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    });
    S.dailySeries = chart.addHistogramSeries();
    S.dailySeries.setData(data);
    S.dailyChart = chart;
    if (S.dailyRO) S.dailyRO.disconnect();
    S.dailyRO = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    S.dailyRO.observe(el);
}

function renderExitDist(dist) {
    const el = $('exit-dist');
    const entries = Object.entries(dist);
    if (entries.length === 0) { el.innerHTML = '<div class="loading">尚無資料 (No Data)</div>'; return; }

    const total = entries.reduce((s, [, v]) => s + v, 0);
    const colors = { TP: '#26a69a', 'MFE-trail': '#5b86e5', MaxHold: '#f0b90b', 'MH-ext': '#06b6d4', BE: '#9c27b0', SafeNet: '#ef5350', Trail: '#5b86e5', EarlyStop: '#ff9800' };

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
// Tab 5: Logs
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function switchLogFile(file) {
    S.logFile = file;
    document.querySelectorAll('.log-tab').forEach(b =>
        b.classList.toggle('active', b.dataset.file === file));
    loadLogs();
}

async function loadLogs() {
    const viewer = $('log-viewer');
    if (!viewer) return;
    const lines = $('log-lines') ? parseInt($('log-lines').value) : 200;
    const search = $('log-search') ? $('log-search').value.trim().toLowerCase() : '';
    try {
        const resp = await fetch(`/api/logs?file=${S.logFile}&lines=${lines}`);
        const d = await resp.json();
        if (!d.lines || d.lines.length === 0) {
            viewer.innerHTML = '<div class="log-empty">暫無日誌</div>';
            return;
        }
        const filtered = search ? d.lines.filter(l => l.toLowerCase().includes(search)) : d.lines;
        if (filtered.length === 0) {
            viewer.innerHTML = `<div class="log-empty">無符合「${escapeHtml(search)}」的日誌</div>`;
            return;
        }
        viewer.innerHTML = filtered.map(line => {
            let cls = 'log-line';
            if (line.includes(' ERROR ') || line.includes('ERROR')) cls += ' log-error';
            else if (line.includes(' WARNING ') || line.includes('WARNING')) cls += ' log-warn';
            else if (line.includes('SIGNAL') || line.includes('ENTRY') || line.includes('EXIT')) cls += ' log-signal';
            return `<div class="${cls}">${escapeHtml(line)}</div>`;
        }).join('');

        const autoScroll = $('log-auto-scroll');
        if (autoScroll && autoScroll.checked) {
            viewer.scrollTop = viewer.scrollHeight;
        }
        setConnStatus(true);
    } catch (e) {
        viewer.innerHTML = `<div class="log-empty">載入失敗: ${e.message}</div>`;
    }
}

function escapeHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Bot Status
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function checkBotStatus() {
    const el = $('bot-status');
    if (!el) return;
    try {
        const resp = await fetch('/api/bot-status');
        const d = await resp.json();
        if (d.running) {
            el.textContent = 'BOT: 運行中';
            el.className = 'bot-status bot-running';
            el.title = `機器人運行中 (PID: ${d.pid})`;
        } else {
            el.textContent = 'BOT: 已停止';
            el.className = 'bot-status bot-stopped';
            el.title = d.exit_code != null ? `已停止 (exit: ${d.exit_code})` : '已停止';
        }
    } catch {
        el.textContent = 'BOT: --';
        el.className = 'bot-status';
    }
}

async function restartDashboard() {
    if (!confirm('確定要重啟儀表板和機器人嗎？\n視窗會關閉並自動重新開啟。')) return;
    try { await fetch('/api/dashboard/restart', { method: 'POST' }); } catch {}
}

async function shutdownDashboard() {
    if (!confirm('確定要關閉儀表板和機器人嗎？')) return;
    try { await fetch('/api/dashboard/shutdown', { method: 'POST' }); } catch {}
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 6: Backtest
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const BT_PARAMS = [
    // L strategy
    { key: 'l_gk_th',  label: 'GK 門檻', group: 'long', def: 25,   min: 1,   max: 100, step: 1,   unit: '' },
    { key: 'l_brk',    label: '突破窗口', group: 'long', def: 15,   min: 3,   max: 50,  step: 1,   unit: 'bar' },
    { key: 'l_tp',     label: '止盈 TP',  group: 'long', def: 3.5,  min: 0.5, max: 10,  step: 0.5, unit: '%' },
    { key: 'l_sn',     label: '安全網 SN', group: 'long', def: 3.5,  min: 1,   max: 10,  step: 0.5, unit: '%' },
    { key: 'l_mh',     label: '最大持倉', group: 'long', def: 6,    min: 2,   max: 24,  step: 1,   unit: 'bar' },
    { key: 'l_cd',     label: '冷卻期',   group: 'long', def: 6,    min: 1,   max: 24,  step: 1,   unit: 'bar' },
    { key: 'l_mfe_act',label: 'MFE 啟動', group: 'long', def: 1.0,  min: 0.1, max: 5,   step: 0.1, unit: '%' },
    { key: 'l_mfe_tr', label: 'MFE 回吐', group: 'long', def: 0.8,  min: 0.1, max: 5,   step: 0.1, unit: '%' },
    { key: 'l_cmh_bar',label: 'CMH Bar',  group: 'long', def: 2,    min: 1,   max: 6,   step: 1,   unit: 'bar' },
    { key: 'l_cmh_th', label: 'CMH 門檻', group: 'long', def: -1.0, min: -5,  max: 0,   step: 0.5, unit: '%' },
    // S strategy
    { key: 's_gk_th',  label: 'GK 門檻', group: 'short', def: 35,   min: 1,   max: 100, step: 1,   unit: '' },
    { key: 's_brk',    label: '突破窗口', group: 'short', def: 15,   min: 3,   max: 50,  step: 1,   unit: 'bar' },
    { key: 's_tp',     label: '止盈 TP',  group: 'short', def: 2.0,  min: 0.5, max: 10,  step: 0.5, unit: '%' },
    { key: 's_sn',     label: '安全網 SN', group: 'short', def: 4.0,  min: 1,   max: 10,  step: 0.5, unit: '%' },
    { key: 's_mh',     label: '最大持倉', group: 'short', def: 10,   min: 2,   max: 24,  step: 1,   unit: 'bar' },
    { key: 's_cd',     label: '冷卻期',   group: 'short', def: 8,    min: 1,   max: 24,  step: 1,   unit: 'bar' },
    // Shared
    { key: 'notional', label: '名目金額', group: 'shared', def: 4000, min: 500, max: 20000, step: 500, unit: '$' },
    { key: 'fee',      label: '手續費',   group: 'shared', def: 4,    min: 0,   max: 50,    step: 1,   unit: '$' },
    // V14+R Regime Gate
    { key: 'r_th_up',  label: 'R gate L 門檻', group: 'shared', def: 4.5, min: 0.1, max: 20,  step: 0.1, unit: '%' },
    { key: 'r_th_side',label: 'R gate S 門檻', group: 'shared', def: 1.0, min: 0.01,max: 10,  step: 0.1, unit: '%' },
];

const BT_TOGGLES = [
    { key: 'enable_regime_gate', label: '啟用 R gate (V14+R)', def: true },
];

async function initBtParams() {
    if (S.btInited) return;
    // Load symbol list
    try {
        const resp = await fetch('/api/backtest/symbols');
        const d = await resp.json();
        const sel = $('bt-symbol');
        if (sel && d.symbols) {
            sel.innerHTML = d.symbols.map(s =>
                `<option value="${s}" ${s==='ETHUSDT'?'selected':''}>${s.replace('USDT','')}</option>`
            ).join('');
        }
    } catch (e) { /* keep empty select */ }

    // Build param inputs
    const groups = { long: 'bt-params-long', short: 'bt-params-short', shared: 'bt-params-shared' };
    for (const [group, elId] of Object.entries(groups)) {
        const el = $(elId);
        if (!el) continue;
        const params = BT_PARAMS.filter(p => p.group === group);
        let html = params.map(p => `
            <div class="bt-param-row">
                <label>${p.label}${p.unit ? ' ('+p.unit+')' : ''}</label>
                <input type="number" id="bt-${p.key}" value="${p.def}"
                       min="${p.min}" max="${p.max}" step="${p.step}">
            </div>
        `).join('');
        // Toggles 放在 shared 群組底部
        if (group === 'shared') {
            html += BT_TOGGLES.map(t => `
                <div class="bt-param-row">
                    <label>${t.label}</label>
                    <input type="checkbox" id="bt-${t.key}" ${t.def ? 'checked' : ''} style="width:20px;height:20px">
                </div>
            `).join('');
        }
        el.innerHTML = html;
    }
    S.btInited = true;
}

function resetBtParams() {
    const sym = $('bt-symbol');
    if (sym) sym.value = 'ETHUSDT';
    const sd = $('bt-start-date');
    if (sd) sd.value = '';
    const ed = $('bt-end-date');
    if (ed) ed.value = '';
    for (const p of BT_PARAMS) {
        const el = $(`bt-${p.key}`);
        if (el) el.value = p.def;
    }
}

function collectBtParams() {
    const params = {};
    const sym = $('bt-symbol');
    params.symbol = sym ? sym.value : 'ETHUSDT';
    const sd = $('bt-start-date');
    params.start_date = sd ? sd.value : '';
    const ed = $('bt-end-date');
    params.end_date = ed ? ed.value : '';
    for (const p of BT_PARAMS) {
        const el = $(`bt-${p.key}`);
        params[p.key] = el ? parseFloat(el.value) : p.def;
    }
    for (const t of BT_TOGGLES) {
        const el = $(`bt-${t.key}`);
        params[t.key] = el ? el.checked : t.def;
    }
    return params;
}

function validateDates() {
    const sd = $('bt-start-date');
    const ed = $('bt-end-date');
    sd.classList.remove('bt-input-error');
    ed.classList.remove('bt-input-error');
    const status = $('bt-status');

    if (!sd.value || !ed.value) {
        if (!sd.value) sd.classList.add('bt-input-error');
        if (!ed.value) ed.classList.add('bt-input-error');
        status.textContent = '!! 請填寫開始與結束日期';
        status.classList.add('bt-status-error');
        setTimeout(() => status.classList.remove('bt-status-error'), 3000);
        return false;
    }
    if (sd.value > ed.value) {
        sd.classList.add('bt-input-error');
        ed.classList.add('bt-input-error');
        status.textContent = '!! 開始日期不能晚於結束日期';
        status.classList.add('bt-status-error');
        setTimeout(() => status.classList.remove('bt-status-error'), 3000);
        return false;
    }
    return true;
}

function showBtOverlay(text) {
    const ov = $('bt-overlay');
    if (ov) { $('bt-overlay-text').textContent = text || '回測執行中...'; ov.classList.add('active'); }
}
function hideBtOverlay() {
    const ov = $('bt-overlay');
    if (ov) ov.classList.remove('active');
}

async function runBacktest() {
    if (S.btRunning) return;
    if (!validateDates()) return;
    S.btRunning = true;
    const btn = $('bt-run-btn');
    if (btn) btn.classList.add('running');
    showBtOverlay('回測執行中... (Running Backtest)');

    try {
        const params = collectBtParams();
        const resp = await fetch('/api/backtest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        });
        if (!resp.ok) {
            const body = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${body.slice(0, 200)}`);
        }
        S.btResult = await resp.json();
        if (S.btResult.error) throw new Error(S.btResult.error);

        // Show result sections
        $('bt-placeholder').style.display = 'none';
        $('bt-summary-cards').style.display = '';
        $('bt-charts-row1').style.display = '';
        $('bt-charts-row2').style.display = '';
        $('bt-trades-section').style.display = '';

        renderBtSummary(S.btResult.summary);
        renderBtEquity(S.btResult.equity_curve);
        renderBtMonthly(S.btResult.monthly);
        renderBtExitDist(S.btResult.exit_distribution);
        renderBtStratCompare(S.btResult.summary);
        S.btPage = 0;
        renderBtTrades();

        $('bt-status').textContent = `完成 — ${S.btResult.symbol} / ${S.btResult.elapsed_ms}ms / ${S.btResult.summary.total_trades} 筆 / ${S.btResult.data_range}`;
    } catch (e) {
        $('bt-status').textContent = `錯誤: ${e.message}`;
        alert(`回測執行失敗\n\n${e.message}`);
    } finally {
        S.btRunning = false;
        if (btn) btn.classList.remove('running');
        hideBtOverlay();
    }
}

async function loadBacktest() {
    await initBtParams();
    // Re-render existing results on tab switch (no re-run)
}

function renderBtSummary(s) {
    $('bt-summary-cards').innerHTML = `
        <div class="card">
            <div class="card-label">總損益 (Total P&L)</div>
            <div class="card-value ${s.total_pnl>0?'green':s.total_pnl<0?'red':''}">${pnlStr(s.total_pnl)}</div>
            <div class="card-sub">L: ${pnlStr(s.l_pnl)} / S: ${pnlStr(s.s_pnl)}</div>
        </div>
        <div class="card">
            <div class="card-label">總交易 (Trades)</div>
            <div class="card-value">${s.total_trades}</div>
            <div class="card-sub">L: ${s.l_trades} / S: ${s.s_trades}</div>
        </div>
        <div class="card">
            <div class="card-label">勝率 (Win Rate)</div>
            <div class="card-value">${s.win_rate.toFixed(1)}%</div>
            <div class="card-sub">L: ${s.l_wr.toFixed(1)}% / S: ${s.s_wr.toFixed(1)}%</div>
        </div>
        <div class="card">
            <div class="card-label">盈利因子 (Profit Factor)</div>
            <div class="card-value">${s.profit_factor.toFixed(2)}</div>
        </div>
        <div class="card">
            <div class="card-label">最大回撤 (Max DD)</div>
            <div class="card-value red">-$${s.max_drawdown.toFixed(0)}</div>
        </div>
        <div class="card">
            <div class="card-label">平均持倉 (Avg Hold)</div>
            <div class="card-value">${s.avg_hold.toFixed(1)}h</div>
            <div class="card-sub">最佳: ${pnlStr(s.best_trade)} / 最差: ${pnlStr(s.worst_trade)}</div>
        </div>
    `;
}

function renderBtEquity(data) {
    const el = $('bt-equity-chart');
    if (!data || data.length === 0) { el.innerHTML = '<div class="loading">尚無資料</div>'; return; }
    if (S.btEquityChart && S.btEquitySeries) {
        S.btEquitySeries.setData(data);
        return;
    }
    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth, height: 250,
        layout: { background: { color: '#000000' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        timeScale: { borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    });
    S.btEquitySeries = chart.addAreaSeries({
        topColor: 'rgba(38,166,154,0.4)', bottomColor: 'rgba(38,166,154,0.0)',
        lineColor: '#26a69a', lineWidth: 2,
    });
    S.btEquitySeries.setData(data);
    S.btEquityChart = chart;
    if (S.btEquityRO) S.btEquityRO.disconnect();
    S.btEquityRO = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    S.btEquityRO.observe(el);
}

function renderBtMonthly(monthly) {
    const el = $('bt-monthly-chart');
    if (!monthly || monthly.length === 0) { el.innerHTML = '<div class="loading">尚無資料</div>'; return; }
    const data = monthly.map(m => ({
        time: m.month + '-01',
        value: m.pnl,
        color: m.pnl >= 0 ? '#26a69a' : '#ef5350',
    }));
    if (S.btMonthlyChart && S.btMonthlySeries) {
        S.btMonthlySeries.setData(data);
        return;
    }
    el.innerHTML = '';
    const chart = LightweightCharts.createChart(el, {
        width: el.clientWidth, height: 250,
        layout: { background: { color: '#000000' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        timeScale: { borderColor: '#2B2B43' },
        rightPriceScale: { borderColor: '#2B2B43' },
    });
    S.btMonthlySeries = chart.addHistogramSeries();
    S.btMonthlySeries.setData(data);
    S.btMonthlyChart = chart;
    if (S.btMonthlyRO) S.btMonthlyRO.disconnect();
    S.btMonthlyRO = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    S.btMonthlyRO.observe(el);
}

function renderBtExitDist(dist) {
    const el = $('bt-exit-dist');
    const entries = Object.entries(dist || {});
    if (entries.length === 0) { el.innerHTML = '<div class="loading">尚無資料</div>'; return; }
    const total = entries.reduce((s, [, v]) => s + v, 0);
    const colors = { TP: '#26a69a', 'MFE-trail': '#5b86e5', MH: '#f0b90b', 'MH-ext': '#06b6d4', BE: '#9c27b0', SN: '#ef5350', MaxHold: '#f0b90b', SafeNet: '#ef5350' };
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

function renderBtStratCompare(s) {
    const el = $('bt-strat-compare');
    if (!s || s.total_trades === 0) { el.innerHTML = '<div class="loading">尚無資料</div>'; return; }
    el.innerHTML = `<table class="strat-table"><thead><tr>
        <th>策略</th><th>筆數</th><th>勝率</th><th>總損益</th><th>均損益</th>
    </tr></thead><tbody>
    <tr>
        <td><b>L 做多</b></td>
        <td>${s.l_trades}</td>
        <td>${s.l_wr.toFixed(1)}%</td>
        <td class="${pnlClass(s.l_pnl)}">${pnlStr(s.l_pnl)}</td>
        <td class="${pnlClass(s.l_pnl)}">${s.l_trades > 0 ? pnlStr(s.l_pnl / s.l_trades) : '-'}</td>
    </tr>
    <tr>
        <td><b>S 做空</b></td>
        <td>${s.s_trades}</td>
        <td>${s.s_wr.toFixed(1)}%</td>
        <td class="${pnlClass(s.s_pnl)}">${pnlStr(s.s_pnl)}</td>
        <td class="${pnlClass(s.s_pnl)}">${s.s_trades > 0 ? pnlStr(s.s_pnl / s.s_trades) : '-'}</td>
    </tr>
    </tbody></table>`;
}

function renderBtTrades() {
    if (!S.btResult || !S.btResult.trades) return;
    let data = [...S.btResult.trades];

    // Sort
    data.sort((a, b) => {
        let va = a[S.btSortCol], vb = b[S.btSortCol];
        if (va == null) va = '';
        if (vb == null) vb = '';
        if (typeof va === 'string') return S.btSortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
        return S.btSortAsc ? va - vb : vb - va;
    });

    // Pagination
    const total = data.length;
    const ps = S.btPageSize;
    const maxPage = Math.max(0, Math.ceil(total / ps) - 1);
    if (S.btPage > maxPage) S.btPage = maxPage;
    const pageData = data.slice(S.btPage * ps, (S.btPage + 1) * ps);

    const si = (col) => S.btSortCol === col ? (S.btSortAsc ? ' ▲' : ' ▼') : '';

    let html = `<table><thead><tr>
        <th onclick="btSortBy('no')">#${si('no')}</th>
        <th onclick="btSortBy('side')">方向${si('side')}</th>
        <th onclick="btSortBy('entry_dt')">進場時間${si('entry_dt')}</th>
        <th onclick="btSortBy('exit_dt')">出場時間${si('exit_dt')}</th>
        <th onclick="btSortBy('entry_price')">進場價${si('entry_price')}</th>
        <th onclick="btSortBy('exit_price')">出場價${si('exit_price')}</th>
        <th onclick="btSortBy('pnl_usd')">損益 $${si('pnl_usd')}</th>
        <th onclick="btSortBy('pnl_pct')">損益 %${si('pnl_pct')}</th>
        <th onclick="btSortBy('bars_held')">持倉h${si('bars_held')}</th>
        <th onclick="btSortBy('exit_reason')">出場原因${si('exit_reason')}</th>
        <th onclick="btSortBy('gk_pctile')">GK${si('gk_pctile')}</th>
        <th onclick="btSortBy('mfe_pct')">MFE%${si('mfe_pct')}</th>
        <th onclick="btSortBy('mae_pct')">MAE%${si('mae_pct')}</th>
    </tr></thead><tbody>`;

    for (const t of pageData) {
        const sideCls = t.side === 'L' ? 'dir-long' : 'dir-short';
        const pCls = pnlClass(t.pnl_usd);
        html += `<tr>
            <td>${t.no}</td>
            <td class="${sideCls}">${t.side === 'L' ? 'LONG' : 'SHORT'}</td>
            <td>${fmtTime(t.entry_dt)}</td>
            <td>${fmtTime(t.exit_dt)}</td>
            <td>$${Number(t.entry_price).toFixed(2)}</td>
            <td>$${Number(t.exit_price).toFixed(2)}</td>
            <td class="${pCls}">${pnlStr(t.pnl_usd)}</td>
            <td class="${pCls}">${t.pnl_pct.toFixed(2)}%</td>
            <td>${t.bars_held}</td>
            <td>${t.exit_reason}</td>
            <td>${t.gk_pctile.toFixed(1)}</td>
            <td>${t.mfe_pct.toFixed(2)}%</td>
            <td>${t.mae_pct.toFixed(2)}%</td>
        </tr>`;
    }
    html += '</tbody></table>';

    if (total > ps) {
        html += `<div class="pagination">
            <button onclick="btPageGo(0)" ${S.btPage===0?'disabled':''}>«</button>
            <button onclick="btPageGo(${S.btPage-1})" ${S.btPage===0?'disabled':''}>‹</button>
            <span>${S.btPage+1} / ${maxPage+1}（共 ${total} 筆）</span>
            <button onclick="btPageGo(${S.btPage+1})" ${S.btPage>=maxPage?'disabled':''}>›</button>
            <button onclick="btPageGo(${maxPage})" ${S.btPage>=maxPage?'disabled':''}>»</button>
        </div>`;
    }
    $('bt-trades-table').innerHTML = html;
}

function btSortBy(col) {
    if (S.btSortCol === col) S.btSortAsc = !S.btSortAsc;
    else { S.btSortCol = col; S.btSortAsc = true; }
    renderBtTrades();
}

function btPageGo(page) {
    S.btPage = Math.max(0, page);
    renderBtTrades();
}

async function runBtAudit() {
    const btn = $('bt-audit-btn');
    if (btn.classList.contains('running')) return;
    if (!validateDates()) return;
    if (!S.btResult) {
        alert('請先執行回測再進行稽核驗證');
        return;
    }
    btn.classList.add('running');
    showBtOverlay('稽核驗證中... (Running Audit)');
    const auditEl = $('bt-audit-result');

    try {
        const params = collectBtParams();
        const resp = await fetch('/api/backtest/audit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
        });
        if (!resp.ok) {
            const body = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${body.slice(0, 200)}`);
        }
        const d = await resp.json();
        if (d.error) throw new Error(d.error);

        const allPass = d.pass;
        let html = `<div class="bt-audit-card">
            <div class="bt-audit-title">
                <span style="font-size:20px">${allPass ? '\u2705' : '\u274C'}</span>
                <span class="${allPass ? 'bt-audit-pass' : 'bt-audit-fail'}">
                    ${allPass ? 'ALL PASS — \u7121\u4E0A\u5E1D\u8996\u89D2 (No Look-Ahead Bias)' : 'FAIL — \u767C\u73FE\u554F\u984C'}
                </span>
            </div>
            <div style="font-size:11px;color:var(--text-dim);margin-bottom:12px">
                ${d.symbol} / ${d.total_trades} \u7B46\u4EA4\u6613 / ${d.elapsed_ms}ms / ${d.data_range}
            </div>`;

        for (const t of d.tests) {
            html += `<div class="bt-audit-item">
                <div class="bt-audit-icon">${t.pass ? '\u2705' : '\u274C'}</div>
                <div class="bt-audit-info">
                    <div class="bt-audit-name">${t.name}</div>
                    <div class="bt-audit-desc">${t.desc}</div>
                    <div class="bt-audit-detail">${t.detail}</div>
                </div>
            </div>`;
        }
        html += '</div>';
        auditEl.innerHTML = html;
        auditEl.style.display = '';

        $('bt-status').textContent = `稽核完成 — ${allPass ? 'ALL PASS' : 'FAIL'} / ${d.elapsed_ms}ms`;
    } catch (e) {
        auditEl.innerHTML = `<div class="bt-audit-card"><div class="bt-audit-title"><span style="font-size:20px">\u274C</span> 錯誤: ${e.message}</div></div>`;
        auditEl.style.display = '';
        $('bt-status').textContent = `稽核錯誤: ${e.message}`;
        alert(`稽核驗證失敗\n\n${e.message}`);
    } finally {
        btn.classList.remove('running');
        hideBtOverlay();
    }
}

function exportBtCSV() {
    if (!S.btResult || !S.btResult.trades || S.btResult.trades.length === 0) return;
    const cols = ['no','side','entry_dt','exit_dt','entry_price','exit_price','pnl_usd','pnl_pct','bars_held','exit_reason','gk_pctile','mfe_pct','mae_pct'];
    const header = cols.join(',');
    const rows = S.btResult.trades.map(t => cols.map(c => t[c] != null ? t[c] : '').join(','));
    const csv = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `backtest_v14r_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// WebSocket push（Status 頁用，輪詢做 fallback）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
let _statusWS = null;
let _wsReconnectTimer = null;

function openStatusWS() {
    if (_statusWS && _statusWS.readyState <= 1) return; // 已開或開啟中
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/ws/status?mode=${encodeURIComponent(S.mode)}`;
    try {
        _statusWS = new WebSocket(url);
    } catch (e) { return; }
    _statusWS.onopen = () => {
        setConnStatus(true);
    };
    _statusWS.onmessage = (ev) => {
        try {
            const m = JSON.parse(ev.data);
            if (m.type === 'status' && m.data) {
                if (S.tab === 'status') {
                    renderStatusCards(m.data);
                    renderGK(m.data);
                    renderEntryConditions(m.data.entry_conditions, m.data.positions, m.data.cooldowns);
                    renderRecentTrades(m.data.recent_trades || [], m.data.positions);
                    renderBreakers(m.data.breakers);
                    renderHealth(m.data.health);
                    triggerValuePop();
                }
                if (S.tab === 'chart') {
                    updateChartStatusPanel(m.data);
                }
                lastRefreshTime = new Date();
                refreshCountdown = REFRESH_INTERVAL;
                updateTimerDisplay();
                setConnStatus(true);
            }
        } catch (e) {}
    };
    _statusWS.onclose = () => {
        _statusWS = null;
        // 5s 後重連；期間 polling 仍會正常運作
        if (_wsReconnectTimer) clearTimeout(_wsReconnectTimer);
        _wsReconnectTimer = setTimeout(openStatusWS, 5000);
    };
    _statusWS.onerror = () => {
        try { _statusWS.close(); } catch (e) {}
    };
}

function closeStatusWS() {
    if (_wsReconnectTimer) { clearTimeout(_wsReconnectTimer); _wsReconnectTimer = null; }
    if (_statusWS) {
        try { _statusWS.close(); } catch (e) {}
        _statusWS = null;
    }
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
        const urgentCls = refreshCountdown <= 5 ? ' timer-urgent' : '';
        el.innerHTML = `上次更新 ${timeStr} | 刷新 <span class="timer-sec${urgentCls}">${refreshCountdown}s</span>`;
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
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// Tab 7: V14+R 指標詳細說明（Guide）
// 目的：把策略 entry/exit 全部指標與邏輯用中文完整解釋，
//      讓不熟研究文件的使用者也能直接在儀表板查清楚
// 內容分 8 大章節：總覽 / 進場指標 / 進場流程 / 出場指標 /
//      出場流程 / V25-D regime 出場 / 風控 / 研究背景
// 靜態內容（不需 API），一次 render 完
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function loadGuide() {
    const el = $('guide-container');
    if (!el) return;
    if (el.dataset.rendered === '1') return;  // 僅首次渲染，內容靜態

    el.innerHTML = `
    <div class="guide-wrap">

      <!-- ── 章 1: 策略總覽 ── -->
      <div class="guide-section">
        <h2>1. 策略總覽 (Strategy Overview)</h2>
        <div class="guide-card">
          <p><b>V14+R</b> 是 ETH 1h <b>Garman-Klass 壓縮突破</b>雙策略（L 做多 + S 做空），帶 <b>regime gate</b> 和 <b>V25-D regime-conditional exits</b>。</p>
          <div class="guide-spec">
            <div><span class="sp-k">交易對</span><span class="sp-v">ETHUSDT 永續 (Binance Futures)</span></div>
            <div><span class="sp-k">時框</span><span class="sp-v">1 小時 K 線</span></div>
            <div><span class="sp-k">帳戶設定</span><span class="sp-v">$1,000 / 保證金 $200 / 20x 槓桿 / 名目 $4,000</span></div>
            <div><span class="sp-k">持倉模式</span><span class="sp-v">Hedge Mode（L/S 可同時各 1 筆）</span></div>
            <div><span class="sp-k">手續費模型</span><span class="sp-v">每筆 $4（taker 0.04% × 2 + 滑價 0.01% × 2）</span></div>
            <div><span class="sp-k">回測 OOS 績效</span><span class="sp-v">L+S $4,549 / 12 個月正 13 個月中 / 最差月 -$91 / MDD $334</span></div>
          </div>
        </div>
      </div>

      <!-- ── 章 2: 進場指標詳解 ── -->
      <div class="guide-section">
        <h2>2. 進場指標詳解 (Entry Indicators)</h2>

        <div class="guide-card">
          <h3>2.1 GK 壓縮指數（Garman-Klass Volatility Percentile）</h3>
          <p><b>定義</b>：用 Garman-Klass 高低開收估計波動率，除以基線做<b>相對壓縮程度</b>百分位排序。百分位越低 = 越壓縮。</p>
          <div class="guide-formula">
            gk = 0.5 × ln(H/L)² − (2ln2−1) × ln(C/O)²<br>
            ratio<sub>L</sub> = mean(gk, 5) / mean(gk, 20)　　<span class="cmt">（L 用短期比值）</span><br>
            ratio<sub>S</sub> = mean(gk, 10) / mean(gk, 30)　<span class="cmt">（S 用較長基線）</span><br>
            pctile = ratio.shift(1).rolling(100).apply(rank percentile) × 100
          </div>
          <p><b>為什麼 shift(1)</b>：用昨日 ratio 算今日排序，防止用到當下 bar 的收盤資訊（look-ahead bias）。</p>
          <p><b>閾值</b>：</p>
          <ul>
            <li><b>L 進場</b>：pctile<sub>L</sub> &lt; <b>25</b>（進入觸發區）</li>
            <li><b>S 進場</b>：pctile<sub>S</sub> &lt; <b>35</b>（進入待命區）</li>
          </ul>
          <p><b>直覺</b>：波動壓縮代表 ETH 處於「蓄勢」狀態，壓縮後的突破動能通常比日常波動大，這是 V14 的核心 alpha 源。</p>
        </div>

        <div class="guide-card">
          <h3>2.2 15-bar 收盤突破（Close Breakout）</h3>
          <p><b>定義</b>：本根收盤 &gt; 前 15 根最高收盤（L）或 &lt; 前 15 根最低收盤（S）。</p>
          <div class="guide-formula">
            L：close &gt; close.shift(1).rolling(15).max()<br>
            S：close &lt; close.shift(1).rolling(15).min()
          </div>
          <p><b>用 close 不用 high/low</b>：避免蠟燭實體未確認的假突破（上影線突破後收回）。</p>
          <p><b>為何是 alpha 的核心</b>：V17/V18/V19 三輪研究證實 — ETH 1h 的方向性 alpha <b>只存在於突破 bar</b>；非突破 bar 是 random walk。</p>
        </div>

        <div class="guide-card">
          <h3>2.3 Session Filter（時段過濾，UTC+8）</h3>
          <p><b>阻擋時段</b>（兩向共用）：</p>
          <ul>
            <li><b>小時阻擋</b>：00 / 01 / 02 / 12 點（深夜低流動性 + 午間換手噪音）</li>
            <li><b>L 阻擋星期</b>：週六 / 週日（週末 ETH 上漲動能弱）</li>
            <li><b>S 阻擋星期</b>：週一 / 週六 / 週日（週一開盤波動不穩，S 容易被軋）</li>
          </ul>
          <p><b>背景</b>：session filter 是 V10 研究時用歷史 WR by hour/weekday 統計出的過濾，保留 WR 前段時段。</p>
        </div>

        <div class="guide-card">
          <h3>2.4 V14+R Regime Gate（SMA200 100-bar 斜率）</h3>
          <p><b>定義</b>：SMA200 的 100-bar 相對斜率，分 4 個 regime。</p>
          <div class="guide-formula">
            SMA200 = close.rolling(200).mean()<br>
            slope = (SMA200 − SMA200.shift(100)) / SMA200.shift(100)<br>
            slope = slope.shift(1)　<span class="cmt">（用昨日斜率，防前瞻）</span>
          </div>
          <table class="guide-table">
            <thead><tr><th>Regime</th><th>斜率範圍</th><th>狀態</th><th>R gate 作用</th></tr></thead>
            <tbody>
              <tr><td><b>UP</b></td><td>slope &gt; +4.5%</td><td>強多頭</td><td class="al">擋 L（僅 S 可進）</td></tr>
              <tr><td><b>MILD_UP</b></td><td>0 &lt; slope ≤ +4.5%</td><td>溫和多頭</td><td class="ok">L+S 皆可</td></tr>
              <tr><td><b>DOWN</b></td><td>slope &lt; −1.0%</td><td>下跌</td><td class="ok">L+S 皆可</td></tr>
              <tr><td><b>SIDE</b></td><td>|slope| &lt; 1.0%</td><td>橫盤</td><td class="al">擋 S（僅 L 可進）</td></tr>
            </tbody>
          </table>
          <p><b>為什麼要擋</b>：V23 研究 2 年 OOS 發現 V14 L 在 UP 淨虧、S 在 SIDE 淨虧 → 加 R gate 擋掉。V14+R 整體 PnL +6%、MDD −11%、Sharpe +18%、Worst 30d −35%。</p>
          <p><b>UP 只能 S 的反直覺解釋</b>：V14 L 是「壓縮突破」不是「普通做多」。強多頭裡 GK 壓縮通常出現在 distribution 或 blow-off top，L 進場容易成為頂部騙線；而強多頭的急殺回檔對 S 的 2% TP 反而有利。</p>
        </div>

        <div class="guide-card">
          <h3>2.5 Cooldown + Monthly Cap（交易頻率控制）</h3>
          <ul>
            <li><b>出場冷卻</b>：L 出場後 <b>6 bar</b> 內不再進 L；S 出場後 <b>8 bar</b> 內不再進 S（防止反覆進出同一 setup）</li>
            <li><b>月度上限</b>：L 每月最多 <b>20 筆</b>，S 每月最多 <b>20 筆</b>（防止月內過度交易）</li>
            <li><b>同時持倉</b>：L/S 各最多 <b>1 筆</b>（maxTotal=1 per side），合計最多 2 筆</li>
          </ul>
        </div>
      </div>

      <!-- ── 章 3: 進場流程 ── -->
      <div class="guide-section">
        <h2>3. 進場流程 (Entry Flow)</h2>

        <div class="guide-card">
          <h3>3.1 L 做多進場（6 項全部 PASS 才進）</h3>
          <ol class="guide-ol">
            <li><b>GK<sub>L</sub> &lt; 25</b>　　—　波動壓縮到觸發區</li>
            <li><b>向上突破 15bar</b>　—　close &gt; 前 15 根最高收盤</li>
            <li><b>時段允許</b>　　　—　非 {0,1,2,12} 點、非週六日</li>
            <li><b>非強多頭</b>（R gate）—　SMA200 slope ≤ +4.5%</li>
            <li><b>冷卻結束</b>　　　—　距上次 L 出場 ≥ 6 bar</li>
            <li><b>月度上限未滿</b>　—　當月 L 進場 &lt; 20 筆</li>
          </ol>
          <p class="guide-note">任一條件 FAIL 即放棄本 bar；實盤每根 bar 整點 +10s 重新評估一次。</p>
        </div>

        <div class="guide-card">
          <h3>3.2 S 做空進場（6 項全部 PASS 才進）</h3>
          <ol class="guide-ol">
            <li><b>GK<sub>S</sub> &lt; 35</b>　　—　波動壓縮到待命區</li>
            <li><b>向下突破 15bar</b>　—　close &lt; 前 15 根最低收盤</li>
            <li><b>時段允許</b>　　　—　非 {0,1,2,12} 點、非週一/六/日</li>
            <li><b>非橫盤</b>（R gate）—　|SMA200 slope| ≥ 1.0%</li>
            <li><b>冷卻結束</b>　　　—　距上次 S 出場 ≥ 8 bar</li>
            <li><b>月度上限未滿</b>　—　當月 S 進場 &lt; 20 筆</li>
          </ol>
        </div>
      </div>

      <!-- ── 章 4: 出場指標詳解 ── -->
      <div class="guide-section">
        <h2>4. 出場指標詳解 (Exit Mechanisms)</h2>

        <div class="guide-card">
          <h3>4.1 SafeNet（安全網 / 硬停損）</h3>
          <p><b>定義</b>：單筆最大可承受虧損百分比，超過即強制出場。</p>
          <ul>
            <li><b>L</b>：進場價 × (1 − <b>3.5%</b>) 觸發停損</li>
            <li><b>S</b>：進場價 × (1 + <b>4.0%</b>) 觸發停損</li>
          </ul>
          <p><b>25% 穿透模型</b>：停損觸發後假設成交滑價 25%，L 單筆最大虧損約 −$158、S 單筆最大虧損約 −$200。</p>
          <p><b>Binance Algo Order</b>：實盤用 STOP_MARKET + closePosition=true 下在交易所側，機器人當機也能執行。</p>
        </div>

        <div class="guide-card">
          <h3>4.2 TP（固定止盈）</h3>
          <p><b>定義</b>：固定百分比止盈，觸及 bar 的 high (L) / low (S) 即收盤出場。</p>
          <ul>
            <li><b>L TP</b>：進場價 × (1 + <b>3.5%</b>)　<span class="cmt">(DOWN regime 改 4.0%，見第 6 章)</span></li>
            <li><b>S TP</b>：進場價 × (1 − <b>2.0%</b>)</li>
          </ul>
          <p><b>非對稱原因</b>：L 靠壓縮突破攻上方動能空間通常較大；S 靠壓縮後的急殺，空間小但命中率高。</p>
        </div>

        <div class="guide-card">
          <h3>4.3 MaxHold（持倉上限）</h3>
          <p><b>定義</b>：持倉超過 N 根 1h bar 仍未觸發 TP/SafeNet，強制出場（避免長時間套在不明方向的部位）。</p>
          <ul>
            <li><b>L MaxHold</b>：<b>6 bar</b>　<span class="cmt">(MILD_UP regime 改 7，見第 6 章；Conditional MH 觸發時縮為 5)</span></li>
            <li><b>S MaxHold</b>：<b>10 bar</b>　<span class="cmt">(UP regime 改 8，見第 6 章)</span></li>
          </ul>
          <p><b>Extension 延長</b>：MaxHold 到期時若<b>浮盈為正</b>，延長 2 bar + 啟用 Break-Even Trail：</p>
          <ul>
            <li>延長期間 L：若 low ≤ 進場價 → 以進場價收盤出場（免費延長）</li>
            <li>延長期間 S：若 high ≥ 進場價 → 以進場價收盤出場</li>
            <li>延長期間到期仍未觸發 → MH-ext 直接收盤出場</li>
            <li>MaxHold 到期若浮盈為負 → 直接 MaxHold 收盤出場，不延長</li>
          </ul>
        </div>

        <div class="guide-card">
          <h3>4.4 MFE Trailing（V14 新增，僅 L 使用）</h3>
          <p><b>定義</b>：<b>Max Favorable Excursion 移動鎖利</b> — 浮盈曾達 1.0% 後若回吐 0.8% 即收盤出場。</p>
          <div class="guide-formula">
            running_mfe = max 所有 bar 的 (high − entry) / entry<br>
            啟動條件：running_mfe ≥ <b>1.0%</b><br>
            觸發條件：running_mfe − 當前 close PnL% ≥ <b>0.8%</b><br>
            最早 bar 1 可觸發，extension 期間也有效
          </div>
          <p><b>V14 改進</b>：V13 只靠 TP/MaxHold 出場，L 在 3.5% TP 前常回吐 1–2% 盈利；MFE trail 抓住中段盈利，L OOS +$293（+17%）。</p>
          <p><b>S 沒加</b>：V14 R2 測 45 組 MFE trail for S 全部更差；S 的 edge 在壓縮後急殺，爆發期短，trail 反而砍掉小 TP。</p>
        </div>

        <div class="guide-card">
          <h3>4.5 Conditional MH（V14 新增，僅 L 使用）</h3>
          <p><b>定義</b>：bar 2 的收盤若虧損 ≥ 1.0% → 把 L 的 MaxHold 從 6 縮短為 5。</p>
          <div class="guide-formula">
            若 bars_held == 2 且 (close − entry) / entry ≤ −1.0%<br>
            → mh_reduced = True（從此 MaxHold 改 5）
          </div>
          <p><b>意義</b>：進場後第 2 根就套 1% 以上，通常是「偽突破」，早 1 bar 出場減損。</p>
        </div>
      </div>

      <!-- ── 章 5: 出場流程 ── -->
      <div class="guide-section">
        <h2>5. 出場流程 (Exit Priority Order)</h2>

        <div class="guide-card">
          <h3>5.1 L 出場優先順序（高 → 低）</h3>
          <ol class="guide-ol">
            <li><b>SafeNet −3.5%</b>（硬停損，最高優先）</li>
            <li><b>TP +3.5%</b>（或 DOWN regime 時 +4.0%）</li>
            <li><b>MFE Trailing</b>（浮盈 ≥1% 後回吐 ≥0.8%）</li>
            <li><b>Conditional MH 判定</b>（bar 2 虧 ≥1% → MH 縮為 5）</li>
            <li><b>MaxHold 6 bar</b>（或縮短後 5 bar；MILD_UP regime 時 7 bar）</li>
            <li><b>MaxHold Extension</b>（若浮盈為正則延 2 bar + BE trail）</li>
          </ol>
        </div>

        <div class="guide-card">
          <h3>5.2 S 出場優先順序（高 → 低）</h3>
          <ol class="guide-ol">
            <li><b>SafeNet +4.0%</b>（硬停損）</li>
            <li><b>TP −2.0%</b></li>
            <li><b>MaxHold 10 bar</b>（UP regime 時 8 bar）</li>
            <li><b>MaxHold Extension</b>（若浮盈為正則延 2 bar + BE trail）</li>
          </ol>
          <p class="guide-note">S 沒有 MFE trail / Conditional MH — V14 R2/R3/R4 測 70+ 種 S 出場調整，全部更差，S 是 globally optimal。</p>
        </div>
      </div>

      <!-- ── 章 6: V25-D Regime-Conditional Exits ── -->
      <div class="guide-section">
        <h2>6. V25-D Regime-Conditional Exits（條件出場參數）</h2>
        <div class="guide-card">
          <p>進場 regime 會影響出場參數（進場後 regime 變化不回頭改變）。綠色粗體是 V25-D 相對 V14 baseline 的調整。</p>
          <table class="guide-table">
            <thead><tr><th>Regime</th><th>L_TP</th><th>L_MaxHold</th><th>S_MaxHold</th><th>調整理由</th></tr></thead>
            <tbody>
              <tr><td><b>UP</b></td><td>—<br>(L 擋)</td><td>—</td><td class="ok"><b>8</b></td><td>強多頭 S 回檔快 → 早 2 bar 鎖利</td></tr>
              <tr><td><b>MILD_UP</b></td><td>3.5%</td><td class="ok"><b>7</b></td><td>10</td><td>溫和多頭 L 突破空間大 → 多等 1 bar 觸 TP</td></tr>
              <tr><td><b>DOWN</b></td><td class="ok"><b>4.0%</b></td><td>6</td><td>10</td><td>下跌反彈幅度大 → 拉高 L TP 吃滿反彈</td></tr>
              <tr><td><b>SIDE</b></td><td>3.5%</td><td>6</td><td>—<br>(S 擋)</td><td>橫盤 L 維持 baseline</td></tr>
            </tbody>
          </table>
          <p><b>V25-D 績效（相對 V14+R）</b>：PnL +3.1% / WR +0.7% / MDD −10.5% / Sharpe 6.23 / 通過 12/12 gates。</p>
        </div>
      </div>

      <!-- ── 章 7: 風控熔斷 ── -->
      <div class="guide-section">
        <h2>7. 風控熔斷 (Circuit Breaker)</h2>
        <div class="guide-card">
          <ul>
            <li><b>日虧停止</b>：當日實現 PnL ≤ <b>−$200</b> → 當日停止所有新進場（出場邏輯照常）</li>
            <li><b>月虧停止</b>：L 當月累計 PnL ≤ <b>−$75</b> → 當月停 L；S 累計 ≤ <b>−$150</b> → 當月停 S</li>
            <li><b>連虧冷卻</b>：連續 <b>4 筆</b>虧損 → <b>24 bar</b> 冷卻（約 1 天）</li>
            <li><b>Regime 阻擋</b>：UP regime 阻 L 進場、SIDE regime 阻 S 進場（見第 2.4 節）</li>
          </ul>
          <p class="guide-note">即時狀態頁的「風控熔斷」面板會顯示每項熔斷的當前狀態和剩餘額度。</p>
        </div>
      </div>

      <!-- ── 章 8: 研究背景 ── -->
      <div class="guide-section">
        <h2>8. 研究背景（為什麼是這些參數）</h2>
        <div class="guide-card">
          <ul class="guide-ol">
            <li><b>V1–V10</b>：GK 壓縮突破核心策略定型（1h ETH 唯一可行 alpha）</li>
            <li><b>V11–V13</b>：出場機制優化（TP/MaxHold 組合、extension + BE trail）</li>
            <li><b>V14</b>：L 出場創新 — MFE trailing + Conditional MH，L OOS +$293 (+17%)</li>
            <li><b>V15</b>：嘗試進場過濾（ATR / GK 門檻）— 10-Gate 稽核 REJECTED，cascade 運氣</li>
            <li><b>V16</b>：TBR flow reversal — 核心 alpha 仍是 breakout，TBR 只是過濾器</li>
            <li><b>V17–V19</b>：非 breakout alpha 探索（4+ 輪、572+ 配置）— 全部失敗，確認 ETH 1h alpha 只存在於 breakout bar</li>
            <li><b>V20</b>：V14 框架套 9 個幣種 — 全 FAIL，V14 是 ETH-specific</li>
            <li><b>V21–V22</b>：事件錨 breakout / 古典 TA / Ichimoku / H&amp;S / Harmonic 等 — 全 REJECTED</li>
            <li><b>V23</b>：<b>R gate PROMOTED</b> — 非對稱 SMA200 斜率 gate，12/13 gates PASS，V14+R 成為新 baseline</li>
            <li><b>V24</b>：Vol overlay / 槓桿調整 / 多標的分散 — 全 REJECTED</li>
            <li><b>V25</b>：<b>V25-D PROMOTED</b> — regime-conditional exits，12/12 gates，當前線上版本</li>
          </ul>
          <p class="guide-note">每一輪研究都有 8-10 項稽核門檻（cascade 隨機測試、walk-forward、時序翻轉、參數鄰域等），通過才能部署。詳見 <code>doc/v*_research.md</code>。</p>
        </div>
      </div>

    </div>`;

    el.dataset.rendered = '1';
}

(function init() {
    // Restore mode
    document.querySelectorAll('.mode-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === S.mode));
    loadStatus();
    checkBotStatus();
    updateTimerDisplay();
    setInterval(onRefreshTick, 1000);
    setInterval(checkBotStatus, 10000);
    openStatusWS();

    // 瀏覽器 tab 隱藏時暫停動畫省 CPU
    document.addEventListener('visibilitychange', () => {
        document.body.style.animationPlayState = document.hidden ? 'paused' : 'running';
    });
})();

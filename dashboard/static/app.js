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
    logFile: 'system',
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
    if (S.tab === 'logs') loadLogs();
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
        renderEntryConditions(d.entry_conditions, d.positions);
        renderRecentTrades(d.recent_trades || [], d.positions);
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

function sessionTimeStr() {
    // 封鎖: UTC+8 0,1,2,12 點 + 一/六/日
    const now = new Date();
    const h = now.getHours();  // 本機 = UTC+8
    const d = now.getDay();    // 0=Sun
    const blockH = [0, 1, 2, 12];
    const blockD = [0, 1, 6];  // Sun=0, Mon=1, Sat=6
    const inBlock = blockH.includes(h) || blockD.includes(d);
    const dayNames = ['日','一','二','三','四','五','六'];
    if (inBlock) {
        return `現在 ${dayNames[d]} ${h}:00（封鎖中）`;
    }
    return `二~五 3-11,13-23點`;
}

function renderEntryConditions(ec, positions) {
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

    // L 條件面板
    const lc = ec.L ? ec.L.conditions : {};
    const lPassed = ec.L ? ec.L.passed : 0;
    const lTotal = ec.L ? ec.L.total : 3;
    const lPct = Math.round(lPassed / lTotal * 100);
    const lColor = lPct >= 100 ? 'var(--green)' : lPct >= 50 ? 'var(--gold)' : 'var(--red)';

    let lHtml = `<div class="entry-panel">
        <div class="entry-panel-title">
            <span>L 做多進場條件</span>
            <span class="entry-progress" style="color:${lColor}">${lPassed}/${lTotal}</span>
        </div>`;
    if (lc.gk) lHtml += condRow('', lc.gk.pass, 'GK < 25（壓縮）', lc.gk.value != null ? lc.gk.value.toFixed(1) : '-');
    if (lc.breakout) lHtml += condRow('', lc.breakout.pass, '向上突破 15bar', '');
    if (lc.session) lHtml += condRow('', lc.session.pass, '時段允許', sessionTimeStr());
    lHtml += `<div class="entry-bar"><div class="entry-bar-fill" style="width:${lPct}%;background:${lColor}"></div></div>`;
    lHtml += '</div>';

    // S 條件面板
    const sc = ec.S ? ec.S.conditions : {};
    const sPassed = ec.S ? ec.S.passed : 0;
    const sTotal = ec.S ? ec.S.total : 3;
    const sPct = Math.round(sPassed / sTotal * 100);
    const sColor = sPct >= 100 ? 'var(--green)' : sPct >= 50 ? 'var(--gold)' : 'var(--red)';

    let sHtml = `<div class="entry-panel">
        <div class="entry-panel-title">
            <span>S 做空進場條件</span>
            <span class="entry-progress" style="color:${sColor}">${sPassed}/${sTotal}</span>
        </div>`;
    if (sc.gk) sHtml += condRow('', sc.gk.pass, 'GK < 35（壓縮）', sc.gk.value != null ? sc.gk.value.toFixed(1) : '-');
    if (sc.breakout) sHtml += condRow('', sc.breakout.pass, '向下突破 15bar', '');
    if (sc.session) sHtml += condRow('', sc.session.pass, '時段允許', sessionTimeStr());
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
        setConnStatus(true);
        if (!S.chartReady) initCharts();
        S.trades = td.trades || [];
        updateChartData(kd, S.trades);
        updatePositionLines(st.positions ? st.positions.details : []);
    } catch (e) {
        setConnStatus(false);
        $('main-chart').innerHTML = `<div class="loading">載入失敗: ${e.message}</div>`;
    }
}

function initCharts() {
    const mc = $('main-chart');
    const gc = $('gk-chart');
    mc.innerHTML = '';
    gc.innerHTML = '';

    const baseOpts = {
        layout: { background: { color: '#1a1a2e' }, textColor: '#d1d4dc' },
        grid: { vertLines: { color: '#2B2B43' }, horzLines: { color: '#2B2B43' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#2B2B43', minimumWidth: 80 },
    };

    // 主圖：隱藏底部時間軸（由 GK 副圖統一顯示）
    S.mainChart = LightweightCharts.createChart(mc, {
        ...baseOpts, width: mc.clientWidth, height: 480,
        timeScale: { visible: false, borderColor: '#2B2B43' },
    });
    S.candleSeries = S.mainChart.addCandlestickSeries({
        upColor: '#26a69a', downColor: '#ef5350',
        borderUpColor: '#26a69a', borderDownColor: '#ef5350',
        wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    });
    S.ema20Series = S.mainChart.addLineSeries({ color: '#f0b90b', lineWidth: 2, title: 'EMA20' });

    // GK 副圖：顯示時間軸，作為上下共用的時間標籤
    S.gkChart = LightweightCharts.createChart(gc, {
        ...baseOpts, width: gc.clientWidth, height: 150,
        timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#2B2B43' },
    });
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

    S.chartReady = true;
}

function updateChartData(kd, trades) {
    const candles = kd.candles || [];
    S.candleSeries.setData(candles);
    S.ema20Series.setData(kd.ema20 || []);

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
        <th onclick="sortBy('gk_pctile_at_entry')">GK${sortIcon('gk_pctile_at_entry')}</th>
        <th onclick="sortBy('ema20_distance_pct')">EMA距離%${sortIcon('ema20_distance_pct')}</th>
        <th onclick="sortBy('breakout_strength_pct')">突破力%${sortIcon('breakout_strength_pct')}</th>
        <th onclick="sortBy('max_adverse_excursion_pct')">MAE%${sortIcon('max_adverse_excursion_pct')}</th>
        <th onclick="sortBy('max_favorable_excursion_pct')">MFE%${sortIcon('max_favorable_excursion_pct')}</th>
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
            <td>${t.gk_pctile_at_entry!=null ? Number(t.gk_pctile_at_entry).toFixed(1) : '-'}</td>
            <td>${t.ema20_distance_pct!=null ? Number(t.ema20_distance_pct).toFixed(2)+'%' : '-'}</td>
            <td>${t.breakout_strength_pct!=null ? Number(t.breakout_strength_pct).toFixed(2)+'%' : '-'}</td>
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
        const an = await api('/api/analytics');
        setConnStatus(true);
        renderAnalyticsCards(an);
        renderEquityCurve(an.cumulative_equity || []);
        renderDailyChart(an.daily_pnl || []);
        renderExitDist(an.exit_distribution || {});
        renderStratCompare(an.strategy_comparison || {});
    } catch (e) {
        setConnStatus(false);
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
    // 銷毀舊 chart 防止記憶體洩漏
    if (S.equityChart) { try { S.equityChart.remove(); } catch {} S.equityChart = null; }
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
    // 銷毀舊 chart 防止記憶體洩漏
    if (S.dailyChart) { try { S.dailyChart.remove(); } catch {} S.dailyChart = null; }
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
    const data = daily.filter(d => d.time && d.value != null).map(d => ({
        time: d.time,
        value: d.value,
        color: d.value >= 0 ? '#26a69a' : '#ef5350',
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
    try {
        const resp = await fetch(`/api/logs?file=${S.logFile}&lines=${lines}`);
        const d = await resp.json();
        if (!d.lines || d.lines.length === 0) {
            viewer.innerHTML = '<div class="log-empty">暫無日誌</div>';
            return;
        }
        viewer.innerHTML = d.lines.map(line => {
            let cls = 'log-line';
            if (line.includes(' ERROR ') || line.includes('ERROR')) cls += ' log-error';
            else if (line.includes(' WARNING ') || line.includes('WARNING')) cls += ' log-warn';
            else if (line.includes('SIGNAL') || line.includes('ENTRY') || line.includes('EXIT')) cls += ' log-signal';
            return `<div class="${cls}">${escapeHtml(line)}</div>`;
        }).join('');

        // 自動捲動到底部
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
(function init() {
    // Restore mode
    document.querySelectorAll('.mode-btn').forEach(b =>
        b.classList.toggle('active', b.dataset.mode === S.mode));
    loadStatus();
    checkBotStatus();
    updateTimerDisplay();
    setInterval(onRefreshTick, 1000);
    // 每 10 秒更新 bot 狀態
    setInterval(checkBotStatus, 10000);
})();

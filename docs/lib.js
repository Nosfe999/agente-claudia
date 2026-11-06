// Lógica pura de la app (sin DOM): riesgo, sesiones, diario y selección de modelo Gemini.
(function (root) {
  const CONTRACT = { BTC: 1, XAUUSD: 100, EURUSD: 100000, NASDAQ: 1, SP500: 1 };

  function positionSize(balance, riskPct, entry, stop, asset, contract) {
    if (!(balance > 0) || !(riskPct > 0 && riskPct <= 10)) throw new Error('Balance > 0 y riesgo entre 0 y 10 %');
    const dist = Math.abs(entry - stop);
    if (!(dist > 0)) throw new Error('Entrada y stop no pueden ser iguales');
    const c = contract || CONTRACT[asset];
    if (!c) throw new Error('Activo desconocido');
    const riskUsd = balance * riskPct / 100;
    const sign = entry > stop ? 1 : -1;
    const r5 = v => Math.round(v * 1e5) / 1e5;
    return {
      side: sign > 0 ? 'largo' : 'corto', riskUsd: Math.round(riskUsd * 100) / 100,
      stopDistance: r5(dist), lots: Math.round(riskUsd / (dist * c) * 1000) / 1000, contract: c,
      targets: { '1R': r5(entry + sign * dist), '2R': r5(entry + sign * dist * 2), '3R': r5(entry + sign * dist * 3) },
    };
  }

  // ---- sesiones (con horario de verano correcto vía Intl) ----
  const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  function localParts(date, tz) {
    const o = {};
    new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(date).forEach(p => { o[p.type] = p.value; });
    return { wd: WD.indexOf(o.weekday), min: (+o.hour) * 60 + (+o.minute) };
  }
  const hm = (h, m) => h * 60 + (m || 0);
  const MF = [1, 2, 3, 4, 5], ALL = [0, 1, 2, 3, 4, 5, 6];
  const SESSIONS = [
    { name: 'Tokio', tz: 'Asia/Tokyo', s: hm(9), e: hm(18), days: MF },
    { name: 'Londres', tz: 'Europe/London', s: hm(8), e: hm(17), days: MF },
    { name: 'Nueva York', tz: 'America/New_York', s: hm(8), e: hm(17), days: MF },
  ];
  const KILLZONES = [
    { name: 'Asia', tz: 'America/New_York', s: hm(20), e: hm(0), days: ALL },
    { name: 'Londres', tz: 'America/New_York', s: hm(2), e: hm(5), days: ALL },
    { name: 'NY AM', tz: 'America/New_York', s: hm(7), e: hm(10), days: ALL },
    { name: 'London Close', tz: 'America/New_York', s: hm(10), e: hm(12), days: ALL },
  ];

  function forexOpen(now) {
    const p = localParts(now, 'America/New_York');
    if (p.wd === 6) return false;
    if (p.wd === 5 && p.min >= hm(17)) return false;
    if (p.wd === 0 && p.min < hm(17)) return false;
    return true;
  }

  function windowRow(w, now, localTz) {
    const p = localParts(now, w.tz), m = p.min;
    const crosses = w.s > w.e;
    const inTime = crosses ? (m >= w.s || m < w.e) : (m >= w.s && m < w.e);
    const dayOk = w.days.includes(crosses && m < w.e ? (p.wd + 6) % 7 : p.wd);
    const fmt = d => new Intl.DateTimeFormat('es', { timeZone: localTz, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d);
    if (inTime && dayOk) {
      const left = (w.e - m + 1440) % 1440 || 1440;
      return { name: w.name, open: true, minsLeft: left, at: fmt(new Date(now.getTime() + left * 60000)) };
    }
    for (let d = 0; d < 8; d++) {
      const day = (p.wd + d) % 7;
      if (w.days.includes(day) && (d > 0 || w.s > m)) {
        const delta = d * 1440 + w.s - m;
        return { name: w.name, open: false, minsTo: delta, at: fmt(new Date(now.getTime() + delta * 60000)) };
      }
    }
    return { name: w.name, open: false, minsTo: null, at: '' };
  }

  function sessionsStatus(now, localTz) {
    const fx = forexOpen(now);
    return {
      forexOpen: fx,
      sessions: SESSIONS.map(w => windowRow(w, now, localTz)),
      killzones: KILLZONES.map(w => { const r = windowRow(w, now, localTz); if (!fx) r.open = false; return r; }),
    };
  }

  // ---- diario ----
  function calcR(t) {
    if ([t.entry, t.stop, t.exit].some(v => v == null || isNaN(v)) || t.entry === t.stop) return null;
    return Math.round((t.exit - t.entry) / (t.entry - t.stop) * 100) / 100;
  }
  function journalStats(trades) {
    const rs = trades.filter(t => t.r != null);
    if (!rs.length) return { trades: 0 };
    const total = rs.reduce((a, t) => a + t.r, 0);
    return {
      trades: rs.length, winRate: Math.round(rs.filter(t => t.r > 0).length / rs.length * 1000) / 10,
      totalR: Math.round(total * 100) / 100, avgR: Math.round(total / rs.length * 100) / 100,
      pnl: Math.round(rs.reduce((a, t) => a + (t.pnl || 0), 0) * 100) / 100,
    };
  }

  // ---- modelo Gemini (misma lógica que el agente) ----
  function pickModel(models) {
    let best = null;
    for (const m of models) {
      const name = m.name.split('/').pop();
      if (!(m.supportedGenerationMethods || []).includes('generateContent')) continue;
      if (!name.includes('flash') || /image|tts|live|audio|embed|thinking|robot|computer|native/.test(name)) continue;
      const v = name.match(/gemini-(\d+(?:\.\d+)?)/);
      const key = [/preview|exp/.test(name) ? 0 : 1, v ? parseFloat(v[1]) : 0, name.includes('lite') ? 1 : 0];
      if (!best || cmp(key, best.key) > 0) best = { key, name };
    }
    return best && best.name;
  }
  function cmp(a, b) { for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] - b[i]; return 0; }

  const Lib = { CONTRACT, positionSize, sessionsStatus, forexOpen, calcR, journalStats, pickModel, localParts };
  if (typeof module !== 'undefined' && module.exports) module.exports = Lib; else root.Lib = Lib;
})(typeof window !== 'undefined' ? window : globalThis);

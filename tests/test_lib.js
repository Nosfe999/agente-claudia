const assert = require('assert');
const L = require('../docs/lib.js');

// riesgo
let r = L.positionSize(1000, 1, 2000, 1990, 'XAUUSD');
assert.strictEqual(r.side, 'largo'); assert.strictEqual(r.lots, 0.01); assert.strictEqual(r.targets['2R'], 2020);
r = L.positionSize(1000, 1, 1.1000, 1.1010, 'EURUSD');
assert.strictEqual(r.side, 'corto'); assert.strictEqual(r.targets['1R'], 1.099);
for (const bad of [[0, 1, 1, 2], [1000, 0, 1, 2], [1000, 1, 1, 1]]) assert.throws(() => L.positionSize(...bad, 'XAUUSD'));

// diario
assert.strictEqual(L.calcR({ entry: 100, stop: 99, exit: 102 }), 2);
assert.strictEqual(L.calcR({ entry: 100, stop: 101, exit: 101 }), -1);
assert.strictEqual(L.calcR({ entry: 100, stop: 100, exit: 101 }), null);
const st = L.journalStats([{ r: 2, pnl: 20 }, { r: -1, pnl: -10 }, { r: null }]);
assert.deepStrictEqual([st.trades, st.winRate, st.totalR], [2, 50, 1]);

// sesiones: jueves 5-nov-2026 15:00 UTC = 10:00 NY (EST) -> NY abierta, forex abierto
let s = L.sessionsStatus(new Date('2026-11-05T15:00:00Z'), 'America/Asuncion');
assert.ok(s.forexOpen);
assert.ok(s.sessions.find(x => x.name === 'Nueva York').open);
assert.ok(s.killzones.find(x => x.name === 'London Close').open);
assert.ok(!s.sessions.find(x => x.name === 'Tokio').open);
// sábado: forex cerrado, killzones cerradas, NY con próxima apertura
s = L.sessionsStatus(new Date('2026-11-07T15:00:00Z'), 'America/Asuncion');
assert.ok(!s.forexOpen && s.killzones.every(k => !k.open));
assert.ok(s.sessions.every(x => !x.open && x.minsTo > 0));
// verano de EE.UU. (jul): 13:30 UTC = 09:30 NY (EDT) -> NY abierta
s = L.sessionsStatus(new Date('2026-07-08T13:30:00Z'), 'UTC');
assert.ok(s.sessions.find(x => x.name === 'Nueva York').open);
// Asia cruza medianoche: martes 01:30 UTC = lunes 20:30 NY -> abierta
s = L.sessionsStatus(new Date('2026-11-04T01:30:00Z'), 'UTC');
assert.ok(s.killzones.find(x => x.name === 'Asia').open);

// modelo Gemini
const models = [
  { name: 'models/gemini-2.5-pro', supportedGenerationMethods: ['generateContent'] },
  { name: 'models/gemini-2.5-flash', supportedGenerationMethods: ['generateContent'] },
  { name: 'models/gemini-3.1-flash-lite', supportedGenerationMethods: ['generateContent'] },
  { name: 'models/gemini-3.5-flash-preview', supportedGenerationMethods: ['generateContent'] },
  { name: 'models/gemini-3.5-flash-image', supportedGenerationMethods: ['generateContent'] },
  { name: 'models/text-embedding', supportedGenerationMethods: ['embedContent'] },
];
assert.strictEqual(L.pickModel(models), 'gemini-3.1-flash-lite'); // estable más nuevo, no preview ni imagen
assert.strictEqual(L.pickModel([]), null);
console.log('JS OK');

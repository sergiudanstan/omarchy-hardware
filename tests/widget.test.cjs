const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const model = {};
vm.runInNewContext(readFileSync(join(__dirname, '..', 'BoardsModel.js'), 'utf8')
  .replace(/^\.pragma.*$/m, ''), model);

test('catalog is supplied by board discovery, including Arduino Uno', () => {
  const scan = model.parseScan(JSON.stringify({ok: true, boards: [], supported_boards: ['Arduino Uno']}));
  assert.equal(scan.supportedBoards[0], 'Arduino Uno');
  assert.equal(model.parseScan('{"ok":true}').supportedBoards.length, 0);
});

test('catalog display does not override empty-widget visibility', () => {
  for (const connected of [false, true]) {
    for (const showEmpty of [false, true]) {
      for (const setupProblem of [false, true]) {
        for (const showCatalog of [false, true]) {
          const scan = model.parseScan(JSON.stringify({
            ok: true, boards: connected ? [{board_type: 'arduino_uno'}] : [],
            supported_boards: showCatalog ? ['Arduino Uno'] : []
          }));
          assert.equal(model.widgetVisible(scan.boards, showEmpty, setupProblem),
            connected || showEmpty || setupProblem);
        }
      }
    }
  }
});

test('hidden unknown adapters count as no visible boards', () => {
  const visible = model.visibleBoards([{board_type: 'unknown'}], true);
  assert.equal(model.widgetVisible(visible, false, false), false);
});

test('status output tolerates garbage and a missing config', () => {
  assert.equal(model.parseStatus('').checked, false);
  const bad = model.parseStatus('not json');
  assert.equal(bad.checked, true);
  assert.equal(bad.ok, false);
  assert.equal(model.targetRows(null).length, 0);
});

test('target rows list only configured families and mark disabled ones', () => {
  const rows = model.targetRows({
    pi: ['lab-pi.local'], jetson: [],
    ming: {allow: false, mqtt: ['stack'], influxdb: [], nodered: [], grafana: []},
    weintek: {allow: true, opcua: 1, mqtt: ['hmi.local:8883']}
  });
  assert.deepEqual([...rows.map((row) => row.label)], ['Raspberry Pi', 'MING stack', 'Weintek HMI']);
  assert.equal(rows[1].off, true);
  assert.equal(rows[2].off, false);
  assert.equal(rows[2].detail, 'mqtt: hmi.local:8883 · opc ua: 1 endpoint');
});

test('audit line names the broken record', () => {
  assert.equal(model.auditText({ok: true, records: 1}), 'Audit log: intact, 1 record');
  assert.match(model.auditText({ok: false, broken_at_line: 7, reason: 'x'}), /broken at line 7/);
  assert.equal(model.auditText(null), 'Audit log: not checked');
});

const uno = {port: '/dev/ttyACM0', vid: '2341', pid: '0043', serial: 'A1', board_type: 'arduino_uno'};
const clone = {port: '/dev/ttyUSB0', vid: '1a86', pid: '7523', serial: null, board_type: 'unknown'};

test('boards present at shell start are not announced', () => {
  const first = model.trackArrivals({}, [uno], 1000, true, false);
  assert.equal(first.arrived.length, 0);
  assert.ok(Object.keys(first.seen).length === 1);
});

test('a newly plugged board is announced once', () => {
  let state = model.trackArrivals({}, [], 1000, false, false);
  state = model.trackArrivals(state.seen, [uno], 6000, false, true);
  assert.equal(JSON.stringify(state.arrived.map((b) => b.port)), '["/dev/ttyACM0"]');
  state = model.trackArrivals(state.seen, [uno], 11000, false, true);
  assert.equal(state.arrived.length, 0);
});

test('a reset or reflash inside the grace window is not an arrival', () => {
  let state = model.trackArrivals({}, [uno], 0, false, true);
  state = model.trackArrivals(state.seen, [], 5000, false, true);
  state = model.trackArrivals(state.seen, [{...uno, port: '/dev/ttyACM1'}], 9000, false, true);
  assert.equal(state.arrived.length, 0, 'same serial on a new port is the same board');
  state = model.trackArrivals(state.seen, [], 10000, false, true);
  state = model.trackArrivals(state.seen, [uno], 10000 + model.ARRIVAL_GRACE_MS + 1, false, true);
  assert.equal(state.arrived.length, 1, 'gone longer than the grace window counts as plugged in again');
});

test('unknown adapters are announced only when asked', () => {
  const seen = model.trackArrivals({}, [], 0, false, false).seen;
  assert.equal(model.trackArrivals(seen, [clone], 1000, false, true).arrived.length, 0);
  assert.equal(model.trackArrivals(seen, [clone], 1000, true, true).arrived.length, 1);
});

test('only plain tty ports ever reach a helper command', () => {
  const seen = model.trackArrivals({}, [], 0, true, false).seen;
  const hostile = [
    {...uno, serial: 'x1', port: '/dev/ttyACM0; rm -rf ~'},
    {...uno, serial: 'x2', port: "/dev/ttyACM0' $(id)"},
    {...uno, serial: 'x3', port: '/dev/serial/by-id/usb-evil'},
    {...uno, serial: 'x4', port: '/dev/ttyS0'},
  ];
  assert.equal(model.trackArrivals(seen, hostile, 1000, true, true).arrived.length, 0);
  for (const board of hostile) assert.equal(model.canStartProject(board), false);
  assert.equal(model.canStartProject(uno), true);
});

test('boards without a serial are keyed by port', () => {
  assert.notEqual(model.boardKey({...clone, port: '/dev/ttyUSB0'}), model.boardKey({...clone, port: '/dev/ttyUSB1'}));
  assert.equal(model.boardKey({...uno, port: '/dev/ttyACM0'}), model.boardKey({...uno, port: '/dev/ttyACM3'}));
});

test('a journal label leads the board name', () => {
  assert.equal(model.shortName({friendly_name: 'Arduino Uno', label: 'greenhouse-node'}), 'greenhouse-node · Arduino Uno');
  assert.equal(model.shortName({friendly_name: 'Arduino Uno', label: null}), 'Arduino Uno');
  assert.ok(model.shortName({friendly_name: 'x'.repeat(60)}).length <= 40);
});

test('a scan gap longer than the grace window re-primes instead of announcing', () => {
  let state = model.trackArrivals({}, [uno], 0, false, true, 0);
  const lastOk = 1000;
  // The scan script hung for two minutes; the board never left.
  state = model.trackArrivals(state.seen, [uno], lastOk + 2 * model.ARRIVAL_GRACE_MS, false, true, lastOk);
  assert.equal(state.arrived.length, 0);
  // Scans are regular again: a genuinely new board is announced.
  const later = lastOk + 2 * model.ARRIVAL_GRACE_MS + 5000;
  state = model.trackArrivals(state.seen, [uno, {...uno, serial: 'B2'}], later, false, true, later - 5000);
  assert.equal(state.arrived.length, 1);
});

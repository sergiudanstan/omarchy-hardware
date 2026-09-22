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

test('arrivals keep working at the slowest scan interval the settings allow', () => {
  const interval = 60000;
  const grace = model.arrivalGraceMs(interval);
  let state = model.trackArrivals({}, [uno], 0, false, false, 0, grace);
  let last = 0;
  // Each good scan lands a little late, as a 60 s timer plus scan time does.
  for (let tick = 1; tick <= 3; tick++) {
    const now = tick * (interval + 400);
    state = model.trackArrivals(state.seen, [uno], now, false, true, last, grace);
    last = now;
  }
  const now = 4 * (interval + 400);
  state = model.trackArrivals(state.seen, [uno, {...uno, serial: 'NEW'}], now, false, true, last, grace);
  assert.equal(state.arrived.length, 1);
});

test('two boards that report the same serial are both tracked', () => {
  const a = {...clone, serial: '0001', port: '/dev/ttyUSB0', board_type: 'esp32'};
  const b = {...a, port: '/dev/ttyUSB1'};
  let state = model.trackArrivals({}, [a], 0, false, false);
  state = model.trackArrivals(state.seen, [a, b], 5000, false, true);
  assert.equal(JSON.stringify(state.arrived.map((x) => x.port)), '["/dev/ttyUSB1"]');
});

test('Pico BOOTSEL and HID boards are described, not flagged as permission errors', () => {
  const mounted = {kind: 'uf2_bootloader', port: '/run/media/me/RPI-RP2', volume: '/run/media/me/RPI-RP2', writable: true};
  const unmounted = {kind: 'uf2_bootloader', port: 'usb:1-2', volume: null, writable: false};
  const hid = {kind: 'hid', port: 'usb:1-3', writable: false};
  assert.equal(model.statusText(mounted), 'BOOTSEL, ready for UF2');
  assert.equal(model.portName(mounted), 'RPI-RP2');
  assert.equal(model.statusText(unmounted), 'BOOTSEL, not mounted');
  assert.equal(model.portName(unmounted), 'USB 1-2');
  assert.equal(model.statusText(hid), 'HID only, no serial port');
  for (const board of [mounted, unmounted, hid]) assert.equal(model.statusUrgent(board), false);
  assert.equal(model.statusUrgent({port: '/dev/ttyACM0', writable: false}), true);
  assert.equal(model.statusText({port: '/dev/ttyACM0', writable: false}), 'no access');
});

test('setup summary and doctor parsing', () => {
  assert.equal(model.parseDoctor('').checked, false);
  assert.equal(model.parseDoctor('garbage').checked, false);
  const one = model.parseDoctor('{"ready":false,"problems":[{"id":"venv","label":"Create the venv"}]}');
  assert.equal(model.setupSummary(one), 'Create the venv');
  const two = model.parseDoctor('{"ready":false,"problems":[{"id":"a","label":"A"},{"id":"b","label":"B"}]}');
  assert.equal(model.setupSummary(two), '2 setup steps remaining');
  assert.equal(model.setupSummary(model.parseDoctor('{"ready":true,"problems":[]}')), '');
  assert.equal(model.setupSummary(model.parseDoctor('{"ready":false,"pending_relogin":true,"problems":[]}')),
    'Log out and back in to finish setup');
});

test('a failing status script is not reported as a config.toml problem', () => {
  const failed = model.parseStatus('{"ok":false,"config_error":"","error":"/usr/bin/python3 is missing"}');
  assert.equal(failed.configError, '');
  assert.equal(failed.error, '/usr/bin/python3 is missing');
  assert.equal(model.parseStatus('not json').error, 'unparseable status output');
  assert.equal(model.flashText({flash: false}), 'Flashing: disabled in config.toml');
  assert.match(model.flashText({flash: true}), /confirm=true/);
  assert.equal(model.flashText(null), '');
});

test('non-object entries in script output are dropped', () => {
  const scan = model.parseScan('{"ok":true,"boards":[null,1,"x",[],{"port":"/dev/ttyACM0"}],"supported_boards":[1]}');
  assert.equal(scan.boards.length, 1);
  assert.equal(scan.supportedBoards[0], '1');
  assert.equal(model.parseDoctor('{"ready":false,"problems":[null,{"label":"A"}]}').problems.length, 1);
});

test('every parser survives random input', () => {
  let seed = 7;
  const rand = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
  const alphabet = '{}[]":,.-0123456789 truefalsnokbarsupported_boardserrorproblemsready\\';
  const fixed = ['{"ok":true,"boards":', '{"ready":false,"problems":', '{"ok":true,"targets":', 'null', '[{}]'];
  for (let round = 0; round < 500; round++) {
    let text = rand() < 0.5 ? fixed[Math.floor(rand() * fixed.length)] : '';
    const length = Math.floor(rand() * 80);
    for (let i = 0; i < length; i++) text += alphabet[Math.floor(rand() * alphabet.length)];
    const scan = model.parseScan(text);
    assert.ok(Array.isArray(scan.boards));
    model.visibleBoards(scan.boards, true).forEach((board) => {
      model.shortName(board); model.portName(board); model.statusText(board); model.statusUrgent(board);
    });
    model.trackArrivals({}, scan.boards, round, true, true, 0, 60000);
    model.setupSummary(model.parseDoctor(text));
    const status = model.parseStatus(text);
    model.targetRows(status.targets);
    model.auditText(status.audit);
    model.flashText(status.targets);
  }
});

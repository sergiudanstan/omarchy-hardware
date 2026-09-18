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

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

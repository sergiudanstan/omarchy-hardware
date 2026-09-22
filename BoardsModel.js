.pragma library

// Only plain objects: a null or a string in a list would throw inside a QML
// binding and blank the panel.
function _objects(list) {
  return Array.isArray(list) ? list.filter(function (item) {
    return item !== null && typeof item === "object" && !Array.isArray(item);
  }) : [];
}

function parseScan(raw) {
  var text = String(raw || "").trim();
  if (!text) return { ok: false, boards: [], error: "no output" };

  try {
    var parsed = JSON.parse(text);
    return {
      ok: parsed.ok === true,
      boards: _objects(parsed.boards),
      supportedBoards: Array.isArray(parsed.supported_boards) ? parsed.supported_boards.map(String) : [],
      error: parsed.error || ""
    };
  } catch (e) {
    return { ok: false, boards: [], error: "unparseable scan output" };
  }
}

function visibleBoards(boards, hideUnknown) {
  if (!hideUnknown) return boards;
  return boards.filter(function (b) { return b.board_type !== "unknown"; });
}

function shortName(board) {
  var name = board.friendly_name || board.product || "Serial device";
  if (board.label) name = board.label + " · " + name;
  return name.length > 40 ? name.slice(0, 39) + "…" : name;
}

function portName(board) {
  var port = String(board.port || "");
  if (board.kind === "uf2_bootloader" && board.volume) {
    // The mount point's last component (RPI-RP2, RP2350), not the whole path.
    return port.replace(/\/+$/, "").split("/").pop();
  }
  if (port.indexOf("usb:") === 0) return "USB " + port.slice(4);
  return port.replace("/dev/", "");
}

function idPair(board) {
  if (!board.vid || !board.pid) return "";
  return board.vid + ":" + board.pid;
}

// Pico BOOTSEL volumes and HID-only boards have no serial port, so "not
// writable" there is a state, not a permission problem.
function statusText(board) {
  if (board.kind === "uf2_bootloader") return board.volume ? "BOOTSEL, ready for UF2" : "BOOTSEL, not mounted";
  if (board.kind === "hid") return "HID only, no serial port";
  if (!board.writable) return "no access";
  if (board.busy) return "in use";
  return "ready";
}

function statusUrgent(board) {
  return !board.kind && !board.writable;
}

function parseDoctor(raw) {
  var text = String(raw || "").trim();
  if (!text) return { ready: false, problems: [], checked: false };

  try {
    var parsed = JSON.parse(text);
    return {
      ready: parsed.ready === true,
      problems: _objects(parsed.problems),
      pendingRelogin: parsed.pending_relogin === true,
      checked: true
    };
  } catch (e) {
    return { ready: false, problems: [], checked: false };
  }
}

function setupSummary(doctor) {
  if (!doctor.checked) return "Checking setup…";
  if (doctor.pendingRelogin) return "Log out and back in to finish setup";
  if (doctor.ready) return "";
  var count = doctor.problems.length;
  return count === 1 ? doctor.problems[0].label : count + " setup steps remaining";
}

function widgetVisible(boards, showWhenNoBoards, setupIncomplete) {
  return boards.length > 0 || showWhenNoBoards || setupIncomplete;
}

function parseStatus(raw) {
  var text = String(raw || "").trim();
  var empty = { checked: false, ok: false, configError: "", error: "", targets: null, audit: null };
  if (!text) return empty;

  try {
    var parsed = JSON.parse(text);
    return {
      checked: true,
      ok: parsed.ok === true,
      configError: parsed.config_error || "",
      // A failure of the status script itself, not of config.toml.
      error: parsed.error || "",
      targets: parsed.targets || null,
      audit: parsed.audit || null
    };
  } catch (e) {
    return { checked: true, ok: false, configError: "", error: "unparseable status output", targets: null, audit: null };
  }
}

function _names(list) {
  return Array.isArray(list) ? list.join(", ") : "";
}

// One { label, detail, off } row per configured family; families with nothing
// configured are left out so the section stays short.
function targetRows(targets) {
  if (!targets) return [];
  var rows = [];
  if (targets.pi && targets.pi.length) rows.push({ label: "Raspberry Pi", detail: _names(targets.pi), off: false });
  if (targets.jetson && targets.jetson.length) rows.push({ label: "Jetson", detail: _names(targets.jetson), off: false });

  var ming = targets.ming || {};
  var mingParts = [];
  ["mqtt", "influxdb", "nodered", "grafana"].forEach(function (kind) {
    if (ming[kind] && ming[kind].length) mingParts.push(kind + ": " + _names(ming[kind]));
  });
  if (mingParts.length) rows.push({ label: "MING stack", detail: mingParts.join(" · "), off: ming.allow !== true });

  var weintek = targets.weintek || {};
  var weintekParts = [];
  if (weintek.mqtt && weintek.mqtt.length) weintekParts.push("mqtt: " + _names(weintek.mqtt));
  if (weintek.modbus && weintek.modbus.length) weintekParts.push("modbus: " + _names(weintek.modbus));
  if (weintek.opcua) weintekParts.push("opc ua: " + weintek.opcua + " endpoint" + (weintek.opcua === 1 ? "" : "s"));
  if (weintekParts.length) rows.push({ label: "Weintek HMI", detail: weintekParts.join(" · "), off: weintek.allow !== true });
  return rows;
}

function flashText(targets) {
  if (!targets || typeof targets.flash !== "boolean") return "";
  return targets.flash ? "Flashing: allowed (each upload still needs confirm=true)" : "Flashing: disabled in config.toml";
}

function auditText(log) {
  if (!log) return "Audit log: not checked";
  if (log.ok) return "Audit log: intact, " + log.records + " record" + (log.records === 1 ? "" : "s");
  if (log.broken_at_line) return "Audit log: broken at line " + log.broken_at_line + " (" + (log.reason || "") + ")";
  return "Audit log: " + (log.reason || "cannot be verified");
}

// ---------------------------------------------------------------- arrivals

// Only these ports are ever handed to a helper script. Device-supplied strings
// (product names, by-id paths) never reach a command line.
var PORT_PATTERN = /^\/dev\/tty(ACM|USB)[0-9]{1,3}$/;

// A board that drops off the bus and comes back within this window was reset
// or reflashed (native-USB boards re-enumerate during upload), not plugged in.
var ARRIVAL_GRACE_MS = 60000;

// The window must outlast a few scans: at a 60 s interval, the gap between two
// good scans is itself a little over 60 s.
function arrivalGraceMs(intervalMs) {
  return Math.max(ARRIVAL_GRACE_MS, 3 * (Number(intervalMs) || 0));
}

function boardKey(board) {
  var serial = board.serial || "";
  return [board.vid || "", board.pid || "", serial, serial ? "" : (board.port || "")].join("|");
}

// Keys for one scan. Two boards that report the same serial (CP210x clones
// often all say "0001") are told apart by port, so the second is not taken for
// the first. One of them keeps the plain key, so the board that was already
// there is not announced again when its twin is plugged in.
function scanKeys(boards, known) {
  var has = function (key) { return Object.prototype.hasOwnProperty.call(known, key); };
  var counts = {};
  boards.forEach(function (board) {
    var key = boardKey(board);
    counts[key] = (counts[key] || 0) + 1;
  });
  var plainTaken = {};
  // Boards already tracked under a port key keep it.
  var keys = boards.map(function (board) {
    var key = boardKey(board);
    var portKey = key + "|" + (board.port || "");
    return counts[key] > 1 && has(portKey) ? portKey : null;
  });
  return boards.map(function (board, index) {
    if (keys[index] !== null) return keys[index];
    var key = boardKey(board);
    if (counts[key] > 1 && plainTaken[key]) return key + "|" + (board.port || "");
    plainTaken[key] = true;
    return key;
  });
}

// seen maps boardKey -> last time it was present. Returns the new map and the
// boards that count as newly plugged in. The first scan after the shell starts
// only primes the map: boards already there did not just arrive. So does the
// first good scan after a gap longer than the grace window (a hung or failing
// scan script): the widget could not see what changed meanwhile, and treating
// everything as new would announce boards that never left.
function trackArrivals(seen, boards, nowMs, includeUnknown, primed, lastOkMs, graceMs) {
  var grace = typeof graceMs === "number" && graceMs > 0 ? graceMs : ARRIVAL_GRACE_MS;
  if (typeof lastOkMs === "number" && lastOkMs > 0 && nowMs - lastOkMs > grace) primed = false;
  var next = {};
  Object.keys(seen || {}).forEach(function (key) {
    if (nowMs - seen[key] <= grace) next[key] = seen[key];
  });
  var arrived = [];
  var list = boards || [];
  var keys = scanKeys(list, next);
  list.forEach(function (board, index) {
    var key = keys[index];
    var known = Object.prototype.hasOwnProperty.call(next, key);
    next[key] = nowMs;
    if (!primed || known) return;
    if (!PORT_PATTERN.test(String(board.port || ""))) return;
    if (!includeUnknown && board.board_type === "unknown") return;
    arrived.push(board);
  });
  return { seen: next, arrived: arrived };
}

function canStartProject(board) {
  return PORT_PATTERN.test(String(board.port || ""));
}

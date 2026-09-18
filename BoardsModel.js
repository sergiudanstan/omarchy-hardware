.pragma library

function parseScan(raw) {
  var text = String(raw || "").trim();
  if (!text) return { ok: false, boards: [], error: "no output" };

  try {
    var parsed = JSON.parse(text);
    return {
      ok: parsed.ok === true,
      boards: Array.isArray(parsed.boards) ? parsed.boards : [],
      supportedBoards: Array.isArray(parsed.supported_boards) ? parsed.supported_boards : [],
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

function glyphFor(boards) {
  return boards.length > 0 ? "󰈚" : "󰈚";
}

function labelFor(boards) {
  if (boards.length === 0) return "";
  return String(boards.length);
}

function shortName(board) {
  var name = board.friendly_name || board.product || "Serial device";
  return name.length > 28 ? name.slice(0, 27) + "…" : name;
}

function portName(board) {
  return String(board.port || "").replace("/dev/", "");
}

function idPair(board) {
  if (!board.vid || !board.pid) return "";
  return board.vid + ":" + board.pid;
}

function statusText(board) {
  if (!board.writable) return "no access";
  if (board.busy) return "in use";
  return "ready";
}

function parseDoctor(raw) {
  var text = String(raw || "").trim();
  if (!text) return { ready: false, problems: [], checked: false };

  try {
    var parsed = JSON.parse(text);
    return {
      ready: parsed.ready === true,
      problems: Array.isArray(parsed.problems) ? parsed.problems : [],
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
  var empty = { checked: false, ok: false, configError: "", targets: null, audit: null };
  if (!text) return empty;

  try {
    var parsed = JSON.parse(text);
    return {
      checked: true,
      ok: parsed.ok === true,
      configError: parsed.config_error || "",
      targets: parsed.targets || null,
      audit: parsed.audit || null
    };
  } catch (e) {
    return { checked: true, ok: false, configError: "unparseable status output", targets: null, audit: null };
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
  if (weintek.opcua) weintekParts.push("opc ua: " + weintek.opcua + " endpoint" + (weintek.opcua === 1 ? "" : "s"));
  if (weintekParts.length) rows.push({ label: "Weintek HMI", detail: weintekParts.join(" · "), off: weintek.allow !== true });
  return rows;
}

function auditText(log) {
  if (!log) return "Audit log: not checked";
  if (log.ok) return "Audit log: intact, " + log.records + " record" + (log.records === 1 ? "" : "s");
  if (log.broken_at_line) return "Audit log: broken at line " + log.broken_at_line + " (" + (log.reason || "") + ")";
  return "Audit log: " + (log.reason || "cannot be verified");
}

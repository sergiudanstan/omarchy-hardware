.pragma library

function parseScan(raw) {
  var text = String(raw || "").trim();
  if (!text) return { ok: false, boards: [], error: "no output" };

  try {
    var parsed = JSON.parse(text);
    return {
      ok: parsed.ok === true,
      boards: Array.isArray(parsed.boards) ? parsed.boards : [],
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

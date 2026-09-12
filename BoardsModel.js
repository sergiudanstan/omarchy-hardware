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

function supportedBoards() {
  return [
    "Arduino UNO R4 WiFi",
    "Arduino Nano ESP32",
    "Arduino GIGA R1 WiFi",
    "Arduino Portenta H7",
    "Arduino MKR ZERO",
    "Arduino MKR FOX 1200",
    "Arduino MKR GSM 1400",
    "Arduino MKR WAN 1300",
    "Arduino MKR NB 1500",
    "Arduino MKR Vidor 4000",
    "Arduino MKR WAN 1310",
    "Raspberry Pi Pico 2 (RP2350)",
    "Adafruit Feather M0",
    "Adafruit Feather M4 Express",
    "Adafruit Feather RP2040",
    "Adafruit QT Py RP2040",
    "Adafruit QT Py SAMD21",
    "Adafruit Trinket M0",
    "Adafruit Circuit Playground Express",
    "Adafruit Feather ESP32-S3",
    "Adafruit QT Py ESP32-S3",
    "Seeed XIAO RP2040",
    "Seeed XIAO nRF52840",
    "Seeed Wio Terminal",
    "SparkFun Pro Micro 5V",
    "SparkFun Pro Micro 3.3V",
    "SparkFun Pro Micro RP2040",
    "STM32 Nucleo-64 (ST-LINK V2-1)",
    "STM32 Nucleo (ST-LINK V3)",
    "BBC micro:bit v2"
  ];
}

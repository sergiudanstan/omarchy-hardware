import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "BoardsModel.js" as Model

Panel {
  id: root
  moduleName: "io.github.sergiudanstan.hardware"
  // The base Panel registers open/close/toggle for this target, so a keybind can
  // run `qs ipc call io.github.sergiudanstan.hardware toggle`.
  ipcTarget: "io.github.sergiudanstan.hardware"

  // Absolute path to this plugin folder, so the helper scripts can be spawned
  // regardless of where the user installed it.
  function pluginDirFromUrl(url) {
    var s = String(url || "")
    if (s.indexOf("file://") === 0)
      s = s.slice(7)
    try { s = decodeURIComponent(s) } catch (e) {}
    return s.replace(/\/$/, "")
  }

  readonly property string pluginDir: pluginDirFromUrl(Qt.resolvedUrl("."))

  readonly property int scanIntervalSec: Math.max(2, Math.min(60, setting("scanIntervalSec", 5)))
  readonly property bool showWhenNoBoards: setting("showWhenNoBoards", false) === true
  readonly property bool showSupportedBoards: setting("showSupportedBoards", true) === true
  readonly property bool hideUnknownSerial: setting("hideUnknownSerial", true) === true
  readonly property bool notifyOnArrival: setting("notifyOnArrival", true) === true
  readonly property bool notifyUnknownAdapters: setting("notifyUnknownAdapters", false) === true

  // boardKey -> last time seen; see Model.trackArrivals.
  property var seenBoards: ({})
  property bool arrivalsPrimed: false
  property real lastScanOkMs: 0
  property real lastDoctorMs: 0
  property bool catalogExpanded: false

  property var scan: ({ ok: false, boards: [], error: "" })
  property var doctor: ({ ready: false, problems: [], pendingRelogin: false, checked: false })
  property var status: ({ checked: false, ok: false, configError: "", error: "", targets: null, audit: null })

  readonly property var boards: Model.visibleBoards(scan.boards || [], hideUnknownSerial)
  readonly property var supportedBoards: scan.supportedBoards || []
  readonly property bool setupIncomplete: doctor.checked && !doctor.ready
  readonly property string setupSummary: Model.setupSummary(doctor)
  readonly property var targetRows: Model.targetRows(status.targets)
  readonly property bool auditBroken: status.checked && status.audit !== null && status.audit.ok !== true

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  visible: Model.widgetVisible(boards, showWhenNoBoards, setupIncomplete)
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Setup rarely changes, so the scan timer re-checks it at most once a minute;
  // opening the panel or an explicit refresh checks it at once.
  readonly property int doctorIntervalMs: 60000

  function refresh() {
    if (!scanProc.running) scanProc.running = true
    if (Date.now() - root.lastDoctorMs >= root.doctorIntervalMs) refreshDoctor()
  }

  function refreshDoctor() {
    if (doctorProc.running) return
    root.lastDoctorMs = Date.now()
    doctorProc.running = true
  }

  function refreshAll() {
    refresh()
    refreshDoctor()
    refreshStatus()
  }

  // Targets and the audit log change rarely and verifying the log reads all of
  // it, so this runs when the panel opens or on an explicit refresh, not on the timer.
  function refreshStatus() {
    if (!statusProc.running) statusProc.running = true
  }

  onOpenedChanged: if (opened) {
    refreshStatus()
    refreshDoctor()
  }

  function quotedPath(path) {
    return "'" + String(path).replace(/'/g, "'\\''") + "'"
  }

  function runSetup() {
    if (bar) bar.run("omarchy-launch-terminal " + quotedPath(pluginDir + "/bin/setup.sh") + " --pause")
    root.close()
  }

  function trackArrivals() {
    if (!root.scan.ok) return
    var now = Date.now()
    // The filtered list: a board the panel hides is not announced either.
    var result = Model.trackArrivals(root.seenBoards, root.boards, now,
                                     root.notifyUnknownAdapters, root.arrivalsPrimed, root.lastScanOkMs,
                                     Model.arrivalGraceMs(root.scanIntervalSec * 1000))
    root.seenBoards = result.seen
    root.arrivalsPrimed = true
    root.lastScanOkMs = now
    if (!root.notifyOnArrival || !bar) return
    // The helper waits on the notification's buttons, so it runs detached.
    result.arrived.forEach(function (board) {
      bar.run(quotedPath(root.pluginDir + "/bin/board-arrived.sh") + " " + quotedPath(board.port))
    })
  }

  function startProject(board) {
    if (bar && Model.canStartProject(board))
      bar.run(quotedPath(root.pluginDir + "/bin/start-project.sh") + " " + quotedPath(board.port))
    root.close()
  }

  function viewSetupScript() {
    if (bar) bar.run("omarchy-launch-editor " + quotedPath(pluginDir + "/bin/setup.sh"))
    root.close()
  }

  Component.onCompleted: refresh()

  Timer {
    interval: root.scanIntervalSec * 1000
    running: true
    repeat: true
    triggeredOnStart: false
    onTriggered: root.refresh()
  }

  // Async only: a synchronous call here would block the whole shell process.
  Process {
    id: scanProc
    command: [root.pluginDir + "/bin/scan-boards.sh"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.scan = Model.parseScan(text)
        root.trackArrivals()
      }
    }
  }

  Process {
    id: statusProc
    command: [root.pluginDir + "/bin/panel-status.sh"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.status = Model.parseStatus(text)
    }
  }

  Process {
    id: doctorProc
    command: [root.pluginDir + "/bin/doctor.sh"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.doctor = Model.parseDoctor(text)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.setupIncomplete && root.boards.length === 0 ? "󰀦" : "󰈚"
    slotSize: Style.bar.statusSlot
    tooltipText: ""

    onPressed: function (buttonCode) {
      if (buttonCode === Qt.MiddleButton) root.refreshAll()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(520))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onTextKey: function (t) {
        if (t === "r" || t === "R") root.refreshAll()
        else if (t === "s" || t === "S") root.runSetup()
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: column
          width: panelFlick.width
          spacing: Style.space(10)

          Text {
            text: "Hardware"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
            textFormat: Text.PlainText
          }

          Rectangle {
            width: parent.width
            visible: root.setupIncomplete
            radius: Style.space(6)
            color: Qt.rgba(root.urgent.r, root.urgent.g, root.urgent.b, 0.12)
            implicitHeight: setupColumn.implicitHeight + Style.space(16)

            Column {
              id: setupColumn
              x: Style.space(10)
              y: Style.space(8)
              width: parent.width - Style.space(20)
              spacing: Style.space(6)

              Text {
                width: parent.width
                text: root.setupSummary
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
              }

              Repeater {
                model: root.doctor.problems || []
                Text {
                  width: setupColumn.width
                  text: "· " + (modelData.label || "")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                  textFormat: Text.PlainText
                }
              }

              Row {
                spacing: Style.space(8)
                visible: !root.doctor.pendingRelogin

                PanelActionButton {
                  iconText: "󰅢"
                  tooltipText: "Run setup in a terminal"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.runSetup()
                }

                PanelActionButton {
                  iconText: "󰈔"
                  tooltipText: "Open setup.sh in your editor"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.viewSetupScript()
                }

                PanelActionButton {
                  iconText: "󰑓"
                  tooltipText: "Rescan"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.refreshAll()
                }
              }
            }
          }

          Text {
            width: parent.width
            visible: root.boards.length === 0
            text: root.scan.ok ? "No boards connected."
                               : "Could not scan for boards" + (root.scan.error ? ": " + root.scan.error : ".")
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Repeater {
            model: root.boards

            Item {
              width: column.width
              implicitHeight: entry.implicitHeight + Style.space(10)

              Column {
                id: entry
                width: parent.width
                spacing: Style.space(2)

                Text {
                  width: parent.width
                  text: Model.shortName(modelData)
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                }

                Text {
                  width: parent.width
                  text: Model.portName(modelData) + "  ·  " + Model.idPair(modelData) + "  ·  " + Model.statusText(modelData)
                  color: Model.statusUrgent(modelData) ? root.urgent : root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                }

                PanelActionButton {
                  visible: Model.canStartProject(modelData)
                  iconText: "󰚩"
                  tooltipText: "Start a Claude session for this board"
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.startProject(modelData)
                }
              }
            }
          }

          // Collapsed by default: the catalog is long, and connected boards come first.
          Text {
            width: parent.width
            visible: root.showSupportedBoards
            text: (root.catalogExpanded ? "▾ " : "▸ ") + "Supported boards (" + root.supportedBoards.length + ")"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            textFormat: Text.PlainText

            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.catalogExpanded = !root.catalogExpanded
            }
          }

          Repeater {
            model: root.showSupportedBoards && root.catalogExpanded ? root.supportedBoards : []

            Text {
              width: column.width
              text: "· " + modelData
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              elide: Text.ElideRight
              textFormat: Text.PlainText
            }
          }

          Text {
            width: parent.width
            text: "Targets"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            textFormat: Text.PlainText
          }

          Text {
            width: parent.width
            visible: root.status.configError !== ""
            text: "config.toml: " + root.status.configError
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Text {
            width: parent.width
            visible: root.status.error !== ""
            text: "Status check failed: " + root.status.error
            color: root.urgent
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Text {
            width: parent.width
            visible: root.status.ok && root.targetRows.length === 0
            text: "No remote targets configured."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Repeater {
            model: root.targetRows

            Text {
              width: column.width
              text: modelData.label + (modelData.off ? " (off)" : "") + "  ·  " + modelData.detail
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              elide: Text.ElideRight
              textFormat: Text.PlainText
            }
          }

          Text {
            width: parent.width
            visible: text !== ""
            text: Model.flashText(root.status.targets)
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Text {
            width: parent.width
            visible: root.status.checked
            text: Model.auditText(root.status.audit)
            color: root.auditBroken ? root.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
            textFormat: Text.PlainText
          }

          Text {
            width: parent.width
            text: "r refresh · s setup"
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            textFormat: Text.PlainText
          }
        }
      }
    }
  }
}

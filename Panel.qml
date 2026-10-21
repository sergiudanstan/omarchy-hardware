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
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  // Foreground-tinted fills track light and dark themes. The green and orange
  // are status colors, the same role they have on Apple's system palette.
  readonly property color secondary: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.62)
  readonly property color tertiary: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.42)
  readonly property color groupFill: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.07)
  readonly property color hairline: Qt.rgba(foreground.r, foreground.g, foreground.b, 0.12)
  readonly property color readyTint: "#30D158"
  readonly property color busyTint: "#FF9F0A"
  readonly property int groupRadius: Style.space(16)
  readonly property int rowPadX: Style.space(14)
  readonly property int rowPadY: Style.space(11)

  readonly property string headerDetail: {
    if (!scan.ok && boards.length === 0) return "Waiting for a scan"
    if (boards.length === 0) return "No boards connected"
    return boards.length === 1 ? "1 board connected" : boards.length + " boards connected"
  }
  readonly property string flashLine: Model.flashText(status.targets)
  readonly property bool showDiagnostics: status.configError !== "" || status.error !== "" || flashLine !== "" || status.checked

  function statusTint(board) {
    if (Model.statusUrgent(board)) return urgent
    var label = Model.statusText(board)
    if (label === "ready" || label.indexOf("ready") >= 0) return readyTint
    if (label === "in use") return busyTint
    return secondary
  }

  function statusLabel(board) {
    var label = Model.statusText(board)
    if (label === "ready") return "Ready"
    if (label === "in use") return "In use"
    if (label === "no access") return "No access"
    if (label.indexOf("BOOTSEL") === 0) return "Bootsel"
    if (label.indexOf("HID") === 0) return "HID"
    return label
  }

  function boardMeta(board) {
    var parts = [Model.portName(board)]
    var id = Model.idPair(board)
    if (id) parts.push(id)
    var label = Model.statusText(board)
    if (label !== "ready" && label !== "in use" && label !== "no access") parts.push(label)
    return parts.join("  ·  ")
  }

  function monogram(board) {
    var name = Model.shortName(board)
    return name ? name.charAt(0).toUpperCase() : "•"
  }

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

  component SectionLabel: Text {
    property string label: ""
    width: parent ? parent.width : implicitWidth
    leftPadding: root.rowPadX
    text: label
    color: root.secondary
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    font.weight: Font.Medium
    textFormat: Text.PlainText
  }

  component StatusCapsule: Rectangle {
    property string label: ""
    property color tint: root.secondary
    radius: height / 2
    color: Qt.rgba(tint.r, tint.g, tint.b, 0.16)
    implicitWidth: capsuleLabel.implicitWidth + Style.space(16)
    implicitHeight: Math.max(Style.space(20), capsuleLabel.implicitHeight + Style.space(6))

    Text {
      id: capsuleLabel
      anchors.centerIn: parent
      text: label
      color: tint
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.weight: Font.DemiBold
      textFormat: Text.PlainText
    }
  }

  component TextPill: Rectangle {
    property string label: ""
    signal clicked()
    radius: height / 2
    implicitWidth: pillLabel.implicitWidth + Style.space(22)
    implicitHeight: Math.max(Style.space(28), pillLabel.implicitHeight + Style.space(10))
    color: pillMouse.containsMouse
      ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)
      : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.08)

    Behavior on color { ColorAnimation { duration: 120 } }

    Text {
      id: pillLabel
      anchors.centerIn: parent
      text: label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.weight: Font.Medium
      textFormat: Text.PlainText
    }

    MouseArea {
      id: pillMouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: parent.clicked()
    }
  }

  component KeyHint: Row {
    property string key: ""
    property string hint: ""
    spacing: Style.space(6)

    Rectangle {
      radius: Style.space(6)
      color: root.groupFill
      implicitWidth: keyLabel.implicitWidth + Style.space(12)
      implicitHeight: keyLabel.implicitHeight + Style.space(6)
      anchors.verticalCenter: parent.verticalCenter

      Text {
        id: keyLabel
        anchors.centerIn: parent
        text: key
        color: root.foreground
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.weight: Font.DemiBold
        textFormat: Text.PlainText
      }
    }

    Text {
      anchors.verticalCenter: parent.verticalCenter
      text: hint
      color: root.tertiary
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      textFormat: Text.PlainText
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(420))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(560))

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
          spacing: Style.space(18)

          Row {
            spacing: Style.space(12)

            Rectangle {
              width: Style.space(40)
              height: width
              radius: Style.space(12)
              color: root.groupFill

              Text {
                anchors.centerIn: parent
                text: root.setupIncomplete && root.boards.length === 0 ? "󰀦" : "󰈚"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.iconLarge
                textFormat: Text.PlainText
              }
            }

            Column {
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(1)

              Text {
                text: "Hardware"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                font.weight: Font.DemiBold
                font.letterSpacing: -0.4
                textFormat: Text.PlainText
              }

              Text {
                text: root.headerDetail
                color: root.secondary
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                textFormat: Text.PlainText
              }
            }
          }

          Rectangle {
            width: parent.width
            visible: root.setupIncomplete
            radius: root.groupRadius
            color: Qt.rgba(root.urgent.r, root.urgent.g, root.urgent.b, 0.14)
            implicitHeight: setupColumn.implicitHeight + Style.space(24)

            Column {
              id: setupColumn
              x: root.rowPadX
              y: Style.space(12)
              width: parent.width - root.rowPadX * 2
              spacing: Style.space(8)

              Text {
                width: parent.width
                text: root.setupSummary
                color: root.urgent
                font.family: root.fontFamily
                font.pixelSize: Style.font.subtitle
                font.weight: Font.DemiBold
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
              }

              Repeater {
                model: root.doctor.problems || []
                Text {
                  width: setupColumn.width
                  text: modelData.label || ""
                  color: root.secondary
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  wrapMode: Text.WordWrap
                  textFormat: Text.PlainText
                }
              }

              Row {
                spacing: Style.space(8)
                visible: !root.doctor.pendingRelogin

                TextPill {
                  label: "Set Up"
                  onClicked: root.runSetup()
                }

                TextPill {
                  label: "Script"
                  onClicked: root.viewSetupScript()
                }

                TextPill {
                  label: "Refresh"
                  onClicked: root.refreshAll()
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            SectionLabel { label: "Connected" }

            Rectangle {
              width: parent.width
              radius: root.groupRadius
              color: root.groupFill
              clip: true
              implicitHeight: boardList.implicitHeight

              Column {
                id: boardList
                width: parent.width

                Item {
                  width: parent.width
                  visible: root.boards.length === 0
                  implicitHeight: visible ? emptyText.implicitHeight + root.rowPadY * 2 : 0

                  Text {
                    id: emptyText
                    x: root.rowPadX
                    width: parent.width - root.rowPadX * 2
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.scan.ok ? "No boards connected"
                                       : "Could not scan for boards" + (root.scan.error ? ": " + root.scan.error : "")
                    color: root.scan.ok ? root.secondary : root.urgent
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    wrapMode: Text.WordWrap
                    textFormat: Text.PlainText
                  }
                }

                Repeater {
                  model: root.boards

                  Item {
                    id: boardRow
                    width: boardList.width
                    readonly property bool canOpen: Model.canStartProject(modelData)
                    implicitHeight: Math.max(Style.space(36), nameCol.implicitHeight) + root.rowPadY * 2

                    Rectangle {
                      anchors.fill: parent
                      color: boardHover.containsMouse
                        ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
                        : "transparent"
                      Behavior on color { ColorAnimation { duration: 120 } }
                    }

                    Rectangle {
                      id: mark
                      x: root.rowPadX
                      anchors.verticalCenter: parent.verticalCenter
                      width: Style.space(34)
                      height: width
                      radius: Style.space(10)
                      color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.08)

                      Text {
                        anchors.centerIn: parent
                        text: root.monogram(modelData)
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.subtitle
                        font.weight: Font.DemiBold
                        textFormat: Text.PlainText
                      }
                    }

                    Column {
                      id: nameCol
                      x: mark.x + mark.width + Style.space(12)
                      width: parent.width - x - trailing.width - Style.space(10)
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(2)

                      Text {
                        width: parent.width
                        text: Model.shortName(modelData)
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.subtitle
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                      }

                      Text {
                        width: parent.width
                        text: root.boardMeta(modelData)
                        color: root.secondary
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                      }
                    }

                    Row {
                      id: trailing
                      anchors.right: parent.right
                      anchors.rightMargin: root.rowPadX
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(6)

                      StatusCapsule {
                        anchors.verticalCenter: parent.verticalCenter
                        label: root.statusLabel(modelData)
                        tint: root.statusTint(modelData)
                      }

                      Text {
                        visible: boardRow.canOpen
                        anchors.verticalCenter: parent.verticalCenter
                        text: "›"
                        color: root.tertiary
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.heading
                        textFormat: Text.PlainText
                      }
                    }

                    Rectangle {
                      visible: index < root.boards.length - 1
                      anchors.left: nameCol.left
                      anchors.right: parent.right
                      anchors.bottom: parent.bottom
                      height: 1
                      color: root.hairline
                    }

                    MouseArea {
                      id: boardHover
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: boardRow.canOpen ? Qt.PointingHandCursor : Qt.ArrowCursor
                      onClicked: if (boardRow.canOpen) root.startProject(modelData)
                    }
                  }
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.showSupportedBoards

            SectionLabel { label: "Library" }

            Rectangle {
              width: parent.width
              radius: root.groupRadius
              color: root.groupFill
              clip: true
              implicitHeight: catalogCol.implicitHeight

              Column {
                id: catalogCol
                width: parent.width

                Item {
                  width: parent.width
                  implicitHeight: Style.space(44)

                  Text {
                    x: root.rowPadX
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Supported boards"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.subtitle
                    font.weight: Font.Medium
                    textFormat: Text.PlainText
                  }

                  Row {
                    anchors.right: parent.right
                    anchors.rightMargin: root.rowPadX
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(8)

                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      text: root.supportedBoards.length
                      color: root.tertiary
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      textFormat: Text.PlainText
                    }

                    Item {
                      width: Style.space(12)
                      height: Style.space(16)
                      anchors.verticalCenter: parent.verticalCenter

                      Text {
                        anchors.centerIn: parent
                        text: "›"
                        rotation: root.catalogExpanded ? 90 : 0
                        color: root.tertiary
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.heading
                        textFormat: Text.PlainText

                        Behavior on rotation { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                      }
                    }
                  }

                  MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.catalogExpanded = !root.catalogExpanded
                  }
                }

                Repeater {
                  model: root.catalogExpanded ? root.supportedBoards : []

                  Item {
                    width: catalogCol.width
                    implicitHeight: Style.space(36)

                    Rectangle {
                      anchors.left: parent.left
                      anchors.leftMargin: root.rowPadX
                      anchors.right: parent.right
                      anchors.top: parent.top
                      height: 1
                      color: root.hairline
                    }

                    Text {
                      x: root.rowPadX
                      width: parent.width - root.rowPadX * 2
                      anchors.verticalCenter: parent.verticalCenter
                      text: modelData
                      color: root.secondary
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      elide: Text.ElideRight
                      textFormat: Text.PlainText
                    }
                  }
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            SectionLabel { label: "Targets" }

            Rectangle {
              width: parent.width
              radius: root.groupRadius
              color: root.groupFill
              clip: true
              implicitHeight: targetCol.implicitHeight

              Column {
                id: targetCol
                width: parent.width

                Item {
                  width: parent.width
                  visible: root.status.configError !== "" || root.status.error !== "" || (root.status.ok && root.targetRows.length === 0) || (!root.status.checked && root.targetRows.length === 0)
                  implicitHeight: visible ? targetNote.implicitHeight + root.rowPadY * 2 : 0

                  Text {
                    id: targetNote
                    x: root.rowPadX
                    width: parent.width - root.rowPadX * 2
                    anchors.verticalCenter: parent.verticalCenter
                    wrapMode: Text.WordWrap
                    textFormat: Text.PlainText
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    color: (root.status.configError !== "" || root.status.error !== "") ? root.urgent : root.secondary
                    text: {
                      if (root.status.configError !== "") return "config.toml: " + root.status.configError
                      if (root.status.error !== "") return "Status check failed: " + root.status.error
                      if (!root.status.checked) return "Checking targets…"
                      return "No remote targets configured"
                    }
                  }

                  Rectangle {
                    visible: root.targetRows.length > 0
                    anchors.left: parent.left
                    anchors.leftMargin: root.rowPadX
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                    color: root.hairline
                  }
                }

                Repeater {
                  model: root.targetRows

                  Item {
                    width: targetCol.width
                    implicitHeight: Math.max(Style.space(36), targetText.implicitHeight) + root.rowPadY * 2

                    Column {
                      id: targetText
                      x: root.rowPadX
                      width: parent.width - x - (modelData.off ? offCapsule.width + Style.space(10) : 0) - root.rowPadX
                      anchors.verticalCenter: parent.verticalCenter
                      spacing: Style.space(2)

                      Text {
                        width: parent.width
                        text: modelData.label
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.subtitle
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                      }

                      Text {
                        width: parent.width
                        text: modelData.detail
                        color: root.secondary
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                      }
                    }

                    StatusCapsule {
                      id: offCapsule
                      visible: modelData.off
                      anchors.right: parent.right
                      anchors.rightMargin: root.rowPadX
                      anchors.verticalCenter: parent.verticalCenter
                      label: "Off"
                      tint: root.tertiary
                    }

                    Rectangle {
                      visible: index < root.targetRows.length - 1
                      anchors.left: targetText.left
                      anchors.right: parent.right
                      anchors.bottom: parent.bottom
                      height: 1
                      color: root.hairline
                    }
                  }
                }
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.showDiagnostics

            SectionLabel { label: "Diagnostics" }

            Rectangle {
              width: parent.width
              radius: root.groupRadius
              color: root.groupFill
              clip: true
              implicitHeight: diagCol.implicitHeight

              Column {
                id: diagCol
                width: parent.width

                Item {
                  width: parent.width
                  visible: root.flashLine !== ""
                  implicitHeight: visible ? flashText.implicitHeight + root.rowPadY * 2 : 0

                  Column {
                    id: flashText
                    x: root.rowPadX
                    width: parent.width - root.rowPadX * 2
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(2)

                    Text {
                      width: parent.width
                      text: "Flashing"
                      color: root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.weight: Font.Medium
                      textFormat: Text.PlainText
                    }

                    Text {
                      width: parent.width
                      text: root.flashLine.replace(/^Flashing:\s*/, "")
                      color: root.secondary
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                      textFormat: Text.PlainText
                    }
                  }
                }

                Item {
                  width: parent.width
                  visible: root.status.checked
                  implicitHeight: visible ? Math.max(Style.space(40), auditLabel.implicitHeight + root.rowPadY * 2) : 0

                  Rectangle {
                    visible: root.flashLine !== ""
                    anchors.left: parent.left
                    anchors.leftMargin: root.rowPadX
                    anchors.right: parent.right
                    anchors.top: parent.top
                    height: 1
                    color: root.hairline
                  }

                  Text {
                    id: auditLabel
                    x: root.rowPadX
                    width: parent.width - root.rowPadX * 2
                    anchors.verticalCenter: parent.verticalCenter
                    text: Model.auditText(root.status.audit)
                    color: root.auditBroken ? root.urgent : root.secondary
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                    wrapMode: Text.WordWrap
                    textFormat: Text.PlainText
                  }
                }
              }
            }
          }

          Row {
            spacing: Style.space(16)
            leftPadding: root.rowPadX

            KeyHint { key: "R"; hint: "Refresh" }
            KeyHint { key: "S"; hint: "Set up" }
          }
        }
      }
    }
  }
}

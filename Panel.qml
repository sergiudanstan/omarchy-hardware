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
  ipcTarget: "io.github.sergiudanstan.hardware"
  manageIpc: false

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
  readonly property bool hideUnknownSerial: setting("hideUnknownSerial", true) === true

  property var scan: ({ ok: false, boards: [], error: "" })
  property var doctor: ({ ready: false, problems: [], pendingRelogin: false, checked: false })

  readonly property var boards: Model.visibleBoards(scan.boards || [], hideUnknownSerial)
  readonly property bool setupIncomplete: doctor.checked && !doctor.ready
  readonly property string setupSummary: Model.setupSummary(doctor)

  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  visible: boards.length > 0 || showWhenNoBoards || setupIncomplete
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  function refresh() {
    if (!scanProc.running) scanProc.running = true
    if (!doctorProc.running) doctorProc.running = true
  }

  function quotedPath(path) {
    return "'" + String(path).replace(/'/g, "'\\''") + "'"
  }

  function runSetup() {
    if (bar) bar.run("omarchy-launch-terminal " + quotedPath(pluginDir + "/bin/setup.sh") + " --pause")
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
      onStreamFinished: root.scan = Model.parseScan(text)
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
      if (buttonCode === Qt.MiddleButton) root.refresh()
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
        if (t === "r" || t === "R") root.refresh()
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
                  onClicked: root.refresh()
                }
              }
            }
          }

          Text {
            width: parent.width
            visible: root.boards.length === 0
            text: root.scan.ok ? "No boards connected." : "Could not scan for boards."
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
                  color: modelData.writable ? root.dim : root.urgent
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                  elide: Text.ElideRight
                  textFormat: Text.PlainText
                }
              }
            }
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

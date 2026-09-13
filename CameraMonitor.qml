// Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import QtQuick
import QtQuick.Controls
import QtMultimedia
import Quickshell

FloatingWindow {
  id: monitor
  title: "Blue View OS - Camera Monitor"
  visible: false
  color: "#101924"
  implicitWidth: 760
  implicitHeight: 540
  minimumSize: Qt.size(480, 360)
  property bool hardwareEnabled: false
  property url logoUrl
  property int receivedFrames: 0
  property bool previewPaused: false
  readonly property var currentCamera: captureLoader.item ? captureLoader.item.camera : null
  readonly property bool captureActive: currentCamera ? currentCamera.active : false
  readonly property string captureError: currentCamera ? currentCamera.errorString : ""

  onVisibleChanged: {
    receivedFrames = 0
    if (visible) previewPaused = false
  }

  MediaDevices { id: devices }
  Loader {
    id: captureLoader
    active: monitor.visible && monitor.hardwareEnabled && !monitor.previewPaused
            && devices.videoInputs.length > 0
    sourceComponent: Component {
      CaptureSession {
        camera: Camera {
          cameraDevice: devices.videoInputs[selector.currentIndex] || devices.defaultVideoInput
          active: true
        }
        videoOutput: video
        // No microphone, recorder, or file output is attached.
      }
    }
  }

  Column {
    anchors.fill: parent
    anchors.margins: 18
    spacing: 12
    Row {
      width: parent.width
      height: 48
      spacing: 16
      Image {
        source: monitor.logoUrl
        width: 120
        height: 48
        fillMode: Image.PreserveAspectFit
      }
      Text {
        text: "CAMERA MONITOR"
        color: "#38b6ff"
        font.bold: true
        font.pixelSize: 20
        anchors.verticalCenter: parent.verticalCenter
      }
    }
    ComboBox {
      id: selector
      width: parent.width
      model: devices.videoInputs
      textRole: "description"
      enabled: devices.videoInputs.length > 0
      onCurrentIndexChanged: monitor.receivedFrames = 0
    }
    Rectangle {
      width: parent.width
      height: Math.max(140, monitor.height - 236)
      color: "#080e16"
      radius: 8
      VideoOutput {
        id: video
        anchors.fill: parent
        fillMode: VideoOutput.PreserveAspectFit
        visible: monitor.captureActive && monitor.hardwareEnabled
      }
      Text {
        anchors.centerIn: parent
        width: parent.width - 32
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        color: "#e5eef8"
        visible: !monitor.captureActive || monitor.captureError !== "" || monitor.receivedFrames === 0
        text: !monitor.hardwareEnabled ? "Enable the camera in the Blue View panel to preview it."
              : devices.videoInputs.length === 0 ? "No camera device is available."
              : monitor.captureError !== "" ? monitor.captureError
              : monitor.previewPaused ? "Preview paused"
              : "Waiting for camera frames…"
      }
    }
    Row {
      spacing: 10
      Button {
        text: monitor.previewPaused ? "Resume preview" : "Pause preview"
        enabled: monitor.hardwareEnabled && devices.videoInputs.length > 0
        onClicked: monitor.previewPaused = !monitor.previewPaused
      }
      Button {
        text: "Close preview"
        onClicked: monitor.visible = false
      }
    }
    Text {
      text: monitor.captureActive ? "Live preview · " + monitor.receivedFrames + " frames · Not recording"
                          : "Capture stopped · Not recording"
      color: "#b8c9dc"
      font.pixelSize: 12
    }
  }
  Connections {
    target: video.videoSink
    function onVideoFrameChanged() {
      if (monitor.captureActive) monitor.receivedFrames++
    }
  }
}

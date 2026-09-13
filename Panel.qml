// Omarchy-derived portions: Copyright (c) David Heinemeier Hansson; see third-party/OMARCHY-LICENSE.
// Blue View modifications: Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
// Blue View modifications are licensed under LICENSE.
import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import Quickshell.Services.Mpris
import Quickshell.Services.Pipewire
import qs.Ui
import qs.Commons
import "Model.js" as Model

Panel {
  id: root
  moduleName: "fperez.audio"
  ipcTarget: "omarchy.audio"

  readonly property var sink: Pipewire.defaultAudioSink
  readonly property var source: Pipewire.defaultAudioSource
  readonly property var nodes: Pipewire.nodes ? Pipewire.nodes.values : []
  readonly property var mprisPlayers: Mpris.players ? Mpris.players.values : []
  readonly property var mediaService: bar?.shell?.firstPartyServiceFor("omarchy.media")
  readonly property var activeMediaPlayer: mediaService ? mediaService.activePlayer : null

  readonly property var candidateSinks: {
    var list = []
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i]
      if (n && n.isSink && !n.isStream) list.push(n)
    }
    return list
  }

  readonly property var candidateSources: {
    var list = []
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i]
      if (n && !n.isSink && !n.isStream && isAudioSource(n)) {
        var name = n.name || ""
        if (name === "quickshell") continue
        list.push(n)
      }
    }
    return list
  }

  readonly property var candidateStreams: {
    var list = []
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i]
      if (!n || !n.isStream || !isPlaybackStream(n)) continue
      // A tuning's output is a playback stream too, but it is the processing
      // itself rather than an application, so it does not belong in the list.
      if (String(n.name || "").indexOf("omarchy_speaker_tuning") === 0) continue
      list.push(n)
    }
    return list
  }

  property var sinkAvailability: ({})
  property bool sinkAvailabilityLoaded: false

  // Identify true playback streams without reading node.properties here:
  // PwNode.properties is invalid until the node is bound, and reading it while
  // capture streams are appearing (for example, when Voxtype starts recording)
  // can destabilize Quickshell's Pipewire service. Quickshell versions differ
  // in how `type` is exposed (media.class, enum name, or numeric enum), but
  // playback streams consistently accept audio input from clients and publish
  // `isSink: true`; capture streams publish as stream sources.
  function isPlaybackStream(node) {
    return Model.isPlaybackStream(node)
  }

  function isAudioSource(node) {
    return Model.isAudioSource(node)
  }

  property var cachedAudioSinks: []
  property var cachedAudioSources: []
  property string masterSinkName: ""
  property string speakerRoutingSinkName: ""

  readonly property var rawAudioSinks: {
    var list = []
    for (var i = 0; i < candidateSinks.length; i++)
      if (sinkAvailable(candidateSinks[i])) list.push(candidateSinks[i])
    if (sink && list.indexOf(sink) < 0) list.unshift(sink)
    return list
  }

  readonly property var rawAudioSources: {
    var list = candidateSources.slice()
    if (source && list.indexOf(source) < 0) list.unshift(source)
    return list
  }

  readonly property var audioSinks: rawAudioSinks.length > 0 ? rawAudioSinks : cachedAudioSinks
  readonly property var audioSources: rawAudioSources.length > 0 ? rawAudioSources : cachedAudioSources

  readonly property var audioStreams: {
    var list = []
    for (var i = 0; i < candidateStreams.length; i++)
      if (candidateStreams[i].audio) list.push(candidateStreams[i])
    return list
  }

  // Feed Repeaters with panel-local snapshots instead of the live PipeWire
  // model. PipeWire can remove nodes while Quickshell is dispatching the
  // removal signal; rebuilding a Repeater from that signal path has crashed
  // in Quickshell's PipeWire service. The snapshot timer lets that mutation
  // settle first, and closed panels keep their repeaters detached entirely.
  property var displayAudioSinks: []
  property var displayAudioSources: []
  property var displayAudioStreams: []

  // A DSP sink -- a speaker tuning, or EasyEffects -- can be the selected output
  // without being where loudness lives: changing its volume alters the level going
  // *into* the processing, so the slider would move while the speakers did not,
  // and on a chain with a limiter it would change the tone as well.
  //
  // omarchy-audio-output-sink resolves the *current* default output through any
  // such sink to the physical one, which is the same definition the volume keys
  // and the output switcher use. Resolving the default (rather than "whatever a
  // tuning fronts") is what keeps this correct when headphones or HDMI are
  // selected while a tuning still exists.
  property string volumeSinkName: ""

  // Carry sub-notch touchpad deltas between wheel events.
  property real wheelAccumulator: 0

  readonly property var volumeSink: {
    if (volumeSinkName === "" || !sink) return sink
    if (volumeSinkName === String(sink.name)) return sink
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i]
      if (n && n.isSink && !n.isStream && String(n.name) === volumeSinkName && n.audio)
        return n
    }
    return sink
  }

  // Re-resolve whenever the selected output changes; the timer below is only a
  // safety net for the tuning being applied or removed underneath us.
  onSinkChanged: resolveVolumeSink()

  function resolveVolumeSink() {
    if (!volumeSinkProc.running) volumeSinkProc.running = true
  }

  readonly property real outputVolume: volumeSink && volumeSink.audio ? volumeSink.audio.volume : 0
  readonly property bool outputMuted: volumeSink && volumeSink.audio ? volumeSink.audio.muted : false
  readonly property real inputVolume: source && source.audio ? source.audio.volume : 0
  readonly property bool inputMuted: source && source.audio ? source.audio.muted : false

  readonly property string controlScript: Quickshell.env("HOME") + "/.local/bin/imac-audio-controls"
  property var speakerRoutes: ({
    "rear-left": "front-left",
    "rear-right": "front-right",
    "front-left": "rear-left",
    "front-right": "rear-right"
  })
  property var speakerMutes: ({
    "front-left": false,
    "front-right": false,
    "rear-left": false,
    "rear-right": false
  })
  readonly property var speakerDestinations: [
    { value: "rear-left", label: "Rear Left speaker" },
    { value: "rear-right", label: "Rear Right speaker" },
    { value: "front-left", label: "Front Left speaker" },
    { value: "front-right", label: "Front Right speaker" }
  ]
  readonly property var speakerChoices: ["Front Left", "Front Right", "Rear Left", "Rear Right"]
  readonly property var speakerChoiceValues: ["front-left", "front-right", "rear-left", "rear-right"]
  property bool routingExpanded: false
  property int speakerBalance: 0
  property int speakerFade: 0
  property bool cameraAvailable: false
  property bool cameraEnabled: false
  property bool controlBusy: false
  property string controlMessage: ""
  property bool spatialEnabled: false
  property bool eqEnabled: false
  property bool eqApplied: false
  property string eqPair: "front"
  property var eqGainsFront: [0, 0, 0, 0, 0, 0]
  property var eqGainsRear: [0, 0, 0, 0, 0, 0]
  property var eqDraftFront: [0, 0, 0, 0, 0, 0]
  property var eqDraftRear: [0, 0, 0, 0, 0, 0]
  property bool eqDirty: false
  readonly property var eqBandLabels: ["80 Hz", "250 Hz", "700 Hz", "2 kHz", "5 kHz", "10 kHz"]
  property real wavePhase: 0

  function routeChoiceIndex(destination) {
    return speakerChoiceValues.indexOf(speakerRoutes[destination])
  }

  function runControl(args) {
    if (deviceControlProc.running) return
    controlBusy = args.length > 0 && args[0] !== "status"
    if (controlBusy) controlMessage = "Applying…"
    deviceControlProc.command = [controlScript].concat(args)
    deviceControlProc.running = true
  }

  function receiveDeviceControlResult(raw) {
    controlBusy = false
    try {
      var result = JSON.parse(String(raw))
      if (result.routes) speakerRoutes = result.routes
      if (result.speakerMutes) speakerMutes = result.speakerMutes
      if (!positionApplyTimer.running && !balanceSlider.pressed && !fadeSlider.pressed) {
        if (result.speakerBalance !== undefined) speakerBalance = result.speakerBalance
        if (result.speakerFade !== undefined) speakerFade = result.speakerFade
      }
      if (result.masterSink !== undefined) masterSinkName = String(result.masterSink || "")
      if (result.speakerRoutingSink !== undefined)
        speakerRoutingSinkName = String(result.speakerRoutingSink || "")
      if (result.cameraAvailable !== undefined) cameraAvailable = result.cameraAvailable
      if (result.cameraEnabled !== undefined) cameraEnabled = result.cameraEnabled
      if (result.spatialEnabled !== undefined) spatialEnabled = result.spatialEnabled
      if (result.eqEnabled !== undefined) eqEnabled = result.eqEnabled
      if (result.eqApplied !== undefined) eqApplied = result.eqApplied
      if (result.eqGainsFront && result.eqGainsFront.length === 6) {
        eqGainsFront = result.eqGainsFront.slice()
        if (!eqDirty) eqDraftFront = result.eqGainsFront.slice()
      }
      if (result.eqGainsRear && result.eqGainsRear.length === 6) {
        eqGainsRear = result.eqGainsRear.slice()
        if (!eqDirty) eqDraftRear = result.eqGainsRear.slice()
      }
      controlMessage = result.ok ? (result.message || "Ready") : (result.error || "The change failed")
      if (result.ok && (result.message === "Equalizer applied" || result.message === "Equalizer bypassed")) {
        eqDirty = false
        eqDraftFront = eqGainsFront.slice()
        eqDraftRear = eqGainsRear.slice()
      } else if (result.ok && !eqDirty) {
        eqDraftFront = eqGainsFront.slice()
        eqDraftRear = eqGainsRear.slice()
      }
      scheduleDisplayAudioModelRefresh()
    } catch (e) {
      controlMessage = "Could not read audio control status"
    }
  }

  function setSpeakerRoute(destination, choiceIndex) {
    if (choiceIndex < 0 || choiceIndex >= speakerChoiceValues.length) return
    runControl(["set-route", destination, speakerChoiceValues[choiceIndex]])
  }

  function setSpeakerMute(speaker, muted) {
    runControl(["set-speaker-mute", speaker, muted ? "on" : "off"])
  }

  function setEqGain(index, value) {
    var next = (eqPair === "front" ? eqDraftFront : eqDraftRear).slice()
    next[index] = Math.round(value)
    if (eqPair === "front") eqDraftFront = next
    else eqDraftRear = next
    eqDirty = true
  }

  function applyEq() {
    if (controlBusy) return
    runControl(["eq-apply", eqDraftFront.join(","), eqDraftRear.join(","), eqEnabled ? "on" : "off"])
  }

  function toggleEq() {
    if (controlBusy) return
    eqEnabled = !eqEnabled
    applyEq()
  }

  function setCameraEnabled(enabled) {
    if (controlBusy) return
    runControl(["camera", enabled ? "on" : "off"])
  }

  function setSpatialEnabled(enabled) {
    if (controlBusy) return
    runControl(["spatial", enabled ? "on" : "off"])
  }

  function openEqWindow() {
    eqWindow.visible = true
    if (!deviceControlProc.running) runControl(["status"])
  }

  Timer {
    id: positionApplyTimer
    interval: 250
    onTriggered: {
      if (balanceSlider.pressed || fadeSlider.pressed || deviceControlProc.running) {
        restart()
        return
      }
      root.runControl(["set-position", String(root.speakerBalance), String(root.speakerFade)])
    }
  }

  onRawAudioSinksChanged: if (rawAudioSinks.length > 0) cachedAudioSinks = rawAudioSinks
  onRawAudioSourcesChanged: if (rawAudioSources.length > 0) cachedAudioSources = rawAudioSources

  // Single cursor model shared by keyboard and mouse. Sections:
  //   "output"  — output slider + sink device list
  //   "input"   — input slider + source device list
  //   "streams" — per-app playback streams
  // selectedIndex semantics within a section:
  //   -1            → on the slider row (h/l adjusts volume, m/Enter mute)
  //   0..N-1        → on the Nth device/stream row
  // Visuals derive from hasCursor/current via CursorSurface, never
  // from containsMouse — that's what keeps the highlight unique across
  // keyboard + mouse like wifi does.
  property string focusSection: "output"
  property int selectedIndex: -1
  property bool cursorActive: false

  // "header" is a virtual section for the hero output mute toggle; it sits
  // above the output section so the speaker can be muted from the keyboard.
  readonly property bool headerHasCursor: cursorActive && focusSection === "header"
  // Only channels that actually exist get a vote. A box with no default source
  // would otherwise report "input unmuted" forever, leaving the hero switch
  // able to mute but never to unmute.
  readonly property bool hasOutput: !!(volumeSink && volumeSink.audio)
  readonly property bool hasInput: !!(source && source.audio)
  readonly property bool anyAudible: (hasOutput && !outputMuted) || (hasInput && !inputMuted)
  readonly property string toggleHint: anyAudible ? "Mute" : "Unmute"

  readonly property color hoverFill: bar
    ? Style.hoverFillFor(bar.foreground, Color.accent)
    : "transparent"
  readonly property color selectedFill: bar
    ? Style.selectedFillFor(bar.foreground, Color.accent)
    : "transparent"

  function sectionCount(section) {
    if (section === "output") return displayAudioSinks.length
    if (section === "input") return displayAudioSources.length
    if (section === "streams") return displayAudioStreams.length
    return 0
  }

  function sectionVisible(section) {
    if (section === "output") return true
    if (section === "input") return displayAudioSources.length > 0 || !!source
    if (section === "streams") return displayAudioStreams.length > 0
    return false
  }

  function sectionHasSlider(section) {
    if (section === "output") return true
    if (section === "input") return !!source
    return false  // stream rows carry their own sliders inline; not a section-level slider
  }

  // Order of visible sections, recomputed reactively so dropping a section
  // (e.g. no input devices) doesn't leave the cursor pointing at it.
  readonly property var visibleSections: {
    var list = []
    if (sectionVisible("output")) list.push("output")
    if (sectionVisible("input")) list.push("input")
    if (sectionVisible("streams")) list.push("streams")
    return list
  }

  function moveCursor(delta) {
    var sections = visibleSections
    if (sections.length === 0) return
    if (focusSection === "header") {
      if (delta > 0) { focusSection = sections[0]; selectedIndex = sectionHasSlider(sections[0]) ? -1 : 0 }
      return
    }
    var sIdx = sections.indexOf(focusSection)
    if (sIdx < 0) { focusSection = sections[0]; selectedIndex = sectionHasSlider(focusSection) ? -1 : 0; return }

    var idx = selectedIndex
    var max = sectionCount(focusSection) - 1  // last device index
    var hasSlider = sectionHasSlider(focusSection)
    var floor = hasSlider ? -1 : 0  // -1 = slider row

    if (delta > 0) {
      if (idx < max) { selectedIndex = idx + 1; return }
      // Fall through to next section.
      if (sIdx < sections.length - 1) {
        focusSection = sections[sIdx + 1]
        selectedIndex = sectionHasSlider(focusSection) ? -1 : 0
      }
    } else {
      if (idx > floor) { selectedIndex = idx - 1; return }
      // Escape upward.
      if (sIdx > 0) {
        focusSection = sections[sIdx - 1]
        var prevMax = sectionCount(focusSection) - 1
        selectedIndex = prevMax >= 0 ? prevMax : (sectionHasSlider(focusSection) ? -1 : 0)
      } else {
        focusSection = "header"
      }
    }
  }

  function setHeaderCursor() {
    cursorActive = true
    focusSection = "header"
    selectedIndex = -1
  }

  function moveSection(delta) {
    var sections = visibleSections
    if (sections.length === 0) return
    var current = sections.indexOf(focusSection)
    if (current < 0) current = delta > 0 ? -1 : 0
    var next = (current + delta + sections.length) % sections.length
    focusSection = sections[next]
    selectedIndex = sectionHasSlider(focusSection) ? -1 : 0
    cursorActive = true
  }

  // Adjust the slider associated with the focused section. Output and
  // input sliders are real volume controls; on stream rows h/l adjusts
  // that stream's volume (so keyboard parity with the inline slider).
  // For device rows (selectedIndex >= 0 in output/input) h/l is a no-op
  // — the cursor is on a discrete row, not on the slider, and silently
  // moving the global slider would surprise the user.
  function adjustVolume(delta) {
    if (focusSection === "output" && selectedIndex === -1) {
      setOutputVolume(outputVolume + delta)
      return
    }
    if (focusSection === "input" && selectedIndex === -1) {
      setInputVolume(inputVolume + delta)
      return
    }
    if (focusSection === "streams" && selectedIndex >= 0 && selectedIndex < displayAudioStreams.length) {
      var s = displayAudioStreams[selectedIndex]
      if (s && s.audio) s.audio.volume = Math.max(0, Math.min(1.5, s.audio.volume + delta))
    }
  }

  // Enter/Space: activate whatever the cursor is on.
  function activateCursor() {
    if (focusSection === "header") { toggleAllMuted(); return }
    if (focusSection === "output") {
      if (selectedIndex === -1) { toggleOutputMute(); return }
      var sink = displayAudioSinks[selectedIndex]
      if (sink) setDefaultSink(sink)
      return
    }
    if (focusSection === "input") {
      if (selectedIndex === -1) { toggleInputMute(); return }
      var src = displayAudioSources[selectedIndex]
      if (src) setDefaultSource(src)
      return
    }
    if (focusSection === "streams" && selectedIndex >= 0) {
      var st = displayAudioStreams[selectedIndex]
      if (st && st.audio) st.audio.muted = !st.audio.muted
    }
  }

  onOpenedChanged: {
    if (opened) {
      if (!deviceControlProc.running) root.runControl(["status"])
      refreshDisplayAudioModels()
      focusSection = "output"
      selectedIndex = -1  // first keyboard cursor reveal starts on the output slider
      cursorActive = false
      Qt.callLater(resetScroll)
    } else {
      clearDisplayAudioModels()
    }
  }

  // Clamp / repair the cursor whenever any list refreshes underneath us.
  onAudioSinksChanged: scheduleDisplayAudioModelRefresh()
  onAudioSourcesChanged: scheduleDisplayAudioModelRefresh()
  onAudioStreamsChanged: scheduleDisplayAudioModelRefresh()

  function listSnapshot(list) {
    return Model.listSnapshot(list)
  }

  function refreshDisplayAudioModels() {
    if (!opened) return
    var visibleSinks = audioSinks.slice()
    var hasSpeakerRoutingSink = false
    for (var i = 0; i < visibleSinks.length; i++) {
      if (String(visibleSinks[i].name) === speakerRoutingSinkName) {
        hasSpeakerRoutingSink = true
        break
      }
    }
    if (hasSpeakerRoutingSink && masterSinkName) {
      visibleSinks = visibleSinks.filter(function(node) {
        return String(node.name) !== masterSinkName
      })
    }
    displayAudioSinks = listSnapshot(visibleSinks)
    displayAudioSources = listSnapshot(audioSources)
    displayAudioStreams = listSnapshot(audioStreams)
    clampCursor()
  }

  function scheduleDisplayAudioModelRefresh() {
    if (!opened) return
    audioModelRefreshTimer.restart()
  }

  function clearDisplayAudioModels() {
    audioModelRefreshTimer.stop()
    displayAudioSinks = []
    displayAudioSources = []
    displayAudioStreams = []
  }

  // Keep the keyboard-focused row inside the visible viewport of the
  // ScrollView. Each cursor target (slider rows, SinkRow, SourceRow,
  // StreamRow) calls this when it gains hasCursor. Without it, j/k can
  // walk the selection off-screen — wifi uses ListView.positionViewAtIndex
  // for this; we don't have that affordance with a multi-section Column.
  function resetScroll() {
    if (!scrollArea) return
    var flick = scrollArea.contentItem
    if (flick && flick.contentY !== undefined) flick.contentY = 0
  }

  function ensureCursorVisible(item) {
    if (!item || !scrollArea) return
    var flick = scrollArea.contentItem
    if (!flick || flick.contentY === undefined) return
    var margin = 6
    var maxY = Math.max(0, (flick.contentHeight || 0) - flick.height)
    if (maxY <= Style.space(24) || (root.focusSection === "output" && root.selectedIndex === -1)) {
      flick.contentY = 0
      return
    }
    var pt = item.mapToItem(flick.contentItem || flick, 0, 0)
    var top = pt.y
    var bottom = top + (item.height || 0)
    var viewTop = flick.contentY
    var viewBottom = viewTop + flick.height
    if (top < viewTop + margin) flick.contentY = Math.max(0, Math.min(maxY, top - margin))
    else if (bottom > viewBottom - margin)
      flick.contentY = Math.max(0, Math.min(maxY, bottom + margin - flick.height))
  }

  function clampCursor() {
    var sections = visibleSections
    if (!sections || !sections.length) return
    // "header" is virtual and never appears in visibleSections, so it has to
    // be let through: muting republishes the PipeWire snapshot, and clamping
    // would knock the cursor off the hero switch on every toggle.
    if (focusSection === "header") return
    if (sections.indexOf(focusSection) < 0) {
      focusSection = visibleSections[0]
      selectedIndex = sectionHasSlider(focusSection) ? -1 : 0
      return
    }
    var count = sectionCount(focusSection)
    var hasSlider = sectionHasSlider(focusSection)
    var floor = hasSlider ? -1 : 0
    if (selectedIndex > count - 1) selectedIndex = Math.max(floor, count - 1)
    if (selectedIndex < floor) selectedIndex = floor
  }

  function outputIcon(volume) {
    // Match the old Waybar pulseaudio glyph set. The Material Design speaker
    // icons render visually smaller in JetBrainsMono Nerd Font.
    if (!sink || !sink.audio) return ""
    if (isHeadphones(sink)) return "󰋋"
    if (outputMuted) return ""
    var v = volume === undefined ? outputVolume : volume
    if (v >= 0.67) return ""
    if (v >= 0.34) return ""
    if (v > 0) return ""
    return ""
  }

  function inputIcon() {
    if (!source || !source.audio) return "󰍭"
    return inputMuted ? "󰍭" : "󰍬"
  }

  // Playful mood-name for a given output volume. Mirrors the brightness
  // panel's brightnessName ladder; bands are wide enough that small
  // tweaks don't rename the room you're in.
  function outputVolumeName(volume, muted) {
    return Model.outputVolumeName(volume, muted)
  }

  function setOutputVolume(v) {
    if (!volumeSink || !volumeSink.audio) return outputVolume
    var volume = Math.max(0, Math.min(1, v))
    volumeSink.audio.volume = volume
    return volume
  }

  function showVolumeOsd(volume) {
    if (!bar || !bar.shell) return
    bar.shell.summon("omarchy.osd", JSON.stringify({
      icon: outputIcon(volume),
      value: Math.round(volume * 100)
    }))
  }

  function setInputVolume(v) {
    if (!source || !source.audio) return
    source.audio.volume = Math.max(0, Math.min(1, v))
  }

  function toggleOutputMute() {
    if (volumeSink && volumeSink.audio) volumeSink.audio.muted = !volumeSink.audio.muted
  }

  function toggleInputMute() {
    if (source && source.audio) source.audio.muted = !source.audio.muted
  }

  // The hero switch is the whole panel's on/off, so it carries both channels
  // at once. It reads as on while anything is still audible, which keeps
  // muting a single channel from the row below flipping the master switch.
  function toggleAllMuted() {
    var mute = anyAudible
    if (hasOutput) volumeSink.audio.muted = mute
    if (hasInput) source.audio.muted = mute
  }

  function setDefaultSink(node) {
    if (!node) return
    if (node.id !== undefined && node.name) {
      Quickshell.execDetached([
        "imac-audio-controls",
        "set-output",
        String(node.id),
        String(node.name)
      ])
    }
  }

  function setDefaultSource(node) {
    if (!node) return
    Pipewire.preferredDefaultAudioSource = node
    if (node.id !== undefined && node.name) {
      Quickshell.execDetached([
        "omarchy-audio-input-set-default",
        String(node.id),
        String(node.name)
      ])
    }
  }

  function sinkAvailable(node) {
    if (!node || !node.name || !sinkAvailabilityLoaded) return true
    var name = String(node.name)
    return sinkAvailability[name] !== false
  }

  function updateSinkAvailability(raw) {
    sinkAvailability = Model.parseSinkAvailability(raw)
    sinkAvailabilityLoaded = true
  }

  function friendlyDeviceLabel(text) {
    return Model.friendlyDeviceLabel(text)
  }

  function nodeLabel(node) {
    if (node && String(node.name) === "imac_fixed") return "Blue View Built-in Speakers"
    return Model.nodeLabel(node)
  }

  function nodeProps(node) {
    return Model.nodeProps(node)
  }

  function isHeadphones(node) {
    return Model.isHeadphones(node)
  }

  function sinkGlyph(node) {
    return Model.sinkGlyph(node)
  }

  function sourceGlyph(node) {
    return Model.sourceGlyph(node)
  }

  function friendlyStreamLabel(label) {
    return Model.friendlyStreamLabel(label)
  }

  function streamLabelKey(label) {
    return Model.streamLabelKey(label)
  }

  function streamLabelIsGeneric(label) {
    return Model.streamLabelIsGeneric(label)
  }

  function rawStreamLabel(node) {
    return Model.rawStreamLabel(node)
  }

  function mprisPlayerLabel(player) {
    return Model.mprisPlayerLabel(player)
  }

  function mprisPlayerIsProxy(player) {
    return Model.mprisPlayerIsProxy(player)
  }

  function streamRepresentsMprisPlayer(streamLabel, playerLabel) {
    return Model.streamRepresentsMprisPlayer(streamLabel, playerLabel)
  }

  function mprisLabelsFor(predicate) {
    return Model.mprisLabelsFor(mprisPlayers, predicate)
  }

  function matchingMprisStreamLabel(label) {
    return Model.matchingMprisStreamLabel(label, mprisPlayers)
  }

  function unmatchedMprisStreamLabel(label) {
    // Spotify exposes its PipeWire stream as "audio-src". For generic stream
    // names, use the one MPRIS player not already represented by another audio
    // stream (e.g. Chromium, or ALSA apps like cliamp).
    return Model.unmatchedMprisStreamLabel(label, mprisPlayers, displayAudioStreams)
  }

  function streamLabel(node) {
    return Model.streamLabel(node, mprisPlayers, displayAudioStreams)
  }

  function streamRepresentsPlayer(node, player) {
    return Model.streamRepresentsPlayer(node, player, mprisPlayers, displayAudioStreams)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  PwObjectTracker { objects: root.candidateSinks }
  PwObjectTracker { objects: root.candidateSources }
  PwObjectTracker { objects: root.audioStreams }

  PwNodePeakMonitor {
    id: inputPeakMonitor
    node: root.source
    enabled: root.opened && !!root.source
  }

  PwNodePeakMonitor {
    id: outputPeakMonitor
    node: root.volumeSink
    enabled: eqWindow.visible && !!root.volumeSink
  }

  Process {
    id: deviceControlProc
    command: [root.controlScript, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.receiveDeviceControlResult(text)
    }
  }

  Process {
    id: sinkAvailabilityProc
    command: ["omarchy-audio-sink-availability"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.updateSinkAvailability(text)
    }
  }

  Process {
    id: volumeSinkProc
    command: ["omarchy-audio-output-sink"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.volumeSinkName = String(text).trim()
    }
  }

  Component.onCompleted: root.runControl(["status"])

  Timer {
    interval: 5000
    running: root.opened
    repeat: true
    triggeredOnStart: true
    onTriggered: if (!sinkAvailabilityProc.running) sinkAvailabilityProc.running = true
  }

  // Runs whether or not the panel is open: the bar shows and scrolls the output
  // volume too, so an unresolved sink there would read and change the virtual
  // tuning sink instead of the speakers.
  Timer {
    interval: 15000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.resolveVolumeSink()
  }

  Timer {
    id: audioModelRefreshTimer
    interval: 75
    repeat: false
    onTriggered: root.refreshDisplayAudioModels()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.outputIcon()
    onPressed: function(b) {
      if (b === Qt.RightButton) root.toggleAllMuted()
      else root.toggle()
    }

    onWheelMoved: function(delta) {
      if (!root.hasOutput) return
      var wheel = Util.wheelSteps(root.wheelAccumulator, delta)
      root.wheelAccumulator = wheel.remainder
      if (wheel.steps === 0) return
      var volume = root.setOutputVolume(root.outputVolume + wheel.steps * 0.05)
      root.showVolumeOsd(volume)
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(panelColumn.implicitHeight, Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) {
        if (!root.cursorActive) { root.cursorActive = true; return }
        if (dy !== 0) root.moveCursor(dy)
        else if (dx !== 0) root.adjustVolume(dx * 0.05)
      }
      onActivateRequested: if (root.cursorActive) root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        // 'm' mutes whatever the cursor is on: focused section's slider
        // for output/input, the focused stream for streams.
        if (t === "m" || t === "M") {
          if (!root.cursorActive) return
          if (root.focusSection === "streams" && root.selectedIndex >= 0
              && root.selectedIndex < root.displayAudioStreams.length) {
            var s = root.displayAudioStreams[root.selectedIndex]
            if (s && s.audio) s.audio.muted = !s.audio.muted
          } else if (root.focusSection === "input") {
            root.toggleInputMute()
          } else {
            root.toggleOutputMute()
          }
        }
      }

      ScrollView {
        id: scrollArea
        anchors.fill: parent
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: panelColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        Binding {
          target: scrollArea.contentItem
          property: "interactive"
          value: panelColumn.implicitHeight > scrollArea.height
        }

        Column {
          id: panelColumn
          width: scrollArea.availableWidth
          spacing: Style.space(14)

          // ---------- Hero: speaker icon · title/status ----------
          Item {
            id: heroItem
            width: parent.width
            implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, powerSwitch.implicitHeight)

            // Status only — the switch owns muting, mouse and keyboard alike.
            Text {
              id: heroIcon
              textFormat: Text.PlainText
              text: root.outputIcon()
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.display
              opacity: root.outputMuted ? 0.5 : 1.0
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            // Compact on/off switch on the trailing edge of the hero, and the
            // header's only cursor target. Checked means something is still
            // audible, so muting everything reads as switching audio off.
            ToggleSwitch {
              id: powerSwitch
              checked: root.anyAudible
              hasCursor: root.headerHasCursor
              foreground: root.bar.foreground
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              onHovered: function(on) { if (on) root.setHeaderCursor() }
              onToggled: root.toggleAllMuted()

              PanelToolTip {
                visible: powerSwitch.containsMouse
                text: root.toggleHint
                fontFamily: root.bar.fontFamily
              }
            }

            Column {
              id: heroLabels
              anchors.left: heroIcon.right
              anchors.leftMargin: Style.space(14)
              anchors.right: parent.right
              anchors.rightMargin: powerSwitch.width + Style.space(12)
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              Row {
                width: parent.width
                spacing: Style.space(6)
                Item {
                  width: Style.space(26)
                  height: Style.space(22)
                  Rectangle {
                    width: Style.space(18)
                    height: Style.space(9)
                    radius: height / 2
                    color: "#38b6ff"
                    rotation: -32
                    x: 0
                    y: Style.space(10)
                  }
                  Rectangle {
                    width: Style.space(18)
                    height: Style.space(9)
                    radius: height / 2
                    color: "#ffde59"
                    rotation: -32
                    x: Style.space(8)
                    y: Style.space(3)
                  }
                }
                Text {
                  text: "Blue View Audio"
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.title
                  font.bold: true
                  elide: Text.ElideRight
                  width: parent.width - Style.space(32)
                }
              }

              Text {
                id: heroLabel
                textFormat: Text.PlainText
                text: root.outputVolumeName(
                  outputSlider.dragging ? outputSlider.liveValue : root.outputVolume,
                  root.outputMuted
                ).toUpperCase()
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                font.letterSpacing: 1.2
                elide: Text.ElideRight
                width: parent.width
              }
            }
          }

          // ---- Output devices ----
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(6)

            Item {
              width: parent.width
              implicitHeight: Math.max(outputHeader.implicitHeight, outputPercent.implicitHeight)

              PanelSectionHeader {
                id: outputHeader
                text: "OUTPUT VOLUME"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                id: outputPercent
                textFormat: Text.PlainText
                text: Math.round((outputSlider.dragging ? outputSlider.liveValue : root.outputVolume) * 100) + "%"
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                anchors.right: parent.right
                anchors.rightMargin: Style.space(6)
                anchors.verticalCenter: parent.verticalCenter
                opacity: root.outputMuted ? 0.5 : 1.0
              }
            }

            CursorSurface {
              id: outputSliderRow
              width: parent.width
              height: outputSlider.implicitHeight + Style.spacing.controlGap
              hasCursor: root.cursorActive && root.focusSection === "output" && root.selectedIndex === -1
              onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(outputSliderRow)
              foreground: root.bar.foreground
              outline: true

              PanelSlider {
                id: outputSlider
                bar: root.bar
                anchors.fill: parent
                anchors.leftMargin: Style.space(6)
                anchors.rightMargin: Style.space(6)
                minimum: 0
                maximum: 1
                step: 0.05
                value: root.outputVolume
                opacity: root.outputMuted ? 0.5 : 1.0
                enabled: !!root.sink

                onMoved: function(v) { root.setOutputVolume(v) }
                onRightClicked: root.toggleOutputMute()
              }

              HoverHandler {
                onHoveredChanged: if (hovered) {
                  root.cursorActive = true
                  root.focusSection = "output"
                  root.selectedIndex = -1
                }
              }
            }


          }

          // ---- Input ----
          PanelSeparator {
            visible: root.displayAudioSources.length > 0 || !!root.source
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.displayAudioSources.length > 0 || !!root.source

            Item {
              width: parent.width
              implicitHeight: Math.max(microphoneHeader.implicitHeight, microphonePercent.implicitHeight)

              PanelSectionHeader {
                id: microphoneHeader
                text: "MIC VOLUME"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
              }

              ToggleSwitch {
                id: microphonePowerSwitch
                checked: !root.inputMuted
                foreground: root.bar.foreground
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                enabled: !!root.source
                onToggled: root.toggleInputMute()
              }

              Text {
                id: microphonePercent
                textFormat: Text.PlainText
                text: Math.round((inputSlider.dragging ? inputSlider.liveValue : root.inputVolume) * 100) + "%"
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                anchors.right: microphonePowerSwitch.left
                anchors.rightMargin: Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                opacity: root.inputMuted ? 0.5 : 1.0
              }
            }

            CursorSurface {
              id: inputSliderRow
              visible: !!root.source
              width: parent.width
              height: inputControls.implicitHeight + Style.spacing.controlGap
              hasCursor: root.cursorActive && root.focusSection === "input" && root.selectedIndex === -1
              onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(inputSliderRow)
              foreground: root.bar.foreground
              outline: true

              Column {
                id: inputControls
                anchors.fill: parent
                anchors.leftMargin: Style.space(6)
                anchors.rightMargin: Style.space(6)
                spacing: Style.space(5)

                PanelSlider {
                  id: inputSlider
                  bar: root.bar
                  width: parent.width
                  minimum: 0
                  maximum: 1
                  step: 0.05
                  value: root.inputVolume
                  opacity: root.inputMuted ? 0.5 : 1.0
                  enabled: !!root.source

                  onMoved: function(v) { root.setInputVolume(v) }
                  onRightClicked: root.toggleInputMute()
                }

                Item {
                  width: parent.width
                  height: Style.space(16)
                  opacity: root.inputMuted ? 0.4 : 1.0

                  Rectangle {
                    anchors.left: parent.left
                    anchors.right: inputSignalDb.left
                    anchors.rightMargin: Style.space(10)
                    anchors.verticalCenter: parent.verticalCenter
                    height: Style.space(7)
                    radius: Style.space(3)
                    color: Util.alpha(root.bar.foreground, 0.18)

                    Rectangle {
                      height: parent.height
                      width: parent.width * Math.max(0, Math.min(1, inputPeakMonitor.peak))
                      radius: parent.radius
                      color: root.bar.foreground
                      Behavior on width { NumberAnimation { duration: 70 } }
                    }
                  }

                  Text {
                    id: inputSignalDb
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text: inputPeakMonitor.peak > 0.001
                      ? Math.max(-60, Math.round(20 * Math.log(inputPeakMonitor.peak) / Math.LN10)) + " dB"
                      : "−∞ dB"
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    width: Style.space(54)
                    horizontalAlignment: Text.AlignRight
                  }
                }
              }

              HoverHandler {
                onHoveredChanged: if (hovered) {
                  root.cursorActive = true
                  root.focusSection = "input"
                  root.selectedIndex = -1
                }
              }
            }


          }


          Column {
            width: parent.width
            spacing: Style.space(4)
            Text {
              width: parent.width
              text: "BALANCE · " + (root.speakerBalance === 0 ? "Center"
                    : Math.abs(root.speakerBalance) + "% toward " + (root.speakerBalance < 0 ? "Left" : "Right"))
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
            Slider {
              id: balanceSlider
              width: parent.width
              from: -100
              to: 100
              stepSize: 1
              value: root.speakerBalance
              onMoved: { root.speakerBalance = Math.round(value); positionApplyTimer.restart() }
              Accessible.name: "Left-right speaker balance"
            }
            Row {
              width: parent.width
              Text { width: parent.width / 2; text: "Left"; color: root.bar.foreground; font.pixelSize: Style.font.caption }
              Text { width: parent.width / 2; text: "Right"; horizontalAlignment: Text.AlignRight; color: root.bar.foreground; font.pixelSize: Style.font.caption }
            }
            Text {
              width: parent.width
              text: "FADE · " + (root.speakerFade === 0 ? "Center"
                    : Math.abs(root.speakerFade) + "% toward " + (root.speakerFade < 0 ? "Front" : "Rear"))
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
            }
            Slider {
              id: fadeSlider
              width: parent.width
              from: -100
              to: 100
              stepSize: 1
              value: root.speakerFade
              onMoved: { root.speakerFade = Math.round(value); positionApplyTimer.restart() }
              Accessible.name: "Front-rear speaker fade"
            }
            Row {
              width: parent.width
              Text { width: parent.width / 2; text: "Front"; color: root.bar.foreground; font.pixelSize: Style.font.caption }
              Text { width: parent.width / 2; text: "Rear"; horizontalAlignment: Text.AlignRight; color: root.bar.foreground; font.pixelSize: Style.font.caption }
            }
            Button {
              text: "Center balance and fade"
              onClicked: { root.speakerBalance = 0; root.speakerFade = 0; positionApplyTimer.restart() }
            }
          }

          // ---- Per-app streams ----
          PanelSeparator {
            visible: root.displayAudioStreams.length > 0
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(10)
            visible: root.displayAudioStreams.length > 0

            PanelSectionHeader {
              text: "APPLICATION VOLUMES"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            Repeater {
              model: root.displayAudioStreams

              StreamRow {
                required property var modelData
                required property int index
                width: panelColumn.width
                node: modelData
                rowIndex: index
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.displayAudioSinks.length > 0
            PanelSectionHeader {
              text: "OUTPUT DEVICE"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }
            Repeater {
              model: root.displayAudioSinks

              SinkRow {
                required property var modelData
                required property int index
                width: panelColumn.width
                node: modelData
                rowIndex: index
              }
            }
          }

          Column {
            width: parent.width
            spacing: Style.space(6)
            visible: root.displayAudioSources.length > 0
            PanelSectionHeader {
              text: "MICROPHONE DEVICE"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }
            Repeater {
              model: root.displayAudioSources

              SourceRow {
                required property var modelData
                required property int index
                width: panelColumn.width
                node: modelData
                rowIndex: index
              }
            }
          }

          // ---- EQ routing and tuning ----
          PanelSeparator {
            foreground: root.bar.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(8)

            ToolButton {
              width: parent.width
              text: (root.routingExpanded ? "▾  " : "▸  ") + "FOUR-CHANNEL ROUTING"
              Accessible.name: "Four-channel routing"
              Accessible.description: root.routingExpanded ? "Expanded" : "Collapsed"
              onClicked: root.routingExpanded = !root.routingExpanded
              contentItem: Text {
                text: parent.text
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
                verticalAlignment: Text.AlignVCenter
              }
            }

            Column {
              width: parent.width
              spacing: Style.space(8)
              visible: root.routingExpanded
            Text {
              text: "Stereo playback fills all four speakers; choose the source channel for each speaker."
              color: Qt.darker(root.bar.foreground, 1.35)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
              width: parent.width
            }

            Repeater {
              model: root.speakerDestinations

              Item {
                required property var modelData
                width: parent.width
                height: routeRow.implicitHeight

                Row {
                  id: routeRow
                  width: parent.width
                  spacing: Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter

                  Text {
                    text: modelData.label
                    color: root.bar.foreground
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.body
                    verticalAlignment: Text.AlignVCenter
                    width: parent.width - routeChoice.width - speakerMuteStatus.width
                      - speakerMuteSwitch.implicitWidth - Style.space(24)
                    elide: Text.ElideRight
                  }

                  ComboBox {
                    id: routeChoice
                    width: Style.space(138)
                    model: root.speakerChoices
                    currentIndex: root.routeChoiceIndex(modelData.value)
                    enabled: !root.controlBusy
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    onActivated: function(index) { root.setSpeakerRoute(modelData.value, index) }
                  }

                  Text {
                    id: speakerMuteStatus
                    width: Style.space(44)
                    text: root.speakerMutes[modelData.value] ? "MUTED" : "LIVE"
                    color: root.speakerMutes[modelData.value]
                      ? Color.accent : Qt.darker(root.bar.foreground, 1.25)
                    font.family: root.bar.fontFamily
                    font.pixelSize: Style.font.caption
                    horizontalAlignment: Text.AlignRight
                    verticalAlignment: Text.AlignVCenter
                  }

                  ToggleSwitch {
                    id: speakerMuteSwitch
                    checked: !root.speakerMutes[modelData.value]
                    busy: root.controlBusy
                    foreground: root.bar.foreground
                    onToggled: root.setSpeakerMute(modelData.value, checked)

                    PanelToolTip {
                      visible: speakerMuteSwitch.containsMouse
                      text: root.speakerMutes[modelData.value]
                        ? "Unmute " + modelData.label : "Mute " + modelData.label
                      fontFamily: root.bar.fontFamily
                    }
                  }
                }
              }
            }



            }

            Rectangle {
              width: parent.width
              height: Style.space(36)
              radius: Style.space(6)
              color: Util.alpha(root.bar.foreground, eqOpenMouse.containsMouse ? 0.13 : 0.07)
              border.width: 1
              border.color: Util.alpha(root.bar.foreground, 0.18)

              Text {
                anchors.centerIn: parent
                text: "OPEN EQUALIZER + MULTIWAVE DISPLAY"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }

              MouseArea {
                id: eqOpenMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: root.openEqWindow()
              }
            }

            PanelSeparator {
              foreground: root.bar.foreground
            }

            PanelSectionHeader {
              text: "SPATIAL AUDIO"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            Row {
              width: parent.width
              spacing: Style.space(8)
              Text {
                width: parent.width - openSpatialSwitch.width - Style.space(8)
                text: root.spatialEnabled ? "OPEN QUAD SPATIAL · ACTIVE" : "OPEN QUAD SPATIAL · OFF"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
              }
              ToggleSwitch {
                id: openSpatialSwitch
                checked: root.spatialEnabled
                foreground: root.bar.foreground
                enabled: !root.controlBusy
                onToggled: root.setSpatialEnabled(checked)
              }
            }

            Text {
              width: parent.width
              text: "Open-source PipeWire quad upmix: adds a phase-shaped rear fill to stereo. It is not Dolby Atmos and has no height/object decoding."
              color: Qt.darker(root.bar.foreground, 1.35)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Rectangle {
              width: parent.width
              height: Style.space(34)
              radius: Style.space(6)
              color: Util.alpha(root.bar.foreground, dolbyAccessMouse.containsMouse ? 0.13 : 0.07)
              border.width: 1
              border.color: Util.alpha(root.bar.foreground, 0.18)
              Text {
                anchors.centerIn: parent
                text: "DOLBY ATMOS FOR HEADPHONES · OFFICIAL"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
                font.bold: true
              }
              MouseArea {
                id: dolbyAccessMouse
                anchors.fill: parent
                hoverEnabled: true
                onClicked: Qt.openUrlExternally("https://www.dolby.com/experience/headphones/")
              }
            }

            Text {
              width: parent.width
              text: "Dolby purchase and activation happen in Dolby Access on Windows/Xbox. Linux has no supported Dolby license API for this PipeWire applet."
              color: Qt.darker(root.bar.foreground, 1.35)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          // ---- Camera privacy switch ----
          PanelSeparator {
            visible: root.cameraAvailable
            foreground: root.bar.foreground
          }

          Item {
            visible: root.cameraAvailable
            width: parent.width
            height: Math.max(cameraHeading.implicitHeight, cameraPowerSwitch.implicitHeight)

            PanelSectionHeader {
              id: cameraHeading
              text: "CAMERA"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            ToggleSwitch {
              id: cameraPowerSwitch
              checked: root.cameraEnabled
              foreground: root.bar.foreground
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              enabled: root.cameraAvailable && !root.controlBusy
              onToggled: root.setCameraEnabled(!root.cameraEnabled)
            }

            Text {
              text: root.cameraEnabled ? "Camera enabled" : "Camera disabled at USB device"
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              anchors.left: cameraHeading.right
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              anchors.right: cameraPowerSwitch.left
              anchors.rightMargin: Style.space(10)
              elide: Text.ElideRight
            }
          }

          Text {
            visible: root.controlMessage !== "" && root.controlMessage !== "Ready"
            width: parent.width
            text: root.controlMessage
            color: Qt.darker(root.bar.foreground, 1.3)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Button {
            visible: root.cameraAvailable
            text: "Open camera monitor"
            onClicked: cameraMonitorWindow.visible = true
          }


        }
      }
    }
  }

  CameraMonitor {
    id: cameraMonitorWindow
    hardwareEnabled: root.cameraEnabled
    logoUrl: Qt.resolvedUrl("blueview-logo-dark.svg")
  }

  FloatingWindow {
    id: eqWindow
    title: "Blue View OS - Audio EQ"
    visible: false
    color: Color.background
    implicitWidth: 780
    implicitHeight: 600
    minimumSize: Qt.size(600, 600)

    Column {
      anchors.fill: parent
      anchors.margins: Style.space(20)
      spacing: Style.space(8)

      Row {
        width: parent.width
        height: Style.space(64)
        spacing: Style.space(12)

        Image {
          id: blueViewLogo
          source: Qt.resolvedUrl("blueview-logo-dark.svg")
          width: Style.space(150)
          height: Style.space(64)
          fillMode: Image.PreserveAspectFit
          smooth: true
          anchors.verticalCenter: parent.verticalCenter
        }

        Column {
          id: eqTitle
          spacing: Style.space(3)
          anchors.verticalCenter: parent.verticalCenter
          Text {
            text: "MULTIWAVE EQUALIZER"
            color: Color.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.title
            font.bold: true
          }
          Text {
            text: "BLUE VIEW OS · FOUR CHANNEL"
            color: "#38b6ff"
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1
          }
        }

        Item {
          width: Math.max(0, parent.width - blueViewLogo.width - eqTitle.implicitWidth
            - eqModeLabel.implicitWidth - eqPowerSwitch.implicitWidth - Style.space(48))
          height: 1
        }

        Text {
          id: eqModeLabel
          text: root.eqEnabled ? (root.eqApplied ? "DSP ACTIVE" : "DSP MISSING") : "BYPASS"
          color: root.eqEnabled && !root.eqApplied ? "#ff746c"
            : (root.eqEnabled ? "#82ffc8" : Color.foreground)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
          anchors.verticalCenter: parent.verticalCenter
        }
        ToggleSwitch {
          id: eqPowerSwitch
          checked: root.eqEnabled
          foreground: Color.foreground
          enabled: !root.controlBusy
          anchors.verticalCenter: parent.verticalCenter
          onToggled: root.toggleEq()
        }
      }



      Row {
        width: parent.width
        height: Style.space(30)
        spacing: Style.space(8)

        Rectangle {
          width: (parent.width - Style.space(8)) / 2
          height: parent.height
          radius: Style.space(5)
          color: root.eqPair === "front" ? Util.alpha(Color.accent, 0.22) : "transparent"
          border.width: 1
          border.color: Util.alpha(Color.accent, root.eqPair === "front" ? 0.75 : 0.28)
          Text {
            anchors.centerIn: parent
            text: "FRONT PAIR  ·  L / R"
            color: Color.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
          MouseArea {
            anchors.fill: parent
            onClicked: root.eqPair = "front"
          }
        }

        Rectangle {
          width: (parent.width - Style.space(8)) / 2
          height: parent.height
          radius: Style.space(5)
          color: root.eqPair === "rear" ? Util.alpha(Color.accent, 0.22) : "transparent"
          border.width: 1
          border.color: Util.alpha(Color.accent, root.eqPair === "rear" ? 0.75 : 0.28)
          Text {
            anchors.centerIn: parent
            text: "REAR PAIR  ·  L / R"
            color: Color.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
          MouseArea {
            anchors.fill: parent
            onClicked: root.eqPair = "rear"
          }
        }
      }

      Text {
        text: (root.eqPair === "front" ? "FRONT LEFT + FRONT RIGHT" : "REAR LEFT + REAR RIGHT")
          + "  ·  SIX BANDS ±12 dB"
        color: Qt.darker(Color.foreground, 1.3)
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: true
        font.letterSpacing: 1
      }

      Row {
        width: parent.width
        height: Math.max(Style.space(150), parent.height * 0.28)
        spacing: Style.space(8)

        Repeater {
          model: root.eqBandLabels
          Column {
            required property int index
            required property string modelData
            width: (parent.width - Style.space(8) * 5) / 6
            height: parent.height
            spacing: Style.space(5)
            readonly property real currentGain: root.eqPair === "front"
              ? root.eqDraftFront[index] : root.eqDraftRear[index]

            Text {
              width: parent.width
              text: modelData
              color: Color.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
            }

            Slider {
              width: parent.width
              height: parent.height - Style.space(45)
              orientation: Qt.Vertical
              from: -12
              to: 12
              stepSize: 1
              value: currentGain
              enabled: !root.controlBusy
              onMoved: root.setEqGain(index, value)
            }

            Text {
              width: parent.width
              text: (currentGain > 0 ? "+" : "") + currentGain + " dB"
              color: Color.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              horizontalAlignment: Text.AlignHCenter
            }
          }
        }
      }

      Row {
        width: parent.width
        spacing: Style.space(10)
        Rectangle {
          width: Style.space(155)
          height: Style.space(34)
          radius: Style.space(6)
          color: Util.alpha(Color.accent, eqApplyMouse.containsMouse ? 0.28 : 0.18)
          border.width: 1
          border.color: Util.alpha(Color.accent, 0.55)
          Text {
            anchors.centerIn: parent
            text: root.controlBusy ? "APPLYING…" : "APPLY BOTH EQs"
            color: Color.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }
          MouseArea {
            id: eqApplyMouse
            anchors.fill: parent
            hoverEnabled: true
            enabled: !root.controlBusy
            onClicked: root.applyEq()
          }
        }
        Text {
          text: root.eqDirty ? "Unapplied changes" : root.controlMessage
          color: Qt.darker(Color.foreground, 1.35)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          anchors.verticalCenter: parent.verticalCenter
          elide: Text.ElideRight
          width: parent.width - Style.space(170)
        }
      }

      Item {
        id: waveScope
        width: parent.width
        height: Math.max(Style.space(145), parent.height * 0.29)

        Rectangle {
          anchors.fill: parent
          radius: Style.space(8)
          color: "#07110f"
          border.width: 1
          border.color: Util.alpha(Color.accent, 0.5)
        }

        Canvas {
          id: waveCanvas
          anchors.fill: parent
          anchors.margins: Style.space(10)
          antialiasing: true

          onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            ctx.strokeStyle = "rgba(105, 255, 195, 0.10)"
            ctx.lineWidth = 1
            for (var grid = 1; grid < 8; grid++) {
              var gx = width * grid / 8
              ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, height); ctx.stroke()
            }
            for (var row = 1; row < 4; row++) {
              var gy = height * row / 4
              ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(width, gy); ctx.stroke()
            }

            var peak = Math.max(0.04, Math.min(1, outputPeakMonitor.peak))
            var colors = ["#68ffc0", "#66d9ff", "#ffd166", "#ff72c6"]
            var rates = [1.15, 1.75, 2.55, 3.35]
            var phases = [0.0, 1.1, 2.3, 3.7]
            for (var wave = 0; wave < colors.length; wave++) {
              ctx.beginPath()
              ctx.lineWidth = wave === 0 ? 2.2 : 1.45
              ctx.strokeStyle = colors[wave]
              ctx.globalAlpha = 0.55 + peak * 0.45
              ctx.shadowColor = colors[wave]
              ctx.shadowBlur = 5
              for (var x = 0; x <= width; x += 3) {
                var t = x / Math.max(1, width)
                var envelope = 0.20 + peak * 0.48
                var y = height * 0.5
                  + Math.sin(t * Math.PI * 2 * rates[wave] + root.wavePhase + phases[wave]) * height * envelope * 0.40
                  + Math.sin(t * Math.PI * 2 * (rates[wave] * 2.13) - root.wavePhase * 0.73) * height * envelope * 0.12
                if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
              }
              ctx.stroke()
            }
            ctx.globalAlpha = 1.0
            ctx.shadowBlur = 0
          }
        }

        Row {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.top: parent.top
          anchors.margins: Style.space(14)
          Text {
            text: "LIVE OUTPUT"
            color: "#a5ffd8"
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1
          }
          Item { width: parent.width - 175; height: 1 }
          Text {
            text: outputPeakMonitor.peak > 0.001
              ? Math.max(-60, Math.round(20 * Math.log(outputPeakMonitor.peak) / Math.LN10)) + " dB"
              : "−∞ dB"
            color: "#a5ffd8"
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignRight
          }
        }

        Timer {
          interval: 33
          repeat: true
          running: eqWindow.visible
          onTriggered: {
            root.wavePhase += 0.10
            waveCanvas.requestPaint()
          }
        }
      }

      Text {
        width: parent.width
        text: "Open Quad Spatial is a four-speaker upmix, not Atmos decoding. Dolby activation is handled by Dolby's supported apps."
        color: Qt.darker(Color.foreground, 1.4)
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        wrapMode: Text.WordWrap
      }
    }

    Component.onCompleted: waveCanvas.requestPaint()
  }

  // ---- Reusable inline components ----

  // Output device row — cursor target inside the "output" section. Mouse
  // hover updates the panel cursor at the root; visuals come entirely
  // from hasCursor/current via CursorSurface, never from containsMouse.
  component SinkRow: CursorSurface {
    id: sinkRow
    required property var node
    required property int rowIndex

    readonly property bool isActive: root.sink && node && root.sink.id === node.id
    hasCursor: root.cursorActive && root.focusSection === "output" && root.selectedIndex === rowIndex
    onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(sinkRow)
    current: isActive
    foreground: root.bar.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: sinkInner.implicitHeight + Style.spacing.xl

    Row {
      id: sinkInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: root.sinkGlyph(sinkRow.node)
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.title
        width: Style.space(22)
        horizontalAlignment: Text.AlignHCenter
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        textFormat: Text.PlainText
        text: root.nodeLabel(sinkRow.node)
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.body
        font.bold: sinkRow.isActive
        elide: Text.ElideRight
        width: parent.width - Style.space(22) - Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) {
        root.cursorActive = true
        root.focusSection = "output"
        root.selectedIndex = sinkRow.rowIndex
      }
      onClicked: root.setDefaultSink(sinkRow.node)
    }
  }

  // Input device row — sibling of SinkRow for the "input" section.
  component SourceRow: CursorSurface {
    id: sourceRow
    required property var node
    required property int rowIndex

    readonly property bool isActive: root.source && node && root.source.id === node.id
    hasCursor: root.cursorActive && root.focusSection === "input" && root.selectedIndex === rowIndex
    onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(sourceRow)
    current: isActive
    foreground: root.bar.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: sourceInner.implicitHeight + Style.spacing.xl

    Row {
      id: sourceInner
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(8)

      Text {
        textFormat: Text.PlainText
        text: root.sourceGlyph(sourceRow.node)
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.title
        width: Style.space(22)
        horizontalAlignment: Text.AlignHCenter
        anchors.verticalCenter: parent.verticalCenter
      }

      Text {
        textFormat: Text.PlainText
        text: root.nodeLabel(sourceRow.node)
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.body
        font.bold: sourceRow.isActive
        elide: Text.ElideRight
        width: parent.width - Style.space(22) - Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) {
        root.cursorActive = true
        root.focusSection = "input"
        root.selectedIndex = sourceRow.rowIndex
      }
      onClicked: root.setDefaultSource(sourceRow.node)
    }
  }

  // Per-app stream row — cursor target inside the "streams" section.
  // The stream has its own slider inline, so h/l from the keyboard
  // adjusts THIS stream's volume (not the global output) when the cursor
  // sits on this row. Enter/Space mutes the stream.
  component StreamRow: CursorSurface {
    id: streamRow
    required property var node
    required property int rowIndex

    readonly property real streamVolume: node && node.audio ? node.audio.volume : 0
    readonly property bool streamMuted: node && node.audio ? node.audio.muted : false
    readonly property bool isActive: root.streamRepresentsPlayer(node, root.activeMediaPlayer)

    hasCursor: root.cursorActive && root.focusSection === "streams" && root.selectedIndex === rowIndex
    onHasCursorChanged: if (hasCursor) root.ensureCursorVisible(streamRow)
    current: isActive
    foreground: root.bar.foreground
    fill: root.hoverFill
    currentFill: root.selectedFill
    implicitHeight: streamColumn.implicitHeight + Style.spacing.xl

    Column {
      id: streamColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: Style.space(6)
      anchors.rightMargin: Style.space(6)
      spacing: Style.space(2)

      Row {
        width: parent.width
        spacing: Style.space(8)

        Text {
          id: streamMuteIcon
          textFormat: Text.PlainText
          text: streamRow.streamMuted ? "󰝟" : "󰕾"
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.title
          width: Style.space(22)
          horizontalAlignment: Text.AlignHCenter
          anchors.verticalCenter: parent.verticalCenter
          opacity: streamRow.streamMuted ? 0.5 : 1.0

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              if (streamRow.node && streamRow.node.audio)
                streamRow.node.audio.muted = !streamRow.node.audio.muted
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          text: root.streamLabel(streamRow.node)
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.body
          font.bold: streamRow.isActive
          elide: Text.ElideRight
          width: parent.width - streamMuteIcon.width - streamPct.width - Style.space(16)
          anchors.verticalCenter: parent.verticalCenter
        }

        Text {
          id: streamPct
          textFormat: Text.PlainText
          text: Math.round(streamRow.streamVolume * 100) + "%"
          color: Qt.darker(root.bar.foreground, 1.5)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.caption
          font.bold: true
          width: Style.space(36)
          horizontalAlignment: Text.AlignRight
          anchors.verticalCenter: parent.verticalCenter
          opacity: streamRow.streamMuted ? 0.5 : 1.0
        }
      }

      PanelSlider {
        bar: root.bar
        width: parent.width
        minimum: 0
        maximum: 1.5
        step: 0.05
        value: streamRow.streamVolume
        opacity: streamRow.streamMuted ? 0.5 : 1.0

        onMoved: function(v) {
          if (streamRow.node && streamRow.node.audio) streamRow.node.audio.volume = v
        }
        onRightClicked: {
          if (streamRow.node && streamRow.node.audio)
            streamRow.node.audio.muted = !streamRow.node.audio.muted
        }
      }
    }

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
      propagateComposedEvents: true
      onContainsMouseChanged: if (containsMouse) {
        root.cursorActive = true
        root.focusSection = "streams"
        root.selectedIndex = streamRow.rowIndex
      }
    }
  }
}

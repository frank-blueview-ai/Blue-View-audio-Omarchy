#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in test that keeps a music-like client stream alive through Open Quad output handoff."""

import argparse
import array
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
import wave

from e2e_eq_multichannel import CONTROLS, monitor_rms
from e2e_stereo_upmix import CHANNEL_ORDER, active_client_streams, run, sink_index


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "scripts/imac-audio-controls"
RATE = 48_000
MONITOR = "alsa_output.pci-0000_00_1b.0.analog-surround-40.monitor"
SPATIAL_SINK = CONTROLS.SPATIAL_SINK
TEST_SECONDS = 30.0
MIN_LEVEL_DBFS = -80.0


def make_stereo_tone(path):
    samples = array.array("h")
    amplitude = round(32767 * 10 ** (-24 / 20))
    for frame in range(round(TEST_SECONDS * RATE)):
        seconds = frame / RATE
        samples.extend((
            round(amplitude * math.sin(2 * math.pi * 440 * seconds)),
            round(amplitude * math.sin(2 * math.pi * 880 * seconds)),
        ))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples.tobytes())


def active_test_stream_ids(sink_name):
    return CONTROLS.sink_input_ids_for_sink(sink_name)


def run_action(args, timeout=40):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"Control command failed: {' '.join(args)}")
    return result.stdout


def sink_available(name):
    return any(len(fields := line.split("\t")) >= 2 and fields[1] == name
               for line in run(["pactl", "list", "short", "sinks"]).splitlines())


def wait_for_test_stream(sink_name, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ids = active_test_stream_ids(sink_name)
        if len(ids) == 1:
            return ids[0]
        time.sleep(0.05)
    raise RuntimeError(f"Expected one live client stream on {sink_name}")


def monitor_state_signature(state):
    return (state.get("routes"), state.get("eqEnabled"), state.get("eqGainsFront"),
            state.get("eqGainsRear"), state.get("mutedSpeakers"),
            state.get("spatialEnabled"), state.get("spatialPreviousDefault"))


def monitor_level_for_window(path, window, channels=range(4)):
    return [monitor_rms(path, *window, channel) for channel in channels]


def continuous_audio_floor(path, start, end):
    raw = Path(path).read_bytes()
    usable = len(raw) // 16 * 16
    samples = array.array("i")
    samples.frombytes(raw[:usable])
    if os.sys.byteorder != "little":
        samples.byteswap()
    total_frames = len(samples) // 4
    window_frames = int(0.1 * RATE)
    floors = []
    for first in range(max(0, int(start * RATE)), min(total_frames, int(end * RATE)), window_frames):
        last = min(total_frames, first + window_frames)
        values = samples[first * 4:last * 4]
        if not values:
            continue
        rms = math.sqrt(sum(value * value for value in values) / len(values)) / (2**31)
        floors.append(20 * math.log10(rms) if rms else -240.0)
    return min(floors, default=-240.0), len(floors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="play a quiet stereo test while switching Open Quad on and off")
    parser.add_argument("--client", choices=("pulse", "pipewire", "alsa"), default="pulse",
                        help="playback API to exercise (default: pulse)")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to exercise the live output handoff")

    client_label, playback_args = {
        "pulse": ("PulseAudio", lambda wav: ["paplay", str(wav)]),
        "pipewire": ("native PipeWire", lambda wav: ["pw-cat", "--playback", str(wav)]),
        "alsa": ("ALSA default", lambda wav: ["aplay", "-q", "-D", "default", str(wav)]),
    }[args.client]

    initial = json.loads(run([str(CONTROL), "status"]))
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if initial.get("speakerRoutingSink") != "imac_fixed":
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if initial.get("spatialEnabled") or sink_available(SPATIAL_SINK):
        raise RuntimeError("Turn Open Quad Spatial off before running this handoff test")
    if any(initial.get("speakerMutes", {}).values()):
        raise RuntimeError("Unmute all speakers before running this handoff test")
    if active_client_streams(sink_index("imac_fixed")):
        raise RuntimeError("Stop real playback before this test")
    source = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                   if len(line.split("\t")) >= 2 and line.split("\t")[1] == MONITOR), None)
    if source is None or not re.search(r"\b4ch\b", source):
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {MONITOR}")

    saved_state_data = (json.loads(CONTROLS.STATE_FILE.read_text())
                        if CONTROLS.STATE_FILE.exists() else None)
    state_before = CONTROLS.load_state()
    master = initial.get("masterSink", "")
    before_default = run(["pactl", "get-default-sink"]).strip()
    before_master_volume = run(["pactl", "get-sink-volume", master]).strip()
    before_master_mute = run(["pactl", "get-sink-mute", master]).strip()
    transitions = {}
    enabled = False

    with tempfile.TemporaryDirectory(prefix="blue-view-output-handoff-") as temp:
        directory = Path(temp)
        wav_path = directory / "blue-view-output-handoff.wav"
        capture_path = directory / "hardware-monitor.s32le"
        make_stereo_tone(wav_path)
        with capture_path.open("wb") as capture_file:
            recorder_started = time.monotonic()
            recorder = subprocess.Popen([
                "parec", f"--device={MONITOR}", "--format=s32le", f"--rate={RATE}",
                "--channels=4", "--fix-format", "--fix-rate", "--fix-channels",
                "--no-remix", "--raw", "--latency-msec=20",
            ], stdout=capture_file, stderr=subprocess.PIPE, text=True)
            playback = None
            try:
                time.sleep(0.35)
                if recorder.poll() is not None:
                    error = recorder.stderr.read().strip() if recorder.stderr else ""
                    raise RuntimeError(error or f"Could not record hardware monitor {MONITOR}")
                playback_started = time.monotonic()
                playback = subprocess.Popen(playback_args(wav_path), stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE, text=True)
                stream_id = wait_for_test_stream("imac_fixed")
                time.sleep(0.8)
                transitions["built-in"] = (time.monotonic() - recorder_started,
                                           active_test_stream_ids("imac_fixed"))

                enabled = True
                result = run_action([str(CONTROL), "spatial", "on"])
                if run(["pactl", "get-default-sink"]).strip() != SPATIAL_SINK:
                    raise RuntimeError(result.strip() or "Open Quad did not become the default output")
                if stream_id not in active_test_stream_ids(SPATIAL_SINK):
                    raise RuntimeError("The live application stream did not move to Open Quad")
                transitions["open-quad"] = (time.monotonic() - recorder_started,
                                             active_test_stream_ids(SPATIAL_SINK))
                time.sleep(1.0)

                result = run_action([str(CONTROL), "spatial", "off"])
                enabled = False
                if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
                    raise RuntimeError(result.strip() or "Open Quad did not return to built-in output")
                if stream_id not in active_test_stream_ids("imac_fixed"):
                    raise RuntimeError("The live application stream did not return to imac_fixed")
                transitions["returned"] = (time.monotonic() - recorder_started,
                                           active_test_stream_ids("imac_fixed"))
                time.sleep(0.8)

                playback.wait(timeout=35)
                if playback.returncode != 0:
                    error = playback.stderr.read().strip() if playback.stderr else ""
                    raise RuntimeError(error or "Output handoff test playback failed")
            finally:
                if enabled:
                    try:
                        run_action([str(CONTROL), "spatial", "off"])
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        pass
                if playback is not None and playback.poll() is None:
                    playback.terminate()
                    try:
                        playback.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        playback.kill()
                        playback.wait(timeout=2)
                if recorder.poll() is None:
                    recorder.send_signal(signal.SIGINT)
                    try:
                        recorder.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        recorder.kill()
                        recorder.wait(timeout=2)

        offset = playback_started - recorder_started
        windows = {
            "built-in": (offset + 0.2, offset + 0.8),
            "open-quad": (transitions["open-quad"][0] + 0.2,
                          transitions["open-quad"][0] + 0.8),
            "returned": (transitions["returned"][0] + 0.2,
                         transitions["returned"][0] + 0.8),
        }
        levels = {name: monitor_level_for_window(capture_path, window)
                  for name, window in windows.items()}
        floor_db, floor_windows = continuous_audio_floor(
            capture_path, offset + 0.1, transitions["returned"][0] + 0.8)

    passed = True
    print(f"Hardware monitor: {MONITOR}; {client_label} stream ID {stream_id}")
    for name in ("built-in", "open-quad", "returned"):
        sinks = transitions[name][1]
        print(f"{name}: stream ids {sinks}; FL/FR/RL/RR RMS = "
              + ", ".join(f"{value:.1f} dBFS" for value in levels[name]))
        ok = stream_id in sinks and min(levels[name]) > MIN_LEVEL_DBFS
        passed &= ok
        if not ok:
            print(f"FAIL: {name} handoff lost the stream or a hardware lane fell below the measurement threshold")
    continuity_ok = floor_windows > 0 and floor_db > MIN_LEVEL_DBFS
    passed &= continuity_ok
    print(f"100 ms output-window floor during handoffs: {floor_db:.1f} dBFS "
          f"across {floor_windows} windows {'PASS' if continuity_ok else 'FAIL'}")

    final_status = json.loads(run([str(CONTROL), "status"]))
    final_state = CONTROLS.load_state()
    unchanged = monitor_state_signature(final_state) == monitor_state_signature(state_before)
    defaults_unchanged = (
        run(["pactl", "get-default-sink"]).strip() == before_default
        and final_status.get("speakerRoutingSink") == "imac_fixed"
    )
    master_unchanged = (
        run(["pactl", "get-sink-volume", master]).strip() == before_master_volume
        and run(["pactl", "get-sink-mute", master]).strip() == before_master_mute
    )
    current_state_data = (json.loads(CONTROLS.STATE_FILE.read_text())
                          if CONTROLS.STATE_FILE.exists() else None)
    state_file_unchanged = current_state_data == saved_state_data
    print(f"routing/EQ/mute/spatial state unchanged: {'PASS' if unchanged else 'FAIL'}")
    print(f"default and master output restored: {'PASS' if defaults_unchanged and master_unchanged else 'FAIL'}")
    print(f"saved device state unchanged: {'PASS' if state_file_unchanged else 'FAIL'}")
    passed &= unchanged and defaults_unchanged and master_unchanged and state_file_unchanged
    if not passed:
        raise SystemExit("FAIL: output handoff interrupted playback or did not restore state")
    print("PASS: one continuous application stream survived both output switches.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E output-handoff check failed safely: {error}")

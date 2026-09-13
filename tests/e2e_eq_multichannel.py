#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in live EQ response and uninterrupted-stream check for all four channels."""

import argparse
import array
import copy
import importlib.util
from importlib.machinery import SourceFileLoader
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


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "scripts/imac-audio-controls"
LOADER = SourceFileLoader("blueview_audio_controls_e2e", str(CONTROL))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
CONTROLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROLS)

RATE = 48_000
MONITOR = "alsa_output.pci-0000_00_1b.0.analog-surround-40.monitor"
CHANNEL_ORDER = ("front-left", "front-right", "rear-left", "rear-right")
SEGMENT_SECONDS = 2.0
TEST_SECONDS = 6 * SEGMENT_SECONDS
AMPLITUDE = round(32767 * 10 ** (-24 / 20))  # -24 dBFS peak; measurable at low master volume
MIN_BAND_DELTA_DB = 4.0
MAX_RESTORE_ERROR_DB = 1.5
MIN_CONTINUOUS_LEVEL_DBFS = -70.0


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=20).stdout


def make_tones(path):
    samples = array.array("h")
    segment_frames = round(SEGMENT_SECONDS * RATE)
    segment_frequencies = (250, 2000, 250, 2000, 2000)
    for frame in range(round(TEST_SECONDS * RATE)):
        frequency = segment_frequencies[min(frame // segment_frames,
                                            len(segment_frequencies) - 1)]
        value = round(AMPLITUDE * math.sin(2 * math.pi * frequency * frame / RATE))
        samples.extend((value, value, value, value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(4)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples.tobytes())


def monitor_rms(capture_path, start, end, channel):
    raw = Path(capture_path).read_bytes()
    usable = len(raw) // 16 * 16
    samples = array.array("i")
    samples.frombytes(raw[:usable])
    if os.sys.byteorder != "little":
        samples.byteswap()
    first = max(0, int(start * RATE))
    last = min(len(samples) // 4, int(end * RATE))
    values = samples[first * 4 + channel:last * 4:4]
    if not values:
        return -240.0
    rms = math.sqrt(sum(value * value for value in values) / len(values)) / (2**31)
    return 20 * math.log10(rms) if rms else -240.0


def monitor_available():
    source = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                   if len(line.split("\t")) >= 2 and line.split("\t")[1] == MONITOR), None)
    if source is None or not re.search(r"\b4ch\b", source):
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {MONITOR}")


def wait_until_playback_time(playback, start, seconds):
    deadline = start + seconds
    while time.monotonic() < deadline:
        if playback.poll() is not None:
            error = playback.stderr.read().strip() if playback.stderr else ""
            raise RuntimeError(error or "Test playback ended before the EQ transition")
        time.sleep(min(0.05, deadline - time.monotonic()))


def live_test_stream_id():
    output_id = CONTROLS.sink_index("imac_fixed")
    records = [record for record in CONTROLS.sink_input_records()
               if record["sink"] == output_id
               and not record["node"].startswith(("output.", "effect_output."))
               and not (record["filename"] == "/dev/zero"
                        and record["application"] in (
                            "pw-cat", "Blue View Audio Handoff Keepalive"))]
    if len(records) != 1:
        raise RuntimeError("Expected exactly one live E2E playback stream on imac_fixed")
    return records[0]["id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="play quiet tones while switching the EQ graph live")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to play the quiet multichannel EQ check")

    state = CONTROLS.load_state()
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if CONTROLS.sink_index("imac_fixed") is None:
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(state.get("mutedSpeakers", {}).values()):
        raise RuntimeError("Unmute the four speakers before running this EQ check")
    if CONTROLS.sink_input_ids_for_sink("imac_fixed"):
        raise RuntimeError("Stop real playback before this test; active stream(s) are attached")
    monitor_available()
    saved_state_bytes = CONTROLS.STATE_FILE.read_bytes() if CONTROLS.STATE_FILE.exists() else None

    was_eq_active = CONTROLS.eq_graph_is_applied(state)
    restore_state = copy.deepcopy(state)
    if not was_eq_active:
        restore_state["eqEnabled"] = False
    baseline_state = copy.deepcopy(state)
    baseline_state["eqEnabled"] = False
    test_state = copy.deepcopy(state)
    test_state["eqEnabled"] = True
    test_state["eqGainsFront"] = [0, 0, 0, 6, 0, 0]
    test_state["eqGainsRear"] = [0, 0, 0, 6, 0, 0]

    try:
        CONTROLS.apply_eq_graph("imac_fixed", baseline_state)
        if not CONTROLS.eq_graph_is_applied(baseline_state):
            raise RuntimeError("The neutral baseline EQ graph did not become active")
        if CONTROLS.sink_input_ids_for_sink("imac_fixed"):
            raise RuntimeError("Real playback appeared before the E2E stream started")
        with tempfile.TemporaryDirectory(prefix="blue-view-eq-e2e-") as temp:
            directory = Path(temp)
            wav_path = directory / "blue-view-eq-live-update.wav"
            capture_path = directory / "hardware-monitor.s32le"
            make_tones(wav_path)
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
                    playback = subprocess.Popen([
                        "paplay", "--device=imac_fixed", "--channels=4",
                        "--channel-map=" + ",".join(CHANNEL_ORDER), "--no-remix", str(wav_path),
                    ], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    playback_stream_id = None
                    try:
                        deadline = time.monotonic() + 3
                        while playback_stream_id is None and time.monotonic() < deadline:
                            if playback.poll() is not None:
                                error = playback.stderr.read().strip() if playback.stderr else ""
                                raise RuntimeError(error or "EQ test tone playback failed")
                            try:
                                playback_stream_id = live_test_stream_id()
                            except RuntimeError:
                                time.sleep(0.05)
                        if playback_stream_id is None:
                            raise RuntimeError("Could not identify the live E2E playback stream")

                        # Keep one client stream attached while only its processing
                        # graph changes. This exercises the user's no-drop requirement.
                        wait_until_playback_time(playback, playback_started, 4.1)
                        CONTROLS.apply_eq_graph("imac_fixed", test_state)
                        if (not CONTROLS.eq_graph_is_applied(test_state)
                                or live_test_stream_id() != playback_stream_id):
                            raise RuntimeError("Applying EQ interrupted or misconfigured playback")
                        wait_until_playback_time(playback, playback_started, 8.1)
                        CONTROLS.apply_eq_graph("imac_fixed", baseline_state)
                        if (not CONTROLS.eq_graph_is_applied(baseline_state)
                                or live_test_stream_id() != playback_stream_id):
                            raise RuntimeError("Bypassing EQ interrupted or misconfigured playback")
                        restored_at = time.monotonic() - recorder_started
                        playback.wait(timeout=6)
                        if playback.returncode != 0:
                            error = playback.stderr.read().strip() if playback.stderr else ""
                            raise RuntimeError(error or "EQ test tone playback failed")
                    finally:
                        if playback is not None and playback.poll() is None:
                            playback.terminate()
                            try:
                                playback.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                playback.kill()
                                playback.wait(timeout=2)
                finally:
                    if recorder.poll() is None:
                        recorder.send_signal(signal.SIGINT)
                        try:
                            recorder.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            recorder.kill()
                            recorder.wait(timeout=2)

            offset = playback_started - recorder_started
            print("Live EQ test: +6 dB at 2 kHz on both speaker pairs; other bands flat.")
            print("Input peak: -24 dBFS; one playback stream covered both graph updates.")
            print("Monitor: " + MONITOR)
            for channel, name in enumerate(("FL", "FR", "RL", "RR")):
                flat_low = monitor_rms(capture_path, offset + 0.2,
                                       offset + SEGMENT_SECONDS - 0.2, channel)
                flat_high = monitor_rms(capture_path, offset + SEGMENT_SECONDS + 0.2,
                                        offset + 2 * SEGMENT_SECONDS - 0.2, channel)
                eq_low = monitor_rms(capture_path, offset + 2 * SEGMENT_SECONDS + 0.2,
                                     offset + 3 * SEGMENT_SECONDS - 0.2, channel)
                eq_high = monitor_rms(capture_path, offset + 3 * SEGMENT_SECONDS + 0.2,
                                      offset + 4 * SEGMENT_SECONDS - 0.2, channel)
                restored_high = monitor_rms(capture_path, restored_at + 0.3,
                                            restored_at + 1.3, channel)
                delta = eq_high - eq_low
                flat_delta = flat_high - flat_low
                restore_error = restored_high - flat_high
                levels = (flat_low, flat_high, eq_low, eq_high, restored_high)
                print(f"{name}: flat 250/2k {flat_low:.1f}/{flat_high:.1f} dBFS; "
                      f"EQ 250/2k {eq_low:.1f}/{eq_high:.1f}; "
                      f"2 kHz boost {delta:+.1f} dB; restored {restore_error:+.1f} dB")
                if (delta < MIN_BAND_DELTA_DB
                        or abs(flat_delta) > MAX_RESTORE_ERROR_DB
                        or abs(restore_error) > MAX_RESTORE_ERROR_DB
                        or any(level < MIN_CONTINUOUS_LEVEL_DBFS for level in levels)):
                    raise SystemExit(f"FAIL: EQ response or continuity check failed on {name}")
            print("PASS: all four output lanes changed with EQ and returned to baseline.")
            print("PASS: the paplay stream id remained unchanged through both live updates.")
    finally:
        CONTROLS.apply_eq_graph("imac_fixed", restore_state)
        print("Runtime graph restored to the previous saved curve, or flat bypass when it was inactive.")
        print("Saved speaker routes and EQ sliders were not changed.")
        current_state_bytes = CONTROLS.STATE_FILE.read_bytes() if CONTROLS.STATE_FILE.exists() else None
        if current_state_bytes != saved_state_bytes:
            raise RuntimeError("The multichannel EQ test unexpectedly changed saved device state")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E multichannel-EQ check failed safely: {error}")

#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in mono/stereo upmix check; records the ALSA output monitor."""

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


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "scripts/imac-audio-controls"
RATE = 48_000
CHANNEL_ORDER = ("front-left", "front-right", "rear-left", "rear-right")
MONITOR = "alsa_output.pci-0000_00_1b.0.analog-surround-40.monitor"
MIN_DBFS = -75.0
TEST_SECONDS = 3.0


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=20).stdout


def sink_index(name):
    for line in run(["pactl", "list", "short", "sinks"]).splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == name:
            return int(fields[0])
    raise RuntimeError(f"Required sink is unavailable: {name}")


def active_client_streams(sink_id):
    records = []
    output = run(["pactl", "list", "sink-inputs"])
    for block in output.split("Sink Input #")[1:]:
        sink = re.search(r"^\s*Sink:\s*(\d+)", block, re.MULTILINE)
        if not sink or int(sink.group(1)) != sink_id:
            continue
        node = re.search(r'^\s*node\.name = "([^"]+)"', block, re.MULTILINE)
        app = re.search(r'^\s*application\.name = "([^"]+)"', block, re.MULTILINE)
        filename = re.search(r'^\s*media\.filename = "([^"]+)"', block, re.MULTILINE)
        node_name = node.group(1) if node else ""
        app_name = app.group(1) if app else ""
        file_name = filename.group(1) if filename else ""
        internal = node_name.startswith(("output.", "effect_output.")) or (
            file_name == "/dev/zero"
            and app_name in ("pw-cat", "Blue View Audio Handoff Keepalive")
        )
        if not internal:
            records.append(node_name or app_name or "unnamed stream")
    return records


def make_test(path, channels, peak_dbfs=-34):
    """Build a quiet mono or distinct left/right stereo tone."""
    amplitude = round(32767 * 10 ** (peak_dbfs / 20))
    samples = array.array("h")
    for frame in range(round(TEST_SECONDS * RATE)):
        seconds = frame / RATE
        left = round(amplitude * math.sin(2 * math.pi * 440 * seconds))
        if channels == 1:
            samples.append(left)
        else:
            right = round(amplitude * math.sin(2 * math.pi * 880 * seconds))
            samples.extend((left, right))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
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


def capture_stereo_client(label, playback_command, capture_path):
    with Path(capture_path).open("wb") as capture_file:
        recorder_started = time.monotonic()
        recorder = subprocess.Popen([
            "parec", f"--device={MONITOR}", "--format=s32le", f"--rate={RATE}",
            "--channels=4", "--fix-format", "--fix-rate", "--fix-channels",
            "--no-remix", "--raw", "--latency-msec=20",
        ], stdout=capture_file, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(0.35)
            if recorder.poll() is not None:
                error = recorder.stderr.read().strip() if recorder.stderr else ""
                raise RuntimeError(error or f"Could not record hardware monitor {MONITOR}")
            playback_started = time.monotonic()
            playback = subprocess.run(playback_command, text=True, capture_output=True, timeout=10)
            if playback.returncode != 0:
                raise RuntimeError(playback.stderr.strip() or f"{label} stereo playback failed")
            time.sleep(0.3)
        finally:
            if recorder.poll() is None:
                recorder.send_signal(signal.SIGINT)
                try:
                    recorder.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    recorder.kill()
                    recorder.wait(timeout=2)

    offset = playback_started - recorder_started
    return [monitor_rms(capture_path, offset + 0.1, offset + TEST_SECONDS - 0.1, ch)
            for ch in range(4)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="play a quiet three-second stereo test")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to play the quiet stereo upmix test")

    state = json.loads(run([str(CONTROL), "status"]))
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if state.get("speakerRoutingSink") != "imac_fixed":
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(state.get("speakerMutes", {}).values()):
        raise RuntimeError("Unmute the four speakers before running this upmix check")
    routes = state.get("routes", {})
    if set(routes) != set(CHANNEL_ORDER) or set(routes.values()) != set(CHANNEL_ORDER):
        raise RuntimeError("Speaker routes are incomplete or duplicated")
    destination_for_source = {source: destination for destination, source in routes.items()}

    output_id = sink_index("imac_fixed")
    active = active_client_streams(output_id)
    if active:
        raise RuntimeError("Stop real playback before this test; active stream(s): "
                           + ", ".join(active))
    source = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                   if len(line.split("\t")) >= 2 and line.split("\t")[1] == MONITOR), None)
    if source is None or not re.search(r"\b4ch\b", source):
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {MONITOR}")

    with tempfile.TemporaryDirectory(prefix="blue-view-upmix-e2e-") as temp:
        directory = Path(temp)
        clients = ("PulseAudio", "native PipeWire", "ALSA default")
        for channels, label in ((1, "mono"), (2, "stereo")):
            wav_path = directory / f"blue-view-{label}-upmix.wav"
            make_test(wav_path, channels)
            for client in clients:
                if active_client_streams(output_id):
                    raise RuntimeError(f"Real playback appeared before the {client} {label} test")
                if client == "PulseAudio":
                    command = ["paplay", "--device=imac_fixed", str(wav_path)]
                elif client == "native PipeWire":
                    command = ["pw-cat", "--playback", "--target=imac_fixed", str(wav_path)]
                else:
                    command = ["aplay", "-q", "-D", "default", str(wav_path)]
                levels = capture_stereo_client(
                    f"{client} {label}", command,
                    directory / f"{client.lower().replace(' ', '-')}-{label}.s32le")
                tones = "440 Hz" if channels == 1 else "440/880 Hz"
                print(f"{client} {label} {tones} test, peak -34 dBFS, through imac_fixed")
                print(f"Hardware monitor: {MONITOR}; FL/FR/RL/RR RMS = "
                      + ", ".join(f"{value:.1f} dBFS" for value in levels))
                expected_lanes = list(range(4))
                missing = [CHANNEL_ORDER[index] for index in expected_lanes
                           if levels[index] <= MIN_DBFS]
                if missing:
                    raise SystemExit(f"FAIL: {client} {label} playback missed "
                                     + ", ".join(missing))
                expected = "all four hardware lanes"
                print(f"PASS: {client} {label} playback reached {expected}.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E stereo-upmix check failed safely: {error}")

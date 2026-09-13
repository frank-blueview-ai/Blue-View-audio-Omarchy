#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in physical-device lane check; records the ALSA monitor, not a mic."""

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
CUES = (
    ("front-left", "Front_Left.wav"),
    ("front-right", "Front_Right.wav"),
    ("rear-left", "Rear_Left.wav"),
    ("rear-right", "Rear_Right.wav"),
)


def run(args):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=20).stdout


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
            records.append((node_name or app_name or "unnamed stream", file_name))
    return records


def make_cues(directory):
    interleaved = array.array("h")
    durations = []
    for channel, (_, filename) in enumerate(CUES):
        path = Path("/usr/share/sounds/alsa") / filename
        if not path.is_file():
            raise RuntimeError(f"Missing spoken cue file: {path}")
        with wave.open(str(path), "rb") as cue:
            if (cue.getnchannels(), cue.getsampwidth(), cue.getframerate()) != (1, 2, RATE):
                raise RuntimeError(f"Unexpected format for cue file: {path}")
            mono = array.array("h")
            mono.frombytes(cue.readframes(cue.getnframes()))
        durations.append(len(mono) / RATE)
        silence_frames = int(0.35 * RATE)
        for n, sample in enumerate(mono):
            edge = min(1.0, n / (0.05 * RATE), (len(mono) - n) / (0.05 * RATE))
            value = max(-32768, min(32767, int(sample * 0.25 * max(0.0, edge))))
            frame = [0, 0, 0, 0]
            frame[channel] = value
            interleaved.extend(frame)
        interleaved.extend([0] * (silence_frames * 4))

    wav_path = directory / "blue-view-four-speaker-cues.wav"
    with wave.open(str(wav_path), "wb") as out:
        out.setnchannels(4)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(interleaved.tobytes())
    return wav_path, durations


def monitor_rms(capture_path, start, end, channel):
    raw = Path(capture_path).read_bytes()
    samples = array.array("i")
    usable = len(raw) // 16 * 16
    samples.frombytes(raw[:usable])
    if os.sys.byteorder != "little":
        samples.byteswap()
    first = max(0, int(start * RATE))
    last = min(len(samples) // 4, int(end * RATE))
    values = samples[first * 4 + channel:last * 4 + channel:4]
    if not values:
        return -240.0
    rms = math.sqrt(sum(value * value for value in values) / len(values)) / (2**31)
    return 20 * math.log10(rms) if rms else -240.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="play four brief spoken cues")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to play the short channel-identification cues")

    state = json.loads(run([str(CONTROL), "status"]))
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if state.get("speakerRoutingSink") != "imac_fixed":
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(state.get("speakerMutes", {}).values()):
        raise RuntimeError("Unmute the four speakers before running this lane check")

    routes = state.get("routes", {})
    if set(routes) != set(CHANNEL_ORDER) or set(routes.values()) != set(CHANNEL_ORDER):
        raise RuntimeError("Speaker routes are incomplete or duplicated")
    destination_for_source = {source: destination for destination, source in routes.items()}
    sink_id = sink_index("imac_fixed")
    active = active_client_streams(sink_id)
    if active:
        names = ", ".join(name for name, _ in active)
        raise RuntimeError(f"Stop real playback before this test; active stream(s): {names}")

    monitor = str(state.get("masterSink", "")) + ".monitor"
    source_line = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                        if len(line.split("\t")) >= 2 and line.split("\t")[1] == monitor), None)
    if source_line is None or not re.search(r"\b4ch\b", source_line):
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {monitor}")

    with tempfile.TemporaryDirectory(prefix="blue-view-speaker-e2e-") as temp:
        directory = Path(temp)
        wav_path, durations = make_cues(directory)
        capture_path = directory / "hardware-monitor.s32le"
        with capture_path.open("wb") as capture_file:
            recorder_started = time.monotonic()
            recorder = subprocess.Popen([
                "parec", f"--device={monitor}", "--format=s32le", f"--rate={RATE}",
                "--channels=4", "--fix-format", "--fix-rate", "--fix-channels",
                "--no-remix", "--raw",
            ], stdout=capture_file, stderr=subprocess.PIPE, text=True)
            try:
                time.sleep(0.35)
                if recorder.poll() is not None:
                    error = recorder.stderr.read().strip() if recorder.stderr else ""
                    raise RuntimeError(error or f"Could not record hardware monitor {monitor}")
                play_start = time.monotonic()
                playback = subprocess.run([
                    "paplay", "--device=imac_fixed", "--channels=4",
                    "--channel-map=" + ",".join(CHANNEL_ORDER), "--no-remix", str(wav_path),
                ], text=True, capture_output=True, timeout=15)
                if playback.returncode != 0:
                    raise RuntimeError(playback.stderr.strip() or "Cue playback failed")
                time.sleep(0.35)
            finally:
                if recorder.poll() is None:
                    recorder.send_signal(signal.SIGINT)
                    try:
                        recorder.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        recorder.kill()
                        recorder.wait(timeout=2)

        # The recorder starts 0.35s before paplay. Keep an explicit playback offset
        # so the per-cue windows don't count the initial monitor silence.
        playback_offset = play_start - recorder_started
        elapsed = playback_offset
        passed = True
        print(f"Monitor: {monitor}; default output and mute states left unchanged")
        for (source, _), duration in zip(CUES, durations):
            start = elapsed + 0.12
            end = elapsed + duration - 0.10
            levels = [monitor_rms(capture_path, start, end, idx)
                      for idx in range(4)]
            expected = destination_for_source[source]
            expected_index = CHANNEL_ORDER.index(expected)
            expected_db = levels[expected_index]
            others = [value for idx, value in enumerate(levels) if idx != expected_index]
            cue_ok = expected_db > -65.0 and all(value < -85.0 for value in others)
            passed &= cue_ok
            print(f"{source} → {expected}: {expected_db:.1f} dBFS; "
                  f"FL/FR/RL/RR = " + ", ".join(f"{value:.1f}" for value in levels)
                  + (" PASS" if cue_ok else " FAIL"))
            elapsed += duration + 0.35

        if not passed:
            raise SystemExit("FAIL: one or more spoken cues missed the routed hardware lane")
        print("PASS: all four spoken cues reached their expected hardware monitor lanes.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E speaker-channel check failed safely: {error}")

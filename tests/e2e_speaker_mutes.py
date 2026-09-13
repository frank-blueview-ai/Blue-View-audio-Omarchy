#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in live check that each speaker mute silences only its own hardware lane."""

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
INSTALLED_CONTROL = Path.home() / ".local/bin/imac-audio-controls"
CONTROL = INSTALLED_CONTROL if INSTALLED_CONTROL.is_file() else ROOT / "scripts/imac-audio-controls"
RATE = 48_000
CHANNEL_ORDER = ("front-left", "front-right", "rear-left", "rear-right")
SPEAKERS = CHANNEL_ORDER


def run(args, timeout=20):
    return subprocess.run(args, check=True, text=True, capture_output=True,
                          timeout=timeout).stdout


def control_state():
    return json.loads(run([str(CONTROL), "status"]))


def sink_index(name):
    for line in run(["pactl", "list", "short", "sinks"]).splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[1] == name:
            return int(fields[0])
    raise RuntimeError(f"Required sink is unavailable: {name}")


def real_playback_streams(sink_id):
    active = []
    for block in run(["pactl", "list", "sink-inputs"]).split("Sink Input #")[1:]:
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
            active.append(node_name or app_name or "unnamed stream")
    return active


def output_stream_volumes():
    matches = []
    for block in run(["pactl", "list", "sink-inputs"]).split("Sink Input #")[1:]:
        node = re.search(r'^\s*node\.name = "([^"]+)"', block, re.MULTILINE)
        if not node or node.group(1) != "output.imac_fixed":
            continue
        volume_line = next((line for line in block.splitlines()
                            if line.strip().startswith("Volume:")), "")
        values = re.findall(
            r"([a-z]+-[a-z]+):\s*\d+\s*/\s*([0-9]+(?:\.[0-9]+)?%)", volume_line
        )
        volumes = dict(values)
        if len(values) != 4 or set(volumes) != set(CHANNEL_ORDER):
            raise RuntimeError("Could not read all four Blue View hardware output levels")
        matches.append(volumes)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Blue View output stream, found {len(matches)}")
    return matches[0]


def write_test_signal(directory, duration=30.0):
    sample_count = int(RATE * duration)
    frames = array.array("h")
    for frame in range(sample_count):
        sample = int(32767 * 0.08 * math.sin(2 * math.pi * 440 * frame / RATE))
        frames.extend((sample, sample, sample, sample))
    path = directory / "blue-view-speaker-mute-test.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(4)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(frames.tobytes())
    return path


def read_channel_rms(samples, start, end, channel):
    first = max(0, int(start * RATE))
    last = min(len(samples) // 4, int(end * RATE))
    values = samples[first * 4 + channel:last * 4 + channel:4]
    if not values:
        return -240.0
    rms = math.sqrt(sum(value * value for value in values) / len(values)) / (2**31)
    return 20 * math.log10(rms) if rms else -240.0


def db_delta(level, baseline):
    return level - baseline


def state_signature(state):
    return (state.get("routes"), state.get("eqEnabled"), state.get("eqGainsFront"),
            state.get("eqGainsRear"), state.get("eqApplied"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="briefly mute each speaker in turn")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to exercise the four live speaker mute controls")

    initial = control_state()
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if initial.get("speakerRoutingSink") != "imac_fixed":
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(initial.get("speakerMutes", {}).values()):
        raise RuntimeError("Unmute all speakers before running this reversible check")

    sink_id = sink_index("imac_fixed")
    active = real_playback_streams(sink_id)
    if active:
        raise RuntimeError("Stop real playback before the test; active stream(s): "
                           + ", ".join(active))

    master = str(initial.get("masterSink", ""))
    monitor = master + ".monitor"
    source_line = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                        if len(line.split("\t")) >= 2 and line.split("\t")[1] == monitor), None)
    if source_line is None or not re.search(r"\b4ch\b", source_line):
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {monitor}")

    before_default = run(["pactl", "get-default-sink"]).strip()
    before_master_volume = run(["pactl", "get-sink-volume", master]).strip()
    before_master_mute = run(["pactl", "get-sink-mute", master]).strip()
    before_stream_volumes = output_stream_volumes()
    windows = {}
    activated = []

    with tempfile.TemporaryDirectory(prefix="blue-view-speaker-mutes-") as temp:
        directory = Path(temp)
        wav_path = write_test_signal(directory)
        capture_path = directory / "hardware-monitor.s32le"
        with capture_path.open("wb") as capture_file:
            recorder_started = time.monotonic()
            recorder = subprocess.Popen([
                "parec", f"--device={monitor}", "--format=s32le", f"--rate={RATE}",
                "--channels=4", "--fix-format", "--fix-rate", "--fix-channels",
                "--no-remix", "--raw", "--latency-msec=20",
            ], stdout=capture_file, stderr=subprocess.PIPE, text=True)
            playback = None
            try:
                time.sleep(0.35)
                if recorder.poll() is not None:
                    error = recorder.stderr.read().strip() if recorder.stderr else ""
                    raise RuntimeError(error or f"Could not record hardware monitor {monitor}")
                play_start = time.monotonic()
                playback = subprocess.Popen([
                    "paplay", "--device=imac_fixed", "--channels=4",
                    "--channel-map=" + ",".join(CHANNEL_ORDER), "--no-remix", str(wav_path),
                ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                playback_offset = play_start - recorder_started
                windows["baseline"] = (playback_offset + 0.08, playback_offset + 0.24)
                time.sleep(0.32)

                for speaker in SPEAKERS:
                    if playback.poll() is not None:
                        raise RuntimeError("Test tone ended before all mute checks")
                    run([str(CONTROL), "set-speaker-mute", speaker, "on"])
                    activated.append(speaker)
                    muted_at = time.monotonic() - recorder_started
                    time.sleep(0.34)
                    windows[speaker, "muted"] = (muted_at + 0.08,
                                                   time.monotonic() - recorder_started - 0.04)
                    run([str(CONTROL), "set-speaker-mute", speaker, "off"])
                    activated.remove(speaker)
                    restored_at = time.monotonic() - recorder_started
                    time.sleep(0.34)
                    windows[speaker, "restored"] = (restored_at + 0.08,
                                                      time.monotonic() - recorder_started - 0.04)

                playback.wait(timeout=35)
                if playback.returncode != 0:
                    error = playback.stderr.read().strip() if playback.stderr else ""
                    raise RuntimeError(error or "Speaker-mute test playback failed")
            finally:
                for speaker in activated:
                    try:
                        run([str(CONTROL), "set-speaker-mute", speaker, "off"])
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

        raw = capture_path.read_bytes()
        usable = len(raw) // 4 * 4
        samples = array.array("i")
        samples.frombytes(raw[:usable])
        if os.sys.byteorder != "little":
            samples.byteswap()

    baseline = [read_channel_rms(samples, *windows["baseline"], channel)
                for channel in range(4)]
    if any(level <= -65.0 for level in baseline):
        raise RuntimeError("The test tone did not reach every hardware channel: "
                           + ", ".join(f"{level:.1f}" for level in baseline))

    passed = True
    print(f"Monitor: {monitor}; stream and master levels are checked for restoration")
    for speaker in SPEAKERS:
        target = CHANNEL_ORDER.index(speaker)
        muted = read_channel_rms(samples, *windows[speaker, "muted"], target)
        restored = read_channel_rms(samples, *windows[speaker, "restored"], target)
        neighbors = [read_channel_rms(samples, *windows[speaker, "muted"], index)
                     for index in range(4) if index != target]
        mute_delta = db_delta(muted, baseline[target])
        restore_delta = db_delta(restored, baseline[target])
        mute_ok = mute_delta < -50.0 and all(value > -65.0 for value in neighbors)
        restore_ok = restore_delta > -6.0
        passed &= mute_ok and restore_ok
        print(f"{speaker}: baseline {baseline[target]:.1f} dBFS, muted {muted:.1f} dBFS "
              f"({mute_delta:.1f} dB), restored {restored:.1f} dBFS "
              f"({restore_delta:+.1f} dB), other lanes live "
              f"{min(neighbors):.1f}…{max(neighbors):.1f} dBFS"
              + (" PASS" if mute_ok and restore_ok else " FAIL"))

    final = control_state()
    final_volumes = output_stream_volumes()
    checks = {
        "all speaker mutes off": not any(final.get("speakerMutes", {}).values()),
        "routes and EQ unchanged": state_signature(final) == state_signature(initial),
        "hardware master unchanged": (
            run(["pactl", "get-sink-volume", master]).strip() == before_master_volume
            and run(["pactl", "get-sink-mute", master]).strip() == before_master_mute
        ),
        "output stream levels restored": final_volumes == before_stream_volumes,
        "default output unchanged": run(["pactl", "get-default-sink"]).strip() == before_default,
    }
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
        passed &= ok
    if not passed:
        raise SystemExit("FAIL: at least one speaker mute or restoration check failed")
    print("PASS: each control muted one hardware lane, preserved the other three, and restored levels.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E speaker-mute check failed safely: {error}")

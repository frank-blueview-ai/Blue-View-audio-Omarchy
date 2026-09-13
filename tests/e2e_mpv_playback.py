#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in MPV playback check through PipeWire, PulseAudio, and ALSA."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from e2e_stereo_upmix import (
    CHANNEL_ORDER,
    MONITOR,
    active_client_streams,
    capture_stereo_client,
    make_test,
    run,
    sink_index,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "scripts/imac-audio-controls"
BACKENDS = {
    "PipeWire": ("pipewire", "pipewire/imac_fixed"),
    "PulseAudio": ("pulse", "pulse/imac_fixed"),
    "ALSA": ("alsa", "alsa/pipewire"),
}


def state_signature(state):
    return (state.get("routes"), state.get("eqEnabled"),
            state.get("eqGainsFront"), state.get("eqGainsRear"),
            state.get("speakerMutes"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="play quiet mono and stereo tones through MPV")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to exercise MPV playback")
    if not shutil.which("mpv"):
        raise RuntimeError("MPV is required for this end-to-end check")

    state = json.loads(run([str(CONTROL), "status"]))
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if state.get("speakerRoutingSink") != "imac_fixed":
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(state.get("speakerMutes", {}).values()):
        raise RuntimeError("Unmute the four output channels before this MPV check")

    routes = state.get("routes", {})
    if set(routes) != set(CHANNEL_ORDER) or set(routes.values()) != set(CHANNEL_ORDER):
        raise RuntimeError("Speaker routes are incomplete or duplicated")
    destination_for_source = {source: destination for destination, source in routes.items()}
    output_id = sink_index("imac_fixed")
    active = active_client_streams(output_id)
    if active:
        raise RuntimeError("Stop real playback before this test; active stream(s): "
                           + ", ".join(active))

    source_line = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                        if len(line.split("\t")) >= 2
                        and line.split("\t")[1] == MONITOR), None)
    if source_line is None or "4ch" not in source_line:
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {MONITOR}")

    before_default = run(["pactl", "get-default-sink"]).strip()
    with tempfile.TemporaryDirectory(prefix="blue-view-mpv-e2e-") as temp:
        directory = Path(temp)
        for channels, label in ((1, "mono"), (2, "stereo")):
            wav_path = directory / f"{label}.wav"
            make_test(wav_path, channels, peak_dbfs=-24)
            for backend, (ao, device) in BACKENDS.items():
                if active_client_streams(output_id):
                    raise RuntimeError(f"Real playback appeared before MPV/{backend} {label}")
                command = ["mpv", "--no-video", "--really-quiet", "--volume=100", f"--ao={ao}",
                           f"--audio-device={device}", str(wav_path)]
                capture_path = directory / f"{ao}-{label}.s32le"
                levels = capture_stereo_client(
                    f"MPV/{backend} {label}", command, capture_path)
                print(f"MPV/{backend} {label} measured levels: {levels}", flush=True)
                expected_lanes = list(range(4))
                missing = [CHANNEL_ORDER[index] for index in expected_lanes
                           if levels[index] <= -75.0]
                if missing:
                    raise SystemExit(f"FAIL: MPV/{backend} {label} missed "
                                     + ", ".join(missing))
                description = "all four lanes"
                print(f"MPV/{backend} {label}: FL/FR/RL/RR RMS = "
                      + ", ".join(f"{value:.1f} dBFS" for value in levels))
                print(f"PASS: MPV/{backend} reached {description}.")

    final = json.loads(run([str(CONTROL), "status"]))
    state_unchanged = state_signature(final) == state_signature(state)
    default_unchanged = run(["pactl", "get-default-sink"]).strip() == before_default
    print(f"Saved route/EQ/mute state unchanged: {'PASS' if state_unchanged else 'FAIL'}")
    print(f"Default output unchanged: {'PASS' if default_unchanged else 'FAIL'}")
    if not state_unchanged or not default_unchanged:
        raise SystemExit("FAIL: MPV playback changed persistent audio state")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E MPV playback check failed safely: {error}")

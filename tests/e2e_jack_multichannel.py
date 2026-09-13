#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in PipeWire-JACK routing test for four discrete speaker channels."""

import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from e2e_stereo_upmix import (
    CHANNEL_ORDER,
    active_client_streams,
    monitor_rms,
    run,
    sink_index,
)


ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "scripts/imac-audio-controls"
RATE = 48_000
JACK_CONFIG = Path.home() / ".config/pipewire/jack.conf.d/40-blue-view-jack.conf"
JACK_CLIENT = "BlueViewJackMultiE2E"
CUE_SECONDS = 0.55
GAP_SECONDS = 0.15
CUES = (
    ("front-left", 440.0),
    ("front-right", 550.0),
    ("rear-left", 660.0),
    ("rear-right", 770.0),
)
JACK_SOURCE = r'''#include <jack/jack.h>
#include <math.h>
#include <stdio.h>
#include <unistd.h>

static jack_client_t *client;
static jack_port_t *outputs[4];
static double sample_rate;
static unsigned long long frame_index;
static const double frequency[4] = {440.0, 550.0, 660.0, 770.0};

static int process_audio(jack_nframes_t frames, void *arg) {
    (void)arg;
    jack_default_audio_sample_t *buffers[4];
    for (int port = 0; port < 4; ++port)
        buffers[port] = jack_port_get_buffer(outputs[port], frames);
    const unsigned long long cue_frames = (unsigned long long)(0.55 * sample_rate);
    const unsigned long long slot_frames = (unsigned long long)(0.70 * sample_rate);
    for (jack_nframes_t frame = 0; frame < frames; ++frame, ++frame_index) {
        unsigned long long slot = frame_index / slot_frames;
        unsigned long long within = frame_index % slot_frames;
        int active = slot < 4 && within < cue_frames ? (int)slot : -1;
        for (int port = 0; port < 4; ++port) {
            double value = 0.0;
            if (port == active) {
                double t = (double)within / sample_rate;
                value = 0.02 * sin(2.0 * M_PI * frequency[port] * t);
            }
            buffers[port][frame] = (jack_default_audio_sample_t)value;
        }
    }
    return 0;
}

int main(void) {
    jack_status_t status = 0;
    client = jack_client_open("BlueViewJackMultiE2E", JackNullOption, &status);
    if (!client) {
        fprintf(stderr, "jack_client_open failed (status 0x%x)\n", status);
        return 2;
    }
    sample_rate = (double)jack_get_sample_rate(client);
    char name[16];
    for (int port = 0; port < 4; ++port) {
        snprintf(name, sizeof(name), "out_%d", port + 1);
        outputs[port] = jack_port_register(client, name, JACK_DEFAULT_AUDIO_TYPE,
                                           JackPortIsOutput, 0);
        if (!outputs[port]) {
            fprintf(stderr, "Could not register JACK output %s\n", name);
            jack_client_close(client);
            return 3;
        }
    }
    if (jack_set_process_callback(client, process_audio, NULL) || jack_activate(client)) {
        fprintf(stderr, "Could not activate the JACK surround test client\n");
        jack_client_close(client);
        return 4;
    }
    for (int port = 0; port < 4; ++port) {
        char target[32];
        snprintf(target, sizeof(target), "system:playback_%d", port + 1);
        jack_port_t *system_port = jack_port_by_name(client, target);
        if (!system_port || jack_connect(client, jack_port_name(outputs[port]),
                                         jack_port_name(system_port))) {
            fprintf(stderr, "Could not connect JACK output to %s\n", target);
            jack_client_close(client);
            return 5;
        }
    }
    usleep(3300000);
    jack_client_close(client);
    return 0;
}
'''


def jack_nodes():
    output = run(["pw-cli", "ls", "Node"])
    nodes = []
    for block in re.split(r"\n\s*id \d+, type PipeWire:Interface:Node/3", output):
        if 'client.api = "jack"' not in block:
            continue
        match = re.search(r'^\s*node.name = "([^"]+)"', block, re.MULTILINE)
        if match:
            nodes.append(match.group(1))
    return nodes


def compile_jack_client(directory):
    if not all(shutil.which(command) for command in ("gcc", "pkg-config", "pw-jack")):
        raise RuntimeError("JACK E2E needs gcc, pkg-config, and pw-jack")
    if not JACK_CONFIG.is_file() or "jack.default-as-system = true" not in JACK_CONFIG.read_text():
        raise RuntimeError("Run the installer first to expose the Blue View default as JACK system ports")
    source = directory / "blue-view-jack-multichannel.c"
    binary = directory / "blue-view-jack-multichannel"
    source.write_text(JACK_SOURCE)
    flags = shlex.split(run(["pkg-config", "--cflags", "--libs", "jack"]))
    subprocess.run(["gcc", "-O2", str(source), "-o", str(binary), *flags, "-lm"],
                   check=True, text=True, capture_output=True, timeout=20)
    return binary


def state_signature(state):
    return (state.get("routes"), state.get("eqEnabled"), state.get("eqGainsFront"),
            state.get("eqGainsRear"), state.get("speakerMutes"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="play four quiet JACK channel cues")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to play the short JACK channel-identification tones")

    initial = json.loads(run([str(CONTROL), "status"]))
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if initial.get("speakerRoutingSink") != "imac_fixed":
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(initial.get("speakerMutes", {}).values()):
        raise RuntimeError("Unmute the four speakers before running this JACK check")
    routes = initial.get("routes", {})
    if set(routes) != set(CHANNEL_ORDER) or set(routes.values()) != set(CHANNEL_ORDER):
        raise RuntimeError("Speaker routes are incomplete or duplicated")
    output_id = sink_index("imac_fixed")
    active = active_client_streams(output_id)
    if active:
        raise RuntimeError("Stop real playback before this test; active stream(s): "
                           + ", ".join(active))
    active_jack = jack_nodes()
    if active_jack:
        raise RuntimeError("Close other JACK clients before this test: "
                           + ", ".join(active_jack))

    master = str(initial.get("masterSink", ""))
    monitor = master + ".monitor"
    source = next((line for line in run(["pactl", "list", "short", "sources"]).splitlines()
                   if len(line.split("\t")) >= 2 and line.split("\t")[1] == monitor), None)
    if source is None or not re.search(r"\b4ch\b", source):
        raise RuntimeError(f"A four-channel hardware monitor is unavailable: {monitor}")

    before_default = run(["pactl", "get-default-sink"]).strip()
    inverse_routes = {source: destination for destination, source in routes.items()}
    windows = {}
    with tempfile.TemporaryDirectory(prefix="blue-view-jack-e2e-") as temp:
        directory = Path(temp)
        binary = compile_jack_client(directory)
        capture_path = directory / "hardware-monitor.s32le"
        with capture_path.open("wb") as capture_file:
            recorder_started = time.monotonic()
            recorder = subprocess.Popen([
                "parec", f"--device={monitor}", "--format=s32le", f"--rate={RATE}",
                "--channels=4", "--fix-format", "--fix-rate", "--fix-channels",
                "--no-remix", "--raw",
            ], stdout=capture_file, stderr=subprocess.PIPE, text=True)
            playback = None
            try:
                time.sleep(0.35)
                if recorder.poll() is not None:
                    error = recorder.stderr.read().strip() if recorder.stderr else ""
                    raise RuntimeError(error or f"Could not record hardware monitor {monitor}")
                play_start = time.monotonic()
                playback = subprocess.Popen(["pw-jack", str(binary)], stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True)
                time.sleep(0.25)
                links = run(["pw-link", "-l"])
                expected_ports = ("front-left", "front-right", "rear-left", "rear-right")
                for index, port in enumerate(expected_ports, start=1):
                    if (f"{JACK_CLIENT}:out_{index}" not in links
                            or f"imac_fixed:playback_{('FL', 'FR', 'RL', 'RR')[index - 1]}" not in links):
                        raise RuntimeError("JACK system ports did not link into imac_fixed")
                offset = play_start - recorder_started
                for index, (source_name, _) in enumerate(CUES):
                    cue_start = offset + index * (CUE_SECONDS + GAP_SECONDS)
                    windows[source_name] = (cue_start + 0.08, cue_start + 0.44)
                stdout, stderr = playback.communicate(timeout=8)
                if playback.returncode != 0:
                    raise RuntimeError(stderr.strip() or stdout.strip()
                                       or "JACK surround playback failed")
                time.sleep(0.3)
            finally:
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

        passed = True
        print(f"Monitor: {monitor}; JACK system ports link to imac_fixed")
        for source_name, _ in CUES:
            levels = [monitor_rms(capture_path, *windows[source_name], channel)
                      for channel in range(4)]
            expected = inverse_routes[source_name]
            target = CHANNEL_ORDER.index(expected)
            cue_ok = levels[target] > -65.0 and all(
                value < -85.0 for index, value in enumerate(levels) if index != target
            )
            passed &= cue_ok
            print(f"{source_name} → {expected}: {levels[target]:.1f} dBFS; "
                  f"FL/FR/RL/RR = " + ", ".join(f"{level:.1f}" for level in levels)
                  + (" PASS" if cue_ok else " FAIL"))

    final = json.loads(run([str(CONTROL), "status"]))
    unchanged = state_signature(final) == state_signature(initial)
    default_unchanged = run(["pactl", "get-default-sink"]).strip() == before_default
    if not unchanged or not default_unchanged:
        passed = False
    print(f"routes, EQ, and mute state unchanged: {'PASS' if unchanged else 'FAIL'}")
    print(f"default output unchanged: {'PASS' if default_unchanged else 'FAIL'}")
    if not passed:
        raise SystemExit("FAIL: JACK signal missed a routed hardware channel")
    print("PASS: four-channel JACK playback used all assigned Blue View hardware lanes.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E JACK multichannel check failed safely: {error}")

#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in live EQ response test for PipeWire JACK's default-system output path."""

import argparse
import copy
import json
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from e2e_eq_multichannel import (
    CONTROLS,
    MAX_RESTORE_ERROR_DB,
    MIN_BAND_DELTA_DB,
    MIN_CONTINUOUS_LEVEL_DBFS,
    MONITOR,
    RATE,
    monitor_available,
    monitor_rms,
    run,
    wait_until_playback_time,
)


ROOT = Path(__file__).resolve().parents[1]
JACK_CONFIG = Path.home() / ".config/pipewire/jack.conf.d/40-blue-view-jack.conf"
JACK_CLIENT = "BlueViewJackEQE2E"
TEST_SECONDS = 8.5
JACK_SOURCE = r'''#include <jack/jack.h>
#include <math.h>
#include <stdio.h>
#include <unistd.h>

static jack_client_t *client;
static jack_port_t *outputs[4];
static double sample_rate;
static unsigned long long frame_index;

static int process_audio(jack_nframes_t frames, void *arg) {
    (void)arg;
    for (int port = 0; port < 4; ++port) {
        jack_default_audio_sample_t *buffer = jack_port_get_buffer(outputs[port], frames);
        for (jack_nframes_t i = 0; i < frames; ++i) {
            double t = (double)(frame_index + i) / sample_rate;
            buffer[i] = (jack_default_audio_sample_t)(0.02 * sin(2.0 * M_PI * 2000.0 * t));
        }
    }
    frame_index += frames;
    return 0;
}

int main(void) {
    jack_status_t status = 0;
    client = jack_client_open("BlueViewJackEQE2E", JackNullOption, &status);
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
        fprintf(stderr, "Could not activate the JACK EQ test client\n");
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
    usleep(8500000);
    jack_client_close(client);
    return 0;
}
'''


def compile_jack_client(directory):
    if not all(shutil.which(command) for command in ("gcc", "pkg-config", "pw-jack")):
        raise RuntimeError("JACK EQ E2E needs gcc, pkg-config, and pw-jack")
    if not JACK_CONFIG.is_file() or "jack.default-as-system = true" not in JACK_CONFIG.read_text():
        raise RuntimeError("Run the installer first to map JACK system ports to Blue View")
    source = directory / "blue-view-jack-eq.c"
    binary = directory / "blue-view-jack-eq"
    source.write_text(JACK_SOURCE)
    flags = shlex.split(run(["pkg-config", "--cflags", "--libs", "jack"]))
    subprocess.run(["gcc", "-O2", str(source), "-o", str(binary), *flags, "-lm"],
                   check=True, text=True, capture_output=True, timeout=20)
    return binary


def jack_node_id():
    output = run(["pw-cli", "ls", "Node"])
    for match in re.finditer(
        r"^\s*id (\d+), type PipeWire:Interface:Node/3(?P<body>.*?)(?=^\s*id \d+, type PipeWire:Interface:Node/3|\Z)",
        output, re.DOTALL | re.MULTILINE,
    ):
        body = match.group("body")
        if (f'node.name = "{JACK_CLIENT}"' in body
                and 'client.api = "jack"' in body):
            return match.group(1)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true",
                        help="play a quiet four-channel 2 kHz tone during a live EQ transition")
    args = parser.parse_args()
    if not args.run:
        parser.error("add --run to play the quiet JACK equalizer check")

    state = CONTROLS.load_state()
    if run(["pactl", "get-default-sink"]).strip() != "imac_fixed":
        raise RuntimeError("Set Blue View Built-in Speakers as the default output first")
    if CONTROLS.sink_index("imac_fixed") is None:
        raise RuntimeError("Blue View's four-channel routing sink is unavailable")
    if any(state.get("mutedSpeakers", {}).values()):
        raise RuntimeError("Unmute the four speakers before running this JACK EQ check")
    if CONTROLS.sink_input_ids_for_sink("imac_fixed"):
        raise RuntimeError("Stop real playback before this test")
    other_nodes = run(["pw-cli", "ls", "Node"])
    jack_names = []
    for block in re.split(r"\n\s*id \d+, type PipeWire:Interface:Node/3", other_nodes):
        if 'client.api = "jack"' in block:
            match = re.search(r'^\s*node.name = "([^"]+)"', block, re.MULTILINE)
            if match:
                jack_names.append(match.group(1))
    if jack_names:
        raise RuntimeError("Close other JACK clients before this test: "
                           + ", ".join(jack_names))
    monitor_available()
    saved_state_bytes = CONTROLS.STATE_FILE.read_bytes() if CONTROLS.STATE_FILE.exists() else None
    before_default = run(["pactl", "get-default-sink"]).strip()

    was_eq_active = CONTROLS.eq_graph_is_applied(state)
    restore_state = copy.deepcopy(state)
    if not was_eq_active:
        restore_state["eqEnabled"] = False
    baseline = copy.deepcopy(state)
    baseline["eqEnabled"] = False
    boost = copy.deepcopy(state)
    boost["eqEnabled"] = True
    boost["eqGainsFront"] = [0, 0, 0, 6, 0, 0]
    boost["eqGainsRear"] = [0, 0, 0, 6, 0, 0]

    try:
        CONTROLS.apply_eq_graph("imac_fixed", baseline)
        if not CONTROLS.eq_graph_is_applied(baseline):
            raise RuntimeError("The neutral baseline EQ graph did not become active")
        with tempfile.TemporaryDirectory(prefix="blue-view-jack-eq-e2e-") as temp:
            directory = Path(temp)
            binary = compile_jack_client(directory)
            capture_path = directory / "hardware-monitor.s32le"
            with capture_path.open("wb") as capture_file:
                recorder_started = time.monotonic()
                recorder = subprocess.Popen([
                    "parec", f"--device={MONITOR}", "--format=s32le", f"--rate={RATE}",
                    "--channels=4", "--fix-format", "--fix-rate", "--fix-channels",
                    "--no-remix", "--raw",
                ], stdout=capture_file, stderr=subprocess.PIPE, text=True)
                playback = None
                try:
                    time.sleep(0.35)
                    if recorder.poll() is not None:
                        error = recorder.stderr.read().strip() if recorder.stderr else ""
                        raise RuntimeError(error or f"Could not record hardware monitor {MONITOR}")
                    playback_started = time.monotonic()
                    playback = subprocess.Popen(["pw-jack", str(binary)], stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, text=True)
                    playback_stream_id = None
                    deadline = time.monotonic() + 2
                    while playback_stream_id is None and time.monotonic() < deadline:
                        playback_stream_id = jack_node_id()
                        if playback.poll() is not None:
                            _, stderr = playback.communicate()
                            raise RuntimeError(stderr.strip() or "JACK EQ playback ended early")
                        if playback_stream_id is None:
                            time.sleep(0.05)
                    if playback_stream_id is None:
                        raise RuntimeError("Could not identify the live JACK EQ node")

                    links = run(["pw-link", "-l"])
                    for index, port in enumerate(("FL", "FR", "RL", "RR"), start=1):
                        if (f"{JACK_CLIENT}:out_{index}" not in links
                                or f"imac_fixed:playback_{port}" not in links):
                            raise RuntimeError("JACK output is not fully linked to imac_fixed")

                    wait_until_playback_time(playback, playback_started, 2.5)
                    CONTROLS.apply_eq_graph("imac_fixed", boost)
                    if (not CONTROLS.eq_graph_is_applied(boost)
                            or jack_node_id() != playback_stream_id):
                        raise RuntimeError("Applying EQ interrupted or misconfigured JACK playback")
                    wait_until_playback_time(playback, playback_started, 5.5)
                    CONTROLS.apply_eq_graph("imac_fixed", baseline)
                    if (not CONTROLS.eq_graph_is_applied(baseline)
                            or jack_node_id() != playback_stream_id):
                        raise RuntimeError("Bypassing EQ interrupted or misconfigured JACK playback")
                    wait_until_playback_time(playback, playback_started, 7.8)
                    playback.wait(timeout=3)
                    if playback.returncode != 0:
                        _, stderr = playback.communicate()
                        raise RuntimeError(stderr.strip() or "JACK EQ playback failed")
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

            offset = playback_started - recorder_started
            windows = {
                "flat": (offset + 0.4, offset + 2.2),
                "boost": (offset + 3.0, offset + 5.2),
                "restore": (offset + 6.0, offset + 7.6),
            }
            levels = {name: [monitor_rms(capture_path, *window, channel)
                             for channel in range(4)] for name, window in windows.items()}
            delta = [levels["boost"][ch] - levels["flat"][ch] for ch in range(4)]
            restore = [levels["restore"][ch] - levels["flat"][ch] for ch in range(4)]
            passed = True
            print(f"Monitor: {MONITOR}; one four-port JACK client stayed linked through EQ changes")
            for index, name in enumerate(("FL", "FR", "RL", "RR")):
                ok = (delta[index] >= MIN_BAND_DELTA_DB
                      and abs(restore[index]) <= MAX_RESTORE_ERROR_DB
                      and min(levels[key][index] for key in levels)
                      >= MIN_CONTINUOUS_LEVEL_DBFS)
                passed &= ok
                print(f"{name}: flat {levels['flat'][index]:.1f}, +6 dB EQ "
                      f"{levels['boost'][index]:.1f} ({delta[index]:+.1f}), "
                      f"restored {levels['restore'][index]:.1f} dBFS "
                      f"({restore[index]:+.1f} dB)" + (" PASS" if ok else " FAIL"))
            if not passed:
                raise SystemExit("FAIL: JACK EQ response or continuity check failed")
            print("PASS: the live JACK node remained connected and all four output lanes changed with EQ.")
    finally:
        CONTROLS.apply_eq_graph("imac_fixed", restore_state)
        unchanged = (CONTROLS.STATE_FILE.read_bytes() if CONTROLS.STATE_FILE.exists() else None)
        if unchanged != saved_state_bytes:
            raise RuntimeError("The JACK EQ test unexpectedly changed saved device state")
        if run(["pactl", "get-default-sink"]).strip() != before_default:
            raise RuntimeError("The JACK EQ test unexpectedly changed the default output")
        print("Runtime EQ restored; saved routes, mute state, sliders, and default output unchanged.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise SystemExit(f"E2E JACK-EQ check failed safely: {error}")

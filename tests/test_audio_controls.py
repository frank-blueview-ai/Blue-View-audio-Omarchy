# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch


CONTROL_PATH = Path(__file__).resolve().parents[1] / "scripts/imac-audio-controls"
LOADER = SourceFileLoader("blueview_audio_controls", str(CONTROL_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
CONTROLS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROLS)


class SinkInputSelectionTests(unittest.TestCase):
    def test_migrations_skip_internal_keepalives_but_keep_client_streams(self):
        records = [
            {"id": "1", "sink": "100", "node": "output.imac_fixed",
             "application": "", "filename": ""},
            {"id": "2", "sink": "100", "node": "pw-cat",
             "application": "pw-cat", "filename": "/dev/zero"},
            {"id": "3", "sink": "100", "node": "blue-view-audio-handoff-keepalive",
             "application": "Blue View Audio Handoff Keepalive", "filename": "/dev/zero"},
            {"id": "4", "sink": "100", "node": "Spotify",
             "application": "Spotify", "filename": "/music/song.ogg"},
            {"id": "5", "sink": "100", "node": "pw-cat",
             "application": "NativePipeWireApp", "filename": "/dev/zero"},
            {"id": "6", "sink": "200", "node": "OtherOutput",
             "application": "OtherOutput", "filename": "/music/song.ogg"},
        ]
        old_sink_index = CONTROLS.sink_index
        old_records = CONTROLS.sink_input_records
        try:
            CONTROLS.sink_index = lambda name: "100" if name == "imac_fixed" else None
            CONTROLS.sink_input_records = lambda: records
            self.assertEqual(CONTROLS.sink_input_ids_for_sink("imac_fixed"), ["4", "5"])
        finally:
            CONTROLS.sink_index = old_sink_index
            CONTROLS.sink_input_records = old_records


class AudioDefaultTests(unittest.TestCase):
    def test_eq_apply_updates_live_graph_without_reloading_sink_or_defaults(self):
        state = {
            **CONTROLS.DEFAULT_STATE,
            "routes": dict(CONTROLS.DEFAULT_STATE["routes"]),
            "eqEnabled": False,
            "mutedSpeakers": {speaker: False for speaker in CONTROLS.CHANNELS},
        }
        with patch.object(CONTROLS, "atomic_write") as atomic_write, \
                patch.object(CONTROLS, "apply_eq_graph") as apply_graph, \
                patch.object(CONTROLS, "reload_remap") as reload:
            CONTROLS.apply_eq(state)

        atomic_write.assert_called_once_with(CONTROLS.WP_CONFIG, CONTROLS.wp_config(state))
        apply_graph.assert_called_once_with("imac_fixed", state)
        reload.assert_not_called()

    def test_pulse_config_pairs_routes_with_the_master_hardware_order(self):
        routes = {
            "front-left": "rear-left",
            "front-right": "rear-right",
            "rear-left": "front-left",
            "rear-right": "front-right",
        }
        with patch.object(CONTROLS, "MASTER_MAP",
                          ["front-left", "front-right", "rear-left", "rear-right"]):
            config = CONTROLS.pulse_config(routes)
        self.assertIn(
            "master_channel_map=front-left,front-right,rear-left,rear-right "
            "channel_map=rear-left,rear-right,front-left,front-right remix=no",
            config,
        )


class SpeakerMuteTests(unittest.TestCase):
    def test_mute_zeros_only_the_selected_output_channel(self):
        speakers = CONTROLS.CHANNELS
        state = {
            "mutedSpeakers": {speaker: speaker == "rear-left" for speaker in speakers},
            "speakerBaseVolumes": {},
        }
        current = {speaker: "84%" for speaker in speakers}
        order = ["front-left", "front-right", "rear-left", "rear-right"]

        with patch.object(CONTROLS, "master_output_input_id", return_value="24188"), \
                patch.object(CONTROLS, "sink_input_channel_volumes", return_value=(order, current)), \
                patch.object(CONTROLS, "run") as run:
            CONTROLS.apply_speaker_mutes(state, {speaker: False for speaker in speakers})

        self.assertEqual(state["speakerBaseVolumes"]["rear-left"], "84%")
        self.assertEqual(
            run.call_args.args[0],
            ["pactl", "set-sink-input-volume", "24188", "84%", "84%", "0%", "84%"],
        )

    def test_unmute_restores_only_the_channel_saved_baseline(self):
        speakers = CONTROLS.CHANNELS
        state = {
            "mutedSpeakers": {speaker: False for speaker in speakers},
            "speakerBaseVolumes": {"rear-left": "84%"},
        }
        current = {speaker: "84%" for speaker in speakers}
        current["rear-left"] = "0%"
        order = ["front-left", "front-right", "rear-left", "rear-right"]
        previous = {speaker: speaker == "rear-left" for speaker in speakers}

        with patch.object(CONTROLS, "master_output_input_id", return_value="24188"), \
                patch.object(CONTROLS, "sink_input_channel_volumes", return_value=(order, current)), \
                patch.object(CONTROLS, "run") as run:
            CONTROLS.apply_speaker_mutes(state, previous)

        self.assertEqual(
            run.call_args.args[0],
            ["pactl", "set-sink-input-volume", "24188", "84%", "84%", "84%", "84%"],
        )


class EqualizerGraphTests(unittest.TestCase):
    def test_graph_uses_front_and_rear_gains_for_the_physical_channel_order(self):
        state = {
            **CONTROLS.DEFAULT_STATE,
            "eqEnabled": True,
            "eqGainsFront": [1, 2, 3, 4, 5, 6],
            "eqGainsRear": [-1, -2, -3, -4, -5, -6],
        }
        graph = CONTROLS.eq_filter_graph(state)
        self.assertIn('inputs = [ "blue_view_param_eq:In 1" "blue_view_param_eq:In 2" '
                      '"blue_view_param_eq:In 3" "blue_view_param_eq:In 4" ]', graph)
        self.assertIn('outputs = [ "blue_view_eq_probe:Out" "blue_view_param_eq:Out 2" '
                      '"blue_view_param_eq:Out 3" "blue_view_param_eq:Out 4" ]', graph)
        self.assertIn('name = blue_view_eq_probe label = linear control = { Mult = 1.0 Add = 0.0 }', graph)
        expected = {
            "filters1": state["eqGainsFront"],
            "filters2": state["eqGainsFront"],
            "filters3": state["eqGainsRear"],
            "filters4": state["eqGainsRear"],
        }
        for channel, gains in expected.items():
            match = re.search(rf"{channel} = \[ (.*?) \]", graph)
            self.assertIsNotNone(match, channel)
            actual = [float(value) for value in re.findall(r"gain = (-?[0-9]+\.[0-9]+)", match.group(1))]
            self.assertEqual(actual, [float(gain) for gain in gains], channel)

    def test_bypass_keeps_six_flat_bands_on_each_of_four_channels(self):
        state = {
            **CONTROLS.DEFAULT_STATE,
            "eqEnabled": False,
            "eqGainsFront": [12, 8, 4, 0, -4, -8],
            "eqGainsRear": [-12, -8, -4, 0, 4, 8],
        }
        graph = CONTROLS.eq_filter_graph(state)
        self.assertEqual(len(re.findall(r"filters[1-4] =", graph)), 4)
        self.assertEqual(len(re.findall(r"gain = 0\.0", graph)), 24)

    def test_live_graph_is_set_and_verified_on_the_pipewire_node(self):
        state = {
            **CONTROLS.DEFAULT_STATE,
            "eqEnabled": True,
            "eqGainsFront": [0, 0, 0, 0, 0, 0],
            "eqGainsRear": [0, 0, 0, 0, 0, 0],
        }
        graph = CONTROLS.eq_filter_graph(state)
        commands = []

        def fake_run(args, **kwargs):
            commands.append(args)
            output = f'String "{CONTROLS.EQ_GRAPH_PROBE}"' if args[1:3] == ["enum-params", "35"] else ""
            return CONTROLS.subprocess.CompletedProcess(args, 0, output, "")

        with patch.object(CONTROLS, "wait_for_node", return_value="35"), \
                patch.object(CONTROLS, "run", side_effect=fake_run), \
                patch.object(CONTROLS, "node_object_serial", return_value="24189"), \
                patch.object(CONTROLS, "atomic_write") as atomic_write:
            CONTROLS.apply_eq_graph("imac_fixed", state)

        self.assertEqual(commands[0][:4], ["pw-cli", "set-param", "35", "Props"])
        self.assertEqual(commands[1], ["pw-cli", "enum-params", "35", "PropInfo"])
        self.assertEqual(atomic_write.call_args.args[0], CONTROLS.EQ_RUNTIME_FILE)
        marker = json.loads(atomic_write.call_args.args[1])
        self.assertEqual(marker["nodeSerial"], "24189")
        self.assertEqual(marker["graphHash"], CONTROLS.eq_graph_hash(state))

    def test_runtime_status_matches_graph_hash_and_node_serial(self):
        state = {
            **CONTROLS.DEFAULT_STATE,
            "eqEnabled": True,
            "eqGainsFront": [1, 2, 3, 4, 5, 6],
            "eqGainsRear": [-1, -2, -3, -4, -5, -6],
        }
        prop_info = f'String "{CONTROLS.EQ_GRAPH_PROBE}"'
        result = CONTROLS.subprocess.CompletedProcess([], 0, prop_info, "")
        with tempfile.TemporaryDirectory() as directory:
            runtime_file = Path(directory) / "equalizer-runtime.json"
            config_file = Path(directory) / "unloaded.conf"
            runtime_file.write_text(json.dumps({
                "nodeSerial": "24189",
                "graphHash": CONTROLS.eq_graph_hash(state),
            }))
            with patch.object(CONTROLS, "node_id_for_sink", return_value="35"), \
                    patch.object(CONTROLS, "node_object_serial", return_value="24189"), \
                    patch.object(CONTROLS, "run", return_value=result), \
                    patch.object(CONTROLS, "EQ_RUNTIME_FILE", runtime_file), \
                    patch.object(CONTROLS, "WP_CONFIG", config_file):
                self.assertTrue(CONTROLS.eq_graph_is_applied(state))
                runtime_file.write_text(json.dumps({
                    "nodeSerial": "old-node",
                    "graphHash": CONTROLS.eq_graph_hash(state),
                }))
                self.assertFalse(CONTROLS.eq_graph_is_applied(state))

    def test_status_recognizes_matching_wireplumber_startup_graph(self):
        state = {
            **CONTROLS.DEFAULT_STATE,
            "eqEnabled": True,
            "eqGainsFront": [1, 2, 3, 4, 5, 6],
            "eqGainsRear": [-1, -2, -3, -4, -5, -6],
        }
        result = CONTROLS.subprocess.CompletedProcess(
            [], 0, f'String "{CONTROLS.EQ_GRAPH_PROBE}"', ""
        )
        with tempfile.TemporaryDirectory() as directory:
            runtime_file = Path(directory) / "missing-runtime.json"
            config_file = Path(directory) / "50-imac-equalizer.conf"
            config_file.write_text(CONTROLS.wp_config(state))
            with patch.object(CONTROLS, "node_id_for_sink", return_value="35"), \
                    patch.object(CONTROLS, "node_object_serial", return_value="24189"), \
                    patch.object(CONTROLS, "run", return_value=result), \
                    patch.object(CONTROLS, "EQ_RUNTIME_FILE", runtime_file), \
                    patch.object(CONTROLS, "WP_CONFIG", config_file):
                self.assertTrue(CONTROLS.eq_graph_is_applied(state))


class AudioHandoffTests(unittest.TestCase):
    def test_staging_sink_is_kept_running_until_its_eq_graph_is_ready(self):
        events = []
        state = {
            **CONTROLS.DEFAULT_STATE,
            "routes": dict(CONTROLS.DEFAULT_STATE["routes"]),
        }

        def fake_run(args, **kwargs):
            events.append(("run", tuple(args)))
            return CONTROLS.subprocess.CompletedProcess(args, 0, "", "")

        def fake_apply(sink_name, _state):
            events.append(("eq", sink_name))

        def fake_load(sink_name, _routes):
            events.append(("load", sink_name))
            return "module-id"

        with patch.object(CONTROLS, "current_sink_state",
                          return_value=("100%", "0", "imac_fixed")), \
                patch.object(CONTROLS, "load_state", return_value=state), \
                patch.object(CONTROLS, "remap_module_id",
                             side_effect=[None, "old-module", "staged-module"]), \
                patch.object(CONTROLS, "load_remap_sink", side_effect=fake_load), \
                patch.object(CONTROLS, "wait_for_sink"), \
                patch.object(CONTROLS, "run", side_effect=fake_run), \
                patch.object(CONTROLS, "sink_input_ids_for_sink",
                             return_value=["app-id"]), \
                patch.object(CONTROLS, "start_handoff_keepalive",
                             side_effect=lambda sink: events.append(("keepalive-start", sink)) or "silent"), \
                patch.object(CONTROLS, "stop_handoff_keepalive",
                             side_effect=lambda process: events.append(("keepalive-stop", process))), \
                patch.object(CONTROLS, "apply_eq_graph", side_effect=fake_apply), \
                patch.object(CONTROLS, "migrate_sink_inputs",
                             side_effect=lambda source, dest, **kwargs:
                             events.append(("migrate", source, dest))):
            CONTROLS.reload_remap(dict(CONTROLS.DEFAULT_STATE["routes"]))

        stage_ready = events.index(("keepalive-start", "imac_fixed_staging"))
        stage_eq = events.index(("eq", "imac_fixed_staging"))
        keepalive_stopped = events.index(("keepalive-stop", "silent"))
        default_switched = events.index(("run", ("pactl", "set-default-sink", "imac_fixed_staging")))
        stream_moved = events.index(("migrate", "imac_fixed", "imac_fixed_staging"))
        stable_ready = events.index(("keepalive-start", "imac_fixed"))
        stable_eq = events.index(("eq", "imac_fixed"))
        stable_keepalive_stopped = events.index(("keepalive-stop", "silent"), keepalive_stopped + 1)
        stream_returned = events.index(("migrate", "imac_fixed_staging", "imac_fixed"))
        self.assertLess(stage_ready, stage_eq)
        self.assertLess(stage_eq, keepalive_stopped)
        self.assertLess(keepalive_stopped, default_switched)
        self.assertLess(default_switched, stream_moved)
        self.assertLess(stream_moved, stable_ready)
        self.assertLess(stable_ready, stable_eq)
        self.assertLess(stable_eq, stable_keepalive_stopped)
        self.assertLess(stable_keepalive_stopped, stream_returned)

    def test_failed_stage_move_restores_clients_before_unloading_the_stage(self):
        events = []
        state = {
            **CONTROLS.DEFAULT_STATE,
            "routes": dict(CONTROLS.DEFAULT_STATE["routes"]),
        }

        def fake_run(args, **kwargs):
            events.append(("run", tuple(args)))
            return CONTROLS.subprocess.CompletedProcess(args, 0, "", "")

        def fake_migrate(source, dest, **kwargs):
            events.append(("migrate", source, dest))
            raise RuntimeError("simulated relink lag")

        with patch.object(CONTROLS, "current_sink_state",
                          return_value=("100%", "0", "imac_fixed")), \
                patch.object(CONTROLS, "load_state", return_value=state), \
                patch.object(CONTROLS, "remap_module_id",
                             side_effect=[None, "staged-module"]), \
                patch.object(CONTROLS, "load_remap_sink", return_value="module-id"), \
                patch.object(CONTROLS, "wait_for_sink"), \
                patch.object(CONTROLS, "run", side_effect=fake_run), \
                patch.object(CONTROLS, "sink_input_ids_for_sink",
                             return_value=["chromium-id"]), \
                patch.object(CONTROLS, "sink_input_records", return_value=[
                    {"id": "chromium-id", "sink": "100", "node": "Chromium",
                     "object": "42", "application": "Chromium", "filename": ""},
                ]), \
                patch.object(CONTROLS, "sink_index",
                             side_effect=lambda name: "200" if name == "imac_fixed_staging" else "100"), \
                patch.object(CONTROLS, "start_handoff_keepalive",
                             side_effect=lambda sink: events.append(("keepalive-start", sink)) or "silent"), \
                patch.object(CONTROLS, "stop_handoff_keepalive",
                             side_effect=lambda process: events.append(("keepalive-stop", process))), \
                patch.object(CONTROLS, "apply_eq_graph"), \
                patch.object(CONTROLS, "migrate_sink_inputs", side_effect=fake_migrate), \
                patch.object(CONTROLS, "migrate_sink_input_ids",
                             side_effect=lambda ids, dest, **kwargs:
                             events.append(("rollback", tuple(ids), dest))):
            with self.assertRaisesRegex(RuntimeError, "simulated relink lag"):
                CONTROLS.reload_remap(dict(CONTROLS.DEFAULT_STATE["routes"]))

        rollback = events.index(("rollback", ("chromium-id",), "imac_fixed"))
        unload = events.index(("run", ("pactl", "unload-module", "staged-module")))
        default_restored = events.index(("run", ("pactl", "set-default-sink", "imac_fixed")))
        self.assertLess(default_restored, rollback)
        self.assertLess(rollback, unload)


class CameraControlTests(unittest.TestCase):
    def test_authorization_switch_reads_back_on_and_off(self):
        with tempfile.TemporaryDirectory() as directory:
            authorization = Path(directory) / "authorized"
            authorization.write_text("0\n")

            def fake_pkexec(args, *, input, **kwargs):
                self.assertEqual(args, ["/usr/bin/pkexec", "/usr/bin/tee", str(authorization)])
                authorization.write_text(input)
                return CONTROLS.subprocess.CompletedProcess(args, 0, input, "")

            with patch.object(CONTROLS, "CAMERA_AUTH", authorization), \
                    patch.object(CONTROLS.subprocess, "run", side_effect=fake_pkexec), \
                    patch.object(CONTROLS.time, "sleep"):
                CONTROLS.set_camera(True)
                self.assertEqual(authorization.read_text().strip(), "1")
                CONTROLS.set_camera(False)
                self.assertEqual(authorization.read_text().strip(), "0")


if __name__ == "__main__":
    unittest.main()

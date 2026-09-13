#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in 44.1-to-48 kHz playback/quality verification on the configured iMac."""
import argparse
import array
import json
import math
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import wave
from e2e_stereo_upmix import active_client_streams, sink_index, MONITOR, run
from e2e_output_handoff import continuous_audio_floor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    if not parser.parse_args().run:
        parser.error('add --run to play quiet test tones')
    live = json.loads(run(['pactl', '-f', 'json', 'list', 'sink-inputs']))
    if any(o['sink'] == sink_index('imac_fixed') and not o['mute'] and not o.get('corked', False)
           and not o.get('properties', {}).get('node.name', '').startswith(('output.', 'effect_output.'))
           and o.get('properties', {}).get('media.filename') != '/dev/zero' for o in live):
        raise RuntimeError('Pause or mute application playback before the quality check')
    results = []
    with tempfile.TemporaryDirectory(prefix='blue-view-quality-') as tmp:
        wav = Path(tmp) / 'quality.wav'
        samples = array.array('h')
        for frame in range(44100 * 8):
            samples.extend(round(32767 * 10**(-24/20) * math.sin(2*math.pi*hz*frame/44100))
                           for hz in (440, 880))
        with wave.open(str(wav), 'wb') as out:
            out.setnchannels(2); out.setsampwidth(2); out.setframerate(44100)
            out.writeframes(samples.tobytes())
        for client, command in (
            ('PulseAudio', ['paplay', '--device=imac_fixed', str(wav)]),
            ('PipeWire', ['pw-cat', '--playback', '--target=imac_fixed', str(wav)]),
            ('ALSA', ['aplay', '-q', '-D', 'default', str(wav)]),
        ):
            capture = Path(tmp) / 'monitor.raw'
            player = None
            with capture.open('wb') as output:
                recorder = subprocess.Popen(['parec', '--device='+MONITOR, '--format=s32le',
                    '--rate=48000', '--channels=4', '--channel-map=front-left,front-right,rear-left,rear-right',
                    '--fix-format', '--fix-rate', '--fix-channels', '--raw', '--no-remix', '--latency-msec=20'],
                    stdout=output, stderr=subprocess.PIPE)
                try:
                    time.sleep(.4)
                    player = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    deadline = time.monotonic()+5
                    node = None
                    while time.monotonic() < deadline:
                        snapshot = json.loads(run(['pw-dump']))
                        client_ids = {str(o['id']) for o in snapshot if str(o.get('info', {}).get('props', {}).get('application.process.id')) == str(player.pid)}
                        for obj in snapshot:
                            props = obj.get('info', {}).get('props', {})
                            if ((str(props.get('application.process.id')) == str(player.pid) or str(props.get('client.id')) in client_ids)
                                    and props.get('media.class') == 'Stream/Output/Audio'):
                                node = obj['id']
                                break
                        if node is not None: break
                        time.sleep(.1)
                    if node is None: raise RuntimeError(client+': stream not identified')
                    props = run(['pw-cli', 'enum-params', str(node), 'Props'])
                    quality = 'String "resample.quality"\n        Int 14' in props
                    if player.wait(timeout=12) != 0: raise RuntimeError(client+': playback failed')
                    time.sleep(.2)
                finally:
                    if player is not None and player.poll() is None:
                        player.terminate(); player.wait(timeout=3)
                    recorder.send_signal(signal.SIGINT)
                    recorder.wait(timeout=3)
            raw = array.array('i'); raw.frombytes(capture.read_bytes())
            if __import__('sys').byteorder != 'little': raw.byteswap()
            lanes = []
            for ch in range(4):
                values=raw[ch::4]
                rms=math.sqrt(sum(x*x for x in values)/max(1,len(values)))/(2**31)
                lanes.append(20*math.log10(rms) if rms else -240)
            # Ignore stream startup/tail. The central four seconds must stay live.
            floor, windows = continuous_audio_floor(capture, 2, 6)
            passed=quality and min(lanes)>-80 and floor>-80 and windows==40
            results.append(dict(client=client, quality14=quality, lane_rms_dbfs=lanes,
                                floor_100ms_dbfs=floor, windows=windows, passed=passed))
            if not passed:
                print(json.dumps(results,indent=2)); raise RuntimeError(client+': quality gate failed')
    print(json.dumps(results,indent=2))

if __name__ == '__main__':
    main()

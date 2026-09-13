#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Opt-in mic/monitor comparison. Raw microphone data is never written to disk."""
import argparse
import array
import json
import math
from pathlib import Path
import statistics
import subprocess
import threading
import time

RATE = 48000
ORDER = ('front-left', 'front-right', 'rear-left', 'rear-right')
FREQ = 1000
BLOCK = RATE // 10
ROOT = Path(__file__).resolve().parents[1]


def call(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def objects(kind):
    return json.loads(call('pactl', '--format=json', 'list', kind))


def stop(proc):
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


class Capture:
    def __init__(self, source, channels, label):
        self.label, self.source, self.channels = label, source, channels
        self.data = bytearray()
        self.proc = subprocess.Popen([
            'parec', '--device=' + source, '--raw', '--format=s32le',
            '--rate=48000', '--channels=' + str(channels), '--fix-format',
            '--fix-rate', '--fix-channels', '--no-remix', '--latency-msec=20',
            '--stream-name=' + label,
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        while chunk := self.proc.stdout.read(8192):
            self.data.extend(chunk)

    def verify(self):
        match = [o for o in objects('source-outputs')
                 if o.get('properties', {}).get('media.name') == self.label]
        source = next(o for o in objects('sources') if o['name'] == self.source)
        expected = f's32le {self.channels}ch 48000Hz'
        if len(match) != 1 or match[0]['source'] != source['index'] or match[0]['sample_specification'] != expected:
            raise RuntimeError(f'Capture source or format not verified for {self.label}')
        if match[0]['mute'] or source['mute']:
            raise RuntimeError(f'Capture is muted: {self.label}')
        return {'source': self.source, 'format': expected, 'channel_map': match[0]['channel_map']}

    def close(self):
        stop(self.proc)
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError('Capture reader did not stop')
        samples = array.array('i')
        samples.frombytes(self.data[:len(self.data) // (4*self.channels) * (4*self.channels)])
        if __import__('sys').byteorder != 'little':
            samples.byteswap()
        self.data.clear()
        return samples


def tone_db(values, frequency=FREQ):
    # Goertzel evaluates the known stimulus frequency, rather than room RMS.
    coef = 2 * math.cos(2 * math.pi * frequency / RATE)
    a = b = 0.0
    for value in values:
        a, b = value / 2147483648 + coef * a - b, a
    power = max(0, a*a + b*b - coef*a*b)
    rms = math.sqrt(2*power) / len(values) if values else 0
    return 20*math.log10(max(rms, 1e-12))


def windows(samples, channels, channel, frequency=FREQ):
    mono = samples[channel::channels]
    return [tone_db(mono[i:i+BLOCK], frequency) for i in range(0, len(mono)-BLOCK+1, BLOCK)]


def stimulus(peak_dbfs, frequency=FREQ):
    result = array.array('h', [0]) * (RATE * 4)
    amplitude = 32767 * 10**(peak_dbfs/20)
    for channel in range(4):
        for n in range(RATE):
            fade = min(1, n/(RATE*.03), (RATE-1-n)/(RATE*.03))
            value = round(amplitude * fade * math.sin(2*math.pi*frequency*n/RATE))
            result.extend(value if c == channel else 0 for c in range(4))
        result.extend([0] * round(.4*RATE*4))
    result.extend([0] * RATE * 4)
    if __import__('sys').byteorder != 'little':
        result.byteswap()
    return result.tobytes()


def probe(sink, mic, monitor, peak_dbfs, frequency=FREQ):
    captures = []
    samples = []
    try:
        captures.append(Capture(mic, 2, 'BlueView-acoustic-mic'))
        captures.append(Capture(monitor, 4, 'BlueView-acoustic-monitor'))
        time.sleep(.4)
        bindings = [c.verify() for c in captures]
        p = subprocess.Popen(['paplay', '--raw', '--format=s16le', '--rate=48000',
                              '--channels=4', '--channel-map=' + ','.join(ORDER),
                              '--no-remix', '--volume=65536', '--latency-msec=20',
                              '--device=' + sink, '--stream-name=BlueView-acoustic-tone'],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            _, err = p.communicate(stimulus(peak_dbfs, frequency), timeout=15)
            if p.returncode:
                raise RuntimeError(err.decode())
        finally:
            stop(p)
        time.sleep(.4)
    finally:
        for c in captures:
            samples.append(c.close())
    mic_data, monitor_data = samples
    mic_w = [windows(mic_data, 2, c, frequency) for c in range(2)]
    side_w = [[windows(mic_data, 2, c, f) for f in (max(10, frequency-50), frequency+50)] for c in range(2)]
    monitor_w = [windows(monitor_data, 4, c, frequency) for c in range(4)]
    # Find the four actual tone runs on the monitor, avoiding process-start timing.
    active = [max(v) > -70 for v in zip(*monitor_w)]
    runs = []
    start = None
    for i, on in enumerate(active + [False]):
        if on and start is None:
            start = i
        if not on and start is not None:
            if i-start >= 5:
                runs.append((start, i))
            start = None
    if len(runs) != 4:
        raise RuntimeError(f'Expected four isolated monitor tones; got {runs}')
    result = []
    for logical, (first, last) in zip(ORDER, runs):
        lane_levels = [statistics.median(w[first+1:last-1]) for w in monitor_w]
        lane = max(range(4), key=lambda c: lane_levels[c])
        mic_results = []
        for c, w in enumerate(mic_w):
            # Use only the steady interior; a median avoids selecting noise peaks.
            on = w[first+2:min(len(w), last-2)]
            if len(on) < 3:
                raise RuntimeError('Insufficient microphone samples for a tone window')
            off = w[:max(1, runs[0][0]-3)] + w[min(len(w), runs[-1][1]+3):]
            baseline = statistics.median(off)
            response = statistics.median(on)
            side = statistics.median([v for band in side_w[c] for v in band[first+2:last-2]])
            mic_results.append({'tone_dbfs': round(response, 1), 'baseline_dbfs': round(baseline, 1),
                                'rise_db': round(response-baseline, 1),
                                'above_neighbor_bins_db': round(response-side, 1),
                                'clear_tone_detected': response-baseline >= 15 and response-side >= 15})
        result.append({'logical_channel': logical, 'monitor_lane': ORDER[lane],
                       'monitor_tone_dbfs': round(lane_levels[lane], 1), 'microphones': mic_results})
    return {'sink': sink, 'bindings': bindings, 'frequency_hz': frequency, 'stimulus_peak_dbfs': peak_dbfs,
            'channels': result,
            'interpretation': 'Mic tone rise is evidence to review, not a pass for perceived loudness or speaker location.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--temporarily-mute-playback', action='store_true')
    parser.add_argument('--peak-dbfs', type=float, default=-30,
                        help='Quiet tone peak, between -40 and -24 dBFS (default -30)')
    args = parser.parse_args()
    if not args.run:
        parser.error('Use --run to play quiet tones and capture the built-in microphone briefly.')
    if not -40 <= args.peak_dbfs <= -24:
        parser.error('--peak-dbfs must be between -40 and -24')
    state = json.loads(call(str(ROOT/'scripts/imac-audio-controls'), 'status'))
    if any(state['speakerMutes'].values()) or state['spatialEnabled']:
        raise RuntimeError('Unmute speaker lanes and disable Open Quad before this isolation test.')
    mic = call('pactl', 'get-default-source')
    if mic.endswith('.monitor'):
        raise RuntimeError('Select a real microphone first.')
    master = state['masterSink']
    before_default = call('pactl', 'get-default-sink')
    before_state = (Path.home()/'.local/state/blueview-audio/device-state.json').read_bytes()
    before_devices = {(kind, o['index']): (o['mute'], o['volume'])
                      for kind in ('sinks', 'sources') for o in objects(kind)}
    clients = [o for o in objects('sink-inputs') if not o.get('properties', {}).get('node.name', '').startswith(('output.', 'effect_output.'))
               and o.get('properties', {}).get('media.filename') != '/dev/zero']
    if clients and not args.temporarily_mute_playback:
        raise RuntimeError('Playback is active; use --temporarily-mute-playback for brief isolation with streams kept running.')
    muted = []
    results = []
    try:
        for o in clients:
            if not o['mute']:
                call('pactl', 'set-sink-input-mute', str(o['index']), 'true')
                muted.append(o['index'])
        for sink in ('imac_fixed', master):
            results.append(probe(sink, mic, master+'.monitor', args.peak_dbfs))
    finally:
        live = {o['index'] for o in objects('sink-inputs')}
        for index in muted:
            if index in live:
                call('pactl', 'set-sink-input-mute', str(index), 'false')
        assert call('pactl', 'get-default-sink') == before_default, 'Default output changed'
        assert (Path.home()/'.local/state/blueview-audio/device-state.json').read_bytes() == before_state, 'Saved state changed'
    live = {o['index']: o for o in objects('sink-inputs')}
    restored = all(o['index'] in live and live[o['index']]['mute'] == o['mute']
                   and live[o['index']]['corked'] == o['corked']
                   and live[o['index']]['volume'] == o['volume'] for o in clients)
    after_devices = {(kind, o['index']): (o['mute'], o['volume'])
                     for kind in ('sinks', 'sources') for o in objects(kind)}
    restored = restored and before_devices == after_devices
    acoustic_clear = all(any(m['clear_tone_detected'] for m in c['microphones'])
                         for r in results for c in r['channels'])
    print(json.dumps({'results': results, 'client_mute_and_cork_restored': restored,
                      'acoustic_gate': 'clear-tone-detected' if acoustic_clear else 'inconclusive',
                      'microphone_recordings_retained': False}, indent=2))
    if not restored:
        raise RuntimeError('An application stream changed during the test; review restoration.')
    if not acoustic_clear:
        raise SystemExit(2)


if __name__ == '__main__':
    main()

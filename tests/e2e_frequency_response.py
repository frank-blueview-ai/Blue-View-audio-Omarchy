#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Low-level per-lane response survey; not driver-limit or calibrated SPL testing."""
import argparse
import json
from pathlib import Path
import sys
from e2e_acoustic_probe import ROOT, call, objects, probe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--temporarily-mute-playback', action='store_true')
    args = parser.parse_args()
    if not args.run:
        parser.error('add --run to play quiet per-channel tones and sample the microphone')
    state = json.loads(call(str(ROOT/'scripts/imac-audio-controls'), 'status'))
    if state['eqEnabled'] or state['spatialEnabled'] or any(state['speakerMutes'].values()):
        raise RuntimeError('Use EQ bypass, spatial off, and unmuted channels for response measurement')
    if state['speakerBalance'] or state['speakerFade']:
        raise RuntimeError('Center balance/fade first; this test never moves saved sliders')
    mic = call('pactl','get-default-source')
    if mic.endswith('.monitor'): raise RuntimeError('Select a physical microphone')
    saved = Path.home()/'.local/state/blueview-audio/device-state.json'
    before = saved.read_bytes()
    clients = [o for o in objects('sink-inputs') if not o.get('properties',{}).get('node.name','').startswith(('output.','effect_output.'))
               and o.get('properties',{}).get('media.filename') != '/dev/zero']
    if clients and not args.temporarily_mute_playback:
        raise RuntimeError('Use --temporarily-mute-playback to isolate test tones')
    muted=[]; results=[]
    try:
        for o in clients:
            if not o['mute']:
                call('pactl','set-sink-input-mute',str(o['index']),'true'); muted.append(o['index'])
        for frequency in (40,63,100,160,250,500,1000,2000,4000,8000,12000,16000):
            print(f'Measuring {frequency} Hz at -30 dBFS peak',file=sys.stderr,flush=True)
            results.append(probe('imac_fixed',mic,state['masterSink']+'.monitor',-30,frequency))
    finally:
        live={o['index'] for o in objects('sink-inputs')}
        for index in muted:
            if index in live: call('pactl','set-sink-input-mute',str(index),'false')
        assert saved.read_bytes()==before, 'Saved controls changed'
    print(json.dumps({'measurements':results,'saved_controls_unchanged':True,
        'raw_audio_retained':False,'interpretation':
        'Combined speaker/room/uncalibrated-mic response. Weak pickup does not prove a speaker cutoff. '
        'No automatic EQ, crossover, or driver-limit claim is made.'},indent=2))

if __name__=='__main__': main()

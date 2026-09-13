#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Isolated mono/centered-stereo fill regression: no physical playback or mic capture."""
import sys,subprocess,time,array,math,statistics,json,threading
from pathlib import Path
if '--run' not in sys.argv:
 raise SystemExit('Pass --run to create a temporary silent virtual sink and measure its channels.')
from e2e_acoustic_probe import Capture,windows,call
import os
sink='blueview_fill_probe_'+str(os.getpid())
module=call('pactl','load-module','module-null-sink','sink_name='+sink,'format=s32le','channels=4','channel_map=front-left,front-right,rear-left,rear-right')
results=[]
try:
 for method in ['psd','simple']:
  for channels in [1,2]:
   capture=Capture(sink+'.monitor',4,'BlueView-fill-probe')
   player = None
   try:
    time.sleep(.25); capture.verify()
    samples=array.array('f')
    for n in range(48000*2): samples.extend([.02*math.sin(2*math.pi*1000*n/48000)]*channels)
    player=subprocess.Popen(['pw-cat','--playback','--raw','--format=f32','--rate=48000','--channels='+str(channels),'--channel-map='+('MONO' if channels==1 else 'FL,FR'),'--target='+sink,'-'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL)
    worker=threading.Thread(target=lambda: player.communicate(samples.tobytes()))
    worker.start(); time.sleep(.4)
    destination=next(o['index'] for o in json.loads(call('pactl','-f','json','list','sinks')) if o['name']==sink)
    record=next(o for o in json.loads(call('pactl','-f','json','list','sink-inputs')) if o['sink']==destination)
    node=record['properties']['object.id']
    call('pw-cli','set-param',str(node),'Props','{ params = [ channelmix.upmix true channelmix.upmix-method "'+method+'" ] }')
    worker.join(6)
    if worker.is_alive(): player.kill(); worker.join(); raise RuntimeError('Playback timeout')
    time.sleep(.2)
   finally:
    if player is not None and player.poll() is None:
     player.terminate()
     try: player.wait(timeout=2)
     except subprocess.TimeoutExpired: player.kill(); player.wait(timeout=2)
    data=capture.close()
   levels=[round(statistics.median(sorted(windows(data,4,c),reverse=True)[:10]),1) for c in range(4)]
   results.append({'method':method,'channels':channels,'same_signal_all_inputs':True,'tone_dbfs':levels})
 print(json.dumps(results,indent=2))
 for result in results:
  levels=result['tone_dbfs']
  assert min(levels[:2]) > -60, result
  if result['method']=='simple': assert min(levels) > -60, result
  else: assert max(levels[2:]) < -100, result
finally: call('pactl','unload-module',module)

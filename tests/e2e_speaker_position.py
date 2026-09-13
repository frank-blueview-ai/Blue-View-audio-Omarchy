#!/usr/bin/env python3
# Copyright © 2026 The Blue View Group Corporation and Frank Perez (frank@blueview.ai).
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Tests balance/fade through a remap sink into a silent virtual output."""
import sys,runpy,subprocess,time,threading,array,math,json,os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if '--run' not in sys.argv: raise SystemExit('Pass --run for the isolated speaker-position check.')
from e2e_acoustic_probe import Capture,call,stop
h=runpy.run_path(str(ROOT/'scripts/imac-audio-controls'))
sink='blueview_position_probe_'+str(os.getpid())
module=call('pactl','load-module','module-null-sink','sink_name='+sink,'format=s32le','channels=4','channel_map=front-left,front-right,rear-left,rear-right')
remap=sink+'_processed'
remap_module=call('pactl','load-module','module-remap-sink','sink_name='+remap,'master='+sink,'channels=4','channel_map=front-left,front-right,rear-left,rear-right','master_channel_map=front-left,front-right,rear-left,rear-right','remix=no')
capture=player=None
try:
 capture=Capture(sink+'.monitor',4,'BlueView-position-probe')
 time.sleep(.3); capture.verify()
 block=array.array('f')
 for i in range(48000): block.extend([.02*math.sin(2*math.pi*1000*i/48000)]*4)
 player=subprocess.Popen(['pw-cat','--playback','--raw','--format=f32','--rate=48000','--channels=4','--target='+remap,'-'],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 def feed():
  try:
   while player.poll() is None:player.stdin.write(block.tobytes()); player.stdin.flush()
  except (BrokenPipeError,ValueError):pass
 thread=threading.Thread(target=feed,daemon=True);thread.start()
 results=[]
 for balance,fade in [(0,0),(50,-50),(0,0)]:
  state={**h['load_state'](),'eqEnabled':False,'speakerBalance':balance,'speakerFade':fade}
  h['apply_eq_graph'](remap,state)
  time.sleep(.5)
  start=len(capture.data)//16*16
  time.sleep(1)
  end=len(capture.data)//16*16
  samples=array.array('i');samples.frombytes(capture.data[start:end])
  assert len(samples)>48000,'Insufficient capture samples'
  levels=[20*math.log10(max(1e-12,math.sqrt(sum((x/2**31)**2 for x in samples[c::4])/len(samples[c::4])))) for c in range(4)]
  results.append({'balance':balance,'fade':fade,'levels_dbfs':levels})
  assert player.poll() is None,'Playback stream stopped'
 expected=[-6.0206,0,-12.0412,-6.0206]
 deltas=[b-a for a,b in zip(results[0]['levels_dbfs'],results[1]['levels_dbfs'])]
 assert all(abs(a-b)<.5 for a,b in zip(deltas,expected)),deltas
 assert all(abs(a-b)<.5 for a,b in zip(results[0]['levels_dbfs'],results[2]['levels_dbfs']))
 print(json.dumps({'results':results,'measured_attenuation_db':deltas,'gate':'passed'},indent=2))
finally:
 if player is not None:stop(player)
 if capture is not None:capture.close()
 call('pactl','unload-module',remap_module)
 call('pactl','unload-module',module)

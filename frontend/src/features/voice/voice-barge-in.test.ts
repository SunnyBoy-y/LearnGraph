import { describe, expect, it, vi } from 'vitest';
import { VoiceBargeIn } from './voice-barge-in';
const speech={probability:0.95,rms:0.02,durationMs:10};
function feed(gate:VoiceBargeIn, start:number, count:number, voiced=true){
  for(let i=0;i<count;i++)gate.frame(voiced?speech:{...speech,probability:0.05},start+i*10,true);
}
describe('reversible local playback gate',()=>{
  it('suppresses sustained speech before ASR, then releases the replacement reply',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);feed(gate,0,6);
    expect(apply).toHaveBeenCalledWith(true,'local-speech');
    gate.confirm(800);gate.botStarted(1100);
    expect(apply).toHaveBeenLastCalledWith(false,'replacement-reply');
  });
  it('ignores short spikes and low-probability loud noise',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);feed(gate,0,5);feed(gate,50,100,false);
    expect(apply).not.toHaveBeenCalled();
  });
  it('restores playback after a rejected noise/backchannel without cancelling any generation',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);feed(gate,0,6);feed(gate,60,100,false);
    expect(gate.muted).toBe(false);expect(apply).toHaveBeenLastCalledWith(false,'unconfirmed-resume');
  });
  it('does not count a missing detector interval as sustained speech',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);feed(gate,0,3);feed(gate,200,3);
    expect(apply).not.toHaveBeenCalled();
  });
  it('keeps old playback suppressed while a meaningful partial awaits final',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);feed(gate,0,6);
    gate.transcription('停一下，只回答',700);gate.tick(1100);
    expect(gate.muted).toBe(true);
    gate.confirm(1250);gate.botStarted(1800);
    expect(apply).toHaveBeenLastCalledWith(false,'replacement-reply');
  });
  it('fails open on detector loss and clears mute at teardown',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);feed(gate,0,6);gate.tick(1700);
    expect(gate.muted).toBe(false);feed(gate,2000,6);gate.reset('disconnect');
    expect(apply).toHaveBeenLastCalledWith(false,'disconnect');
  });
  it('never suppresses idle listening or non-finite detector values',()=>{
    const apply=vi.fn(),gate=new VoiceBargeIn(apply);
    for(let t=0;t<200;t+=10)gate.frame(speech,t,false);
    gate.frame({...speech,probability:NaN},300,true);
    expect(apply).not.toHaveBeenCalled();
  });
});

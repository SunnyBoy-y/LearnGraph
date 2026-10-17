import {RnnoiseEngine} from '/src/features/voice/rnnoise/engine.ts';
class PriorityProbe extends AudioWorkletProcessor{
 constructor(){super();const engine=new RnnoiseEngine(),frame=new Float32Array(480),times=[];let seed=7;
  for(let f=-24;f<96;f++){
   for(let i=0;i<480;i++){seed=(Math.imul(seed,1664525)+1013904223)>>>0;frame[i]=(seed/4294967296-.5)*.2;}
   const start=Date.now();engine.process(frame);if(f>=0)times.push(Date.now()-start);
  }
  times.sort((a,b)=>a-b);this.port.postMessage({meanMs:times.reduce((a,b)=>a+b)/times.length,p95Ms:times[Math.floor(times.length*.95)],maxMs:times.at(-1),frames:96});engine.destroy();
 }
 process(){return false;}
}
registerProcessor('priority-probe',PriorityProbe);

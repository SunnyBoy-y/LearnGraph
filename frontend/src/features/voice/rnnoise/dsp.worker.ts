import { RnnoiseEngine } from './engine';
import { FastRnnoiseEngine } from './fast-engine';

let engine: RnnoiseEngine | FastRnnoiseEngine | null = null;
let audioPort: MessagePort | null = null;
self.onmessage = (event: MessageEvent<{ type: string; port?: MessagePort; module?: WebAssembly.Module }>) => {
  try {
    if (event.data.type === 'start') {
      engine = event.data.module ? new FastRnnoiseEngine(event.data.module) : new RnnoiseEngine();
      self.postMessage({ type: 'ready' });
    }
    if (event.data.type === 'port' && event.data.port) {
      audioPort = event.data.port;
      audioPort.onmessage = (message: MessageEvent<{ id: number; frame: Float32Array<ArrayBuffer> }>) => {
        try {
          if (!engine) throw new Error('RNNoise Worker not initialized');
          const { id, frame } = message.data;
          const start = performance.now();
          const rms=Math.sqrt(frame.reduce((sum,value)=>sum+value*value,0)/frame.length);
          const probability=engine.process(frame);
          audioPort!.postMessage({ type: 'frame', id, frame, probability, rms, computeMs: performance.now() - start }, [frame.buffer]);
        } catch (error) { self.postMessage({ type: 'failure', reason: String(error) }); }
      };
      audioPort.start();
    }
  } catch (error) { self.postMessage({ type: 'failure', reason: String(error) }); }
};

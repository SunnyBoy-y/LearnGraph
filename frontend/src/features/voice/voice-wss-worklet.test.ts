import { expect, it } from "vitest";
import source from "./voice-wss-worklet.js?raw";

it("keeps capturing after postMessage detaches the previous 20ms buffer", () => {
  const packets: Float32Array[] = [];
  class Processor {
    port = {
      postMessage: (data: { audio: Float32Array }, transfer: ArrayBuffer[]) => {
        packets.push(structuredClone(data, { transfer }).audio);
        expect(data.audio.byteLength).toBe(0);
      },
    };
  }
  let Capture!: new () => { process(inputs: Float32Array[][], outputs: Float32Array[][]): boolean };
  new Function("AudioWorkletProcessor", "sampleRate", "currentTime", "registerProcessor", source)(
    Processor, 48000, 0, (_name: string, constructor: typeof Capture) => { Capture = constructor; },
  );
  const capture = new Capture();
  const input = new Float32Array(128).fill(0.25);
  const output = new Float32Array(128).fill(1);
  for (let i = 0; i < 75; i++) {
    expect(capture.process([[input]], [[output]])).toBe(true);
  }
  expect(packets).toHaveLength(10);
  expect(packets.every(packet => packet.length === 960 && packet[959] === 0.25)).toBe(true);
  expect(output.every(sample => sample === 0)).toBe(true);
});

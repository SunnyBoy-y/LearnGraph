/* 20 ms mono capture. Only silence is routed to the local output. */
class VoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(Math.round(sampleRate * 0.02));
    this.offset = 0;
  }

  process(inputs, outputs) {
    for (const channel of outputs[0] ?? []) channel.fill(0);
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (let index = 0; index < input.length; index++) {
      this.buffer[this.offset++] = input[index];
      if (this.offset === this.buffer.length) {
        const audio = this.buffer;
        const length = audio.length;
        this.port.postMessage({ audio, capturedAt: currentTime * 1000 }, [audio.buffer]);
        this.buffer = new Float32Array(length);
        this.offset = 0;
      }
    }
    return true;
  }
}

registerProcessor("learngraph-voice-capture", VoiceCaptureProcessor);

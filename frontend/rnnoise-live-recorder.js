class VoiceAcceptanceRecorder extends AudioWorkletProcessor {
  constructor(){super();this.size=4800;this.used=0;this.buffer=new Float32Array(this.size*4);this.firstFrame=0;}
  process(inputs,outputs){
    const channels=inputs[0]||[];
    const size=outputs[0]?.[0]?.length??128;
    for(let i=0;i<size;i++){
      if(this.used===0)this.firstFrame=currentFrame+i;
      for(let channel=0;channel<4;channel++)this.buffer[this.used*4+channel]=channels[channel]?.[i]??0;
      this.used++;
      if(this.used===this.size){
        const data=this.buffer;
        this.port.postMessage({firstFrame:this.firstFrame,sampleRate,channels:4,samples:this.size,data},[data.buffer]);
        this.buffer=new Float32Array(this.size*4);this.used=0;
      }
    }
    return true;
  }
}
registerProcessor('voice-acceptance-recorder',VoiceAcceptanceRecorder);

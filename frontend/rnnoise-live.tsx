import { useEffect, useState, useSyncExternalStore } from 'react';
import { createRoot } from 'react-dom/client';
import './src/index.css';
import { apiClient } from './src/api/client';
import { authStore } from './src/api/auth-store';
import { voiceSessionController as controller } from './src/features/voice/voice-session-controller';
import { VoiceLatencyRail } from './src/features/voice/voice-debug-panel';
import { getVoiceLatencySnapshot } from './src/features/voice/voice-latency-store';
import { useVoiceNoiseStatus } from './src/features/voice/voice-noise-store';

type Trace = { at: number; type: string; data: unknown };
const trace: Trace[] = [];
const capture = (type: string, data: unknown) => {
  const row={at:performance.now(),type,data};trace.push(row);
  (window as unknown as {saveVoiceTrace?: (row: Trace) => void}).saveVoiceTrace?.(row);
};
let selectedDevice = '';
let actualRaw: MediaStream | null = null;
let actualSent: MediaStream | null = null;
let actualRemote: MediaStream | null = null;
let playbackMuted = false;
window.addEventListener('learngraph:voice-playback-gate',event=>{
  const detail=(event as CustomEvent).detail;playbackMuted=detail.muted;
  capture('local-playback-gate',detail);
});
const nativePlay = HTMLMediaElement.prototype.play;
HTMLMediaElement.prototype.play = async function () {
  if (this.srcObject && selectedDevice) {
    const devices=await navigator.mediaDevices.enumerateDevices();
    const selected=devices.find(d=>d.deviceId===selectedDevice);
    const output=selected?.label.includes('Redmi')?devices.find(d=>d.kind==='audiooutput'&&d.label.includes('Redmi')):undefined;
    if(output&&this.sinkId!==output.deviceId){
      try{await this.setSinkId(output.deviceId);capture('playback-device',{label:output.label});}
      catch(error){capture('playback-device-error',String(error));}
    }
  }
  return nativePlay.call(this);
};
const nativeCapture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = async (constraints) => {
  const rnnoiseOnly=localStorage.getItem('learngraph.voice.validationRnnoiseOnly')==='1';
  const requested={...constraints,audio:typeof constraints.audio==='object'
    ?{...constraints.audio,...(rnnoiseOnly?{noiseSuppression:false}:{}),...(selectedDevice?{deviceId:{exact:selectedDevice}}:{})}:constraints.audio};
  actualRaw=await nativeCapture(requested);
  const track=actualRaw.getAudioTracks()[0];
  const settings=track.getSettings();delete settings.deviceId;delete settings.groupId;
  capture('physical-capture',{label:track.label,settings});
  return actualRaw;
};
const NativePeer=window.RTCPeerConnection;
class TracedPeer extends NativePeer {
  constructor(configuration?: RTCConfiguration){
    super(configuration);this.addEventListener('track',event=>{actualRemote=event.streams[0]});
  }
  createDataChannel(label: string, options?: RTCDataChannelInit) {
    const channel=super.createDataChannel(label,options);
    channel.addEventListener('message',event=>{
      try{capture('rtvi',JSON.parse(String(event.data)))}catch{capture('rtvi-non-json',String(event.data))}
    });return channel;
  }
  addTrack(track: MediaStreamTrack,...streams: MediaStream[]) {
    actualSent=streams[0];capture('sent-track',{processed:track!==actualRaw?.getAudioTracks()[0],label:track.label});
    return super.addTrack(track,...streams);
  }
}
window.RTCPeerConnection=TracedPeer;
window.addEventListener('learngraph:voice-render',event=>capture('render',(event as CustomEvent).detail));
window.addEventListener('pagehide',()=>controller.disconnect());

function TestPage(){
  const noise=useVoiceNoiseStatus();
  const voice=useSyncExternalStore(controller.subscribe,controller.getSnapshot,controller.getSnapshot);
  const [devices,setDevices]=useState<MediaDeviceInfo[]>([]);
  const [device,setDevice]=useState('');
  const [note,setNote]=useState('准备设备检查。正式测试前请保持安静。');
  const [busy,setBusy]=useState(false);
  const [marker,setMarker]=useState('');
  const [levels,setLevels]=useState({raw:0,processed:0});
  const [fastBarge,setFastBarge]=useState(()=>localStorage.getItem('learngraph.voice.fastBargeIn')==='1');
  const [gateMuted,setGateMuted]=useState(false);
  useEffect(()=>{
    const changed=(event:Event)=>setGateMuted((event as CustomEvent).detail.muted);
    window.addEventListener('learngraph:voice-playback-gate',changed);
    return()=>window.removeEventListener('learngraph:voice-playback-gate',changed);
  },[]);
  useEffect(()=>{
    const {calibration,...rest}=noise;
    const publicCalibration=calibration?(({module,...value})=>value)(calibration):null;
    capture('noise-status',{...rest,calibration:publicCalibration});
  },[noise]);
  useEffect(()=>{
    void navigator.mediaDevices.enumerateDevices().then(all=>{
      const inputs=all.filter(d=>d.kind==='audioinput');setDevices(inputs);
      const preferred=inputs.find(d=>d.label.includes('Redmi'))??inputs.find(d=>d.label.includes('英特尔'))??inputs.find(d=>!d.label.includes('ToDesk')&&!d.label.includes('虚拟'));
      if(preferred){selectedDevice=preferred.deviceId;setDevice(preferred.deviceId)}
    });
  },[]);
  useEffect(()=>{
    const timer=setInterval(()=>capture('latency',getVoiceLatencySnapshot()),1000);
    return()=>clearInterval(timer);
  },[]);
  useEffect(()=>{
    if(voice.transport!=='connected'||!actualRaw||!actualSent)return;
    const context=new AudioContext({sampleRate:48000});
    const raw=context.createAnalyser(),processed=context.createAnalyser();
    context.createMediaStreamSource(actualRaw).connect(raw);
    context.createMediaStreamSource(actualSent).connect(processed);void context.resume();
    let recorder: AudioWorkletNode | null=null;
    const merger=context.createChannelMerger(4);
    context.createMediaStreamSource(actualRaw).connect(merger,0,0);
    context.createMediaStreamSource(actualSent).connect(merger,0,1);
    const rendered=context.createGain();rendered.gain.value=playbackMuted?0:1;
    if(actualRemote){
      const remote=context.createMediaStreamSource(actualRemote);remote.connect(merger,0,2);
      remote.connect(rendered).connect(merger,0,3);
    }
    const gateChanged=(event:Event)=>rendered.gain.setValueAtTime((event as CustomEvent).detail.muted?0:1,context.currentTime);
    window.addEventListener('learngraph:voice-playback-gate',gateChanged);
    void context.audioWorklet.addModule('/rnnoise-live-recorder.js').then(()=>{
      if(context.state==='closed')return;
      recorder=new AudioWorkletNode(context,'voice-acceptance-recorder',{outputChannelCount:[1]});
      recorder.port.onmessage=event=>{
        const bytes=new Uint8Array(event.data.data.buffer);let text='';
        for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));
        const metadata={at:performance.now(),contextTime:context.currentTime,firstFrame:event.data.firstFrame,
          samples:event.data.samples,sampleRate:event.data.sampleRate,channels:4,baseLatency:context.baseLatency,outputLatency:context.outputLatency};
        (window as unknown as {saveVoiceAudio?: (meta:unknown,data:string)=>void}).saveVoiceAudio?.(metadata,btoa(text));
      };
      merger.connect(recorder).connect(context.destination);
      capture('recorder-started',{channels:['capture_after_browser_processing','rnnoise_sent','remote_received','remote_after_local_gate_proxy_not_dac'],sampleRate:48000});
    });
    const data=new Float32Array(raw.fftSize);
    const rms=(analyser:AnalyserNode)=>{analyser.getFloatTimeDomainData(data);return Math.sqrt(data.reduce((s,v)=>s+v*v,0)/data.length)};
    const timer=setInterval(()=>{
      const next={raw:rms(raw),processed:rms(processed)};setLevels(next);capture('mic-level',next);
    },50);
    return()=>{clearInterval(timer);window.removeEventListener('learngraph:voice-playback-gate',gateChanged);recorder?.disconnect();merger.disconnect();void context.close()};
  },[voice.transport]);
  async function connect(){
    setBusy(true);
    try{
      if(!authStore.getWorkspaceId())throw Error('验收会话尚未登录，请等待测试脚本完成准备。');
      const session=await apiClient.post<{id:string}>('/sessions',{title:'RNNoise 物理麦克风验收 2026-10-17',memory_enabled:false});
      controller.open(authStore.getWorkspaceId()!,session.id);controller.setThinkingLimit('off');
      capture('connecting',{chatSessionId:session.id});await controller.connect();
      setNote('连接完成后，等测试员提示再读口令；可以随时点击停止。');
    }catch(error){setNote(String(error));capture('test-error',String(error))}
    finally{setBusy(false)}
  }
  function mark(value:string){capture('manual-marker',{value});setMarker(value)}
  return <main className="mx-auto max-w-6xl p-5 md:p-8">
    <h1 className="text-2xl font-semibold">RNNoise · 真实麦克风与云端 ASR 验收</h1>
    <p className="mt-2 text-sm text-muted-foreground">本地隔离实例，未发布生产。使用真实麦克风、真实云端识别及原有全双工控制器。</p>
    {localStorage.getItem('learngraph.voice.validationRnnoiseOnly')==='1'&&<p className="mt-2 text-sm">采集对照：打开麦克风时关闭浏览器 NS，仅用 RNNoise；AEC 保留。</p>}
    <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_360px]">
      <section className="min-w-0 space-y-4">
        <label className="block text-sm">麦克风<select className="mt-2 block w-full rounded-md border bg-background p-2" value={device} disabled={busy||voice.transport==='connected'} onChange={e=>{selectedDevice=e.target.value;setDevice(e.target.value)}}>
          {devices.map(d=><option key={d.deviceId} value={d.deviceId}>{d.label||'音频输入设备'}</option>)}
        </select></label>
        <label className="block text-sm"><input type="checkbox" checked={fastBarge} disabled={busy||voice.transport==='connected'} onChange={e=>{setFastBarge(e.target.checked);localStorage.setItem('learngraph.voice.fastBargeIn',e.target.checked?'1':'0')}}/> 本地快速让话（实验开关，默认关闭）</label>
        <p className="text-sm">本地播放：{gateMuted?'检测到人声，暂时静音等待识别确认':'正常播放'}</p>
        <div className="flex flex-wrap gap-2">
          <button className="rounded-md border px-3 py-2 text-sm" disabled={busy||voice.transport==='connected'} onClick={()=>void connect()}>连接验收通话</button>
          <button className="rounded-md border px-3 py-2 text-sm" onClick={()=>{controller.disconnect();capture('stopped',{});setNote('已停止麦克风采集和通话。')}}>停止通话</button>
          {voice.playbackBlocked&&<button className="rounded-md border px-3 py-2 text-sm" onClick={()=>void controller.retryPlayback()}>允许播放回复</button>}
        </div>
        <p role="status" className="break-words text-sm">{voice.transport} · {voice.state} · {voice.error||note}</p>
        <p className="text-sm tabular-nums">输入 RMS {levels.raw.toFixed(5)} → RNNoise 后 {levels.processed.toFixed(5)}</p>
        <div className="rounded-xl border p-4 text-sm leading-7">
          <p>第一轮：请说“请从一数到二十，每个数字之间稍作停顿。”</p>
          <p>第二轮：等回复开始后，明确说“停一下，请只回答：测试完成。”</p>
          <p>安静与背景噪声阶段：保持安静约 5 秒；稍后按测试员提示制造键盘声。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {['第一轮开始','我已说完','正在打断','安静阶段','键盘噪声阶段'].map(value=><button key={value} className="rounded-md border px-2 py-1 text-xs" onClick={()=>mark(value)}>{value}</button>)}
        </div>
        <p className="text-xs text-muted-foreground">最近人工标记：{marker||'尚无'}。这些按钮只记录时刻，不触发语音回合或打断。</p>
        <h2 className="font-medium">实时识别与回复</h2>
        <p className="min-h-7 text-sm">{voice.interimUserText}</p>
        <div className="space-y-3">{voice.transcript.map(item=><p className="break-words rounded-md border p-3 text-sm" key={item.id}><b>{item.role==='user'?'你：':'助手：'}</b>{item.text}</p>)}</div>
      </section>
      <aside className="min-w-0 rounded-xl border"><VoiceLatencyRail/></aside>
    </div>
  </main>
}
createRoot(document.getElementById('root')!).render(<TestPage/>);
Object.assign(window,{voiceAcceptance:{trace,getLatency:getVoiceLatencySnapshot,getState:controller.getSnapshot,capture}});

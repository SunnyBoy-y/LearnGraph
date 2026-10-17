import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import './src/index.css';
import { VoiceLatencyRail } from './src/features/voice/voice-debug-panel';
import { createVoiceNoiseInput, calibrateRnnoise, type VoiceNoiseInput } from './src/features/voice/rnnoise/input';
import { resetVoiceNoiseStatus, setVoiceNoiseStatus } from './src/features/voice/voice-noise-store';

let input: VoiceNoiseInput | null = null;
let raw: MediaStream | null = null;
let audioContext: AudioContext | null = null;
function stop() {
  input?.close(); input = null;
  raw?.getTracks().forEach(track => track.stop()); raw = null;
  void audioContext?.close(); audioContext = null;
  resetVoiceNoiseStatus();
}
window.addEventListener('pagehide', stop);

function Preview() {
  const [message, setMessage] = useState('尚未启动。本页使用与全双工通话相同的降噪代码。');
  const [busy, setBusy] = useState(false);
  async function start(file?: File) {
    stop(); setBusy(true);
    try {
      if (file) {
        audioContext = new AudioContext({ sampleRate: 48000 });
        const source = audioContext.createBufferSource();
        source.buffer = await audioContext.decodeAudioData(await file.arrayBuffer());
        source.loop = true;
        const destination = audioContext.createMediaStreamDestination();
        source.connect(destination); source.start(); await audioContext.resume();
        raw = destination.stream;
      } else {
        raw = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1,
          sampleRate: 48000, echoCancellation: true, noiseSuppression: true, autoGainControl: false } });
      }
      input = await createVoiceNoiseInput(raw, { onStatus: setVoiceNoiseStatus });
      const audio = document.querySelector('audio')!;
      // Files are safe to monitor; live microphone monitoring stays muted.
      audio.muted = !file; audio.srcObject = input.stream;
      if (file) await audio.play();
      setMessage(file ? `正在循环试听：${file.name}` : '麦克风处理中，试听已静音以避免扬声器回授。');
    } catch (error) { stop(); setMessage(String(error)); }
    finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-5xl p-5 md:p-10">
    <h1 className="text-2xl font-semibold">RNNoise · 语音输入</h1>
    <p className="mt-3 text-sm text-muted-foreground">麦克风 AEC → 单声道 48 kHz → RNNoise 音频线程 → WebRTC。无需注册声纹。</p>
    <p className="mt-2 text-sm text-muted-foreground">10 ms 帧；保留模型完整状态。设备预检或运行超预算时回退浏览器降噪。CPU 核心数只是提示。</p>
    <div className="mt-6 grid gap-6 md:grid-cols-[1fr_360px]">
      <section className="min-w-0 space-y-4">
        <h2 className="font-medium">测试真实处理链路</h2>
        <div className="flex flex-wrap gap-2">
          <button className="rounded-md border px-3 py-2 text-sm" disabled={busy} onClick={() => void start()}>启动麦克风</button>
          <button className="rounded-md border px-3 py-2 text-sm" disabled={busy} onClick={() => { stop(); setMessage('已停止并释放音轨与音频线程。'); }}>停止</button>
          <button className="rounded-md border px-3 py-2 text-sm" disabled={busy} onClick={async () => {
            setBusy(true); try { const value = await calibrateRnnoise(); setMessage(`后台线程预检：平均 ${value.meanMs.toFixed(3)} ms，P95 ${value.p95Ms.toFixed(3)} ms / 10 ms 帧。`); }
            catch(error) { setMessage(String(error)); } finally { setBusy(false); }
          }}>仅检测设备性能</button>
        </div>
        <label className="block text-sm">上传本地录音试听<input className="mt-2 block w-full text-sm" type="file" accept="audio/*" disabled={busy} onChange={event => {
          const file = event.target.files?.[0]; if (file) void start(file);
        }}/></label>
        <audio className="max-w-full" controls />
        <p role="status" className="break-words text-sm">{message}</p>
        <p className="text-xs text-muted-foreground">这是处理链路预览；没有连接 ASR 或云端服务。RNNoise 降噪不能保证排除远处人声。</p>
      </section>
      <aside className="min-w-0 rounded-xl border"><VoiceLatencyRail /></aside>
    </div>
  </main>;
}

createRoot(document.getElementById('root')!).render(<Preview />);

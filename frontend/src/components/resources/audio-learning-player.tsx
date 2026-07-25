import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Pause, Play, RotateCcw, Volume2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";

function clock(value: number) {
  if (!Number.isFinite(value)) return "0:00";
  const seconds = Math.max(0, Math.floor(value));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function AudioLearningPlayer({ blob, filename }: { blob: Blob; filename: string }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [source, setSource] = useState("");
  const [peaks, setPeaks] = useState<number[]>([]);
  const [decodeError, setDecodeError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [volume, setVolume] = useState(1);
  const [rate, setRate] = useState(1);

  useEffect(() => {
    const url = URL.createObjectURL(blob);
    setSource(url);
    return () => URL.revokeObjectURL(url);
  }, [blob]);

  useEffect(() => {
    let cancelled = false;
    setPeaks([]);
    setDecodeError("");
    void blob.arrayBuffer().then(async (data) => {
      const context = new AudioContext();
      try {
        const buffer = await context.decodeAudioData(data.slice(0));
        if (cancelled) return;
        const channel = buffer.getChannelData(0);
        const count = 180;
        const block = Math.max(1, Math.floor(channel.length / count));
        const next = Array.from({ length: count }, (_, index) => {
          let max = 0;
          const end = Math.min(channel.length, (index + 1) * block);
          for (let offset = index * block; offset < end; offset += 1) {
            max = Math.max(max, Math.abs(channel[offset] ?? 0));
          }
          return max;
        });
        const largest = Math.max(...next, 0.01);
        setPeaks(next.map((value) => value / largest));
      } catch {
        if (!cancelled) setDecodeError("当前浏览器无法解码波形，仍可使用原生时间轴播放。");
      } finally {
        void context.close();
      }
    });
    return () => { cancelled = true; };
  }, [blob]);

  const progress = duration ? currentTime / duration : 0;
  const activeBars = useMemo(() => Math.floor(progress * peaks.length), [peaks.length, progress]);

  function seek(ratio: number) {
    const audio = audioRef.current;
    if (!audio || !duration) return;
    audio.currentTime = Math.min(duration, Math.max(0, ratio * duration));
    setCurrentTime(audio.currentTime);
  }

  return (
    <section className="audio-learning-player" aria-label={`音频播放器 ${filename}`}>
      <audio
        onDurationChange={(event) => setDuration(event.currentTarget.duration || 0)}
        onEnded={() => setPlaying(false)}
        onPause={() => setPlaying(false)}
        onPlay={() => setPlaying(true)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        preload="metadata"
        ref={audioRef}
        src={source || undefined}
      />
      <div className="audio-learning-player__heading">
        <div>
          <p>音频资料</p>
          <strong>{filename}</strong>
        </div>
        <span>{clock(currentTime)} / {clock(duration)}</span>
      </div>
      <button
        aria-label="音频波形时间轴"
        className="audio-waveform"
        disabled={!peaks.length}
        onClick={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          seek((event.clientX - rect.left) / rect.width);
        }}
        type="button"
      >
        {peaks.length ? peaks.map((peak, index) => (
          <i
            className={cn(index <= activeBars && "is-played")}
            key={index}
            style={{ height: `${Math.max(8, peak * 82)}%` }}
          />
        )) : <LoaderCircle className="size-5 animate-spin" />}
      </button>
      {!peaks.length ? (
        <Slider
          aria-label="音频播放位置"
          max={Math.max(1, duration)}
          onValueChange={([value]) => seek(value / Math.max(1, duration))}
          step={0.1}
          value={[currentTime]}
        />
      ) : null}
      {decodeError ? <p className="audio-learning-player__notice">{decodeError}</p> : null}
      <div className="audio-learning-player__controls">
        <Button
          aria-label={playing ? "暂停" : "播放"}
          onClick={() => {
            const audio = audioRef.current;
            if (!audio) return;
            if (audio.paused) void audio.play(); else audio.pause();
          }}
          size="icon"
        >
          {playing ? <Pause /> : <Play />}
        </Button>
        <Button aria-label="回到开头" onClick={() => seek(0)} size="icon-sm" variant="ghost">
          <RotateCcw />
        </Button>
        <Volume2 className="size-4 text-muted-foreground" />
        <Slider
          aria-label="音量"
          className="max-w-28"
          max={1}
          onValueChange={([value]) => {
            setVolume(value);
            if (audioRef.current) audioRef.current.volume = value;
          }}
          step={0.05}
          value={[volume]}
        />
        <label>
          <span className="sr-only">播放速度</span>
          <select
            aria-label="播放速度"
            onChange={(event) => {
              const value = Number(event.currentTarget.value);
              setRate(value);
              if (audioRef.current) audioRef.current.playbackRate = value;
            }}
            value={rate}
          >
            {[0.75, 1, 1.25, 1.5, 2].map((value) => <option key={value} value={value}>{value}x</option>)}
          </select>
        </label>
      </div>
    </section>
  );
}

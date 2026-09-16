import { useEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { Mic, MicOff, LoaderCircle, PhoneOff } from "lucide-react";
import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { cn } from "@/lib/utils";
import {
  useVoiceSession,
  type VoiceAudioSource,
  type VoiceSessionState,
  type VoiceTransportState,
} from "./voice-session-controller";
import { voiceStatusText } from "./voice-status";

/**
 * The composer's voice controls -- and the only place a call's connection state
 * is presented (2026-09-14 revision).
 *
 * The voice-tutor button is the dial key: entering voice mode starts the call,
 * so the control has to answer "is this thing actually connected?" by itself.
 * Right-hand slot:
 *
 *   connecting / reconnecting -> grey + spinner
 *   connected                 -> coloured, and the connect cue plays right then
 *   error                     -> grey
 *
 * There is deliberately no floating caption card: live transcript and the
 * tutor's answer belong to the conversation itself, and the call's incidental
 * status (model pin, ICE path, degradation) is a single in-flow line above the
 * composer -- see `VoiceStatusLine`'s host in the chat page. A floating caption
 * layer used to sit on top of the messages it was describing.
 *
 * The orb is back (2026-09-15) as a *state indicator only*: one coloured sphere
 * that shows whether the call is hearing, thinking, or speaking, sitting inline
 * above that status line so it never covers the conversation. Restoring it did
 * not restore the auto-dial effect that used to live in `VoiceOrbDock` -- the
 * chat page owns dialing now, and a second dialer would make entering voice mode
 * open two calls.
 */

export interface VoiceOrbProps {
  transport: VoiceTransportState;
  state: VoiceSessionState;
  error?: string | null;
  muted?: boolean;
  audioLevel?: number;
  audioSource?: VoiceAudioSource;
}

/**
 * The orb's motion engine.
 *
 * Two things separate "a coloured ball" from "a ball breathing with the
 * conversation":
 *
 * 1. Loudness is low-passed before it drives anything. Instantaneous RMS is a
 *    per-frame number, so wiring it straight into a scale makes the sphere
 *    flicker on every syllable; a ~0.35s envelope followed by a fast-attack /
 *    slow-release smoother turns it into a loudness contour instead.
 * 2. While the tutor speaks the orb breathes on a human rhythm -- randomised
 *    syllables (~0.22-0.42s) separated by short pauses, with an occasional
 *    longer breath -- rather than following the real syllables.
 *
 * Everything it produces is published as CSS custom properties on the drag
 * handle, so the look stays in CSS and the loop never triggers a React render.
 */
interface OrbEngine {
  /** Low-passed raw loudness 0..1 (the envelope). */
  level: number;
  /** Smoothed output level that drives the visuals. */
  energy: number;
  /** Current syllable envelope 0..1 of the speaking rhythm. */
  syllable: number;
  syllableTimer: number;
  inSyllable: boolean;
  syllableLen: number;
  pauseLen: number;
  /** Click pulse, decaying to 0. */
  pop: number;
  /** Click ring, decaying to 0. */
  ring: number;
  /** Click-accumulated hue speed (deg/s), decaying back to the base rotation. */
  hueBoost: number;
  /** Breath phase in seconds. */
  breath: number;
  /** Timestamp of the previous frame. */
  last: number;
}

interface OrbSignal {
  audioLevel: number;
  listening: boolean;
  speaking: boolean;
}

const ORB_ENVELOPE_SECONDS = 0.35;
/** Rise is quicker than fall: the ball reacts to speech but settles gently. */
const ORB_ATTACK_SECONDS = 0.14;
const ORB_RELEASE_SECONDS = 0.42;

function createOrbEngine(): OrbEngine {
  return {
    level: 0,
    energy: 0,
    syllable: 0,
    syllableTimer: 0,
    inSyllable: false,
    syllableLen: 0.16,
    pauseLen: 0.12,
    pop: 0,
    ring: 0,
    hueBoost: 0,
    breath: 0,
    last: 0,
  };
}

function stepOrbEngine(engine: OrbEngine, dt: number, signal: OrbSignal): void {
  engine.breath += dt;

  // Speaking rhythm: a cosine window per syllable (0 -> 1 -> 0) reads as a slow
  // nod rather than a flicker; most pauses are short, some are a breath.
  if (signal.speaking) {
    engine.syllableTimer += dt;
    if (engine.inSyllable) {
      const phase = Math.min(1, engine.syllableTimer / engine.syllableLen);
      engine.syllable = 0.5 - 0.5 * Math.cos(2 * Math.PI * phase);
      if (phase >= 1) {
        engine.inSyllable = false;
        engine.syllableTimer = 0;
        engine.pauseLen =
          Math.random() < 0.2 ? 0.3 + Math.random() * 0.3 : 0.1 + Math.random() * 0.14;
      }
    } else {
      engine.syllable = 0;
      if (engine.syllableTimer >= engine.pauseLen) {
        engine.inSyllable = true;
        engine.syllableTimer = 0;
        engine.syllableLen = 0.22 + Math.random() * 0.2;
      }
    }
  } else {
    engine.syllable = 0;
    engine.syllableTimer = 0;
    engine.inSyllable = false;
  }

  // Loudness contour: envelope first (kills per-frame jitter), then a fast
  // attack / slow release so the sphere leans into speech and settles gently.
  const raw = Math.max(0, Math.min(1, signal.audioLevel));
  const envelope = 1 - Math.exp(-dt / ORB_ENVELOPE_SECONDS);
  engine.level += (raw - engine.level) * envelope;
  const botPulse = signal.speaking ? 0.32 + 0.22 * engine.syllable : 0;
  const userPulse = signal.listening ? 0.2 : 0;
  const target = Math.min(1, Math.max(engine.level, botPulse, userPulse));
  const tau = target > engine.energy ? ORB_ATTACK_SECONDS : ORB_RELEASE_SECONDS;
  engine.energy += (target - engine.energy) * (1 - Math.exp(-dt / tau));

  engine.pop *= Math.pow(0.002, dt);
  engine.ring = Math.max(0, engine.ring - dt * 1.6);
  engine.hueBoost *= Math.pow(0.1, dt);
}

function publishOrbEngine(element: HTMLElement, engine: OrbEngine): void {
  const amplitude = 0.04 + engine.energy * 0.14;
  const frequency = 0.9 + engine.energy;
  const scale =
    1 +
    amplitude * Math.sin(engine.breath * frequency) +
    engine.energy * 0.24 +
    engine.pop * 0.45;
  element.style.setProperty("--voice-level", engine.energy.toFixed(3));
  element.style.setProperty("--voice-scale", scale.toFixed(4));
  element.style.setProperty("--voice-ring", engine.ring.toFixed(3));
  element.style.setProperty("--voice-hue-shift", (engine.hueBoost * 0.25).toFixed(1));
}

function orbPrefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * The orb: a visual meter for both sides of the conversation.
 *
 * It is not a control -- mute and hang-up live on the composer buttons -- so the
 * only interaction it carries is a small drag, plus a tap that pulses the sphere
 * and spins its colour faster. State comes from `voice-session-controller`;
 * nothing here touches the transcript.
 */
export function VoiceOrb({
  transport,
  state,
  error,
  muted = false,
  audioLevel = 0,
  audioSource = "idle",
}: VoiceOrbProps) {
  const isListening = state === "listening";
  const isSpeaking = state === "speaking";
  const status = voiceStatusText(transport, state, error);
  const [drag, setDrag] = useState({ x: 0, y: 0 });
  const [pressed, setPressed] = useState(false);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const draggedRef = useRef(false);
  const visualRef = useRef<HTMLSpanElement | null>(null);
  const engineRef = useRef<OrbEngine | null>(null);
  // The loop reads the latest state through a ref so it never has to restart.
  const signalRef = useRef<OrbSignal>({ audioLevel, listening: isListening, speaking: isSpeaking });
  signalRef.current = { audioLevel, listening: isListening, speaking: isSpeaking };
  const dragDistance = Math.min(1, Math.hypot(drag.x, drag.y) / 42);
  const visualStyle = {
    ["--orb-drag" as string]: dragDistance,
    transform: `translate3d(${drag.x}px, ${drag.y}px, 0) rotate(${drag.x / 10}deg) scale(${pressed ? 0.94 : 1})`,
  } as CSSProperties;

  useEffect(() => {
    const element = visualRef.current;
    if (!element) return;
    if (orbPrefersReducedMotion()) {
      // No motion at all: the level still tints the sphere, but nothing moves.
      element.style.setProperty(
        "--voice-level",
        Math.max(0, Math.min(1, signalRef.current.audioLevel)).toFixed(3),
      );
      return;
    }
    const engine = (engineRef.current ??= createOrbEngine());
    let frame = window.requestAnimationFrame(function tick(now: number) {
      const dt = engine.last ? Math.min(0.05, (now - engine.last) / 1000) : 0;
      engine.last = now;
      stepOrbEngine(engine, dt, signalRef.current);
      publishOrbEngine(element, engine);
      frame = window.requestAnimationFrame(tick);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const releasePointer = (event: ReactPointerEvent<HTMLSpanElement>) => {
    const wasDragging = dragStart.current !== null;
    const wasTap = wasDragging && !draggedRef.current;
    if (wasDragging) event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragStart.current = null;
    draggedRef.current = false;
    setDrag({ x: 0, y: 0 });
    setPressed(false);
    if (!wasTap) return;
    // Tap: pulse the sphere, send a ring out, and spin the colour up. Repeated
    // taps stack, so the hue can be wound noticeably faster.
    const engine = engineRef.current;
    if (engine) {
      engine.pop = 1;
      engine.ring = 1;
      engine.hueBoost = Math.min(engine.hueBoost + 40, 260);
    }
    if (typeof navigator !== "undefined" && typeof navigator.vibrate === "function") {
      navigator.vibrate(18);
    }
  };

  return (
    <div
      className={cn(
        "chat-voice-orb-dock",
        muted && "is-muted",
        transport === "error" && "is-error",
        audioSource === "user" && "is-user-voice",
        audioSource === "assistant" && "is-assistant-voice",
      )}
    >
      <span
        aria-label={`语音球：${status}`}
        className="chat-voice-orb-dock__visual"
        ref={visualRef}
        role="img"
        style={visualStyle}
        onPointerDown={(event) => {
          if (!event.isPrimary) return;
          dragStart.current = { x: event.clientX - drag.x, y: event.clientY - drag.y };
          draggedRef.current = false;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          setPressed(true);
        }}
        onPointerMove={(event) => {
          if (!dragStart.current) return;
          const next = {
            x: Math.max(-32, Math.min(32, event.clientX - dragStart.current.x)),
            y: Math.max(-20, Math.min(20, event.clientY - dragStart.current.y)),
          };
          if (!draggedRef.current && Math.hypot(next.x, next.y) > 4) {
            draggedRef.current = true;
          }
          setDrag(next);
        }}
        onPointerUp={releasePointer}
        onPointerCancel={releasePointer}
      >
        <span
          className={cn(
            "chat-voice-orb",
            isListening && "is-listening",
            isSpeaking && "is-speaking",
          )}
        >
          <span className="chat-voice-orb__pulse" />
          <span className="chat-voice-orb__core" />
          <span className="chat-voice-orb__halo chat-voice-orb__halo--one" />
          <span className="chat-voice-orb__halo chat-voice-orb__halo--two" />
        </span>
      </span>
      <span aria-live="polite" className="sr-only-voice-status" role="status">
        {status}
      </span>
    </div>
  );
}

interface VoiceScopeProps {
  workspaceId: string;
  sessionId: string;
  modelId?: string | null;
  providerId?: string | null;
}

export interface VoiceComposerActionsProps extends VoiceScopeProps {
  /** Mirrors the mode toggle in the composer so the orb and mode stay in sync. */
  onExit?: () => void;
}

/**
 * The single left voice control. Before a call the chat page renders the ASR
 * microphone here; while connected this slot becomes the local mute toggle.
 */
export function VoiceComposerActions({
  workspaceId,
  sessionId,
  modelId,
  providerId,
}: VoiceComposerActionsProps) {
  const voice = useVoiceSession(workspaceId, sessionId, modelId, providerId);
  const canControl =
    voice.transport === "connected" || voice.transport === "connecting";

  return (
    <>
      <PromptInputButton
        aria-label={voice.muted ? "取消静音" : "静音"}
        aria-pressed={voice.muted}
        className={cn("chat-composer__mute", voice.muted && "is-active")}
        disabled={!canControl}
        onClick={() => voice.setMuted(!voice.muted)}
        tooltip={voice.muted ? "取消静音" : "静音"}
      >
        {voice.muted ? <MicOff className="size-4" /> : <Mic className="size-4" />}
      </PromptInputButton>
    </>
  );
}

/**
 * 全双工语音入口字形：五根圆头实心竖条（中间最高，向两侧对称递减）。
 *
 * 用代码绘制，不引位图。几何取自设计稿：条宽 : 条间距 = 1 : 1，
 * 三档高度比（中 : 次 : 外）= 4.45 : 2.90 : 1.45；
 * 五根条共宽 19.8，在 24×24 里左右各留 2.1 边距，并共用同一条水平中线（y = 12）。
 */
const VOICE_WAVE_BAR_WIDTH = 2.2;
const VOICE_WAVE_CENTER = 12;
const VOICE_WAVE_BARS = [
  { height: 6.4, x: 2.1 },
  { height: 12.8, x: 6.5 },
  { height: 19.8, x: 10.9 },
  { height: 12.8, x: 15.3 },
  { height: 6.4, x: 19.7 },
] as const;

function VoiceWaveGlyph({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={cn("size-4", className)}
      viewBox="0 0 24 24"
    >
      {VOICE_WAVE_BARS.map((bar) => (
        <rect
          fill="currentColor"
          height={bar.height}
          key={bar.x}
          rx={VOICE_WAVE_BAR_WIDTH / 2}
          width={VOICE_WAVE_BAR_WIDTH}
          x={bar.x}
          y={VOICE_WAVE_CENTER - bar.height / 2}
        />
      ))}
    </svg>
  );
}

export interface VoiceCallControlProps extends VoiceScopeProps {
  active: boolean;
  onStart: () => void;
  onExit: () => void;
}

/**
 * The single right-hand slot: dial while idle, hang up after WebRTC connects.
 *
 * It is also the connection indicator: nothing about the call may look "live"
 * before the transport is up, and `reconnecting` must not look identical to
 * idle (it used to, which is what made a stalled call indistinguishable from a
 * call that had not started).
 */
export function VoiceCallControl({
  workspaceId,
  sessionId,
  modelId,
  providerId,
  active,
  onStart,
  onExit,
}: VoiceCallControlProps) {
  const voice = useVoiceSession(workspaceId, sessionId, modelId, providerId);
  const connected = voice.transport === "connected";
  const connecting =
    voice.transport === "connecting" || voice.transport === "reconnecting";
  const label = active
    ? connected
      ? "挂断全双工语音"
      : connecting
        ? "取消语音连接"
        : "结束语音导师通话"
    : "开始全双工语音";
  return (
    <PromptInputButton
      aria-label={label}
      aria-pressed={active}
      className={cn(
        "chat-composer__voice-mode",
        active && "is-active",
        // Grey + spinner until the peer connection is actually up: "connected"
        // is a fact about the transport, not an intention.
        active && connecting && "is-connecting",
        active && connected && "is-live",
        active && voice.transport === "error" && "is-error",
      )}
      disabled={!active && (!workspaceId || !sessionId)}
      onClick={() => {
        if (!active) return onStart();
        voice.disconnect();
        onExit();
      }}
      tooltip={active && !connected && !connecting ? "结束语音导师通话" : label}
    >
      {active && connected ? (
        <PhoneOff className="size-4" />
      ) : active && connecting ? (
        <LoaderCircle className="size-4 animate-spin" />
      ) : (
        <VoiceWaveGlyph />
      )}
    </PromptInputButton>
  );
}

import { useEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { AudioWaveform, LoaderCircle, Mic, MicOff, PhoneOff } from "lucide-react";
import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { cn } from "@/lib/utils";
import {
  useVoiceSession,
  voiceSessionController,
  type VoiceSessionState,
  type VoiceTranscript,
  type VoiceTransportState,
  type VoiceAudioSource,
} from "./voice-session-controller";

/**
 * Full-duplex voice surface, inline rather than modal.
 *
 * The transport, session lifecycle and speech state all live in
 * `voice-session-controller`; these components only map that state onto the orb
 * and the two composer controls, and report the user's intent back. Nothing here
 * touches the conversation canvas, which is what lets the messages stay visible
 * while a call is running.
 *
 * 交互约定（2026-09-12 修订）：语音导师按钮即"拨号键"——进入语音模式（本组件
 * 挂载）就自动接线麦克风并开始通话，不再需要先点悬浮球连接；悬浮球退化为
 * 状态指示器（听到/思考/说话）；球体可轻微点击/拖动产生反馈，但不承担挂断或
 * 打断操作（打断由本机 VAD 自动处理，挂断由右侧通话按钮处理）。
 *
 * 位置约定（2026-09-12）：悬浮球由 chat 页面挂在工作台功能条（资料/目标/联网/…）
 * 的**上方**，而不是塞在功能条与输入框之间的窄缝里；组件本身只负责居中与自身
 * 高度，落点由挂载位置和 `.chat-voice-orb-dock` 的样式决定。
 */

function voiceStatusText(
  transport: VoiceTransportState,
  state: VoiceSessionState,
  error?: string | null,
): string {
  if (transport === "connecting") return "正在接通语音导师…";
  if (transport === "reconnecting") return "语音连接中断，正在重连…";
  if (transport === "error") return error || "语音连接失败";
  if (state === "speaking") return "导师正在回答，你随时可以插话";
  if (state === "thinking") return "导师正在思考，你随时可以插话";
  if (state === "listening") return "正在聆听，请直接说话";
  if (transport === "connected") return "语音导师已连接，请直接说话";
  return "语音导师未连接";
}

export interface VoiceOrbProps {
  transport: VoiceTransportState;
  state: VoiceSessionState;
  error?: string | null;
  muted?: boolean;
  audioLevel?: number;
  audioSource?: VoiceAudioSource;
}

/**
 * The orb is a visual meter for both sides of the conversation. It accepts a
 * small drag so it feels alive without taking over call controls; dragging is
 * purely visual and the composer buttons remain the source of truth for mute
 * and hang-up.
 */
export function VoiceOrb({ transport, state, error, muted, audioLevel = 0, audioSource = "idle" }: VoiceOrbProps) {
  const isListening = state === "listening";
  const isSpeaking = state === "speaking";
  const status = voiceStatusText(transport, state, error);
  const [drag, setDrag] = useState({ x: 0, y: 0 });
  const [pressed, setPressed] = useState(false);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const dragDistance = Math.min(1, Math.hypot(drag.x, drag.y) / 42);
  const visualStyle = {
    ["--voice-level" as string]: audioLevel,
    ["--orb-drag" as string]: dragDistance,
    transform: `translate3d(${drag.x}px, ${drag.y}px, 0) rotate(${drag.x / 10}deg) scale(${pressed ? 0.94 : 1})`,
  } as CSSProperties;
  const releasePointer = (event: ReactPointerEvent<HTMLSpanElement>) => {
    if (dragStart.current) event.currentTarget.releasePointerCapture?.(event.pointerId);
    dragStart.current = null;
    setDrag({ x: 0, y: 0 });
    setPressed(false);
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
        role="img"
        style={visualStyle}
        onPointerDown={(event) => {
          if (!event.isPrimary) return;
          dragStart.current = { x: event.clientX - drag.x, y: event.clientY - drag.y };
          event.currentTarget.setPointerCapture?.(event.pointerId);
          setPressed(true);
        }}
        onPointerMove={(event) => {
          if (!dragStart.current) return;
          setDrag({
            x: Math.max(-32, Math.min(32, event.clientX - dragStart.current.x)),
            y: Math.max(-20, Math.min(20, event.clientY - dragStart.current.y)),
          });
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

/**
 * Owns the lifetime of the voice surface. It is mounted only while voice mode is
 * on, so unmounting it (mode toggled off, session switched, navigating away) ends
 * the call: the mic must never stay open with no visible control. This is also
 * what makes a page reload end the session, since the whole tree is torn down.
 *
 * Mounting also *starts* the call (see the module docstring): the composer's
 * voice-tutor button is the only thing the user has to press.
 */
export function VoiceOrbDock({
  workspaceId,
  sessionId,
  modelId,
  providerId,
}: VoiceScopeProps) {
  const voice = useVoiceSession(workspaceId, sessionId, modelId, providerId);
  const { connect } = voice;
  // 每个挂载只自动拨号一次：失败不自动重试（避免"错误↔重连"死循环），
  // 退出再进入语音模式即可重试；挂断后本组件随模式关闭而卸载，不会重连。
  const autoDialed = useRef(false);

  useEffect(() => {
    if (autoDialed.current) return;
    if (!workspaceId || !sessionId) return;
    autoDialed.current = true;
    void connect();
  }, [connect, sessionId, workspaceId]);

  useEffect(() => {
    return () => {
      voiceSessionController.disconnect();
    };
  }, [workspaceId, sessionId]);

  return (
    <VoiceOrb
      error={voice.error}
      muted={voice.muted}
      audioLevel={voice.audioLevel}
      audioSource={voice.audioSource}
      state={voice.state}
      transport={voice.transport}
    />
  );
}

export interface VoiceCaptionStreamProps {
  transport: VoiceTransportState;
  state: VoiceSessionState;
  transcript: VoiceTranscript[];
  interimUserText: string;
  streamingAssistantText: string;
}

/**
 * The page-side view of what the call is actually hearing and answering.
 *
 * Everything here comes from the pipeline's RTVI channel (interim hypothesis,
 * finalized user turns, assistant text as it streams). Without it a voice call
 * is a black box: the mic goes up and audio comes back, but the user cannot see
 * the transcript that is driving the turn.
 */
export function VoiceCaptionStream({
  transport,
  state,
  transcript,
  interimUserText,
  streamingAssistantText,
}: VoiceCaptionStreamProps) {
  const recent = transcript.slice(-6);
  if (transport !== "connected" && recent.length === 0 && !interimUserText) return null;
  const status = voiceStatusText(transport, state);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none absolute bottom-24 left-1/2 z-30 w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-border/60 bg-background/85 p-3 text-xs shadow-lg backdrop-blur"
    >
      <p className="mb-1 text-[0.68rem] uppercase tracking-wide text-muted-foreground">{status}</p>
      <div className="flex max-h-40 flex-col gap-1 overflow-y-auto">
        {recent.map((item) => (
          <p
            className={cn(
              "leading-snug",
              item.role === "user" ? "text-muted-foreground" : "text-foreground",
            )}
            key={item.id}
          >
            <span className="mr-1 font-medium">{item.role === "user" ? "我：" : "导师："}</span>
            {item.text}
            {item.interrupted ? <span className="text-muted-foreground">（被打断）</span> : null}
          </p>
        ))}
        {interimUserText ? (
          <p className="italic leading-snug text-muted-foreground">我：{interimUserText}</p>
        ) : null}
        {streamingAssistantText ? (
          <p className="leading-snug text-foreground">导师：{streamingAssistantText}</p>
        ) : null}
      </div>
    </div>
  );
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

export interface VoiceCallControlProps extends VoiceScopeProps {
  active: boolean;
  onStart: () => void;
  onExit: () => void;
}

/** The single right-hand slot: dial while idle, hang up after WebRTC connects. */
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
  const connecting = voice.transport === "connecting";
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
      className={cn("chat-composer__voice-mode", active && "is-active")}
      disabled={!active && (!workspaceId || !sessionId)}
      onClick={() => {
        if (!active) return onStart();
        voice.disconnect();
        onExit();
      }}
      tooltip={label}
    >
      {active && connected ? <PhoneOff className="size-4" /> : active && connecting ? <LoaderCircle className="size-4 animate-spin" /> : <AudioWaveform className="size-4" />}
    </PromptInputButton>
  );
}

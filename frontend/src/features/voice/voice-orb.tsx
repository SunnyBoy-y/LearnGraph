import { Mic, MicOff, LoaderCircle, PhoneOff } from "lucide-react";
import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import { cn } from "@/lib/utils";
import { useVoiceSession } from "./voice-session-controller";

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
 * There is deliberately no floating orb and no floating caption card any more.
 * Live transcript and the tutor's answer belong to the conversation itself, and
 * the call's incidental status (model pin, ICE path, degradation) is a single
 * in-flow line above the composer -- see `VoiceStatusLine`'s host in the chat
 * page. A floating layer used to sit on top of the messages it was describing.
 */

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

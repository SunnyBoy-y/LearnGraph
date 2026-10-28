export interface VoiceConversationState {
  quiet: boolean;
  background_muted: boolean;
  wait_for_scene: boolean;
  mode: string;
}

export function VoiceConversationNotice({ policy }: { policy?: VoiceConversationState }) {
  const text = !policy ? null : policy.quiet ? "已停止播报，继续聆听"
    : policy.mode === "display" ? "仅展示页面，暂不播报"
    : policy.mode === "verbatim" ? "原样复述，不添加开场白"
    : policy.background_muted ? "后台结果静默保留" : null;
  return text ? <p role="status" className="mb-3 rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">{text}</p> : null;
}

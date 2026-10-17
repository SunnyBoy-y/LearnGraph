import type { CSSProperties } from "react";
import { ArrowUp, Square, X } from "lucide-react";

export function DictationBar({ level = 0, canceling = false, finalizing = false, holdToTalk = false, onCancel, onStop, onSend }: {
  level?: number;
  canceling?: boolean;
  finalizing?: boolean;
  holdToTalk?: boolean;
  onCancel: () => void;
  onStop: () => void;
  onSend: () => void;
}) {
  const label = canceling ? "松开取消" : finalizing ? "正在转写语音" : holdToTalk ? "正在聆听，松开发送，上滑取消" : "正在聆听";
  return <div className={`chat-voice-bar${canceling ? " is-canceling" : ""}${finalizing ? " is-finalizing" : ""}`}>
    <button aria-label="取消语音输入" className="chat-voice-bar__button" onClick={onCancel} type="button"><X size={18} /></button>
    <div aria-label={label} className="chat-voice-bar__dots" role="status">
      <div aria-hidden="true" className="chat-voice-bar__track">
        {Array.from({ length: 88 }, (_, index) => <span className="chat-voice-bar__dot" key={index} style={{
          "--dot-delay": `${index * -36}ms`,
          "--dot-scale": 1 + Math.min(1, level) * (1.5 + Math.sin(index * 1.7)),
        } as CSSProperties} />)}
      </div>
      {canceling ? <span className="chat-voice-bar__hint">松开取消</span> : null}
    </div>
    <button aria-label="停止语音输入" className="chat-voice-bar__button" disabled={finalizing} onClick={onStop} type="button"><Square size={14} fill="currentColor" strokeWidth={0} /></button>
    <button aria-label="发送语音转写" className="chat-voice-bar__button chat-voice-bar__send" disabled={finalizing || canceling} onClick={onSend} type="button"><ArrowUp size={20} /></button>
  </div>;
}

/**
 * 右侧栏「语音延迟」栏目（语音调试面板）。
 *
 * 只在设置里的「语音调试面板」开关打开时渲染（见 workspace-shell 的 ChatGraphRail），
 * 关掉时整块不挂载 —— 与同栏的「轨迹」标签页同一个模式。
 *
 * 展示口径照参考实现 `demo-voice2o2/frontend/index.html` 的时序图：
 * 顶部一个总耗时（用户说完 → 导师出声），下面是本轮各环节的「累计 ms · Δ 每段」，
 * 再往下是内容对照与最近若干轮的总耗时 chip。行尾标注每个环节的时间点取自哪条通道，
 * 少了哪条信号一眼看得出来（面板的所有时间点都是本机收到的时刻，不是服务端时刻）。
 */
import { useSyncExternalStore } from "react";
import { Gauge } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  VOICE_LATENCY_SOURCE_LABELS,
  VOICE_LATENCY_TOTAL_LABEL,
  shortTurnId,
  voiceLatencyFocusTurn,
  voiceLatencyHeldCount,
  voiceLatencyRecognizedToSpeechMs,
  voiceLatencyRows,
  voiceLatencySpeechMs,
  voiceLatencyTotalMs,
} from "./voice-latency";
import { resetVoiceLatency, useVoiceLatency } from "./voice-latency-store";
import { voiceSessionController } from "./voice-session-controller";

function voiceTransportLabel(transport: string, state: string): string {
  if (transport === "idle" || state === "closed") return "未连接";
  if (transport === "connecting") return "接通中";
  if (transport === "reconnecting") return "重连中";
  return state === "speaking" ? "导师在说话" : state === "thinking" ? "思考中" : "聆听中";
}

export function VoiceLatencyRail() {
  const latency = useVoiceLatency();
  const voice = useSyncExternalStore(
    voiceSessionController.subscribe,
    voiceSessionController.getSnapshot,
    voiceSessionController.getSnapshot,
  );
  const turn = voiceLatencyFocusTurn(latency);
  const rows = voiceLatencyRows(turn);
  const total = voiceLatencyTotalMs(turn);
  const recognizedToSpeech = voiceLatencyRecognizedToSpeechMs(turn);
  const held = voiceLatencyHeldCount(latency);
  const speechMs = voiceLatencySpeechMs(turn);
  const serverTurnId = shortTurnId(turn?.turnId ?? null);
  /** 这一轮还开着（服务端尚未收尾）——用来把"还没收尾"与"信号丢了"分开说。 */
  const ongoing = Boolean(turn) && latency.current?.id === turn?.id;
  const userDoneSource = turn?.sources.userDone
    ? VOICE_LATENCY_SOURCE_LABELS[turn.sources.userDone]
    : null;
  const botSpeakingSource = turn?.sources.botSpeaking
    ? VOICE_LATENCY_SOURCE_LABELS[turn.sources.botSpeaking]
    : null;

  return (
    <div
      aria-label="语音调试面板"
      className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-2 text-xs"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 font-medium">
          <Gauge className="size-3.5 shrink-0 text-primary" />
          <span className="truncate">语音延迟</span>
          <span className="truncate text-[11px] font-normal text-muted-foreground">
            {voiceTransportLabel(voice.transport, voice.state)}
          </span>
        </div>
        <Button
          disabled={!turn && latency.history.length === 0}
          onClick={() => resetVoiceLatency()}
          size="xs"
          type="button"
          variant="ghost"
        >
          清空
        </Button>
      </div>

      <p className="text-[11px] leading-4 text-muted-foreground">
        全双工各环节耗时，全部按本机收到的时刻计（刷新即清空）。每条记号按服务端回合身份
        归位：身份比信号后到（打字回合就是这样）也会补记，不会被扔掉或算错轮。
      </p>

      {latency.dropped > 0 || held > 0 ? (
        <p
          className="rounded-lg border border-dashed px-2 py-1 text-[11px] leading-4 text-muted-foreground"
          data-role="dropped"
        >
          {held > 0 ? (
            <>
              有 {held} 条信号正按回合身份寄存：它们带的身份还没在面板里出现过（打字回合的身份
              由服务端受理应答给出，其余靠轮询补投），那一轮一认领就把它们补记上去，
              不会被算错轮。
            </>
          ) : null}
          {latency.dropped > 0 ? (
            <>
              {held > 0 ? " " : ""}
              已丢弃 {latency.dropped} 条无主信号：它们既没有对应的活跃轮次，也没有可归位的回合
              身份（或身份一直没出现、寄存超期），没有被算进任何一轮。
            </>
          ) : null}
        </p>
      ) : null}

      {!turn ? (
        <div className="grid place-items-center gap-1 px-4 py-10 text-center text-[11px] leading-5 text-muted-foreground">
          <Gauge className="mb-1 size-5 opacity-60" />
          <p>发起一次全双工语音后，这里实时显示每一轮的链路耗时。</p>
        </div>
      ) : (
        <>
          <div className="rounded-xl border bg-muted/30 p-3">
            <p className="text-[11px] text-muted-foreground">
              {VOICE_LATENCY_TOTAL_LABEL}
            </p>
            <p
              aria-label="语音结束到语音开始耗时"
              className="mt-0.5 text-xl font-semibold tabular-nums"
            >
              {total === null ? "未测得" : `${total} ms`}
            </p>
            <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
              第 {turn.id} 轮 · {turn.kind === "text" ? "打字回合" : "语音回合"}
              {serverTurnId ? ` · 服务端 ${serverTurnId}` : ""}
              {ongoing ? " · 进行中" : ""}
              {speechMs !== null ? ` · 说话 ${speechMs} ms` : ""}
              {userDoneSource ? ` · 说完锚点：${userDoneSource}` : ""}
              {botSpeakingSource ? ` · 出声锚点：${botSpeakingSource}` : ""}
            </p>
            {total === null ? (
              <p className="mt-1 text-[11px] leading-4 text-muted-foreground">
                {turn.marks.userDone === undefined
                  ? "这一轮没收到「用户说完」信号，无法给出总量。"
                  : "还没听到导师出声（或这一轮先被打断），总量待定。"}
                {ongoing && turn.marks.userDone !== undefined
                  ? "（服务端尚未收尾，这一轮还在进行）"
                  : ""}
              </p>
            ) : null}
            {total === null && recognizedToSpeech !== null ? (
              <p
                className="mt-1 text-[11px] leading-4 text-muted-foreground"
                data-role="recognized-to-speech"
              >
                只算「识别落定 → 导师出声」：
                <span className="font-medium tabular-nums">{recognizedToSpeech} ms</span>
                （不含识别收尾那一段，所以比真实感知延迟小；缺「用户说完」锚点时的补充读数）
              </p>
            ) : null}
          </div>

          <ol className="grid gap-1">
            {rows.map((row) => {
              const pending = row.cumMs === null;
              return (
                <li
                  className="rounded-lg border bg-card px-2 py-1.5"
                  data-stage={row.stage}
                  key={row.stage}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate">
                      {row.icon} {row.label}
                    </span>
                    <span
                      className={`shrink-0 tabular-nums ${pending ? "text-muted-foreground" : ""}`}
                    >
                      {pending ? "等待…" : `${row.cumMs} ms`}
                    </span>
                  </div>
                  {!pending ? (
                    <div className="mt-0.5 flex items-baseline justify-between gap-2 text-[11px] text-muted-foreground">
                      <span className="truncate">
                        {row.hint} <span className="text-primary">+{row.deltaMs} ms</span>
                      </span>
                      {row.source ? (
                        <span className="shrink-0">{VOICE_LATENCY_SOURCE_LABELS[row.source]}</span>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>

          {turn.transcript || turn.reply ? (
            <div className="rounded-xl border bg-card p-2">
              <p className="text-[11px] font-medium text-muted-foreground">内容</p>
              {turn.transcript ? (
                <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-5">
                  🎙️ {turn.transcript}
                </p>
              ) : null}
              {turn.reply ? (
                <p className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-5">
                  🤖 {turn.reply}
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="rounded-xl border bg-card p-2">
            <p className="text-[11px] font-medium text-muted-foreground">
              最近 {latency.history.length} 轮 · 说完 → 出声
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {latency.history.length === 0 ? (
                <span className="text-[11px] text-muted-foreground">本轮进行中</span>
              ) : (
                latency.history.map((item) => {
                  const ms = voiceLatencyTotalMs(item);
                  const itemTurnId = shortTurnId(item.turnId);
                  return (
                    <span
                      className="rounded-md border bg-background px-1.5 py-0.5 text-[11px] tabular-nums text-muted-foreground"
                      key={item.id}
                      title={
                        itemTurnId
                          ? `本机第 ${item.id} 轮 · 服务端回合 ${itemTurnId}`
                          : `本机第 ${item.id} 轮 · 服务端回合未知（起音信号没带身份）`
                      }
                    >
                      {item.id}# {ms === null ? "未测得" : `${ms} ms`}
                    </span>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

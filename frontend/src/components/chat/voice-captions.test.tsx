import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { VoiceCaptions, readVoiceCaptionParts } from "./voice-captions";

import type { VoiceBotOutputPart } from "@/features/voice/voice-bot-output";

const parts: VoiceBotOutputPart[] = [
  { segmentId: 1, text: "第一句。", spokenChars: 4, status: "completed", willBeSpoken: true },
  { segmentId: 2, text: "第二句。", spokenChars: 0, status: "new", willBeSpoken: true },
];

describe("VoiceCaptions · 官方 Karaoke 渲染", () => {
  it("整段都在：已点亮的句子正常色、未朗读的句子在灰字容器里", () => {
    const { container } = render(<VoiceCaptions parts={parts} />);
    expect(container.textContent).toBe("第一句。第二句。");
    const muted = container.querySelectorAll(".chat-voice-captions__unread");
    expect(muted).toHaveLength(1);
    expect(muted[0].textContent).toBe("第二句。");
  });

  it("captions 模式不显示未读文本（但仍然保留完整文本节点）", () => {
    const { container } = render(<VoiceCaptions parts={parts} mode="captions" />);
    expect(container.textContent).toBe("第一句。");
    expect(container.querySelectorAll(".chat-voice-captions__unread")).toHaveLength(0);
  });

  it("instant 模式全文同色", () => {
    const { container } = render(<VoiceCaptions parts={parts} mode="instant" />);
    expect(container.textContent).toBe("第一句。第二句。");
    expect(container.querySelectorAll(".chat-voice-captions__unread")).toHaveLength(0);
  });

  it("没有段落时不渲染任何 DOM", () => {
    const { container } = render(<VoiceCaptions parts={[]} />);
    expect(container.textContent).toBe("");
  });

  it("readVoiceCaptionParts 读回画布写入的段落（缺字段时给安全默认值）", () => {
    const read = readVoiceCaptionParts({
      voice_captions: {
        parts: [
          { text: "半句", spokenChars: 2, status: "in-progress", segmentId: 7, willBeSpoken: true },
          { text: "坏数据" },
        ],
      },
    });
    expect(read[0]).toEqual({
      text: "半句",
      spokenChars: 2,
      status: "in-progress",
      segmentId: 7,
      willBeSpoken: true,
    });
    expect(read[1]).toEqual({
      text: "坏数据",
      spokenChars: 0,
      status: "new",
      segmentId: null,
      willBeSpoken: true,
    });
    expect(readVoiceCaptionParts(null)).toEqual([]);
  });
});

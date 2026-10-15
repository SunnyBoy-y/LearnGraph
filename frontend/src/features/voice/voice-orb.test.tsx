import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import typingSource from "../../components/chat/voice-captions.tsx?raw";

import { VoiceComposerActions, VoiceOrb } from "./voice-orb";
import { voiceSessionController } from "./voice-session-controller";
import type { VoiceSessionState } from "./voice-session-controller";

/**
 * `index.css?raw` 在本仓库的 vitest 管线里返回**空串**（Vite 的 CSS 处理优先于 `?raw`，
 * 实测 length === 0，而同一写法对 `.tsx` 正常）。样式表是契约 §4.3 的落地载体，必须真的读到，
 * 所以直接从磁盘读文件，不走 `?raw`。
 */
const cssSource = readFileSync(resolve(import.meta.dirname, "../../index.css"), "utf8");

/** 去掉注释后的源码：注释里提到某个字符串不构成依赖。 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/[^\n]*/g, "");
}

/**
 * 契约 §4.2（F10）与 §4.3：停止播报按钮只在导师正在出声时出现，调用 `interrupt()`，
 * 并且**绝不**触碰麦克风 mute —— 打断的是"这一次播报"，下一轮必须还能听到声音。
 */
describe("voice orb stop-speaking control", () => {
  it("只在导师正在说话时出现", () => {
    const quiet: VoiceSessionState[] = [
      "closed",
      "ready",
      "listening",
      "thinking",
      "error",
    ];
    for (const state of quiet) {
      const { unmount } = render(<VoiceOrb state={state} transport="connected" />);
      expect(screen.queryByRole("button", { name: "停止播报" })).toBeNull();
      unmount();
    }
    render(<VoiceOrb state="speaking" transport="connected" />);
    expect(screen.getByRole("button", { name: "停止播报" })).not.toBeNull();
  });

  it("带上契约 §4.2 与 §4.3 两个冻结类名", () => {
    render(<VoiceOrb state="speaking" transport="connected" />);
    const button = screen.getByRole("button", { name: "停止播报" });
    expect(button).toHaveClass("chat-voice-stop");
    expect(button).toHaveClass("chat-composer__stop-speaking");
  });

  it("点击后调用 interrupt() 打断当前播报", async () => {
    const user = userEvent.setup();
    const interrupt = vi.spyOn(voiceSessionController, "interrupt").mockResolvedValue(true);
    render(<VoiceOrb state="speaking" transport="connected" />);
    await user.click(screen.getByRole("button", { name: "停止播报" }));
    expect(interrupt).toHaveBeenCalledTimes(1);
  });

  it("不改变麦克风静音状态", async () => {
    const user = userEvent.setup();
    const setMuted = vi.spyOn(voiceSessionController, "setMuted");
    const interrupt = vi.spyOn(voiceSessionController, "interrupt").mockResolvedValue(true);
    const before = voiceSessionController.getSnapshot().muted;
    render(<VoiceOrb muted={before} state="speaking" transport="connected" />);
    await user.click(screen.getByRole("button", { name: "停止播报" }));
    // 停止播报若顺手动静音，用户会以为是自己关掉了导师的声音，下一轮也听不到。
    expect(interrupt).toHaveBeenCalledTimes(1);
    expect(setMuted).not.toHaveBeenCalled();
    expect(voiceSessionController.getSnapshot().muted).toBe(before);
  });

  it("安静时不渲染停止播报，也就没有打断入口", () => {
    render(<VoiceOrb state="listening" transport="connected" />);
    expect(screen.queryByRole("button", { name: "停止播报" })).toBeNull();
  });

  it("输入区的静音控件仍在（停止播报没有把它顶掉）", () => {
    // VoiceComposerActions 是麦克风静音的家：新控件属于球体旁边的语音区，
    // 不能顺手把静音键挪走或改名。真实页面里 TooltipProvider 由 App 提供，夹具要自己补上，
    // 否则 PromptInputButton 的 tooltip 会直接抛 "must be used within TooltipProvider"。
    render(
      <TooltipProvider>
        <VoiceComposerActions sessionId="s-1" workspaceId="w-1" />
      </TooltipProvider>,
    );
    expect(screen.getByRole("button", { name: "静音" })).not.toBeNull();
    expect(screen.getByRole("button", { name: "静音" })).toHaveClass("chat-composer__mute");
  });
});

/**
 * 契约 §4.3：类名是跨文件约定（页面写类名、样式表给外观），改名等于把控件变成裸 HTML，
 * 所以这里逐条核对它们确实作为**选择器**存在，而不是只在注释里被提到。
 */
const REQUIRED_SELECTORS = [
  ".chat-voice-stop",
  ".chat-composer__stop-speaking",
  ".chat-voice-resume-audio",
  ".chat-voice-notice",
  ".chat-voice-notice.is-playback",
  ".chat-voice-notice.is-tts",
  ".chat-voice-notice.is-asr",
  ".chat-voice-unheard",
  ".chat-voice-status-line.is-connecting",
  ".chat-voice-sentence.is-speaking",
  ".chat-voice-detail",
  ".chat-voice-typing",
  ".chat-voice-typing__caret",
];

function escapeForRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** `@media (prefers-reduced-motion: reduce)` 每块的内部文本（含嵌套规则）。 */
function reducedMotionBlocks(css: string): string[] {
  const blocks: string[] = [];
  const marker = "@media (prefers-reduced-motion: reduce)";
  let index = css.indexOf(marker);
  while (index !== -1) {
    const open = css.indexOf("{", index);
    let depth = 0;
    let cursor = open;
    for (; cursor < css.length; cursor += 1) {
      if (css[cursor] === "{") depth += 1;
      else if (css[cursor] === "}") {
        depth -= 1;
        if (depth === 0) break;
      }
    }
    blocks.push(css.slice(open + 1, cursor));
    index = css.indexOf(marker, cursor);
  }
  return blocks;
}

/** 块内所有最内层规则的声明属性名。 */
function declaredProperties(block: string): string[] {
  const properties: string[] = [];
  const source = block.replace(/\/\*[\s\S]*?\*\//gu, "");
  for (const match of source.matchAll(/\{([^{}]*)\}/g)) {
    for (const declaration of match[1].split(";")) {
      const trimmed = declaration.trim();
      const colon = trimmed.indexOf(":");
      if (colon === -1) continue;
      properties.push(trimmed.slice(0, colon).trim());
    }
  }
  return properties;
}

/**
 * 减少动效只允许"关掉动画"。用白名单而不是黑名单：`display: none`、`visibility:
 * hidden`、`font-size: 0`、`content` 之类一旦被写进这一块，界面就会在减少动效时少显示
 * 文字 —— 那改变的是内容，不是动效，而白名单会让新增的这类属性直接测试失败。
 */
const MOTION_ONLY_PROPERTIES = [
  "animation",
  "animation-name",
  "animation-duration",
  "animation-delay",
  "animation-direction",
  "animation-fill-mode",
  "animation-iteration-count",
  "animation-play-state",
  "animation-timing-function",
  "transition",
  "transition-property",
  "transition-duration",
  "transition-delay",
  "transition-timing-function",
  "scroll-behavior",
  "opacity",
  "transform",
  "transform-origin",
  "filter",
  "box-shadow",
  "will-change",
];

describe("voice control styles", () => {
  it("CSS 注释不会被当作声明，也不会隐藏真正的内容属性", () => {
    expect(declaredProperties(".caption { /* comment: braces {}; */ transition-property: none; display: none; }")).toEqual([
      "transition-property", "display",
    ]);
  });
  it("契约 §4.3 的类名都作为选择器存在", () => {
    for (const selector of REQUIRED_SELECTORS) {
      const pattern = new RegExp(`${escapeForRegExp(selector)}\\s*[,{]`);
      expect(pattern.test(cssSource), `${selector} 未在 index.css 里作为选择器出现`).toBe(
        true,
      );
    }
  });

  it("每个 reduced-motion 块只关动画，不改变可见文本边界", () => {
    const blocks = reducedMotionBlocks(cssSource);
    expect(blocks.length).toBeGreaterThan(0);
    for (const block of blocks) {
      for (const property of declaredProperties(block)) {
        expect(
          MOTION_ONLY_PROPERTIES.includes(property),
          `reduced-motion 里出现了会改变可见内容的属性：${property}`,
        ).toBe(true);
      }
    }
  });

  it("走字的字幕不读 prefers-reduced-motion：可见字符只由时钟决定", () => {
    // 减少动效时把没播到的文字补齐，等于让用户读到从未出声的内容；那是内容错误，
    // 不是动画偏好。字幕组件的可见边界因此完全不受该媒体查询影响（§3.7）。
    // 只看代码：注释里说明这条规则本身不算依赖。
    const code = stripComments(typingSource);
    expect(code.includes("prefers-reduced-motion")).toBe(false);
    expect(code.includes("matchMedia")).toBe(false);
  });
});

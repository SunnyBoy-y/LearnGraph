/** 工作区设置读取：语音调试面板开关。 */

import { describe, expect, it } from "vitest";

import {
  isVoiceDebugPanelEnabled,
  VOICE_DEBUG_PANEL_SETTING_KEY,
  readChatThinkingChainDefault,
  CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY,
} from "./workspace-settings";

import type { WorkspaceSetting } from "@/types/settings";

const settings = (entries: [string, unknown][]): WorkspaceSetting[] =>
  entries.map(([key, value]) => ({ key, value }) as WorkspaceSetting);

describe("thinking chain preference", () => {
  it("defaults missing or invalid preferences to collapsed while preserving explicit open", () => {
    expect(readChatThinkingChainDefault(undefined)).toBe(false);
    expect(readChatThinkingChainDefault([])).toBe(false);
    for (const value of [null, {}, {default_state: "collapsed"}, {default_state: "unknown"}, {default_state: true}]) {
      expect(readChatThinkingChainDefault(settings([[CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY, value]]))).toBe(false);
    }
    expect(readChatThinkingChainDefault(settings([[CHAT_THINKING_CHAIN_DEFAULT_SETTING_KEY, {default_state: "open"}]]))).toBe(true);
  });
});

describe("workspace-settings · 语音调试面板开关", () => {
  it("设置项 key 固定为 voice.debug_panel", () => {
    expect(VOICE_DEBUG_PANEL_SETTING_KEY).toBe("voice.debug_panel");
  });

  it("未配置 / 未加载 / 非 true 都视为关闭", () => {
    expect(isVoiceDebugPanelEnabled(undefined)).toBe(false);
    expect(isVoiceDebugPanelEnabled([])).toBe(false);
    expect(isVoiceDebugPanelEnabled(settings([["voice.debug_panel", false]]))).toBe(false);
    expect(isVoiceDebugPanelEnabled(settings([["voice.debug_panel", "true"]]))).toBe(false);
  });

  it("存了 true 才打开", () => {
    expect(isVoiceDebugPanelEnabled(settings([["voice.debug_panel", true]]))).toBe(true);
  });
});

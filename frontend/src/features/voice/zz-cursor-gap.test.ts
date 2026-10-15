/**
 * 轮询游标的推进规则（数据通道 → 轮询补投）。
 *
 * 这条规则护的是"控制面写的事件能不能回到客户端"：数据通道只带 worker 写的事件，
 * 打字回合的 `user.final` / `turn.accepted`（`accept_turn` 只写库）、HTTP 触发的
 * `turn.interrupted`、`session.closed` 都不在上面。按序号直接推进游标就会把它们
 * 永久跳过 —— 真机访问日志里的 `…27 → 32`（跳过的 29/30 正是那条打字回合的身份）
 * 就是这么来的。
 */

import { describe, expect, it } from "vitest";

import { advanceDurableCursor } from "./voice-session-controller";

describe("数据通道事件推进轮询游标", () => {
  it("连续的事件照常推进", () => {
    expect(advanceDurableCursor(27, 28)).toEqual({ cursor: 28, backfill: false });
  });

  it("有缺口时不推进，并要求补投一轮轮询", () => {
    // 真机现场：游标 27，下一条数据通道事件是 32（28~31 里有控制面写的事件）。
    expect(advanceDurableCursor(27, 32)).toEqual({ cursor: 27, backfill: true });
    // 缺口补上之后（轮询把游标推到 31），32 又是连续的。
    expect(advanceDurableCursor(31, 32)).toEqual({ cursor: 32, backfill: false });
  });

  it("本次连接还没有游标时允许直接采纳（中途加入的客户端等不到开头）", () => {
    expect(advanceDurableCursor(0, 57)).toEqual({ cursor: 57, backfill: false });
  });

  it("回放/重投的旧事件既不推进也不要求补投", () => {
    expect(advanceDurableCursor(40, 40)).toEqual({ cursor: 40, backfill: false });
    expect(advanceDurableCursor(40, 12)).toEqual({ cursor: 40, backfill: false });
  });
});

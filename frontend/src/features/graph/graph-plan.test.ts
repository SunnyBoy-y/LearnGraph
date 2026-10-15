// 学习计划（Graph 的时间行动层）纯函数补测。
// 覆盖：day_index ⇄ 日期适配、用户文案（今天/明天/周二）、任务类型 → 执行面路由、
// 容量超载、计划路径序列、stale 变更检测、加入计划的推荐槽位。
import { describe, expect, it } from "vitest";

import {
  buildPlanModel,
  buildPlanPathEdges,
  planActionRouting,
  planChanges,
  planDateForDayIndex,
  planDayChoices,
  planDayHeadline,
  planDayIndexForDate,
  planDayIndexForOffset,
  planDayLabel,
  planDayOffset,
  planDurationLabel,
  planItemTitle,
  planItemViews,
  planNodeMarkers,
  planSequence,
  planTimeInputs,
  suggestPlanSlot,
  type PlanTimeInputs,
} from "./graph-plan-model";
import type { ActionItem, Roadmap } from "@/types/workflow";
import type { Goal } from "@/types/goals";
import type { Graph, GraphNode } from "@/types/graphs";

const TODAY = new Date(2026, 8, 13); // 2026-09-13 (周日)

function goalNode(overrides: Partial<GraphNode> & { id: string; label: string }): GraphNode {
  return {
    graph_id: "graph-1",
    workspace_id: "ws-1",
    description: "",
    node_type: "concept",
    external_concept_id: null,
    target_weight: 50,
    mastery_stars: 0,
    retrieval_state: "unverified",
    evidence_state: "none",
    attention_state: "normal",
    ...overrides,
  } as GraphNode;
}

function action(overrides: Partial<ActionItem> & { id: string }): ActionItem {
  return {
    title: "学习：内存管理",
    description: "",
    status: "pending",
    source: "roadmap",
    action_type: "learn",
    project_id: null,
    goal_id: "goal-1",
    graph_id: "graph-1",
    node_id: null,
    roadmap_id: "roadmap-1",
    day_index: 1,
    duration_minutes: 30,
    due_at: null,
    priority: 50,
    position: 0,
    completed_at: null,
    metadata_json: {},
    ...overrides,
  } as ActionItem;
}

function roadmap(overrides: Partial<Roadmap> = {}): Roadmap {
  return {
    id: "roadmap-1",
    goal_id: "goal-1",
    graph_id: "graph-1",
    graph_revision: 3,
    title: "操作系统路线",
    version: 2,
    status: "published",
    rationale: "",
    planning_snapshot: {
      plan_started_at: new Date(2026, 8, 13).toISOString(),
      graph_revision: 3,
      goal: {
        availability: { minutes_per_day: 60, days_per_week: 5 },
        preferences: { session_minutes: 30 },
      },
    },
    published_at: null,
    items: [],
    created_at: new Date(2026, 8, 13).toISOString(),
    updated_at: new Date(2026, 8, 13).toISOString(),
    ...overrides,
  } as Roadmap;
}

function inputs(overrides: Partial<PlanTimeInputs> = {}): PlanTimeInputs {
  return {
    minutesPerDay: 60,
    daysPerWeek: 5,
    sessionMinutes: 30,
    deadlineAt: null,
    fromSnapshot: true,
    ...overrides,
  };
}

describe("learning-day ⇄ calendar date adapter", () => {
  it("把 day_index 分摊到每周可用天数（与后端 _scheduled_due_at 一致）", () => {
    expect([1, 2, 3, 4, 5, 6].map((day) => planDayOffset(day, 5))).toEqual([
      0, 2, 3, 5, 6, 7,
    ]);
    expect([1, 2, 3].map((day) => planDayOffset(day, 7))).toEqual([0, 1, 2]);
  });

  it("offset → day_index 往返一致", () => {
    for (const daysPerWeek of [1, 2, 3, 5, 7]) {
      for (let dayIndex = 1; dayIndex <= 20; dayIndex += 1) {
        const offset = planDayOffset(dayIndex, daysPerWeek);
        expect(planDayIndexForOffset(offset, daysPerWeek)).toBe(dayIndex);
      }
    }
  });

  it("日期 → day_index 与 day_index → 日期互为反函数", () => {
    const start = new Date(2026, 8, 13);
    const date = planDateForDayIndex(start, 4, 5); // day 4 → +5 天
    expect(date.getDate()).toBe(18);
    expect(planDayIndexForDate(date, start, 5)).toBe(4);
  });
});

describe("user-facing day wording", () => {
  it("今天 / 明天 / 后天 / 周几 / 具体日期，绝不出现 D1", () => {
    expect(planDayHeadline(new Date(2026, 8, 13), TODAY)).toBe("今天");
    expect(planDayHeadline(new Date(2026, 8, 14), TODAY)).toBe("明天");
    expect(planDayHeadline(new Date(2026, 8, 15), TODAY)).toBe("后天");
    expect(planDayHeadline(new Date(2026, 8, 16), TODAY)).toBe("周三");
    expect(planDayHeadline(new Date(2026, 8, 25), TODAY)).toBe("9月25日");
    expect(planDayLabel(new Date(2026, 8, 13), TODAY)).toBe("今天 · 9月13日");
    expect(planDayLabel(new Date(2026, 8, 13), TODAY)).not.toContain("D1");
  });

  it("时长文案在人话与小时之间切换", () => {
    expect(planDurationLabel(45)).toBe("45 分钟");
    expect(planDurationLabel(360)).toBe("6 小时");
    expect(planDurationLabel(90)).toBe("1.5 小时");
  });
});

describe("action type → execution surface", () => {
  it("learn 走节点学习，review / practice / assessment 走练习中心", () => {
    expect(planActionRouting("learn")).toEqual({
      route: "learn",
      label: "开始学习",
      kind: "learn",
    });
    expect(planActionRouting("review").route).toBe("practice");
    expect(planActionRouting("review").label).toBe("开始复习");
    expect(planActionRouting("practice").label).toBe("开始练习");
    expect(planActionRouting("assessment").label).toBe("开始测验");
  });

  it("未知类型退化为学习节点，而不是造一条新路由", () => {
    expect(planActionRouting("todo").route).toBe("learn");
  });

  it("计划任务去掉 planner 前缀只显示节点名", () => {
    expect(planItemTitle({ title: "学习：内存管理" })).toBe("内存管理");
    expect(planItemTitle({ title: "待解锁：文件系统" })).toBe("文件系统");
  });
});

describe("plan item projection", () => {
  it("按 day_index / position 排序，未解锁项排在可执行项之后", () => {
    const data = roadmap({
      items: [
        action({ id: "c", node_id: "n3", day_index: 2, position: 0 }),
        action({ id: "b", node_id: "n2", day_index: 1, position: 1 }),
        action({ id: "a", node_id: "n1", day_index: 1, position: 0 }),
        action({ id: "d", status: "blocked", day_index: 0, position: 0 }),
      ],
    });
    const items = planItemViews(data, inputs(), TODAY);
    expect(items.map((item) => item.id)).toEqual(["a", "b", "c", "d"]);
    // day_index 0 的阻塞项落到第 1 天，而不是显示 "D0"。
    expect(items[3].dayIndex).toBe(1);
  });

  it("把 due_at、前置阻塞与容量告警投影成可读字段", () => {
    const data = roadmap({
      items: [
        action({
          id: "x",
          title: "待解锁：文件系统",
          status: "blocked",
          action_type: "learn",
          node_id: "n9",
          due_at: new Date(2026, 8, 14).toISOString(),
          metadata_json: {
            ranking_reason: "blocked_by_prerequisite",
            prerequisites: { blocked_by: [{ label: "进程状态", node_id: "n8" }] },
          },
        }),
        action({
          id: "y",
          node_id: "n1",
          metadata_json: {
            ranking_reason: "user_rescheduled",
            schedule: { exceeds_daily_capacity: true, day_total_minutes: 90 },
          },
        }),
      ],
    });
    const items = planItemViews(data, inputs(), TODAY);
    const blocked = items.find((item) => item.id === "x");
    const moved = items.find((item) => item.id === "y");
    expect(blocked?.title).toBe("文件系统");
    expect(blocked?.headline).toBe("明天");
    expect(blocked?.reason).toBe("前置未完成：进程状态");
    expect(blocked?.blockedBy).toEqual(["进程状态"]);
    expect(moved?.reason).toBe("已手动调整过时间");
    expect(moved?.warnings[0]).toContain("超出每日 60 分钟容量");
  });

  it("复习任务说明为什么到期", () => {
    const data = roadmap({
      items: [action({ id: "r", action_type: "review", node_id: "n1" })],
    });
    const item = planItemViews(data, inputs(), TODAY)[0];
    expect(item.routing.route).toBe("practice");
    expect(item.reason).toBe("复习已到期，需要重新检索");
  });
});

describe("plan model aggregation", () => {
  const data = roadmap({
    items: [
      action({ id: "a", node_id: "n1", day_index: 1, duration_minutes: 45 }),
      action({ id: "b", node_id: "n2", day_index: 2, duration_minutes: 30 }),
      action({ id: "c", node_id: "n3", day_index: 2, duration_minutes: 45 }),
      action({ id: "d", node_id: "n4", status: "completed", day_index: 1 }),
    ],
  });

  it("按天聚合容量并标出超载日", () => {
    const model = buildPlanModel({ roadmap: data, today: TODAY });
    expect(model.days.map((day) => day.dayIndex)).toEqual([1, 2]);
    const day2 = model.days[1];
    expect(day2.minutes).toBe(75);
    expect(day2.overMinutes).toBe(15);
    // 已完成的任务同样计入当日负荷，所以超载提示对两天都成立。
    expect(model.overCapacityDays.map((day) => day.overMinutes)).toEqual([15, 15]);
    expect(model.overCapacityDays[1].headline).toBe("后天");
    expect(model.overCapacityDays[1].overMinutes).toBe(15);
    expect(model.overCapacityDays[1].item?.id).toBe("c");
  });

  it("区分任务完成度与节点掌握（不因任务完成而宣布掌握）", () => {
    const model = buildPlanModel({ roadmap: data, today: TODAY });
    expect(model.totals.completedCount).toBe(1);
    expect(model.totals.openCount).toBe(3);
    expect(model.finished).toBe(false);
  });

  it("全部完成或阻塞时视为本轮计划结束", () => {
    const finished = buildPlanModel({
      roadmap: roadmap({
        items: [
          action({ id: "a", status: "completed" }),
          action({ id: "b", status: "blocked" }),
        ],
      }),
      today: TODAY,
    });
    expect(finished.finished).toBe(true);
  });

  it("没有计划时给出空模型而不是崩溃", () => {
    const model = buildPlanModel({ today: TODAY });
    expect(model.items).toEqual([]);
    expect(model.days).toEqual([]);
    expect(model.finished).toBe(false);
    expect(model.totals.itemCount).toBe(0);
  });
});

describe("canvas markers and plan path", () => {
  const data = roadmap({
    items: [
      action({ id: "today", node_id: "n1", day_index: 1 }),
      action({ id: "soon", node_id: "n2", day_index: 2 }),
      action({ id: "later", node_id: "n3", day_index: 6 }),
      action({ id: "done", node_id: "n4", status: "completed", day_index: 1 }),
      action({ id: "no-node", day_index: 1 }),
    ],
  });

  it("近期待办标文案，远期只留轻标记，已完成节点不打扰画布", () => {
    const markers = planNodeMarkers(planItemViews(data, inputs(), TODAY), TODAY);
    expect(markers.get("n1")?.label).toBe("今天");
    expect(markers.get("n1")?.tone).toBe("today");
    expect(markers.get("n2")?.tone).toBe("soon");
    expect(markers.get("n3")?.tone).toBe("later");
    expect(markers.has("n4")).toBe(false);
  });

  it("计划路径只连可执行且未完成的节点，并受限长度", () => {
    const items = planItemViews(data, inputs(), TODAY);
    const sequence = planSequence(items, 2);
    expect(sequence.map((item) => item.nodeId)).toEqual(["n1", "n2"]);
    expect(sequence.every((item) => !item.done && !item.blocked)).toBe(true);
  });

  it("路径叠加按执行顺序连成虚线序列，不成环也不假造顺序", () => {
    const items = planItemViews(data, inputs(), TODAY);
    const edges = buildPlanPathEdges(planSequence(items, 4));
    expect(edges.map((edge) => [edge.source, edge.target])).toEqual([
      ["n1", "n2"],
      ["n2", "n3"],
    ]);
    expect(edges.every((edge) => edge.type === "planSequence")).toBe(true);
    expect(edges.every((edge) => edge.markerEnd !== undefined)).toBe(true);
    // 单一节点没有顺序可表达，不能凭空画一条自环。
    expect(buildPlanPathEdges(planSequence(items, 1))).toEqual([]);
  });
});

describe("stale detection", () => {
  const nodes = [
    goalNode({ id: "n1", label: "内存管理", mastery_stars: 5, attention_state: "mastered" }),
    goalNode({ id: "n2", label: "并发与同步", retrieval_state: "due" }),
    goalNode({ id: "n3", label: "文件系统" }),
  ] as GraphNode[];
  const graph = {
    id: "graph-1",
    goal_id: "goal-1",
    workspace_id: "ws-1",
    title: "操作系统图谱",
    status: "published",
    revision: 3,
    published_at: null,
    nodes,
    edges: [],
  } as GraphNode extends never ? never : Graph;

  it("没有真实变化时不提示更新", () => {
    const data = roadmap({
      items: [action({ id: "a", node_id: "n3", action_type: "learn" })],
    });
    const model = buildPlanModel({ roadmap: data, graph, today: TODAY });
    expect(model.changes).toEqual([]);
  });

  it("只报告重规划会真正处理的漂移：图谱修订、节点消失、已完成任务", () => {
    const data = roadmap({
      items: [
        action({ id: "a", node_id: "n1", action_type: "learn" }),
        action({ id: "c", node_id: "n3", status: "completed" }),
        action({ id: "d", node_id: "missing", action_type: "learn" }),
      ],
    });
    const changes = planChanges(
      data,
      { ...graph, revision: 5 },
      planItemViews(data, inputs(), TODAY),
      null,
      inputs(),
    );
    const labels = changes.map((change) => change.label);
    expect(labels).toContain("图谱已更新（修订 v3 → v5）");
    expect(labels.some((label) => label.includes("不在图谱中"))).toBe(true);
    expect(labels.some((label) => label.includes("已完成"))).toBe(true);
    // 节点掌握状态不是计划层面的漂移：重新规划也会照样安排它们，
    // 这类信息只挂在对应任务行上（见 planItemReason），避免"更新计划"点了也没用。
    expect(labels.some((label) => label.includes("已掌握"))).toBe(false);
    expect(labels.some((label) => label.includes("待复习"))).toBe(false);
  });

  it("节点状态与计划不一致时，在任务行上说明原因", () => {
    const nodesById = new Map([
      ["n1", goalNode({ id: "n1", label: "数据库", mastery_stars: 5, attention_state: "mastered" })],
      ["n2", goalNode({ id: "n2", label: "事务", retrieval_state: "due" })],
    ]);
    const data = roadmap({
      items: [
        action({ id: "a", node_id: "n1", action_type: "learn", title: "学习：数据库" }),
        action({ id: "b", node_id: "n2", action_type: "learn", title: "学习：事务" }),
      ],
    });
    const items = planItemViews(data, inputs(), TODAY, nodesById);
    expect(items[0].reason).toBe("节点已掌握，计划仍在安排学习");
    expect(items[1].reason).toBe("节点已进入待复习，建议先复习");
  });

  it("检测计划生成后的时间约束变化", () => {
    const data = roadmap({
      items: [action({ id: "a", node_id: "n3" })],
    });
    const liveGoal = {
      id: "goal-1",
      availability: { minutes_per_day: 120, days_per_week: 5 },
      preferences: { session_minutes: 30 },
      deadline_at: null,
    } as unknown as Goal;
    const changes = planChanges(
      data,
      null,
      planItemViews(data, inputs(), TODAY),
      liveGoal,
      inputs(),
    );
    expect(changes.some((change) => change.label.includes("时间约束已调整"))).toBe(true);
  });
});

describe("planning inputs", () => {
  it("优先使用计划快照里的约束，快照缺失时退回目标行", () => {
    const fromSnapshot = planTimeInputs(roadmap(), null);
    expect(fromSnapshot.minutesPerDay).toBe(60);
    expect(fromSnapshot.daysPerWeek).toBe(5);
    expect(fromSnapshot.sessionMinutes).toBe(30);
    expect(fromSnapshot.fromSnapshot).toBe(true);

    const liveGoal = {
      availability: { minutes_per_day: 90, days_per_week: 6 },
      preferences: { session_minutes: 45 },
      deadline_at: null,
    } as unknown as Goal;
    const custom = planTimeInputs(
      roadmap({ planning_snapshot: {} }),
      liveGoal,
    );
    expect(custom.minutesPerDay).toBe(90);
    expect(custom.daysPerWeek).toBe(6);
    expect(custom.sessionMinutes).toBe(45);
    expect(custom.fromSnapshot).toBe(false);
  });
});

describe("add-to-plan and reschedule adapters", () => {
  it("推荐第一个仍有容量的学习日，并解释依据", () => {
    const data = roadmap({
      items: [
        action({ id: "a", node_id: "n1", day_index: 1, duration_minutes: 60 }),
        action({ id: "b", node_id: "n2", day_index: 2, duration_minutes: 30 }),
      ],
    });
    const model = buildPlanModel({ roadmap: data, graph: null, today: TODAY });
    const suggestion = suggestPlanSlot(model);
    expect(suggestion.dayIndex).toBe(2);
    expect(suggestion.durationMinutes).toBe(30);
    expect(suggestion.reasons[0]).toBe("前置节点已满足");
    expect(suggestion.reasons[2]).toContain("仍有 30 分钟可用");

    const blocked = suggestPlanSlot(model, { blockedByPrerequisite: true });
    expect(blocked.reasons[0]).toContain("待解锁");
  });

  it("日期选项覆盖未来学习日，并保留当前项所在的那一天", () => {
    const data = roadmap({
      items: [action({ id: "a", node_id: "n1", day_index: 1, duration_minutes: 30 })],
    });
    const model = buildPlanModel({ roadmap: data, graph: null, today: TODAY });
    const choices = planDayChoices(model, 1);
    expect(choices[0].dayIndex).toBe(1);
    expect(choices[0].minutes).toBe(30);
    expect(choices[0].label).toBe("今天 · 9月13日");
    // 未来学习日都是真实日期，可直接回填 day_index。
    const day3 = choices.find((choice) => choice.dayIndex === 3);
    expect(day3).toBeDefined();
    expect(planDayIndexForDate(new Date(day3!.date), model.planStartedAt, 5)).toBe(3);
  });

  it("过去的排期在重新选择日期时不再出现", () => {
    const late = new Date(2026, 8, 20);
    const data = roadmap({
      items: [action({ id: "a", node_id: "n1", day_index: 8, duration_minutes: 30 })],
    });
    const model = buildPlanModel({ roadmap: data, graph: null, today: late });
    const choices = planDayChoices(model, 8);
    expect(choices.map((choice) => choice.dayIndex)).toContain(8);
    expect(choices.every((choice) => choice.dayIndex >= 1)).toBe(true);
    expect(
      choices.filter((choice) => choice.dayIndex !== 8).every(
        (choice) => choice.date.getTime() >= new Date(2026, 8, 20).getTime(),
      ),
    ).toBe(true);
  });
});

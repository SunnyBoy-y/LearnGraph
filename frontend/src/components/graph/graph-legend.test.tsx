import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  type NodeLearningStatusId,
  type NodeTypeId,
} from "./node-presentation";
import { GraphLegend } from "./graph-legend";

const emptyTypes = new Map<NodeTypeId, number>();
const emptyStatuses = new Map<NodeLearningStatusId, number>();

describe("graph legend marker", () => {
  it("documents the generated learning page marker with its count", () => {
    render(
      <GraphLegend
        pageCount={3}
        statusCounts={emptyStatuses}
        typeCounts={emptyTypes}
      />,
    );

    const entry = screen.getByTitle(/学习页（教材 \/ 互动实验 \/ 闯关测评）已经生成/);
    expect(entry).toHaveTextContent("已生成交互页");
    expect(entry).not.toHaveClass("is-absent");
    expect(entry.querySelector("em")).toHaveTextContent("3");
  });

  it("keeps the marker listed but muted when no node has a learning page", () => {
    render(
      <GraphLegend statusCounts={emptyStatuses} typeCounts={emptyTypes} />,
    );

    const entry = screen.getByTitle("当前图谱还没有已生成学习页的节点");
    expect(entry).toHaveTextContent("已生成交互页");
    expect(entry).toHaveClass("is-absent");
    expect(entry.querySelector("em")).toBeNull();
  });
});

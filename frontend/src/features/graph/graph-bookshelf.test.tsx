import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { Database } from "lucide-react";
import { GraphBookshelf } from "./graph-pages";

function renderShelf() {
  const ready = {
    id: "graph-ready", graphId: "ready", goalId: "goal", title: "操作系统原理", summary: "课程学习",
    status: "正在学习", progress: "修订 1", icon: Database, isGoalBook: false, needsReview: false,
    masteryProgress: 0.25, nodeCount: 32, masteredCount: 8, revision: 1, cover: "/cover.webp",
  };
  const review = { ...ready, id: "graph-review", graphId: "review", title: "曲奇烘焙入门", summary: "生活技能", needsReview: true };
  const props = { books: [ready, review], onDelete: vi.fn(), onOpen: vi.fn(), onReview: vi.fn(), onStartGoal: vi.fn(), onStartLearning: vi.fn() };
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><GraphBookshelf {...props} /></MemoryRouter></QueryClientProvider>);
  return { ...props, ready, review };
}

describe("图谱书架", () => {
  it("展示实际掌握进度，把审核和学习保留为独立主操作", () => {
    const { onStartLearning, onReview, ready, review } = renderShelf();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText("已掌握 8 / 32 个知识点")).toBeInTheDocument();
    expect(screen.queryByText("修订 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续学习" }));
    fireEvent.click(screen.getByRole("button", { name: "去审核" }));
    expect(onStartLearning).toHaveBeenCalledWith(ready);
    expect(onReview).toHaveBeenCalledWith(review);
  });

  it("在紧凑列表中保留搜索结果和卡片操作", () => {
    const { onDelete, review } = renderShelf();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "生活技能" } });
    expect(screen.getAllByRole("article")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "列表视图" }));
    expect(screen.getByRole("button", { name: "列表视图" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(within(screen.getByRole("article")).getByRole("button", { name: /更多操作/ }), { key: "Enter" });
    expect(screen.getByRole("menuitem", { name: "版本记录" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "更换封面" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("menuitem", { name: "删除图谱" }));
    expect(onDelete).toHaveBeenCalledWith(review);
  });
});

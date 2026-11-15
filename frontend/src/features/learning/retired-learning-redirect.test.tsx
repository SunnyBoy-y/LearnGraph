import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NodeLearningPage } from "./retired-learning-redirect";

function Destination() {
  const location = useLocation();
  return <output>{location.pathname}{location.search}</output>;
}

describe("retired learning package bookmarks", () => {
  it.each([
    ["", "/w/workspace/graphs?node=node-1"],
    ["?tab=exam", "/w/workspace/graphs?node=node-1"],
    ["?graphId=graph-1&tab=lesson", "/w/workspace/graphs/graph-1?node=node-1"],
    ["?returnSession=session-1&attempt=attempt-1", "/w/workspace/chat/session-1"],
  ])("redirects %s without mounting the old package page", (query, expected) => {
    render(<MemoryRouter initialEntries={[`/w/workspace/learn/nodes/node-1${query}`]}>
      <Routes>
        <Route path="/w/:workspaceId/learn/nodes/:nodeId" element={<NodeLearningPage />} />
        <Route path="*" element={<Destination />} />
      </Routes>
    </MemoryRouter>);
    expect(screen.getByRole("status")).toHaveTextContent(expected);
    expect(screen.queryByText("学习包")).toBeNull();
  });
});

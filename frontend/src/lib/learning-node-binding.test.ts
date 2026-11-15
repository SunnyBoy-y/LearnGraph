import { describe, expect, it } from "vitest";
import { claimLearningNodeNavigation, matchesSessionLearningNode } from "./learning-node-binding";

const session = { id: "s1", graph_id: "g1", current_node_id: "n1" };

describe("learning navigation event ownership", () => {
  it("lets only one mounted listener handle each event but permits a fresh retry", () => {
    const event = new CustomEvent("learngraph:open-learning-project", { detail: { graphId: "g1", nodeId: "n1" } });
    expect(claimLearningNodeNavigation(event)).toBe(true);
    expect(claimLearningNodeNavigation(event)).toBe(false);
    expect(claimLearningNodeNavigation(new CustomEvent(event.type, { detail: event.detail }))).toBe(true);
  });
});

describe("persisted learning node context", () => {
  it("accepts the saved node without requiring an event session id", () => {
    expect(matchesSessionLearningNode("s1", session, { graphId: "g1", nodeId: "n1" })).toBe(true);
  });
  it.each([
    { graphId: "g1", nodeId: "n2", sessionId: "s1" },
    { graphId: "g1", nodeId: "n2" },
    { graphId: "g2", nodeId: "n1", sessionId: "s1" },
    { graphId: "g1", nodeId: "n1", sessionId: "s2" },
    { graphId: "g1" },
  ])("rejects a selection that conflicts with the saved binding: %j", (context) => {
    expect(matchesSessionLearningNode("s1", session, context)).toBe(false);
  });
  it("does not implicitly bind an unbound conversation", () => {
    expect(matchesSessionLearningNode("s1", { ...session, current_node_id: null }, { graphId: "g1", nodeId: "n2" })).toBe(false);
  });
  it("waits for the persisted session instead of accepting a preview during loading", () => {
    expect(matchesSessionLearningNode("s1", undefined, { graphId: "g1", nodeId: "n2" })).toBe(false);
  });
  it("still allows a new learning draft to receive its initial node", () => {
    expect(matchesSessionLearningNode("new", undefined, { graphId: "g1", nodeId: "n2" })).toBe(true);
    expect(matchesSessionLearningNode("new", undefined, { graphId: "g1", nodeId: "n2", sessionId: "s1" })).toBe(false);
  });
});

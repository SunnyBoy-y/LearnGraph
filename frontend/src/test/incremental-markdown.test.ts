import { describe, expect, it } from "vitest";
import { IncrementalMarkdownParser } from "@/components/ai-elements/incremental-markdown";

describe("IncrementalMarkdownParser", () => {
  it("keeps an open fence visible while its final line grows", () => {
    const parser = new IncrementalMarkdownParser();
    const epoch = {};
    parser.update("```ts\nconst answer = ", epoch);
    const next = parser.update("```ts\nconst answer = 42", epoch);
    const code = next.tail.find((block) => block.node.type === "code");
    expect(code?.node.type).toBe("code");
    if (code?.node.type === "code") expect(code.node.value).toBe("const answer = 42");
  });

  it("uses the bounded raw tail for a long open fence and resets on replacement", () => {
    const parser = new IncrementalMarkdownParser();
    const firstEpoch = {};
    const longFence = `\`\`\`ts\n${"line\n".repeat(2_000)}`;
    const long = parser.update(longFence, firstEpoch);
    expect(long.tail[0]?.node.type).toBe("code");
    const replaced = parser.update("fresh answer", {});
    expect(replaced.generation).toBeGreaterThan(long.generation);
    expect(replaced.frozen).toHaveLength(0);
  });

  it("recognizes a closing fence split across stream chunks", () => {
    const parser = new IncrementalMarkdownParser();
    const epoch = {};
    const opening = `\`\`\`ts\n${"line\n".repeat(2_000)}`;
    parser.update(opening, epoch);
    parser.update(`${opening}\`\``, epoch);
    const closed = parser.update(`${opening}\`\`\`\nanswer`, epoch);
    const code = closed.tail.find((block) => block.node.type === "code");
    expect(code && "streamInitial" in code.node).toBe(false);
    expect(closed.tail.some((block) => block.node.type === "paragraph")).toBe(true);
  });
});

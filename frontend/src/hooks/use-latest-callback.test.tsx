import { memo } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useLatestCallback } from "./use-latest-callback";

it("memoized UI actions use the model loaded after the message first rendered", () => {
  const invoked = vi.fn();
  const rendered = vi.fn();
  const Card = memo(
    ({ onExplain }: { onExplain: () => void }) => {
      rendered();
      return <button onClick={onExplain}>解释节点</button>;
    },
    () => true,
  );
  function Conversation({ model }: { model?: string }) {
    const explain = useLatestCallback(() => invoked(model));
    return <Card onExplain={explain} />;
  }
  const { rerender } = render(<Conversation />);
  rerender(<Conversation model="deepseek-flash" />);
  expect(rendered).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "解释节点" }));
  expect(invoked).toHaveBeenCalledWith("deepseek-flash");
});

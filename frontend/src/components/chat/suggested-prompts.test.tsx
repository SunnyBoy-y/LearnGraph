import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FollowUpPrompts, SuggestedPromptContent } from "./suggested-prompts";

const prompts = [
  { id: "first", content: "这个概念可以怎样应用？" },
  { id: "second", content: "请根据上一轮回答给我一个练习。" },
];

function callbacks() {
  return { onSelect: vi.fn(), onConfigureProvider: vi.fn() };
}

describe("shared suggested prompts", () => {
  it("renders the original question text and selects its full content", async () => {
    const actions = callbacks();
    const user = userEvent.setup();
    render(
      <SuggestedPromptContent
        {...actions}
        prompts={prompts}
        disabled={false}
        isPending={false}
        isUnavailable={false}
      />,
    );

    const questions = screen.getByLabelText("推荐问题");
    expect(within(questions).getAllByRole("button")).toHaveLength(2);
    await user.click(within(questions).getByRole("button", { name: prompts[1].content }));
    expect(actions.onSelect).toHaveBeenCalledExactlyOnceWith(prompts[1].content);
    expect(actions.onConfigureProvider).not.toHaveBeenCalled();
  });

  it("prevents selection when the composer disables suggestions", async () => {
    const actions = callbacks();
    const user = userEvent.setup();
    render(
      <SuggestedPromptContent
        {...actions}
        prompts={prompts}
        disabled
        isPending={false}
        isUnavailable={false}
      />,
    );

    const question = screen.getByRole("button", { name: prompts[0].content });
    expect(question).toBeDisabled();
    await user.click(question);
    expect(actions.onSelect).not.toHaveBeenCalled();
  });

  it("shows the loading state without settings or question buttons", () => {
    const { container } = render(
      <SuggestedPromptContent
        {...callbacks()}
        prompts={[]}
        disabled={false}
        isPending
        isUnavailable
      />,
    );

    expect(screen.getByText("正在生成问题提示…")).toBeVisible();
    expect(container.querySelector('[aria-live="polite"]')).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText("需配置真实模型 Provider")).not.toBeInTheDocument();
  });

  it("opens provider settings when no questions and no usable provider exist", async () => {
    const actions = callbacks();
    const user = userEvent.setup();
    const { container } = render(
      <SuggestedPromptContent
        {...actions}
        prompts={[]}
        disabled
        isPending={false}
        isUnavailable
      />,
    );

    expect(screen.getByText("需配置真实模型 Provider")).toBeVisible();
    expect(container.querySelector('[aria-live="polite"]')).toHaveAttribute("aria-busy", "false");
    await user.click(screen.getByRole("button", { name: "前往设置" }));
    expect(actions.onConfigureProvider).toHaveBeenCalledOnce();
    expect(actions.onSelect).not.toHaveBeenCalled();
  });

  it("keeps existing memory questions selectable when the provider is unavailable", async () => {
    const actions = callbacks();
    const user = userEvent.setup();
    render(
      <SuggestedPromptContent
        {...actions}
        prompts={prompts}
        disabled={false}
        isPending={false}
        isUnavailable
      />,
    );

    await user.click(screen.getByRole("button", { name: prompts[0].content }));
    expect(actions.onSelect).toHaveBeenCalledExactlyOnceWith(prompts[0].content);
    expect(screen.queryByRole("button", { name: "前往设置" })).not.toBeInTheDocument();
  });

  it("uses the follow-up section and disables existing questions during regeneration", async () => {
    const actions = callbacks();
    const user = userEvent.setup();
    const { rerender } = render(
      <FollowUpPrompts {...actions} prompts={prompts} isPending isUnavailable={false} />,
    );

    const section = screen.getByRole("region", { name: "对话问题提示" });
    expect(within(section).getByText("接下来可以问")).toBeVisible();
    const question = within(section).getByRole("button", { name: prompts[0].content });
    expect(question).toBeDisabled();
    await user.click(question);
    expect(actions.onSelect).not.toHaveBeenCalled();
    expect(screen.queryByText("正在生成问题提示…")).not.toBeInTheDocument();

    rerender(
      <FollowUpPrompts {...actions} prompts={prompts} isPending={false} isUnavailable={false} />,
    );
    await user.click(screen.getByRole("button", { name: prompts[0].content }));
    expect(actions.onSelect).toHaveBeenCalledExactlyOnceWith(prompts[0].content);
  });

  it("uses the shared unavailable state inside the follow-up section", async () => {
    const actions = callbacks();
    const user = userEvent.setup();
    render(<FollowUpPrompts {...actions} prompts={[]} isPending={false} isUnavailable />);

    const section = screen.getByRole("region", { name: "对话问题提示" });
    await user.click(within(section).getByRole("button", { name: "前往设置" }));
    expect(actions.onConfigureProvider).toHaveBeenCalledOnce();
  });

  it("renders neither content nor follow-up section when there is no state to show", () => {
    const { container } = render(
      <>
        <SuggestedPromptContent
          {...callbacks()}
          prompts={[]}
          disabled={false}
          isPending={false}
          isUnavailable={false}
        />
        <FollowUpPrompts {...callbacks()} prompts={[]} isPending={false} isUnavailable={false} />
      </>,
    );

    expect(container).toBeEmptyDOMElement();
  });
});

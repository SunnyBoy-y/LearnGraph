import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as sessionsApi from "@/api/sessions";
import { MessagePartRenderer } from "@/components/chat/message-part-renderer";
import { OptionGroup } from "@/components/chat/option-group";
import {
  QuestionSetPager,
  type QuestionItem,
} from "@/components/chat/question-set-pager";
import { TrustedComponentRenderer } from "@/components/chat/trusted-component-renderer";
import { PartUIStateProvider } from "@/lib/part-ui-state";
import { workspaceQueryKey } from "@/lib/query-keys";
import {
  clearQuestionChoiceState,
  readQuestionChoiceState,
  writeQuestionChoiceState,
} from "@/lib/question-choice-state";
import type { Message, MessagePart } from "@/types/sessions";

function makeChoiceQuestion(
  overrides: Partial<QuestionItem> = {},
): QuestionItem {
  return {
    partId: "part-q1",
    componentId: "comp-q1",
    componentType: "single_choice",
    title: "TCP 三次握手的第一步发送什么标志位？",
    mode: "single",
    options: [
      { id: "a", label: "SYN" },
      { id: "b", label: "ACK" },
      { id: "c", label: "FIN" },
    ],
    allowCustom: false,
    allowSkip: false,
    multiline: false,
    answerKey: {
      correctOptionIds: ["a"],
      correctAnswers: [],
      explanation: "客户端首先发送 SYN=1 请求建立连接。",
    },
    ...overrides,
  };
}

describe("question choice persistence and UI morphology", () => {
  beforeEach(() => {
    window.localStorage.clear();
    clearQuestionChoiceState();
  });

  it("reads, writes, clears, and tolerates malformed localStorage payloads", () => {
    writeQuestionChoiceState("unit:key", { values: ["a"], submitted: true });
    expect(
      readQuestionChoiceState<{ values: string[]; submitted: boolean }>(
        "unit:key",
      ),
    ).toEqual({ values: ["a"], submitted: true });

    clearQuestionChoiceState();
    expect(readQuestionChoiceState("unit:key")).toBeNull();

    window.localStorage.setItem("learngraph.question.choices.v1", "{bad-json");
    expect(readQuestionChoiceState("unit:key")).toBeNull();
  });

  it("persists standalone OptionGroup selection and submission across remounts", () => {
    const onSubmit = vi.fn();
    const options = [
      { id: "theory", label: "先学原理", description: "从概念推导开始" },
      { id: "example", label: "先看例子", description: "从具体代码切入" },
    ];

    const first = render(
      <OptionGroup
        allowCustom={false}
        allowSkip={false}
        onSubmit={onSubmit}
        options={options}
        storageKey="standalone-opt-1"
        title="你更偏向哪种学习方式？"
      />,
    );

    const submitBtn = screen.getByRole("button", { name: "提交答案" });
    expect(submitBtn).toHaveClass("option-group__submit-pill");
    expect(submitBtn).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/先看例子/));
    expect(submitBtn).toBeEnabled();

    fireEvent.click(submitBtn);
    expect(onSubmit).toHaveBeenCalledWith({
      values: ["example"],
      labels: ["先看例子"],
    });
    expect(screen.getByRole("status")).toHaveTextContent("已提交选择：先看例子");

    first.unmount();

    // Simulated page refresh: remount with same storageKey.
    render(
      <OptionGroup
        allowCustom={false}
        allowSkip={false}
        options={options}
        storageKey="standalone-opt-1"
        title="你更偏向哪种学习方式？"
      />,
    );

    expect(screen.getByRole("radio", { name: /先看例子/ })).toBeChecked();
    expect(screen.getByRole("status")).toHaveTextContent("已提交选择：先看例子");
  });

  it("renders optionStates verdict styling when graded and honors disabled state", () => {
    const onSubmit = vi.fn();
    const { container } = render(
      <OptionGroup
        allowCustom={false}
        allowSkip={false}
        disabled
        hideActions
        optionStates={{ a: "correct", b: "wrong" }}
        options={[
          { id: "a", label: "SYN" },
          { id: "b", label: "ACK" },
        ]}
        title="已批改题"
        value={["b"]}
      />,
    );

    const choices = container.querySelectorAll(".option-group__choice");
    expect(choices[0]).toHaveClass("is-correct", "is-disabled");
    expect(choices[1]).toHaveClass("is-wrong", "is-disabled", "is-selected");
    expect(
      container.querySelector(".option-group__status-icon.is-correct"),
    ).not.toBeNull();
    expect(
      container.querySelector(".option-group__status-icon.is-wrong"),
    ).not.toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("persists QuestionSetPager graded verdict across remounts and supports retry", async () => {
    const onSubmit = vi.fn();
    const questions = [
      makeChoiceQuestion({ partId: "p-1", componentId: "c-1" }),
      makeChoiceQuestion({
        partId: "p-2",
        componentId: "c-2",
        title: "HTTP 默认端口是？",
        options: [
          { id: "80", label: "80" },
          { id: "443", label: "443" },
        ],
        answerKey: {
          correctOptionIds: ["80"],
          correctAnswers: [],
          explanation: "HTTP 明文默认使用 80 端口。",
        },
      }),
    ];

    const first = render(
      <QuestionSetPager
        onSubmit={onSubmit}
        questions={questions}
        storageKey="p-1:p-2"
      />,
    );

    // Page 1: select wrong option B ("ACK"), click primary pill button 下一题
    fireEvent.click(screen.getByLabelText("ACK"));
    fireEvent.click(
      first.container.querySelector(".question-set-pager__submit-pill")!,
    );

    // Page 2: select correct option "80", submit
    fireEvent.click(screen.getByLabelText("80"));
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }));

    await waitFor(() =>
      expect(screen.getByLabelText("练习批改结果")).toBeInTheDocument(),
    );
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(screen.getByText("1/2 正确")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("回答错误，正确答案是 SYN");

    first.unmount();

    // Simulated refresh: remounting restores graded result state immediately.
    render(
      <QuestionSetPager
        onSubmit={onSubmit}
        questions={questions}
        storageKey="p-1:p-2"
      />,
    );

    expect(screen.getByLabelText("练习批改结果")).toBeInTheDocument();
    expect(screen.getByText("1/2 正确")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("回答错误，正确答案是 SYN");

    // Clicking 重新作答 returns to active edit mode on that question.
    fireEvent.click(screen.getByRole("button", { name: "重新作答" }));
    expect(screen.getByLabelText("交互练习")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "ACK" })).toBeChecked();
  });

  it("renders single-question pager pill submit button and gates streaming interactivity", () => {
    const question = makeChoiceQuestion({ partId: "single-1" });
    const { rerender } = render(
      <QuestionSetPager
        interactive={false}
        questions={[question]}
        storageKey="single-1"
      />,
    );

    const submitBtn = screen.getByRole("button", { name: "提交答案" });
    expect(submitBtn).toHaveClass("question-set-pager__submit-pill", "is-full");
    expect(submitBtn).toBeDisabled();
    expect(screen.getByRole("radio", { name: "SYN" })).toBeDisabled();

    rerender(
      <QuestionSetPager
        interactive
        questions={[question]}
        storageKey="single-1"
      />,
    );
    expect(screen.getByRole("radio", { name: "SYN" })).toBeEnabled();
  });

  it("persists QuizPart selection, avoids duplicate letter prefixes, and restores verdict on remount", () => {
    const onAction = vi.fn();
    const quizPart: MessagePart = {
      id: "quiz-part-1",
      type: "quiz",
      status: "completed",
      data: {
        prompt: "以下哪个协议运行在传输层？",
        options: [
          { id: "opt-a", label: "A. TCP", is_correct: true },
          { id: "opt-b", label: "HTTP" },
        ],
        explanation: "TCP 和 UDP 属于传输层协议。",
      },
    };

    const first = render(
      <MemoryRouter>
        <MessagePartRenderer onAction={onAction} part={quizPart} />
      </MemoryRouter>,
    );

    // "A. TCP" should not become "A. A. TCP", while "HTTP" gets "B. HTTP".
    expect(screen.getByText("A. TCP")).toBeInTheDocument();
    expect(screen.getByText("B. HTTP")).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("A. TCP"));
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }));

    expect(onAction).toHaveBeenCalledWith({
      componentId: "quiz-part-1",
      componentType: "quiz",
      event: "submit",
      payload: { answer_id: "opt-a", answer: "A. TCP" },
    });
    expect(screen.getByRole("status")).toHaveTextContent("回答正确");
    expect(screen.getByText("TCP 和 UDP 属于传输层协议。")).toBeInTheDocument();

    first.unmount();

    // Remount restores submitted verdict and explanation.
    render(
      <MemoryRouter>
        <MessagePartRenderer onAction={onAction} part={quizPart} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toHaveTextContent("回答正确");
    expect(screen.getByRole("button", { name: "重新作答" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重新作答" }));
    expect(screen.getByRole("button", { name: "提交答案" })).toBeInTheDocument();
  });

  it("persists TrustedComponentRenderer question_batch answers and submitted state across remounts", () => {
    const onAction = vi.fn();
    const batchData = {
      component_type: "question_batch",
      schema_version: "1.0",
      allowed_events: ["submit"],
      props: {
        title: "学习背景澄清",
        questions: [
          {
            key: "goal",
            prompt: "当前主要目标是什么？",
            input_type: "single_choice",
            options: [
              { id: "interview", label: "准备面试" },
              { id: "project", label: "项目实战" },
            ],
            allow_custom: false,
            allow_skip: false,
          },
          {
            key: "level",
            prompt: "目前基础如何？",
            input_type: "single_choice",
            options: [
              { id: "beginner", label: "刚入门" },
              { id: "intermediate", label: "有基础" },
            ],
            allow_custom: false,
            allow_skip: false,
          },
        ],
      },
    };

    const first = render(
      <TrustedComponentRenderer
        data={batchData}
        fallbackId="batch-persist-1"
        onAction={onAction}
      />,
    );

    // Answer Q1 -> auto-advances to Q2
    fireEvent.click(screen.getByLabelText("准备面试"));
    fireEvent.click(screen.getByRole("button", { name: /确认 · 还差 1 题/ }));

    // Answer Q2
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("有基础"));
    fireEvent.click(screen.getByRole("button", { name: "确认本题" }));

    // Submit all
    fireEvent.click(screen.getByRole("button", { name: "一次提交全部答案" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", { name: "已提交（可重新提交）" }),
    ).toBeInTheDocument();

    first.unmount();

    // Remount restores page index, answers, and submitted button state.
    render(
      <TrustedComponentRenderer
        data={batchData}
        fallbackId="batch-persist-1"
        onAction={onAction}
      />,
    );

    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "有基础" })).toBeChecked();
    expect(
      screen.getByRole("button", { name: "已提交（可重新提交）" }),
    ).toBeInTheDocument();
  });

  it("persists choice state to backend via PartUIStateProvider and hydrates from backend ui_state without localStorage", async () => {
    const spy = vi
      .spyOn(sessionsApi, "updateSessionPartUIState")
      .mockResolvedValue({
        part_id: "backend-part-1",
        session_id: "sess-1",
        message_id: "msg-1",
        ui_state: {},
      });

    const onLocalChange = vi.fn();
    const queryClient = new QueryClient();
    const part: MessagePart = {
      id: "backend-part-1",
      type: "component",
      status: "completed",
      data: {
        component_type: "single_choice",
        schema_version: "1.0",
        allowed_events: ["submit"],
        props: {
          title: "HTTP 幂等方法？",
          options: [
            { id: "get", label: "GET" },
            { id: "post", label: "POST" },
          ],
        },
      },
    };
    const cachedMessage: Message = {
      id: "msg-1",
      workspace_id: "ws-1",
      session_id: "sess-1",
      parent_message_id: null,
      role: "assistant",
      version: 1,
      status: "completed",
      content: "",
      parts: [part],
      provider_trace: {},
      created_at: "2026-11-25T00:00:00Z",
    };
    queryClient.setQueryData(
      workspaceQueryKey("ws-1", "messages", "sess-1"),
      [cachedMessage],
    );

    const first = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <PartUIStateProvider
            onLocalPartUIStateChange={onLocalChange}
            sessionId="sess-1"
            workspaceId="ws-1"
          >
            <QuestionSetPager
              questions={[
                makeChoiceQuestion({
                  partId: "backend-part-1",
                  componentId: "backend-part-1",
                }),
              ]}
              storageKey="backend-part-1"
            />
          </PartUIStateProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByLabelText("SYN"));
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy).toHaveBeenCalledWith(
      "sess-1",
      "backend-part-1",
      expect.objectContaining({
        storageKey: "pager:backend-part-1",
        results: expect.arrayContaining([
          expect.objectContaining({ values: ["a"], isCorrect: true }),
        ]),
      }),
    );
    expect(onLocalChange).toHaveBeenCalledWith(
      "backend-part-1",
      expect.objectContaining({ storageKey: "pager:backend-part-1" }),
    );
    const updatedMessages = queryClient.getQueryData<Message[]>(
      workspaceQueryKey("ws-1", "messages", "sess-1"),
    );
    const persistedUiState = updatedMessages?.[0]?.parts[0]?.data?.ui_state as
      | Record<string, unknown>
      | undefined;
    expect(persistedUiState).toBeTruthy();

    first.unmount();

    // Simulate a fresh browser/device with empty localStorage, hydrated solely from backend ui_state.
    window.localStorage.clear();
    clearQuestionChoiceState();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <PartUIStateProvider sessionId="sess-1" workspaceId="ws-1">
            <QuestionSetPager
              questions={[
                makeChoiceQuestion({
                  partId: "backend-part-1",
                  componentId: "backend-part-1",
                  uiState: persistedUiState,
                }),
              ]}
              storageKey="backend-part-1"
            />
          </PartUIStateProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByLabelText("练习批改结果")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("回答正确");
    spy.mockRestore();
  });
});

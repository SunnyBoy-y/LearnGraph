import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RetryDetailsBlock } from "@/components/chat/retry-details-block";
import {
  extractRetryAttemptsFromTrace,
  mergeRetryEventIntoTrace,
} from "@/features/chat/retry-attempts";

describe("retry-attempts and RetryDetailsBlock", () => {
  it("merges scheduled, started, and exhausted events into provider_trace", () => {
    let trace = mergeRetryEventIntoTrace({}, "provider.retry.scheduled", {
      payload: {
        attempt_no: 2,
        max_retries: 3,
        error_category: "upstream_http",
        backoff_ms: 1500,
        error_message: "HTTP 429 rate_limit_exceeded",
      },
    });
    let attempts = extractRetryAttemptsFromTrace(trace);
    expect(attempts).toHaveLength(1);
    expect(attempts[0]).toMatchObject({
      attemptNo: 2,
      maxRetries: 3,
      errorCategory: "upstream_http",
      backoffMs: 1500,
      errorMessage: "HTTP 429 rate_limit_exceeded",
      phase: "scheduled",
    });

    trace = mergeRetryEventIntoTrace(trace, "provider.retry.started", {
      payload: { attempt_no: 2, max_retries: 3 },
    });
    attempts = extractRetryAttemptsFromTrace(trace);
    expect(attempts).toHaveLength(1);
    expect(attempts[0].phase).toBe("started");
    expect(attempts[0].backoffMs).toBe(1500);
    expect(attempts[0].errorCategory).toBe("upstream_http");

    trace = mergeRetryEventIntoTrace(trace, "provider.retry.exhausted", {
      payload: { attempt_no: 3, max_retries: 3, error_category: "timeout" },
    });
    attempts = extractRetryAttemptsFromTrace(trace);
    expect(attempts).toHaveLength(2);
    expect(attempts[1].phase).toBe("exhausted");
  });

  it("renders live retry banner and expands full attempt history on click", () => {
    const attempts = [
      {
        attemptNo: 2,
        maxRetries: 3,
        errorCategory: "upstream_http",
        backoffMs: 2500,
        errorMessage: "HTTP 502 Bad Gateway",
        phase: "scheduled" as const,
      },
    ];

    render(<RetryDetailsBlock attempts={attempts} isActive />);
    const toggle = screen.getByRole("button");
    expect(toggle).toHaveTextContent("等待 2.5s 后");
    expect(toggle).toHaveTextContent("第 2 次尝试");
    expect(screen.queryByText("HTTP 502 Bad Gateway")).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByText("HTTP 502 Bad Gateway")).toBeInTheDocument();
  });

  it("renders settled summary after completion", () => {
    const attempts = [
      {
        attemptNo: 2,
        maxRetries: 3,
        errorCategory: "timeout",
        backoffMs: 1000,
        phase: "started" as const,
      },
    ];
    render(<RetryDetailsBlock attempts={attempts} isActive={false} />);
    expect(screen.getByRole("button")).toHaveTextContent("本次回答共经历 1 次自动重试");
  });
});

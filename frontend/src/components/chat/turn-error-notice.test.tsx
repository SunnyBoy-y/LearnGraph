import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TurnErrorNotice } from "@/components/chat/turn-error-notice";
import { classifyTurnError } from "@/features/chat/turn-error";

describe("turn-error classification and TurnErrorNotice", () => {
  it("classifies auth, rate_limit, context_limit, timeout, server, and connection errors", () => {
    expect(classifyTurnError({ kind: "auth", statusCode: 401, message: "bad key" }).primaryAction).toBe(
      "open_provider_settings",
    );
    expect(classifyTurnError("HTTP 429 · rate_limit · Too Many Requests").kind).toBe("rate_limit");
    expect(
      classifyTurnError({ message: "maximum context length exceeded", statusCode: 400 }).kind,
    ).toBe("context_limit");
    expect(classifyTurnError({ code: "provider_timeout", message: "timed out" }).kind).toBe("timeout");
    expect(classifyTurnError("HTTP 503 · upstream unavailable").kind).toBe("server");
    expect(classifyTurnError("ECONNRESET socket hang up").kind).toBe("connection");
  });

  it("shows provider settings CTA for auth errors and hides raw details until expanded", () => {
    const onOpenProviderSettings = vi.fn();
    const onRetry = vi.fn();

    render(
      <TurnErrorNotice
        error={{ kind: "auth", statusCode: 401, code: "provider_http_error", message: "invalid_api_key" }}
        onOpenProviderSettings={onOpenProviderSettings}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText("模型服务鉴权失败")).toBeInTheDocument();
    expect(screen.queryByText(/invalid_api_key/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /打开模型配置/ }));
    expect(onOpenProviderSettings).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /查看技术详情/ }));
    expect(screen.getByText(/invalid_api_key/)).toBeInTheDocument();
  });

  it("shows compact-and-retry CTA for context_limit errors", () => {
    const onCompactAndRetry = vi.fn();

    render(
      <TurnErrorNotice
        error={{ kind: "context_limit", statusCode: 400, message: "prompt is too long" }}
        onCompactAndRetry={onCompactAndRetry}
      />,
    );

    expect(screen.getByText("上下文长度超出模型上限")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /压缩上下文并重试/ }));
    expect(onCompactAndRetry).toHaveBeenCalledTimes(1);
  });
});

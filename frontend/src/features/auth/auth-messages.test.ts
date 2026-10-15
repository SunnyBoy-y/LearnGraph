import { describe, expect, it } from "vitest";

import { ApiError } from "@/api/client";
import { authErrorMessage, SESSION_EXPIRED_MESSAGE } from "./auth-messages";

describe("authErrorMessage", () => {
  it("maps known backend auth codes to Chinese messages", () => {
    const error = (code: string, status = 400, message = "English message") =>
      new ApiError({ status, code, message, method: "POST", url: "/auth/login" });

    expect(authErrorMessage(error("invalid_credentials", 401))).toBe(
      "用户名或密码错误。",
    );
    expect(
      authErrorMessage(error("account_temporarily_locked", 429)),
    ).toBe("登录失败次数过多，账户已临时锁定，请稍后再试。");
    expect(authErrorMessage(error("weak_password", 422))).toBe(
      "密码不符合要求，请检查下方规则后重试。",
    );
    expect(authErrorMessage(error("identity_conflict", 409))).toBe(
      "用户名或邮箱已被使用。",
    );
  });

  it("maps unexpected server errors to a user-facing message", () => {
    const error = new ApiError({
      status: 500,
      code: "unexpected",
      message: "internal detail",
      method: "POST",
      url: "/auth/login",
    });
    expect(authErrorMessage(error)).toBe("服务暂时不可用，请稍后再试。");
  });

  it("exports the session-expired message used by the login page", () => {
    expect(SESSION_EXPIRED_MESSAGE).toBe("登录状态已失效，请重新登录。");
  });
});

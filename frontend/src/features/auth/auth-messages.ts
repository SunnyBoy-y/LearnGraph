import { ApiError } from "@/api/client";

/**
 * Maps backend auth errors to user-facing Chinese messages. The server keeps
 * English messages for logs; the UI should not leak implementation wording.
 */
export function authErrorMessage(
  reason: unknown,
  fallback = "无法连接 LearnGraph 后端，请确认 API 已启动。",
): string {
  if (reason instanceof ApiError) {
    switch (reason.code) {
      case "invalid_credentials":
        return "用户名或密码错误。";
      case "account_temporarily_locked":
        return "登录失败次数过多，账户已临时锁定，请稍后再试。";
      case "identity_conflict":
        return "用户名或邮箱已被使用。";
      case "weak_password":
        return "密码不符合要求，请检查下方规则后重试。";
      case "password_unchanged":
        return "新密码不能与当前密码相同。";
      case "demo_auth_disabled":
        return "演示登录当前未启用。";
      case "network_error":
        return "网络连接失败，请检查网络后重试。";
      case "auth_session_not_found":
      case "unauthorized":
        return "登录状态已失效，请重新登录。";
      default:
        if (reason.status === 401) return "用户名或密码错误。";
        if (reason.status === 403) return "没有权限执行此操作。";
        if (reason.status === 404) return "请求的资源不存在。";
        if (reason.status >= 500) return "服务暂时不可用，请稍后再试。";
        return reason.message;
    }
  }
  if (reason instanceof Error) return reason.message;
  return fallback;
}

export const SESSION_EXPIRED_MESSAGE = "登录状态已失效，请重新登录。";

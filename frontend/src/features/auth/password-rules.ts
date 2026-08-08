const RESERVED_PASSWORDS = new Set([
  "password1234",
  "adminadmin123",
  "learn-graph-local",
]);

export const PASSWORD_RULE_SUMMARY =
  "密码需至少 12 个字符，同时包含字母和数字，至少使用 6 种不同字符，且不能包含用户名。";

/**
 * Mirrors the backend password policy in auth.py so users see the rules before
 * submitting and do not discover them from a 422 response.
 */
export function passwordRuleViolations(
  password: string,
  username = "",
): string[] {
  const violations: string[] = [];
  if (password.length < 12) {
    violations.push("密码至少需要 12 个字符。");
  }
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) {
    violations.push("密码需要同时包含字母和数字。");
  }
  if (new Set(password).size < 6) {
    violations.push("密码至少需要使用 6 种不同字符。");
  }
  const normalizedPassword = password.toLocaleLowerCase();
  const identity = username.toLocaleLowerCase().trim();
  if (
    identity &&
    !identity.includes("@") &&
    normalizedPassword.includes(identity)
  ) {
    violations.push("密码不能包含用户名。");
  }
  if (RESERVED_PASSWORDS.has(normalizedPassword)) {
    violations.push("不能使用常见或保留密码。");
  }
  return violations;
}

export function passwordMeetsRules(password: string, username = ""): boolean {
  return passwordRuleViolations(password, username).length === 0;
}

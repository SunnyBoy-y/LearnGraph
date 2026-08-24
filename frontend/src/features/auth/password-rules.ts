export const PASSWORD_RULE_SUMMARY =
  "密码需至少 8 个字符，且同时包含字母和数字。";

/**
 * Mirrors the backend password policy in auth.py so users see the rules before
 * submitting and do not discover them from a 422 response.
 */
export function passwordRuleViolations(
  password: string,
  _username = "",
): string[] {
  const violations: string[] = [];
  if (password.length < 8) {
    violations.push("密码至少需要 8 个字符。");
  }
  if (!/[A-Za-z]/.test(password) || !/\d/.test(password)) {
    violations.push("密码需要同时包含字母和数字。");
  }
  return violations;
}

export function passwordMeetsRules(password: string, username = ""): boolean {
  return passwordRuleViolations(password, username).length === 0;
}

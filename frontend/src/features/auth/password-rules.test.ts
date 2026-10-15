import { describe, expect, it } from "vitest";

import {
  passwordMeetsRules,
  passwordRuleViolations,
} from "./password-rules";

describe("passwordRuleViolations", () => {
  it("accepts a valid password", () => {
    expect(passwordRuleViolations("LearnGraph2026!")).toEqual([]);
    expect(passwordMeetsRules("LearnGraph2026!")).toBe(true);
  });

  it("accepts the minimum 8-character alphanumeric password", () => {
    expect(passwordRuleViolations("abcd1234")).toEqual([]);
    expect(passwordMeetsRules("abcd1234")).toBe(true);
  });

  it("rejects passwords that are too short", () => {
    expect(passwordRuleViolations("Ab1!")).toContain(
      "密码至少需要 8 个字符。",
    );
  });

  it("rejects passwords without letters", () => {
    expect(passwordRuleViolations("12345678")).toContain(
      "密码需要同时包含字母和数字。",
    );
  });

  it("rejects passwords without numbers", () => {
    expect(passwordRuleViolations("abcdefgh")).toContain(
      "密码需要同时包含字母和数字。",
    );
  });

  it("no longer rejects passwords containing the username", () => {
    expect(passwordRuleViolations("alice12345678", "alice")).toEqual([]);
  });

  it("no longer rejects former reserved passwords", () => {
    expect(passwordRuleViolations("password1234")).toEqual([]);
  });
});

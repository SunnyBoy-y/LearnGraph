import { readDataPath } from "@/lib/data-model-store";

export type CheckFunctionName =
  | "required"
  | "non_empty"
  | "min_length"
  | "max_length"
  | "min_value"
  | "max_value"
  | "range"
  | "one_of"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "eq"
  | "ne"
  | "is_finite_number";

export type CheckRule = {
  id: string;
  fn: CheckFunctionName;
  path: string;
  args: Array<string | number | boolean | null>;
  message: string;
  severity: "error" | "warning" | "info";
  target_id?: string | null;
};

export type CheckResult = {
  id: string;
  passed: boolean;
  message: string;
  severity: "error" | "warning" | "info";
  targetId?: string | null;
};

function isFiniteNum(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

const CATALOG_FUNCTIONS: Record<
  CheckFunctionName,
  (value: unknown, ...args: unknown[]) => boolean
> = {
  required: (value) => {
    if (value == null) return false;
    if (typeof value === "string") return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "object") return Object.keys(value).length > 0;
    return true;
  },
  non_empty: (value) => {
    if (typeof value === "string") return value.trim().length > 0;
    if (Array.isArray(value)) return value.length > 0;
    if (value && typeof value === "object") return Object.keys(value).length > 0;
    return value != null;
  },
  min_length: (value, limit) => {
    if (!isFiniteNum(limit) || !Number.isInteger(limit)) return false;
    if (typeof value === "string" || Array.isArray(value)) return value.length >= limit;
    if (value && typeof value === "object") return Object.keys(value).length >= limit;
    return false;
  },
  max_length: (value, limit) => {
    if (!isFiniteNum(limit) || !Number.isInteger(limit)) return false;
    if (typeof value === "string" || Array.isArray(value)) return value.length <= limit;
    if (value && typeof value === "object") return Object.keys(value).length <= limit;
    return false;
  },
  min_value: (value, min) => isFiniteNum(value) && isFiniteNum(min) && value >= min,
  max_value: (value, max) => isFiniteNum(value) && isFiniteNum(max) && value <= max,
  range: (value, min, max) =>
    isFiniteNum(value) && isFiniteNum(min) && isFiniteNum(max) && value >= min && value <= max,
  one_of: (value, ...options) => options.some((opt) => Object.is(value, opt)),
  gt: (value, target) => isFiniteNum(value) && isFiniteNum(target) && value > target,
  gte: (value, target) => isFiniteNum(value) && isFiniteNum(target) && value >= target,
  lt: (value, target) => isFiniteNum(value) && isFiniteNum(target) && value < target,
  lte: (value, target) => isFiniteNum(value) && isFiniteNum(target) && value <= target,
  eq: (value, target) => Object.is(value, target),
  ne: (value, target) => !Object.is(value, target),
  is_finite_number: (value) => isFiniteNum(value),
};

export function isRegisteredCatalogFunction(name: string): name is CheckFunctionName {
  return Object.hasOwn(CATALOG_FUNCTIONS, name);
}

export function evaluateCheck(
  rule: CheckRule,
  dataModel: Record<string, unknown>,
): CheckResult {
  const fn = Object.hasOwn(CATALOG_FUNCTIONS, rule.fn)
    ? CATALOG_FUNCTIONS[rule.fn]
    : undefined;
  if (!fn) {
    return {
      id: rule.id,
      passed: false,
      message: rule.message,
      severity: rule.severity,
      targetId: rule.target_id,
    };
  }
  const value = readDataPath(dataModel, rule.path);
  return {
    id: rule.id,
    passed: Boolean(fn(value, ...(rule.args ?? []))),
    message: rule.message,
    severity: rule.severity,
    targetId: rule.target_id,
  };
}

export function evaluateChecks(
  rules: readonly CheckRule[],
  dataModel: Record<string, unknown>,
): CheckResult[] {
  return rules.map((rule) => evaluateCheck(rule, dataModel));
}

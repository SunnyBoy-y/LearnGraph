import { describe, expect, it } from "vitest";
import { nativeMoney, sumMoney, validMoney } from "./money";

describe("native money", () => {
  it.each([
    ["5.585420181740000022158", "USD", "$5.59 USD"],
    ["52.56322882", "USD", "$52.56 USD"],
    ["0.00000000000000000001", "CNY", "¥0.00 CNY"],
    ["0", "USD", "$0.00 USD"],
    ["12.3", "CNY", "¥12.30 CNY"],
    ["1.005", "USD", "$1.01 USD"],
    ["999.995", "CNY", "¥1,000.00 CNY"],
    ["9999999999999999999999999.995", "USD", "$10,000,000,000,000,000,000,000,000.00 USD"],
  ] as const)("formats %s %s to two decimal places", (amount, currency, expected) => {
    expect(nativeMoney(amount, currency)).toBe(expected);
  });
  it("preserves unknown amounts and exact arithmetic", () => {
    expect(nativeMoney(null, "USD")).toBe("金额待核实 USD");
    expect(nativeMoney("100", null)).toBe("金额待核实");
    expect(sumMoney(["0.1", "0.2"])).toBe("0.3");
    expect(sumMoney(["100.00", "0"])).toBe("100");
  });
  it("rejects negative and nonfinite inputs", () => {
    expect(validMoney("NaN")).toBe(false);
    expect(validMoney("-1")).toBe(false);
    expect(validMoney("0.000001")).toBe(true);
  });
});

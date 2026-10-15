import { describe, expect, it } from "vitest";
import { nativeMoney, sumMoney, validMoney } from "./money";

describe("native money", () => {
  it("preserves exact tiny amounts and known zero", () => {
    expect(nativeMoney("0.00000000000000000001", "CNY")).toBe("¥0.00000000000000000001 CNY");
    expect(nativeMoney("0", "USD")).toBe("$0 USD");
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

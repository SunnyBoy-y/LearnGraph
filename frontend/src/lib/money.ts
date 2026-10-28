import type { BillingCurrency } from "@/types/usage";

export function nativeMoney(amount: string | number | null | undefined, currency: BillingCurrency | null | undefined): string {
  if (amount === null || amount === undefined || !currency) return currency ? "金额待核实 " + currency : "金额待核实";
  const value = String(amount);
  if (!/^\d+(?:\.\d+)?$/.test(value)) return "金额待核实";
  const [whole, fraction = ""] = value.split(".");
  // Round the decimal string directly so large amounts and half-cent ties stay exact.
  const cents = (BigInt(whole + fraction.padEnd(2, "0").slice(0, 2)) + (fraction[2] >= "5" ? 1n : 0n))
    .toString().padStart(3, "0");
  const grouped = cents.slice(0, -2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (currency === "USD" ? "$" : "¥") + grouped + "." + cents.slice(-2) + " " + currency;
}

export function validMoney(value: string): boolean { return /^\d{1,25}(?:\.\d{1,24})?$/.test(value); }

export function sumMoney(values: readonly (string | null)[]): string {
  const present = values.filter((value): value is string => value !== null);
  const scale = Math.max(0, ...present.map((value) => (value.split(".")[1] ?? "").length));
  const total = present.reduce((sum, value) => {
    const [whole, fraction = ""] = value.split(".");
    return sum + BigInt(whole + fraction.padEnd(scale, "0"));
  }, 0n).toString().padStart(scale + 1, "0");
  if (!scale) return total;
  return (total.slice(0, -scale) + "." + total.slice(-scale)).replace(/\.?0+$/, "");
}

// Presentation-only formatters. No math that changes meaning — just display.

export const rupees = (paise: number): string => {
  const neg = paise < 0;
  const v = Math.abs(paise) / 100;
  const s = v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${neg ? "−" : ""}₹${s}`;
};

export const pct = (x: number, digits = 1): string => `${(x * 100).toFixed(digits)}%`;

export const signedPct = (x: number, digits = 1): string => {
  const sign = x > 0 ? "+" : x < 0 ? "−" : "";
  return `${sign}${(Math.abs(x) * 100).toFixed(digits)}%`;
};

export const titleCase = (s: string): string =>
  s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export const shortHash = (hex: string, head = 6, tail = 4): string =>
  hex.length > head + tail ? `${hex.slice(0, head)}…${hex.slice(-tail)}` : hex;

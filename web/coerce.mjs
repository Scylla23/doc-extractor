// "1,234.50" -> 1234.5 ; "PO-9" -> "PO-9"
export function coerce(text) {
  const t = text.trim();
  return /^-?[\d,]+(\.\d+)?$/.test(t) ? Number(t.replace(/,/g, "")) : t;
}

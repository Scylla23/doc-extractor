// Runnable check for setPath/getPath's dotted-path hardening: `node web/paths.test.mjs`.
import { setPath, getPath } from "./paths.mjs";

let failed = false;
function check(actual, expected, label) {
  if (actual !== expected) {
    failed = true;
    console.error(`FAIL ${label}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
  }
}

// A numeric-looking custom-field key must not turn custom_fields into an Array.
{
  const obj = {};
  setPath(obj, "custom_fields.2024", { value: "FY24" });
  check(Array.isArray(obj.custom_fields), false, "custom_fields.2024 stays a plain object");
  check(obj.custom_fields["2024"] && obj.custom_fields["2024"].value, "FY24", "custom_fields.2024 value set");
}

// A plain custom-field key creates a single key (one segment), no nesting.
{
  const obj = {};
  setPath(obj, "custom_fields.po_number", { value: "PO-9" });
  check(Object.keys(obj.custom_fields).length, 1, "custom_fields.po_number is a single key");
  check(obj.custom_fields.po_number.value, "PO-9", "custom_fields.po_number value set");
}

// A pre-existing line_items Array must remain an Array after an amount write.
{
  const obj = { line_items: [{}] };
  setPath(obj, "line_items.0.amount", { value: 42 });
  check(Array.isArray(obj.line_items), true, "line_items stays an Array");
  check(obj.line_items[0].amount.value, 42, "line_items.0.amount value set");
}

// getPath round-trips a value written by setPath.
{
  const obj = {};
  setPath(obj, "a.b.c", 7);
  check(getPath(obj, "a.b.c"), 7, "getPath round-trips setPath");
}

if (failed) {
  process.exit(1);
} else {
  console.log("paths self-check OK");
}

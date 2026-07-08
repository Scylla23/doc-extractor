// Runnable check for coerce()'s money-vs-string branch: `node web/coerce.test.mjs`.
import { coerce } from "./coerce.mjs";

let failed = false;
function check(actual, expected, label) {
  if (actual !== expected) {
    failed = true;
    console.error(`FAIL ${label}: got ${JSON.stringify(actual)}, want ${JSON.stringify(expected)}`);
  }
}

check(coerce("1,234.50"), 1234.5, "coerce money");
check(coerce("PO-9"), "PO-9", "coerce string");
check(coerce("  42 "), 42, "coerce trims+numeric");

if (failed) {
  process.exit(1);
} else {
  console.log("coerce self-check OK");
}

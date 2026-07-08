// Dotted-path get/set for the working record.
// setPath always creates a plain object for a missing container — never an
// Array — so a numeric-looking key segment (e.g. a custom-field key of
// "2024") can't corrupt the parent into a sparse Array. The only real array
// in the record is line_items, which always exists (with its items) before
// any edit — buildLedger only emits `line_items.${i}.amount` paths when
// items are present — so setPath never needs to auto-create it.
export function setPath(obj, path, value) {
  const keys = path.split(".");
  let node = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    if (node[k] == null) node[k] = {};
    node = node[k];
  }
  node[keys[keys.length - 1]] = value;
}

export function getPath(obj, path) {
  return path.split(".").reduce((n, k) => (n == null ? n : n[k]), obj);
}

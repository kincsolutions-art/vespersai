import assert from "node:assert/strict";
import test from "node:test";
import { DEFAULT_RETURN_PATH, safeReturnPath } from "./redirects.ts";

test("keeps same-site absolute paths", () => {
  for (const path of ["/account", "/account?tab=1", "/a/b/c", "/x#y"]) {
    assert.equal(safeReturnPath(path), path);
  }
});

test("rejects open-redirect targets", () => {
  const hostile = [
    "https://evil.test/steal",
    "//evil.test",
    "//evil.test/path",
    "/\\evil.test",
    "\\/evil.test",
    "http://evil.test",
    "javascript:alert(1)",
    "/javascript:alert(1)",
    "  /account",
    "/acc\nount",
    "",
    null,
    undefined,
    "/".repeat(600),
  ];
  for (const candidate of hostile) {
    assert.equal(
      safeReturnPath(candidate as string),
      DEFAULT_RETURN_PATH,
      `expected fallback for ${JSON.stringify(candidate)}`,
    );
  }
});

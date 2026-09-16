/**
 * Post-login redirect policy.
 *
 * Only same-site absolute paths are allowed. Everything else — absolute URLs,
 * protocol-relative "//evil.test", backslash variants that some browsers
 * normalise to "/", and control characters — falls back to the account page.
 */
export const DEFAULT_RETURN_PATH = "/account";

const CONTROL_OR_SPACE = /[\x00-\x1F\x7F\s]/;
const LEADING_SCHEME = /^\/[^/]*:/;

export function safeReturnPath(candidate: string | null | undefined): string {
  if (typeof candidate !== "string" || candidate.length === 0) {
    return DEFAULT_RETURN_PATH;
  }
  if (candidate.length > 512) return DEFAULT_RETURN_PATH;
  if (CONTROL_OR_SPACE.test(candidate)) return DEFAULT_RETURN_PATH;
  if (candidate.includes("\\")) return DEFAULT_RETURN_PATH;
  if (!candidate.startsWith("/")) return DEFAULT_RETURN_PATH;
  // "//host" is a network-path reference: the browser would leave our origin.
  if (candidate.startsWith("//")) return DEFAULT_RETURN_PATH;
  if (LEADING_SCHEME.test(candidate)) return DEFAULT_RETURN_PATH;
  return candidate;
}

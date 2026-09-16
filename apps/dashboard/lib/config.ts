/**
 * Server-only configuration. Never import from a Client Component: it reads
 * secrets and the internal API base that the browser must not see.
 */
import "server-only";

export const API_BASE = process.env.VESPERS_API_BASE_URL ?? "http://api:8000";

export function isConfigured(): boolean {
  return Boolean(
    process.env.WORKOS_CLIENT_ID &&
    process.env.WORKOS_API_KEY &&
    process.env.WORKOS_COOKIE_PASSWORD,
  );
}

/**
 * Origins accepted on state-changing BFF requests. Cookie-authenticated routes
 * need this because the browser attaches the session cookie automatically; the
 * FastAPI endpoints are bearer-only and therefore carry no ambient credential
 * for a cross-site form to abuse.
 */
export function allowedOrigins(): string[] {
  const configured = process.env.VESPERS_ALLOWED_ORIGINS;
  if (configured) {
    return configured
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
  }
  const redirect = process.env.NEXT_PUBLIC_WORKOS_REDIRECT_URI;
  if (redirect) {
    try {
      return [new URL(redirect).origin];
    } catch {
      return [];
    }
  }
  return [];
}

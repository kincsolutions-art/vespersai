/**
 * Server-only configuration. Never import from a Client Component: it reads
 * secrets and the internal API base that the browser must not see.
 *
 * The authentication policy itself lives in `./auth-config`, which `proxy.ts`
 * imports too, so the proxy and the pages cannot disagree about whether sign-in
 * is permitted.
 */
import "server-only";
import { authConfigProblem } from "./auth-config";

export {
  appOrigin,
  authConfigProblem,
  deploymentEnvironment,
} from "./auth-config";
export type { AuthConfigProblem, Environment } from "./auth-config";

/**
 * Where the dashboard reaches FastAPI. Under Compose this is the service name on
 * the internal network and is set in `compose.yaml`, deliberately *not* in
 * `.env.dashboard`: it describes container topology, not operator configuration.
 * The default matches Compose so a missing value cannot silently point the BFF
 * somewhere else.
 */
export const API_BASE = process.env.VESPERS_API_BASE_URL ?? "http://api:8000";

/** True only when sign-in may be offered; see `authConfigProblem`. */
export function isConfigured(): boolean {
  return authConfigProblem() === null;
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

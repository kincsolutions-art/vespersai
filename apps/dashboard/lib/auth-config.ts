/**
 * Server-side authentication configuration policy for the dashboard.
 *
 * This module reads environment variables and returns only fixed labels,
 * booleans and an origin. It never returns a secret, which is why it carries no
 * `server-only` marker: `proxy.ts` must import it too, and the proxy runs
 * outside the module graph that `server-only` is designed to police.
 *
 * ## Why not NODE_ENV
 *
 * `NODE_ENV` cannot tell a real deployment from a laptop here. The documented
 * local workflow is `docker compose up`, and `apps/dashboard/Dockerfile` builds
 * a standalone Next.js server with `NODE_ENV=production`. Gating on it would
 * either reject the documented local setup or, worse, pass in production the
 * moment someone ran a dev server there.
 *
 * `VESPERS_ENVIRONMENT` is the discriminator the backend already uses
 * (`backend/config.py`), with the same three values. The dashboard now reads the
 * same variable so one deployment cannot be half production.
 *
 * ## The production rule
 *
 * `@workos-inc/authkit-nextjs` derives the session cookie's `Secure` attribute
 * from the scheme of `NEXT_PUBLIC_WORKOS_REDIRECT_URI`. An `http://` redirect
 * URI therefore does not merely downgrade the redirect — it ships the sealed
 * session cookie without `Secure`, where any plaintext request to the same host
 * can carry it off. In `production` the scheme must be `https:`.
 *
 * `http://localhost` is **not** an exemption from that rule. A hostname is not
 * evidence about the deployment; only `VESPERS_ENVIRONMENT` is. Plain HTTP is
 * permitted exclusively under the explicit local-development policy —
 * `VESPERS_ENVIRONMENT` of `development` or `test`.
 */

export type Environment = "development" | "test" | "production";

/**
 * Fixed labels. These reach an unauthenticated page, so they name the class of
 * misconfiguration and never a value.
 */
export type AuthConfigProblem =
  | "environment-invalid"
  | "authentication-not-configured"
  | "redirect-uri-missing"
  | "redirect-uri-invalid"
  | "redirect-uri-not-https";

export type Environables = Record<string, string | undefined>;

const ENVIRONMENTS: readonly Environment[] = [
  "development",
  "test",
  "production",
];

function present(value: string | undefined): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

/** The declared deployment environment, or `null` when it is not a known value. */
export function deploymentEnvironment(
  env: Environables = process.env,
): Environment | null {
  const declared = env.VESPERS_ENVIRONMENT?.trim();
  if (!declared) return "development";
  return ENVIRONMENTS.includes(declared as Environment)
    ? (declared as Environment)
    : null;
}

/**
 * Why authentication must not be offered, or `null` when it may be.
 *
 * Fails closed: every unknown or unusable state is a problem, so a caller that
 * checks for `null` cannot accidentally enable sign-in.
 */
export function authConfigProblem(
  env: Environables = process.env,
): AuthConfigProblem | null {
  const environment = deploymentEnvironment(env);
  if (environment === null) return "environment-invalid";

  if (
    !present(env.WORKOS_CLIENT_ID) ||
    !present(env.WORKOS_API_KEY) ||
    !present(env.WORKOS_COOKIE_PASSWORD)
  ) {
    // Not an error: an unconfigured checkout is a supported state. The landing
    // page, /api/health and the offline build all keep working; only sign-in is
    // withheld.
    return "authentication-not-configured";
  }

  const redirect = env.NEXT_PUBLIC_WORKOS_REDIRECT_URI?.trim();
  if (!redirect) return "redirect-uri-missing";

  let parsed: URL;
  try {
    parsed = new URL(redirect);
  } catch {
    return "redirect-uri-invalid";
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    return "redirect-uri-invalid";
  }
  if (environment === "production" && parsed.protocol !== "https:") {
    return "redirect-uri-not-https";
  }
  return null;
}

/**
 * The origin the browser actually uses, taken from the registered redirect URI.
 *
 * Returns `undefined` unless the configuration passes the policy above, so a
 * rejected production configuration cannot leak an origin into a redirect.
 */
export function appOrigin(env: Environables = process.env): string | undefined {
  if (authConfigProblem(env) !== null) return undefined;
  const redirect = env.NEXT_PUBLIC_WORKOS_REDIRECT_URI?.trim();
  if (!redirect) return undefined;
  try {
    return new URL(redirect).origin;
  } catch {
    return undefined;
  }
}

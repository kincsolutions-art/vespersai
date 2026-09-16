/**
 * Next.js 16 uses proxy.ts where earlier versions used middleware.ts.
 * authkitProxy keeps the sealed session cookie fresh on matched routes.
 *
 * The configuration policy is shared with the pages (`lib/auth-config`) rather
 * than re-derived here, so the proxy cannot refresh a session on a deployment
 * the pages consider unfit to authenticate — a production build with an http
 * redirect URI, for instance.
 *
 * When authentication is not permitted the proxy passes through instead of
 * throwing: an unconfigured deployment must still serve the landing page and
 * health checks, and render the "sign-in unavailable" state on protected pages.
 */
import { authkitProxy } from "@workos-inc/authkit-nextjs";
import { NextResponse } from "next/server";
import { authConfigProblem } from "./lib/auth-config";

export default authConfigProblem() === null
  ? authkitProxy()
  : () => NextResponse.next();

export const config = {
  matcher: ["/", "/account", "/api/account"],
};

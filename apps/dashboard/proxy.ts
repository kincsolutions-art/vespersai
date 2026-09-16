/**
 * Next.js 16 uses proxy.ts where earlier versions used middleware.ts.
 * authkitProxy keeps the sealed session cookie fresh on matched routes.
 *
 * When WorkOS is not configured the proxy passes through instead of throwing:
 * an unconfigured deployment must still serve the landing page and health
 * checks, and render the "sign-in unavailable" state on protected pages.
 */
import { authkitProxy } from "@workos-inc/authkit-nextjs";
import { NextResponse } from "next/server";

const configured = Boolean(
  process.env.WORKOS_CLIENT_ID &&
  process.env.WORKOS_API_KEY &&
  process.env.WORKOS_COOKIE_PASSWORD &&
  process.env.NEXT_PUBLIC_WORKOS_REDIRECT_URI,
);

export default configured ? authkitProxy() : () => NextResponse.next();

export const config = {
  matcher: ["/", "/account", "/api/account"],
};

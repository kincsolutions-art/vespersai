/**
 * Sign-in entry point.
 *
 * This lives in a Route Handler, not a Server Component: getSignInUrl() stores
 * the PKCE/state material in a cookie, and Next.js only permits cookie writes
 * from Route Handlers and Server Actions.
 */
import { getSignInUrl } from "@workos-inc/authkit-nextjs";
import { NextRequest, NextResponse } from "next/server";
import { safeReturnPath } from "../../../lib/redirects";
import { isConfigured } from "../../../lib/config";

export async function GET(request: NextRequest): Promise<NextResponse> {
  if (!isConfigured()) {
    return NextResponse.json(
      { error: "authentication-not-configured" },
      { status: 503 },
    );
  }
  // Redirect policy applied before the value can influence any redirect.
  const returnPathname = safeReturnPath(
    request.nextUrl.searchParams.get("return_to"),
  );
  const signInUrl = await getSignInUrl({ returnTo: returnPathname });
  return NextResponse.redirect(signInUrl);
}

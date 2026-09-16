/**
 * BFF proxy: browser talks to this route with the sealed session cookie; this
 * route talks to FastAPI with a bearer token. The access token is attached
 * server-side and never serialised into the page.
 */
import { withAuth } from "@workos-inc/authkit-nextjs";
import { NextRequest, NextResponse } from "next/server";
import { API_BASE, allowedOrigins } from "../../../lib/config";

function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  // Browsers omit Origin on same-origin GETs; require it only where it matters.
  if (!origin) return request.method === "GET";
  return allowedOrigins().includes(origin);
}

export async function GET(): Promise<NextResponse> {
  const { accessToken } = await withAuth();
  if (!accessToken) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const response = await fetch(`${API_BASE}/api/account`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  const body: unknown = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  // CSRF: this route is reachable with an ambient cookie, so a cross-site form
  // post must be refused before anything else happens.
  if (!sameOrigin(request)) {
    return NextResponse.json(
      { error: "cross-origin-rejected" },
      { status: 403 },
    );
  }
  const { accessToken } = await withAuth();
  if (!accessToken) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  const response = await fetch(`${API_BASE}/api/account/provision`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  const body: unknown = await response.json().catch(() => ({}));
  return NextResponse.json(body, { status: response.status });
}

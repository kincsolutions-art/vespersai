/**
 * AuthKit callback. The SDK performs the code exchange plus the state/PKCE
 * handling supported by the pinned version, then seals the session into an
 * encrypted HttpOnly cookie. No token reaches browser storage.
 */
import { handleAuth } from "@workos-inc/authkit-nextjs";
import { DEFAULT_RETURN_PATH } from "../../../lib/redirects";
import { API_BASE, appOrigin } from "../../../lib/config";

export const GET = handleAuth({
  returnPathname: DEFAULT_RETURN_PATH,
  // Without this the SDK redirects to the address the server binds to
  // (0.0.0.0), not the one the browser used, and the session cookie is lost.
  baseURL: appOrigin(),
  onSuccess: async ({ accessToken }: { accessToken: string }) => {
    // First-sign-in provisioning. The backend independently re-validates this
    // token and reads the verified email from WorkOS, so this call asserts
    // no identity of its own. It is idempotent, so retries are safe.
    //
    // A failure here must not fail the callback: the session is already valid,
    // and /account re-checks and renders the real state. But it must not vanish
    // either — a silent failure here is exactly how an account gets stranded at
    // `not-provisioned` with nothing in any log to say why. Status and a fixed
    // category only: the response body can echo request content, and the bearer
    // token is never written anywhere.
    try {
      const response = await fetch(`${API_BASE}/api/account/provision`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        cache: "no-store",
      });
      if (!response.ok) {
        console.warn(
          JSON.stringify({
            event: "callback_provisioning_rejected",
            status: response.status,
          }),
        );
      }
    } catch (error) {
      console.warn(
        JSON.stringify({
          event: "callback_provisioning_unreachable",
          reason: error instanceof Error ? error.name : "unknown",
        }),
      );
    }
  },
});

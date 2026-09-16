/**
 * AuthKit callback. The SDK performs the code exchange plus the state/PKCE
 * handling supported by the pinned version, then seals the session into an
 * encrypted HttpOnly cookie. No token reaches browser storage.
 */
import { handleAuth } from "@workos-inc/authkit-nextjs";
import { DEFAULT_RETURN_PATH } from "../../../lib/redirects";
import { API_BASE } from "../../../lib/config";

export const GET = handleAuth({
  returnPathname: DEFAULT_RETURN_PATH,
  onSuccess: async ({ accessToken }: { accessToken: string }) => {
    // First-sign-in provisioning. The backend independently re-validates this
    // token and reads the verified email from WorkOS, so this call asserts
    // no identity of its own. It is idempotent, so retries are safe.
    try {
      await fetch(`${API_BASE}/api/account/provision`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        cache: "no-store",
      });
    } catch {
      // Swallowed on purpose: the account page re-checks and renders the real
      // access-denied or unavailable state rather than failing the callback.
    }
  },
});

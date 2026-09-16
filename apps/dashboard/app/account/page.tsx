import Link from "next/link";
import { withAuth, signOut } from "@workos-inc/authkit-nextjs";
import { API_BASE, isConfigured } from "../../lib/config";

export const dynamic = "force-dynamic";

type Account = {
  email: string;
  tenant_id: string;
  tenant_name: string;
  tenant_kind: string;
  role: string;
};

/** Distinguishes "not signed in", "signed in but denied", and "backend down". */
async function loadAccount(
  accessToken: string,
): Promise<{ status: number; account: Account | null; detail: string }> {
  try {
    const response = await fetch(`${API_BASE}/api/account`, {
      headers: { Authorization: `Bearer ${accessToken}` },
      cache: "no-store",
    });
    if (response.ok) {
      return {
        status: 200,
        account: (await response.json()) as Account,
        detail: "",
      };
    }
    const body = (await response.json().catch(() => ({}))) as {
      detail?: string;
    };
    return {
      status: response.status,
      account: null,
      detail: body.detail ?? "",
    };
  } catch {
    return { status: 503, account: null, detail: "backend-unreachable" };
  }
}

export default async function AccountPage() {
  if (!isConfigured()) {
    return (
      <main className="auth">
        <h1>Sign-in unavailable</h1>
        <p>
          WorkOS is not configured on this server. See docs/authentication.md.
        </p>
      </main>
    );
  }

  const { user, accessToken } = await withAuth();
  if (!user || !accessToken) {
    return (
      <main className="auth">
        <h1>Your session has ended</h1>
        <p>Sign in again to reach your account.</p>
        <Link className="button" href="/auth/sign-in?return_to=/account">
          Sign in
        </Link>
      </main>
    );
  }

  const { status, account, detail } = await loadAccount(accessToken);

  return (
    <main className="auth">
      <h1>Account</h1>
      {account ? (
        <dl>
          <dt>Signed in as</dt>
          <dd>{account.email}</dd>
          <dt>Personal tenant</dt>
          <dd>{account.tenant_name}</dd>
          <dt>Tenant ID</dt>
          <dd>
            <code>{account.tenant_id}</code>
          </dd>
          <dt>Role</dt>
          <dd>{account.role}</dd>
        </dl>
      ) : status === 403 ? (
        <>
          <h2>Access denied</h2>
          <p>
            This account is authenticated but not permitted to use Vespers
            {detail ? ` (${detail})` : ""}. Access is limited to the backend
            signup allowlist.
          </p>
        </>
      ) : status === 409 ? (
        <>
          <h2>Account conflict</h2>
          <p>
            This sign-in could not be bound to an account
            {detail ? ` (${detail})` : ""}. Conflicts are never resolved
            automatically, because merging accounts on a shared email address is
            how one identity takes over another. An administrator must resolve
            it.
          </p>
        </>
      ) : status === 401 ? (
        <>
          <h2>Session expired</h2>
          <p>Sign out and sign in again.</p>
        </>
      ) : (
        <>
          <h2>Temporarily unavailable</h2>
          <p>Your account could not be loaded. Please try again shortly.</p>
        </>
      )}
      <form
        action={async () => {
          "use server";
          await signOut();
        }}
      >
        <button type="submit">Sign out</button>
      </form>
    </main>
  );
}

import assert from "node:assert/strict";
import test from "node:test";
import {
  appOrigin,
  authConfigProblem,
  deploymentEnvironment,
} from "./auth-config.ts";

const SECRETS = {
  WORKOS_CLIENT_ID: "client_synthetic",
  WORKOS_API_KEY: "sk_test_synthetic",
  WORKOS_COOKIE_PASSWORD: "synthetic-cookie-password-at-least-32-chars",
};

const production = (redirect: string) => ({
  ...SECRETS,
  VESPERS_ENVIRONMENT: "production",
  NEXT_PUBLIC_WORKOS_REDIRECT_URI: redirect,
});

test("a production configuration with an https callback is accepted", () => {
  const env = production("https://vespers.example/auth/callback");
  assert.equal(authConfigProblem(env), null);
  assert.equal(appOrigin(env), "https://vespers.example");
});

test("a production configuration with an http callback is rejected", () => {
  for (const redirect of [
    "http://vespers.example/auth/callback",
    // A hostname is not evidence about the deployment. Production means the
    // session cookie must be Secure, wherever it is served from.
    "http://localhost:3000/auth/callback",
    "http://127.0.0.1:3000/auth/callback",
  ]) {
    const env = production(redirect);
    assert.equal(
      authConfigProblem(env),
      "redirect-uri-not-https",
      `expected rejection for ${redirect}`,
    );
    // No origin escapes a rejected configuration into a redirect.
    assert.equal(appOrigin(env), undefined);
  }
});

test("local development may use plain http, explicitly", () => {
  for (const environment of ["development", "test", undefined]) {
    const env = {
      ...SECRETS,
      ...(environment ? { VESPERS_ENVIRONMENT: environment } : {}),
      NEXT_PUBLIC_WORKOS_REDIRECT_URI: "http://localhost:3000/auth/callback",
    };
    assert.equal(
      authConfigProblem(env),
      null,
      `expected http to be permitted for ${environment ?? "(unset)"}`,
    );
    assert.equal(appOrigin(env), "http://localhost:3000");
  }
});

test("an unknown environment fails closed rather than defaulting to permissive", () => {
  const env = {
    ...SECRETS,
    VESPERS_ENVIRONMENT: "prod",
    NEXT_PUBLIC_WORKOS_REDIRECT_URI: "http://localhost:3000/auth/callback",
  };
  assert.equal(deploymentEnvironment(env), null);
  assert.equal(authConfigProblem(env), "environment-invalid");
});

test("missing WorkOS configuration withholds sign-in without erroring", () => {
  // The supported unconfigured state: no secrets at all. Nothing throws, so the
  // landing page, /api/health and an offline `next build` keep working; only
  // authentication is withheld.
  assert.equal(authConfigProblem({}), "authentication-not-configured");
  assert.equal(appOrigin({}), undefined);
  assert.equal(
    authConfigProblem({ VESPERS_ENVIRONMENT: "production" }),
    "authentication-not-configured",
  );
  for (const missing of Object.keys(SECRETS)) {
    const partial: Record<string, string> = { ...SECRETS };
    delete partial[missing];
    assert.equal(
      authConfigProblem({
        ...partial,
        NEXT_PUBLIC_WORKOS_REDIRECT_URI:
          "https://vespers.example/auth/callback",
      }),
      "authentication-not-configured",
      `expected sign-in to be withheld without ${missing}`,
    );
  }
});

test("a blank value is not a configured value", () => {
  assert.equal(
    authConfigProblem({
      ...SECRETS,
      WORKOS_API_KEY: "   ",
      NEXT_PUBLIC_WORKOS_REDIRECT_URI: "https://vespers.example/auth/callback",
    }),
    "authentication-not-configured",
  );
});

test("a missing or unusable callback URL is refused", () => {
  assert.equal(authConfigProblem({ ...SECRETS }), "redirect-uri-missing");
  assert.equal(
    authConfigProblem({ ...SECRETS, NEXT_PUBLIC_WORKOS_REDIRECT_URI: "  " }),
    "redirect-uri-missing",
  );
  for (const hostile of [
    "/auth/callback",
    "vespers.example/auth/callback",
    "javascript:alert(1)",
    "file:///etc/passwd",
  ]) {
    assert.equal(
      authConfigProblem({
        ...SECRETS,
        NEXT_PUBLIC_WORKOS_REDIRECT_URI: hostile,
      }),
      "redirect-uri-invalid",
      `expected rejection for ${hostile}`,
    );
  }
});

test("no problem label carries a configured value", () => {
  const labels = [
    authConfigProblem({}),
    authConfigProblem({ ...SECRETS }),
    authConfigProblem(production("http://localhost:3000/auth/callback")),
    authConfigProblem({
      ...SECRETS,
      VESPERS_ENVIRONMENT: "prod",
      NEXT_PUBLIC_WORKOS_REDIRECT_URI: "https://vespers.example/auth/callback",
    }),
  ];
  for (const label of labels) {
    assert.ok(label !== null);
    for (const secret of Object.values(SECRETS)) {
      assert.ok(!label.includes(secret), `label leaked a value: ${label}`);
    }
    assert.ok(!label.includes("vespers.example"));
    assert.ok(!label.includes("localhost"));
  }
});

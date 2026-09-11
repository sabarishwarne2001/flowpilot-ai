/**
 * ARCH-30 Tranche 1 (T4-F4) — enterprise SSO discovery.
 *
 * `GET /api/v1/sso/discover` declares no `response_model`, so this shape sits
 * OUTSIDE the OpenAPI contract gate (`verify-api-contracts.mjs`). It is written
 * by hand against `discover` in backend/app/api/v1/saml.py. Adding a response
 * model there, regenerating `api.generated.d.ts`, and deriving this type from
 * it is the right follow-up; it is not done in this tranche because it changes
 * the published schema of an unauthenticated endpoint.
 *
 * A discriminated union rather than one interface with optional fields: the
 * login page must not be able to read `display_name` without first proving
 * `sso_enabled` is true, and with `exactOptionalPropertyTypes` the optional
 * form would let an absent field and an explicit undefined drift apart.
 */
export type SsoDiscovery =
  | {
      readonly sso_enabled: false;
    }
  | {
      readonly sso_enabled: true;
      /** `IdpProtocol` value from the backend, e.g. "SAML2". Informational. */
      readonly protocol: string;
      /** The IdP configuration's display name, e.g. "Acme Okta". */
      readonly display_name: string;
      /** Present for API compatibility; the SPA does not follow it. */
      readonly start_url: string;
    };

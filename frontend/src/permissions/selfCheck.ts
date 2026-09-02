/**
 * Development-only parity assertions for the permission mirror.
 *
 * Every assertion below is transcribed from the verification suite that gated
 * ARCH-01 backend Step 6. If this file passes and that suite passes, the two
 * implementations agree on every rule either one considers load-bearing.
 *
 * WHY THIS EXISTS: the whole risk of mirroring server logic on the client is
 * silent drift. A helper that disagrees with the server produces buttons that
 * 403, and nobody notices until a user reports it — because nothing fails, the
 * UI is simply subtly wrong. This turns that silent failure into a console
 * error the moment someone edits a table.
 *
 * Stripped from production bundles: the only caller is guarded by
 * import.meta.env.DEV, which Vite statically evaluates and tree-shakes.
 */

import type { OrganizationRole, WorkspaceRole } from "@/types/tenancy";
import * as org from "@/permissions/organizationPermissions";
import * as ws from "@/permissions/workspacePermissions";

const ORG_ROLES: OrganizationRole[] = ["OWNER", "ADMIN", "BILLING", "MEMBER"];

/**
 * Runs every parity assertion.
 *
 * @returns A list of failure descriptions. Empty means the mirror is intact.
 */
export const runPermissionSelfCheck = (): string[] => {
  const failures: string[] = [];

  const expect = (label: string, condition: boolean): void => {
    if (!condition) {
      failures.push(label);
    }
  };

  /* --- Effective role derivation: the core of ARCH-01 ------------------- */
  expect(
    "org OWNER derives workspace ADMIN with no explicit grant",
    ws.resolveEffectiveWorkspaceRole("OWNER", null) === "ADMIN",
  );
  expect(
    "org ADMIN derives workspace ADMIN with no explicit grant",
    ws.resolveEffectiveWorkspaceRole("ADMIN", null) === "ADMIN",
  );
  expect(
    "derived elevation overrides a weaker explicit grant",
    ws.resolveEffectiveWorkspaceRole("OWNER", "VIEWER") === "ADMIN",
  );
  expect(
    "org MEMBER falls through to the explicit grant",
    ws.resolveEffectiveWorkspaceRole("MEMBER", "CONTRIBUTOR") === "CONTRIBUTOR",
  );
  expect(
    "org MEMBER with no grant has no access",
    ws.resolveEffectiveWorkspaceRole("MEMBER", null) === null,
  );
  expect(
    "BILLING with no grant has no workspace access",
    ws.resolveEffectiveWorkspaceRole("BILLING", null) === null,
  );
  expect(
    "BILLING with an explicit grant keeps exactly that grant",
    ws.resolveEffectiveWorkspaceRole("BILLING", "VIEWER") === "VIEWER",
  );
  expect(
    "no organization membership means no access, grant or not",
    ws.resolveEffectiveWorkspaceRole(null, "ADMIN") === null,
  );

  /* --- Escalation is closed (backend blocker B5) ------------------------ */
  expect(
    "ADMIN cannot assign OWNER",
    !org.canAssignOrganizationRole("ADMIN", "OWNER"),
  );
  expect(
    "ADMIN cannot manufacture a peer ADMIN",
    !org.canAssignOrganizationRole("ADMIN", "ADMIN"),
  );
  expect(
    "ADMIN may assign MEMBER and BILLING",
    org.canAssignOrganizationRole("ADMIN", "MEMBER") &&
      org.canAssignOrganizationRole("ADMIN", "BILLING"),
  );
  expect(
    "OWNER may assign every role, including ownership transfer",
    ORG_ROLES.every((r) => org.canAssignOrganizationRole("OWNER", r)),
  );
  expect(
    "non-administrators may assign nothing",
    ORG_ROLES.every(
      (r) =>
        !org.canAssignOrganizationRole("MEMBER", r) &&
        !org.canAssignOrganizationRole("BILLING", r),
    ),
  );

  /* --- Peers cannot act on one another ---------------------------------- */
  expect("ADMIN cannot act on ADMIN", !org.canModifyMember("ADMIN", "ADMIN"));
  expect("OWNER cannot act on OWNER", !org.canModifyMember("OWNER", "OWNER"));
  expect("ADMIN cannot act on OWNER", !org.canModifyMember("ADMIN", "OWNER"));
  expect(
    "OWNER acts on ADMIN, ADMIN acts on MEMBER",
    org.canModifyMember("OWNER", "ADMIN") &&
      org.canModifyMember("ADMIN", "MEMBER"),
  );

  /* --- Both halves of a role change are enforced ------------------------ */
  expect(
    "ADMIN may act on MEMBER but not promote to OWNER",
    !org.canModifyMemberRole("ADMIN", "MEMBER", "OWNER"),
  );
  expect(
    "ADMIN may assign MEMBER but not act on an OWNER",
    !org.canModifyMemberRole("ADMIN", "OWNER", "MEMBER"),
  );
  expect(
    "OWNER may promote a MEMBER to ADMIN",
    org.canModifyMemberRole("OWNER", "MEMBER", "ADMIN"),
  );

  /* --- BILLING is orthogonal, not a rung -------------------------------- */
  expect(
    "BILLING sees spend but cannot change the plan",
    org.canViewBilling("BILLING") && !org.canManageBilling("BILLING"),
  );
  expect(
    "BILLING cannot cause spend or administer members",
    !org.canManageSeats("BILLING") && !org.canManageMembers("BILLING"),
  );
  expect(
    "contract authority is OWNER only",
    org.canManageBilling("OWNER") && !org.canManageBilling("ADMIN"),
  );
  expect("MEMBER cannot view billing", !org.canViewBilling("MEMBER"));
  expect(
    "BILLING and MEMBER are precedence peers",
    org.precedence("BILLING") === org.precedence("MEMBER"),
  );

  /* --- Workspace ADMIN grants require organization standing -------------- */
  expect(
    "workspace ADMIN may grant CONTRIBUTOR and VIEWER",
    ws.canAssignWorkspaceRole("MEMBER", "ADMIN", "CONTRIBUTOR") &&
      ws.canAssignWorkspaceRole("MEMBER", "ADMIN", "VIEWER"),
  );
  expect(
    "workspace ADMIN alone may NOT grant workspace ADMIN",
    !ws.canAssignWorkspaceRole("MEMBER", "ADMIN", "ADMIN"),
  );
  expect(
    "organization administrators may grant workspace ADMIN",
    ws.canAssignWorkspaceRole("ADMIN", "ADMIN", "ADMIN") &&
      ws.canAssignWorkspaceRole("OWNER", "ADMIN", "ADMIN"),
  );
  expect(
    "contributors and non-members grant nothing",
    !ws.canAssignWorkspaceRole("MEMBER", "CONTRIBUTOR", "VIEWER") &&
      !ws.canAssignWorkspaceRole("MEMBER", null, "VIEWER"),
  );
  expect(
    "revocation is symmetric with assignment",
    !ws.canModifyWorkspaceMember("MEMBER", "ADMIN", "ADMIN") &&
      ws.canModifyWorkspaceMember("ADMIN", "ADMIN", "ADMIN"),
  );

  /* --- Workspace ladder holds ------------------------------------------- */
  expect(
    "ADMIN satisfies every workspace minimum",
    (["ADMIN", "CONTRIBUTOR", "VIEWER"] as WorkspaceRole[]).every((min) =>
      ws.isAtLeast("ADMIN", min),
    ),
  );
  expect(
    "VIEWER reads but does not write",
    ws.canViewContent("VIEWER") && !ws.canCreateContent("VIEWER"),
  );
  expect(
    "CONTRIBUTOR writes but does not administer",
    ws.canCreateContent("CONTRIBUTOR") &&
      !ws.canManageWorkspaceSettings("CONTRIBUTOR"),
  );

  return failures;
};

/**
 * Runs the self-check and reports failures to the console.
 *
 * Intended to be called once at startup behind import.meta.env.DEV.
 */
export const assertPermissionParity = (): void => {
  const failures = runPermissionSelfCheck();

  if (failures.length === 0) {
     
    console.info("[permissions] parity self-check passed");
    return;
  }

   
  console.error(
    `[permissions] PARITY SELF-CHECK FAILED — ${failures.length} rule(s) ` +
      "disagree with the backend contract:\n  - " +
      failures.join("\n  - "),
  );
};

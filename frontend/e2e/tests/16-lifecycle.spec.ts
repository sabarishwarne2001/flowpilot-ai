/**
 * ARCHIVE AND RESTORE, from the workspace picker ("Choose a workspace").
 *
 * F-131/F-132: archiving was documented as reversible, but an archived workspace vanished from
 * every list and its only Restore button sat on its own (unreachable) settings page, and an
 * archived organization could not be restored at all. The picker now lists both, segmented and
 * badged, with Restore for the people allowed to use it.
 *
 * Each test makes its own workspace or organization so the shared tenants are untouched, and
 * archives it again at the end (archived ones do not count towards the workspace limit).
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { TENANTS, runId } from "../support/env";
import { api, loginAs, resolveWorkspaceId } from "../support/api";

test.use({ user: "C.owner" });

test("an archived workspace is listed for its admins and can be restored from the picker", async ({ page }) => {
  const session = await loginAs("C.owner");
  const { organizationId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);
  const name = `Archive drill ${runId()}`;
  const created = await api<{ id: string; slug: string }>(session, "POST", `/organizations/${organizationId}/workspaces`, {
    workspace_name: name,
  });
  expect(created.status, created.text).toBeLessThan(300);
  const workspaceId = created.body.id;
  expect((await api(session, "POST", `/workspaces/${workspaceId}/archive`)).status).toBe(200);

  try {
    await page.goto("/workspaces");
    const archived = page.getByRole("region", { name: "Archived workspaces in Caretakers Global Inc" });
    const row = archived.getByRole("listitem").filter({ hasText: name });
    await expect(row).toBeVisible();
    await expect(row).toContainText(/archived/i);
    await row.getByRole("button", { name: `Restore ${name}` }).click();
    await expect(page.getByText(`${name} restored.`)).toBeVisible();

    const link = page.getByRole("link", { name: new RegExp(name) });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(new RegExp(`/${TENANTS.C.org}/${created.body.slug}`));
    await expectHealthyPage(page);
  } finally {
    await api(session, "POST", `/workspaces/${workspaceId}/archive`);
  }
});

test("an archived organization is shown apart, and its owner can restore it", async ({ page }) => {
  const session = await loginAs("C.owner");
  const slug = `archive-org-${runId()}`.toLowerCase().slice(0, 40);
  const created = await api<{ id: string; slug: string; name: string }>(session, "POST", "/organizations", {
    organization_name: `Archive Org ${runId()}`,
    organization_slug: slug,
  });
  expect(created.status, created.text).toBeLessThan(300);
  const organizationId = created.body.id;
  const archive = await api(session, "POST", `/organizations/${organizationId}/archive`, { confirm_slug: created.body.slug });
  expect(archive.status, archive.text).toBe(200);

  try {
    await page.goto("/workspaces");
    const section = page.getByRole("region", { name: "Archived organizations" });
    const card = section.getByRole("article").filter({ hasText: created.body.name });
    await expect(card).toBeVisible();
    await expect(card).toContainText(/archived/i);

    await card.getByRole("button", { name: "Restore organization" }).click();
    const confirm = card.getByRole("textbox", { name: "Type the organization's slug to confirm" });
    await confirm.fill(created.body.slug);
    await card.getByRole("button", { name: "Restore", exact: true }).click();
    await expect(page.getByText(`${created.body.name} restored.`)).toBeVisible();
    await expect(section.getByRole("article").filter({ hasText: created.body.name })).toHaveCount(0);
    await expectHealthyPage(page);
  } finally {
    await api(session, "POST", `/organizations/${organizationId}/archive`, { confirm_slug: created.body.slug });
  }
});

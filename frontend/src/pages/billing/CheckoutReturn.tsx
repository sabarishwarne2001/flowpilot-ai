import React, { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";

import { billingKeys } from "@/services/api/queryKeys";
import { organizationBillingPath } from "@/routes/tenantPaths";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";

export const CheckoutReturn: React.FC = () => {
  const [params] = useSearchParams();
  const outcome = params.get("outcome");
  const queryClient = useQueryClient();
  const { organization, organizationId } = useResolvedOrganization();

  const billingPath = organizationBillingPath(organization.organization_slug);

  useEffect(() => {
    void queryClient.invalidateQueries({
      queryKey: billingKeys.all(organizationId),
    });
  }, [queryClient, organizationId]);

  const succeeded = outcome === "success";

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-xl p-4 sm:p-6">
        <div className="rounded-lg border border-border p-6 text-center">
          {succeeded ? (
            <>
              <CheckCircle2 className="mx-auto h-10 w-10 text-primary" />
              <h1 className="mt-3 text-lg font-semibold">Payment received</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">
                Thanks. We&apos;re activating your subscription now — this
                usually takes a few seconds. Your plan and seat count will
                appear on the billing page once it&apos;s confirmed.
              </p>
            </>
          ) : (
            <>
              <XCircle className="mx-auto h-10 w-10 text-muted-foreground" />
              <h1 className="mt-3 text-lg font-semibold">Checkout cancelled</h1>
              <p className="mt-1.5 text-sm text-muted-foreground">
                No payment was taken and nothing has changed. You can pick a
                plan again whenever you&apos;re ready.
              </p>
            </>
          )}

          <Link
            to={billingPath}
            className="mt-5 inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Back to billing
          </Link>
        </div>
      </div>
    </div>
  );
};

export default CheckoutReturn;

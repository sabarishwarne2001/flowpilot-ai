"""Platform-scoped API surface.

Everything under this package reads or writes across tenant boundaries and is
gated by `app.api.deps.require_superadmin`. Nothing here takes an
`organization_id` path parameter, and that is the structural difference from
`app/api/v1/` — a route that scopes to one organization belongs there, behind
`RequireOrgAdmin`, not here.

The gate is applied on the router (`dependencies=[Depends(require_superadmin)]`)
rather than per endpoint, so a route added later cannot be published unguarded
by forgetting a decorator argument.
"""

from app.api.v1.admin import cogs

__all__ = ["cogs"]
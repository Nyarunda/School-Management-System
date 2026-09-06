# Platform Foundation

## Tenant boundary

`Tenant` represents a school. A `User` has no primary school relationship; access is represented by an active `Membership` connecting the user to a tenant and role.

Tenant-owned models inherit `TenantOwnedModel` and expose `objects.for_tenant(tenant)`. Services must receive the tenant explicitly and validate every tenant-owned object before making a change.

This is the first application boundary. API views, background jobs, exports, and reports must use the same boundary rather than filtering by user-supplied identifiers alone.

## Next slice

Add request tenant resolution and audit events, then build student onboarding on top of this foundation.
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from .models import AuditEvent, Membership, Role, User
from .permissions_catalogue import validate_permission_codes

ADMIN_GUARD_PERMISSION = "tenancy.membership.manage"
INVITE_TOKEN_SALT = "apps.tenancy.invite"
INVITE_TOKEN_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

_UNSET = object()


def require_membership(*, user, tenant=None, tenant_slug=None):
    """Return an active membership or reject access to the tenant."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        raise ValidationError("An active authenticated user is required")
    if (tenant is None) == (tenant_slug is None):
        raise ValidationError("Exactly one tenant context is required")
    scope = {"tenant": tenant} if tenant is not None else {"tenant__slug": tenant_slug}
    membership = Membership.objects.filter(
        **scope,
        tenant__is_active=True,
        role__tenant_id=F("tenant_id"),
        user=user,
        user__is_active=True,
        is_active=True,
    ).select_related("role", "tenant").first()
    if membership is None:
        raise ValidationError("User does not have an active membership in this tenant")
    return membership


def require_permission(*, user, permission, tenant=None, tenant_slug=None):
    membership = require_membership(user=user, tenant=tenant, tenant_slug=tenant_slug)
    if not getattr(user, "is_superuser", False) and permission not in membership.role.permissions:
        raise ValidationError(f"User lacks permission: {permission}")
    return membership


def require_same_tenant(*, tenant, **objects):
    """Reject a domain operation when any tenant-owned object crosses boundaries."""
    mismatched = [name for name, value in objects.items() if value.tenant_id != tenant.id]
    if mismatched:
        names = ", ".join(sorted(mismatched))
        raise ValidationError(f"Objects belong to a different tenant: {names}")


# --- Tenant user & role administration (Milestone 22.4) ----------------

def _require_grantable_permissions(*, actor, tenant, permissions):
    """A non-superuser can only grant/assign permissions they themselves
    currently hold in this tenant -- closes the privilege-escalation path
    where a bursar-only admin (holding only ADMIN_GUARD_PERMISSION) could
    otherwise create or assign a role with broader permissions than their
    own. Superusers bypass, matching require_permission's existing
    is_superuser bypass.
    """
    if getattr(actor, "is_superuser", False):
        return
    actor_membership = require_membership(user=actor, tenant=tenant)
    ungranted = set(permissions) - set(actor_membership.role.permissions)
    if ungranted:
        raise ValidationError(f"Cannot grant permission(s) you do not hold yourself: {', '.join(sorted(ungranted))}")


def _ensure_not_removing_last_administrator(*, tenant, excluding_membership=None, role_losing_permission=None):
    """Raises if the change being made would leave zero active memberships
    whose role includes ADMIN_GUARD_PERMISSION. Locks the tenant's active
    memberships (select_for_update) so two concurrent removals of the last
    two administrators can't both pass this check before either commits --
    mirrors the select_for_update idiom already used in apps.finance.services.
    Must be called from inside an existing transaction.atomic() block.
    """
    memberships = Membership.objects.select_for_update().filter(tenant=tenant, is_active=True).select_related("role")
    remaining = 0
    for membership in memberships:
        if excluding_membership is not None and membership.pk == excluding_membership.pk:
            continue
        permissions = membership.role.permissions
        if role_losing_permission is not None and membership.role_id == role_losing_permission.pk:
            permissions = [code for code in permissions if code != ADMIN_GUARD_PERMISSION]
        if ADMIN_GUARD_PERMISSION in permissions:
            remaining += 1
    if remaining == 0:
        raise ValidationError("This change would leave the tenant with no active administrator")


def create_role(*, actor, tenant, name, permissions):
    canonical = validate_permission_codes(permissions)
    _require_grantable_permissions(actor=actor, tenant=tenant, permissions=canonical)
    try:
        with transaction.atomic():
            role = Role.objects.create(tenant=tenant, name=name, permissions=canonical)
            AuditEvent.objects.create(
                tenant=tenant, actor=actor, action="tenancy.role.created", resource_type="Role",
                resource_id=str(role.id), metadata={"name": role.name, "permissions": role.permissions},
            )
            return role
    except IntegrityError as error:
        raise ValidationError("A role with this name already exists") from error


def update_role(*, actor, tenant, role, name=None, permissions=None):
    require_same_tenant(tenant=tenant, role=role)
    canonical = None
    if permissions is not None:
        canonical = validate_permission_codes(permissions)
        # Only additions and removals require the actor's own authorization --
        # a permission already on the role that stays on the role is neutral,
        # regardless of whether the actor holds it themselves. Checking the
        # full requested set here (as create_role/invite_user/update_membership
        # correctly do, since every permission there is genuinely new to the
        # grant) made any role holding so much as one permission outside the
        # actor's own set permanently unmodifiable by that actor -- not even a
        # rename-plus-unrelated-addition could get past the check, since the
        # untouched permission was always present in the full submitted list.
        # Removals are included in "changed" too: letting a restricted actor
        # strip a permission they don't hold themselves isn't privilege
        # escalation in the narrow sense, but it's still unauthorized
        # privilege administration (a form of governance bypass / denial of
        # service against a more-privileged role), so it's rejected the same
        # way an unauthorized addition is.
        current = set(role.permissions)
        requested = set(canonical)
        added = requested - current
        removed = current - requested
        _require_grantable_permissions(actor=actor, tenant=tenant, permissions=added | removed)
    try:
        with transaction.atomic():
            # Lock + last-admin check happen inside the atomic block, right
            # before the role is actually mutated, so a concurrent update
            # can't slip in between the check and the save.
            if canonical is not None and ADMIN_GUARD_PERMISSION in role.permissions and ADMIN_GUARD_PERMISSION not in canonical:
                _ensure_not_removing_last_administrator(tenant=tenant, role_losing_permission=role)
            if name is not None:
                role.name = name
            if canonical is not None:
                role.permissions = canonical
            role.save()
            AuditEvent.objects.create(
                tenant=tenant, actor=actor, action="tenancy.role.updated", resource_type="Role",
                resource_id=str(role.id), metadata={"name": role.name, "permissions": role.permissions},
            )
            return role
    except IntegrityError as error:
        raise ValidationError("A role with this name already exists") from error


def delete_role(*, actor, tenant, role):
    require_same_tenant(tenant=tenant, role=role)
    if Membership.objects.filter(tenant=tenant, role=role).exists():
        raise ValidationError("Cannot delete a role that is currently assigned to a member")
    with transaction.atomic():
        role_id, name = str(role.id), role.name
        role.delete()
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="tenancy.role.deleted", resource_type="Role",
            resource_id=role_id, metadata={"name": name},
        )


def invite_user(*, actor, tenant, email, role, campus=None):
    require_same_tenant(tenant=tenant, role=role)
    if campus is not None:
        require_same_tenant(tenant=tenant, campus=campus)
    _require_grantable_permissions(actor=actor, tenant=tenant, permissions=role.permissions)

    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValidationError("An email address is required")

    # Local import: apps.notifications.services already imports from
    # apps.tenancy.services at module level, so importing it back at this
    # module's top level would be circular. Deferred here instead.
    from apps.notifications.models import NotificationChannel
    from apps.notifications.services import enqueue_notification

    with transaction.atomic():
        user = User.objects.filter(email__iexact=normalized_email).first()
        if user is None:
            user = User(username=normalized_email, email=normalized_email)
            user.set_unusable_password()
            try:
                user.save()
            except IntegrityError as error:
                raise ValidationError("A user with this email already exists under a different address") from error

        try:
            # is_active=False regardless of whether the user is new or
            # already exists elsewhere -- membership only becomes active
            # once the invite is actually accepted (see accept_invite).
            membership = Membership.objects.create(tenant=tenant, user=user, role=role, campus=campus, is_active=False)
        except IntegrityError as error:
            raise ValidationError("This person is already a member of this tenant") from error

        token = signing.dumps({"user_id": str(user.id), "tenant_id": str(tenant.id)}, salt=INVITE_TOKEN_SALT)
        invite_link = f"{settings.PUBLIC_BASE_URL}/accept-invite?token={token}"
        enqueue_notification(
            tenant=tenant, channel=NotificationChannel.EMAIL, message_type="tenancy.user_invited",
            idempotency_key=f"tenancy-invite-{membership.id}", recipient=normalized_email,
            context={"invite_link": invite_link, "tenant_name": tenant.name, "role_name": role.name},
        )
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="tenancy.user.invited", resource_type="Membership",
            resource_id=str(membership.id), metadata={"email": normalized_email, "role": role.name},
        )
    return membership


def accept_invite(*, token, password=None):
    try:
        payload = signing.loads(token, salt=INVITE_TOKEN_SALT, max_age=INVITE_TOKEN_MAX_AGE_SECONDS)
    except signing.SignatureExpired as error:
        raise ValidationError("This invite link has expired") from error
    except signing.BadSignature as error:
        raise ValidationError("This invite link is invalid") from error

    try:
        membership = Membership.objects.select_related("user").get(
            tenant_id=payload.get("tenant_id"), user_id=payload["user_id"],
        )
    except Membership.DoesNotExist as error:
        raise ValidationError("This invite link is no longer valid") from error

    # invite_accepted_at (not is_active, and not the user's global password
    # state) is the single-use replay guard -- it's the one flag that means
    # exactly "this specific membership's invite was accepted", independent
    # of an admin separately activating/deactivating the membership, and
    # independent of the user's password already being usable from another
    # tenant's membership.
    if membership.invite_accepted_at is not None:
        raise ValidationError("This invite has already been accepted")

    user = membership.user
    # A genuinely new user (set_unusable_password() in invite_user) must set
    # a password to accept; an existing user (invited to an additional
    # tenant) already has one from elsewhere and keeps it untouched.
    needs_password = not user.has_usable_password()
    if needs_password:
        if not password:
            raise ValidationError("A password is required to accept this invite")
        validate_password(password, user=user)

    with transaction.atomic():
        if needs_password:
            user.set_password(password)
            user.save(update_fields=["password"])
        membership.invite_accepted_at = timezone.now()
        membership.is_active = True
        membership.save(update_fields=["invite_accepted_at", "is_active"])
        AuditEvent.objects.create(
            tenant_id=membership.tenant_id, actor=user, action="tenancy.user.invite_accepted",
            resource_type="User", resource_id=str(user.id), metadata={},
        )
    return user


def update_membership(*, actor, tenant, membership, role=None, campus=_UNSET):
    require_same_tenant(tenant=tenant, membership=membership)
    updated_fields = []
    with transaction.atomic():
        if role is not None and role.pk != membership.role_id:
            require_same_tenant(tenant=tenant, role=role)
            _require_grantable_permissions(actor=actor, tenant=tenant, permissions=role.permissions)
            losing_admin = (
                ADMIN_GUARD_PERMISSION in membership.role.permissions
                and ADMIN_GUARD_PERMISSION not in role.permissions
            )
            if losing_admin:
                _ensure_not_removing_last_administrator(tenant=tenant, excluding_membership=membership)
            membership.role = role
            updated_fields.append("role")
        if campus is not _UNSET:
            if campus is not None:
                require_same_tenant(tenant=tenant, campus=campus)
            membership.campus = campus
            updated_fields.append("campus")
        if updated_fields:
            membership.save(update_fields=updated_fields)
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="tenancy.membership.updated", resource_type="Membership",
            resource_id=str(membership.id), metadata={"updated_fields": updated_fields},
        )
    return membership


def activate_membership(*, actor, tenant, membership):
    require_same_tenant(tenant=tenant, membership=membership)
    with transaction.atomic():
        membership.is_active = True
        membership.save(update_fields=["is_active"])
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="tenancy.membership.activated", resource_type="Membership",
            resource_id=str(membership.id), metadata={"user": membership.user.get_username()},
        )
    return membership


def deactivate_membership(*, actor, tenant, membership):
    require_same_tenant(tenant=tenant, membership=membership)
    with transaction.atomic():
        if ADMIN_GUARD_PERMISSION in membership.role.permissions:
            _ensure_not_removing_last_administrator(tenant=tenant, excluding_membership=membership)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        AuditEvent.objects.create(
            tenant=tenant, actor=actor, action="tenancy.membership.deactivated", resource_type="Membership",
            resource_id=str(membership.id), metadata={"user": membership.user.get_username()},
        )
    return membership

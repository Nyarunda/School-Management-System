from .models import ActivityEvent


def record_activity(*, tenant, action, resource_type, resource_id, actor=None, metadata=None):
    return ActivityEvent.objects.create(
        tenant=tenant,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata or {},
    )
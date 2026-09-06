from contextvars import ContextVar


active_tenant = ContextVar("active_tenant", default=None)
# School Management System

Multi-tenant school management platform.

## First milestone

The first milestone establishes the platform foundation:

- Schools are tenants.
- Users can belong to multiple schools through memberships.
- Tenant-owned records require an explicit tenant.
- Tenant-scoped queries are available through `for_tenant()`.
- Cross-tenant relationships are rejected by service-level validation.

## Local development

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\base.txt
python manage.py test
```

The frontend is a Vite React TypeScript application in `frontend/`.

To start the containerized development services:

```powershell
docker compose up --build
```
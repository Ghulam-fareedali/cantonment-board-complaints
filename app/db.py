"""Database access with Row-Level Security wired to the current tenant/role.

Every request opens a connection and sets:
    SET app.tenant_id = '<uuid>'
    SET app.role      = 'owner' | 'operator' | 'techdeva_admin'
so the Postgres RLS policies (schema.sql) enforce isolation automatically.
The app connects as the NON-superuser role `techdeva_app` so RLS is not bypassed.
"""
import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://techdeva_app:techdeva@localhost:5432/techdeva",
)


@contextmanager
def tenant_session(tenant_id: str | None, role: str):
    """Yield a cursor scoped to a tenant + role. techdeva_admin passes tenant_id=None."""
    with psycopg.connect(DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # set_config(..., is_local=true) -> scoped to this transaction only
            cur.execute("SELECT set_config('app.tenant_id', %s, true)",
                        (str(tenant_id) if tenant_id else "",))
            cur.execute("SELECT set_config('app.role', %s, true)", (role,))
            yield cur
        conn.commit()

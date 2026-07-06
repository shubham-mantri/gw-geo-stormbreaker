"""m5: unique constraint on billing_invoice(tenant_id, period_start, period_end).

Durably backs the period-close job's check-then-insert idempotency guard
(`handlers.close_billing.handler`, `billing.trigger.run_billing_close_job`): with this constraint a
concurrent or retried close for an already-closed period can no longer race the existence SELECT and
insert a *second* draft for the same period -- the duplicate INSERT now fails the unique constraint
instead of slipping through the gap between check and insert. Mirrors the ORM
`BillingInvoice.__table_args__` (`gw_geo.common.db`).

Uses `op.batch_alter_table` so the one migration runs on both Postgres (native
`ALTER TABLE ... ADD CONSTRAINT`) and SQLite (table copy-and-move), keeping the full 0001->0007 chain
runnable on SQLite too (as `0006` documents).

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_billing_invoice_tenant_period"
_COLUMNS = ["tenant_id", "period_start", "period_end"]


def upgrade() -> None:
    with op.batch_alter_table("billing_invoice") as batch_op:
        batch_op.create_unique_constraint(_CONSTRAINT, _COLUMNS)


def downgrade() -> None:
    with op.batch_alter_table("billing_invoice") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT, type_="unique")

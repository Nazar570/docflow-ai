from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_async_retry_fields"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "documents",
        sa.Column("last_error_class", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "last_error_class")
    op.drop_column("documents", "retry_count")

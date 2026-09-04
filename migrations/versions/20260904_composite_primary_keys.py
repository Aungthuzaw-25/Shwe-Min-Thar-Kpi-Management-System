"""Align association-table primary keys with the ORM.

Revision ID: 20260904_composite_pks
Revises: 20260904_schema_hardening
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260904_composite_pks"
down_revision = "20260904_schema_hardening"
branch_labels = None
depends_on = None





def _has_table(table_name):
    return table_name in inspect(op.get_bind()).get_table_names()


def upgrade():
    # The initial migration accidentally included inherited BaseModel.id in
    # association-table primary keys. Remove only that key component; the id
    # column and all rows remain intact for rollback compatibility.
    for table_name, columns in (
        ("user_departments", ["user_id", "department_id"]),
        ("role_permissions", ["role_id", "permission_id"]),
    ):
        if not _has_table(table_name):
            continue
        bind = op.get_bind()
        pk = inspect(bind).get_pk_constraint(table_name)
        if pk.get("constrained_columns") == columns:
            continue
        name = pk.get("name")
        if name:
            op.drop_constraint(name, table_name, type_="primary")
        op.create_primary_key("pk_" + table_name, table_name, columns)
        op.create_unique_constraint("uq_" + table_name + "_id", table_name, ["id"])


def downgrade():
    for table_name, columns in (
        ("user_departments", ["user_id", "department_id", "id"]),
        ("role_permissions", ["role_id", "permission_id", "id"]),
    ):
        if not _has_table(table_name):
            continue
        bind = op.get_bind()
        pk = inspect(bind).get_pk_constraint(table_name)
        if pk.get("constrained_columns") == columns:
            continue
        name = pk.get("name")
        if name:
            op.drop_constraint(name, table_name, type_="primary")
        op.drop_constraint("uq_" + table_name + "_id", table_name, type_="unique")
        op.create_primary_key("pk_" + table_name + "_legacy", table_name, columns)

"""
ARCH-04 Step 2 exit gate: verify the declarative layer with no database.
"""

from __future__ import annotations

import sys
from collections import Counter

from sqlalchemy.orm import configure_mappers

from app.models import Base

PASS = "  [PASS]"
FAIL = "  [FAIL]"

EXPECTED: dict[str, dict[str, bool]] = {
    "organization_invitations": {
        "id": False,
        "organization_id": False,
        "inviter_id": False,
        "invited_user_id": True,
        "email": False,
        "organization_role": False,
        "status": False,
        "token_hash": False,
        "expires_at": False,
        "accepted_at": True,
        "rejected_at": True,
        "revoked_at": True,
        "revoked_by_id": True,
        "last_sent_at": True,
        "send_count": False,
        "created_at": False,
        "updated_at": False,
    },
    "invitation_workspace_grants": {
        "id": False,
        "invitation_id": False,
        "workspace_id": False,
        "role": False,
        "created_at": False,
        "updated_at": False,
    },
}

EXPECTED_ORGANIZATIONS_ADDITION = {"seat_limit": True}

EXPECTED_CHECK_CONSTRAINTS: dict[str, dict[str, str]] = {
    "organization_invitations": {
        "ck_organization_invitations_role_not_owner": "organization_role <> 'OWNER'",
    },
    "organizations": {
        "ck_organizations_seat_limit_positive": "seat_limit IS NULL OR seat_limit >= 1",
    },
}

CASCADE_FKS: list[tuple[str, str, str]] = [
    ("invitation_workspace_grants", "invitation_id", "organization_invitations"),
    ("invitation_workspace_grants", "workspace_id", "workspaces"),
    ("organization_invitations", "organization_id", "organizations"),
    ("organization_invitations", "inviter_id", "users"),
]


def check_mappers() -> list[str]:
    print("=== 1. Mapper configuration ===")
    try:
        configure_mappers()
    except Exception as exc:
        print(f"{FAIL} configure_mappers() raised: {exc}")
        return [f"configure_mappers() failed: {exc}"]
    print(f"{PASS} configure_mappers() resolved {len(Base.metadata.tables)} tables.")
    return []


def check_index_names() -> list[str]:
    print("\n=== 2. Index name collisions ===")
    failures: list[str] = []
    names: Counter[str] = Counter()
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            names[index.name] += 1

    duplicates = sorted(name for name, count in names.items() if count > 1)
    if duplicates:
        for name in duplicates:
            failures.append(f"Index name declared more than once: {name}")
            print(f"{FAIL} {name} declared {names[name]} times")
    else:
        print(f"{PASS} {len(names)} index names, all distinct.")
    return failures


def check_columns() -> list[str]:
    print("\n=== 3. Columns and nullability — new tables ===")
    failures: list[str] = []

    for table_name, expected in EXPECTED.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            failures.append(f"Table not registered: {table_name}")
            print(f"{FAIL} {table_name} is absent from Base.metadata")
            continue

        actual = {column.name: column.nullable for column in table.columns}
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        if missing:
            failures.append(f"{table_name}: missing {missing}")
            print(f"{FAIL} {table_name} missing columns: {missing}")
        if extra:
            failures.append(f"{table_name}: unexpected {extra}")
            print(f"{FAIL} {table_name} unexpected columns: {extra}")

        for column, nullable in expected.items():
            if column in actual and actual[column] != nullable:
                failures.append(
                    f"{table_name}.{column}: nullable={actual[column]}, "
                    f"expected {nullable}"
                )
                print(
                    f"{FAIL} {table_name}.{column} nullable="
                    f"{actual[column]}, expected {nullable}"
                )

        if not missing and not extra:
            print(f"{PASS} {table_name}: {len(actual)} columns as specified.")

    print("\n=== 3b. organizations.seat_limit ===")
    organizations = Base.metadata.tables.get("organizations")
    if organizations is None:
        failures.append("organizations is absent from Base.metadata")
        print(f"{FAIL} organizations absent")
    else:
        actual_cols = {c.name: c.nullable for c in organizations.columns}
        for column, nullable in EXPECTED_ORGANIZATIONS_ADDITION.items():
            if column not in actual_cols:
                failures.append(f"organizations.{column} missing")
                print(f"{FAIL} organizations.{column} missing")
            elif actual_cols[column] != nullable:
                failures.append(
                    f"organizations.{column}: nullable={actual_cols[column]}, "
                    f"expected {nullable}"
                )
                print(f"{FAIL} organizations.{column} wrong nullability")
            else:
                print(f"{PASS} organizations.{column} present, nullable={nullable}.")

    return failures


def check_constraints() -> list[str]:
    print("\n=== 4. CheckConstraints ===")
    failures: list[str] = []

    for table_name, expected_constraints in EXPECTED_CHECK_CONSTRAINTS.items():
        table = Base.metadata.tables.get(table_name)
        if table is None:
            failures.append(f"Table not registered: {table_name}")
            continue

        actual = {
            c.name: str(c.sqltext) if hasattr(c, "sqltext") else str(c.sqltext)
            for c in table.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }

        for name, expected_sql in expected_constraints.items():
            if name not in actual:
                failures.append(f"{table_name}: missing CHECK {name}")
                print(f"{FAIL} {table_name} missing CHECK {name}")
            elif expected_sql not in actual[name]:
                failures.append(
                    f"{table_name}.{name}: SQL text does not contain "
                    f"'{expected_sql}' (got '{actual[name]}')"
                )
                print(f"{FAIL} {table_name}.{name} unexpected SQL text")
            else:
                print(f"{PASS} {table_name}.{name} present.")

    return failures


def check_cascade_fks() -> list[str]:
    print("\n=== 5. ON DELETE CASCADE ===")
    failures: list[str] = []

    for table_name, column_name, referred_table in CASCADE_FKS:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            failures.append(f"Table not registered: {table_name}")
            continue

        column = table.columns.get(column_name)
        if column is None:
            failures.append(f"{table_name}.{column_name} not registered")
            continue

        fks = [
            fk for fk in column.foreign_keys
            if fk.column.table.name == referred_table
        ]
        if not fks:
            failures.append(
                f"{table_name}.{column_name}: no FK to {referred_table}"
            )
            print(f"{FAIL} {table_name}.{column_name} -> {referred_table} missing")
            continue

        fk = fks[0]
        if (fk.ondelete or "").upper() != "CASCADE":
            failures.append(
                f"{table_name}.{column_name}: ondelete={fk.ondelete!r}, "
                f"expected CASCADE"
            )
            print(
                f"{FAIL} {table_name}.{column_name} ondelete="
                f"{fk.ondelete!r}, expected CASCADE"
            )
        else:
            print(f"{PASS} {table_name}.{column_name} -> {referred_table} CASCADE.")

    return failures


def main() -> int:
    failures: list[str] = []
    failures += check_mappers()
    failures += check_index_names()
    failures += check_columns()
    failures += check_constraints()
    failures += check_cascade_fks()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("All declarative-layer checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
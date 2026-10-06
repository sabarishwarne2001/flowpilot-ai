"""
ARCH-03 Step 2 exit gate — model declarations.

Step 2 writes no migration and touches no data, so the only thing that can be
verified is that the declarative layer is internally consistent and says what
the plan intends. That is worth verifying precisely because it is cheap here
and expensive in Step 3: a duplicate index name or a mistyped column is a
migration that fails halfway through, and this phase's migrations run against
a database with live invitations in it.

Checks, in the order a mistake becomes harder to see:

  1. configure_mappers() resolves every relationship in the whole registry.
  2. No index name is emitted twice anywhere in the metadata. A column marked
     index=True whose name collides with an explicit Index in __table_args__
     produces two CREATE INDEX statements with one name; SQLAlchemy allows the
     declaration and Postgres rejects the second statement.
  3. Every table this step adds or alters has exactly the expected columns.
  4. Nullability matches the plan, including the two places where NULL is a
     meaningful value rather than an oversight.

Usage:
    python -m scripts.verify_models_step2
"""

from __future__ import annotations

import sys
from collections import Counter

from sqlalchemy.orm import configure_mappers

from app.models import Base

PASS = "  [PASS]"
FAIL = "  [FAIL]"

# Column name -> expected nullability, for the tables this step introduces or
# changes. Mixin columns (id, created_at, updated_at) are included because
# their presence is what proves the mixins were applied.
EXPECTED: dict[str, dict[str, bool]] = {
    "users": {
        "id": False,
        "email": False,
        "hashed_password": False,
        "is_active": False,
        "is_superuser": False,
        # NULL is "unverified" and remains a permitted value forever. This is
        # the one place the plan text was wrong: Step 5 must NOT add NOT NULL
        # here, or no account can ever be registered in an unverified state.
        "email_verified_at": True,
        # NULL is "no global revocation has ever occurred".
        "sessions_revoked_at": True,
        "created_at": False,
        "updated_at": False,
    },
    "auth_tokens": {
        "id": False,
        "user_id": False,
        "purpose": False,
        "token_hash": False,
        "expires_at": False,
        "consumed_at": True,
        "invalidated_at": True,
        "invalidated_reason": True,
        "requested_ip": True,
        "requested_user_agent": True,
        "created_at": False,
        "updated_at": False,
    },
    "sessions": {
        "id": False,
        "user_id": False,
        "family_id": False,
        "token_hash": False,
        "expires_at": False,
        "last_used_at": True,
        "rotated_at": True,
        "replaced_by_id": True,
        "revoked_at": True,
        "revoked_reason": True,
        "ip_address": True,
        "user_agent": True,
        "created_at": False,
        "updated_at": False,
    },
}

# workspace_invitations is checked separately: only the two token columns
# matter here, and the rest of the table is ARCH-01/02 territory.
INVITATION_TOKEN_COLUMNS: dict[str, bool] = {
    "token": False,       # still NOT NULL; dropped at CONTRACT
    "token_hash": True,   # nullable only until CONTRACT
}

REQUIRED_UNIQUE_HASHES: tuple[tuple[str, str], ...] = (
    ("auth_tokens", "token_hash"),
    ("sessions", "token_hash"),
    ("workspace_invitations", "token_hash"),
)


def check_mappers() -> list[str]:
    print("\n=== 1. Mapper configuration ===")
    try:
        configure_mappers()
    except Exception as exc:  # noqa: BLE001 — the message is the whole point
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
    print("\n=== 3. Columns and nullability ===")
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

    invitations = Base.metadata.tables.get("workspace_invitations")
    if invitations is None:
        failures.append("workspace_invitations is absent from Base.metadata")
        print(f"{FAIL} workspace_invitations absent")
    else:
        actual = {c.name: c.nullable for c in invitations.columns}
        for column, nullable in INVITATION_TOKEN_COLUMNS.items():
            if column not in actual:
                failures.append(f"workspace_invitations.{column} missing")
                print(f"{FAIL} workspace_invitations.{column} missing")
            elif actual[column] != nullable:
                failures.append(
                    f"workspace_invitations.{column}: nullable={actual[column]}, "
                    f"expected {nullable}"
                )
                print(f"{FAIL} workspace_invitations.{column} nullability wrong")
            else:
                print(f"{PASS} workspace_invitations.{column}")

    return failures


def check_unique_hashes() -> list[str]:
    print("\n=== 4. Token hash uniqueness ===")
    failures: list[str] = []

    for table_name, column_name in REQUIRED_UNIQUE_HASHES:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            continue

        column = table.columns.get(column_name)
        if column is None:
            failures.append(f"{table_name}.{column_name} missing")
            print(f"{FAIL} {table_name}.{column_name} missing")
            continue

        unique = column.unique is True or any(
            index.unique and list(index.columns) == [column]
            for index in table.indexes
        )
        if unique:
            print(f"{PASS} {table_name}.{column_name} is unique.")
        else:
            failures.append(f"{table_name}.{column_name} is not unique")
            print(f"{FAIL} {table_name}.{column_name} is not unique")

    return failures


def main() -> int:
    print("ARCH-03 Step 2 gate — model declarations")

    failures = check_mappers()
    if failures:
        # Nothing downstream is meaningful if the registry did not resolve.
        print("\nGATE FAILED — mappers did not configure.")
        return 1

    failures += check_index_names()
    failures += check_columns()
    failures += check_unique_hashes()

    print("\n" + "=" * 60)
    if failures:
        print(f"GATE FAILED — {len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("GATE PASSED — declarative layer matches the ARCH-03 Step 2 spec.")
    print("No migration has been written. The database is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

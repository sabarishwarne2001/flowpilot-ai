from __future__ import annotations

import enum


class EmailEncryption(str, enum.Enum):
    """SMTP transport encryption, shared by every mail configuration.

    The Postgres enum `email_encryption` backs `organization_email_settings.encryption`.

    Campaign session 4: the workspace `email_settings` table this module was named after had no
    reader since ARCH-40 moved workspace mail to `workspace_email_overrides`; its model is gone
    and migration `s4b1_drop_email_settings` drops the table. The enum stays here so its import
    path, and the OpenAPI schema name derived from it, do not change.
    """

    NONE = "NONE"
    TLS = "TLS"
    SSL = "SSL"

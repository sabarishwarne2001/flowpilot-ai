"""ARCH-50 — the public keys a licence or a release must be signed by.

ARCH50-S1:licence-keys

PINNED IN CODE, NOT CONFIGURED. If an operator could add a key through the environment or the database, anyone
running the sovereign edition could sign their own licence. The keys live in the release itself, and the
release's own SHA256SUMS (signed by a RELEASE key) covers this file, so replacing a key breaks the release
signature.

Each entry: key id (from `app.core.signing.key_id`) -> public key (base64, raw 32 bytes), what it signs, and
whether it is a production key. A NON-production key's signatures are refused when ENVIRONMENT=production, so a
development key can live here without being able to license a production deployment.

EMPTY IN THE REPOSITORY. The owner generates FlowPilot's keys once, offline, and pins the public halves:

    python scripts/licence_tool.py keygen --out <private-key-file>            # keep the file OFF this machine
    python scripts/licence_tool.py trust --public-key <b64> --purpose licence --production
    python scripts/licence_tool.py trust --public-key <b64> --purpose release --production

`trust` rewrites the two dictionaries below (and nothing else in this file).
"""

from __future__ import annotations

from typing import TypedDict


class TrustedKey(TypedDict):
    public_key: str
    production: bool
    label: str


# BEGIN TRUSTED LICENCE KEYS
TRUSTED_LICENCE_KEYS: dict[str, TrustedKey] = {}
# END TRUSTED LICENCE KEYS

# BEGIN TRUSTED RELEASE KEYS
TRUSTED_RELEASE_KEYS: dict[str, TrustedKey] = {}
# END TRUSTED RELEASE KEYS

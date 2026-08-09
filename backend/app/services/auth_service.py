"""
Authentication and account enrolment business logic for FlowPilot AI.

REGISTRATION DOES NOT REVEAL WHETHER AN ACCOUNT EXISTS
------------------------------------------------------
Until ARCH-03 Step 10 this module raised ValueError("Email already registered")
and the router turned it into a 400. That is an account-enumeration oracle:
paste a list of addresses into the sign-up form and learn which of them have
accounts here. It sat directly beside /auth/forgot-password, which goes to
considerable trouble to avoid exactly that — same information, one form over.

So registration now returns a result rather than raising, and the router
answers identically in both branches. Something still happens in each: a new
address gets a verification link, an existing one gets a notice saying somebody
tried to sign up with it. Both land in the same mailbox, which the person
making the request may well not be able to read.

TIMING IS PART OF THE RESPONSE
------------------------------
Hashing a password with bcrypt takes on the order of a quarter of a second, and
skipping it when the account already exists would make the two branches
distinguishable by a stopwatch — an oracle that survives every identical byte in
the body. The hash is therefore computed in both branches and discarded in one.

WHAT THIS COSTS
---------------
A user who mistypes their address gets no immediate feedback, because there is
no longer anything to give them. They will notice when no email arrives. That
trade is the standard one and it is the right way round: a typo costs one
person a minute, an enumeration oracle costs every user on the platform.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Union

from sqlalchemy.orm import Session

from app import crud
from app.core import security
from app.models.user import User
from app.schemas.auth import UserRegister

logger = logging.getLogger("app.services.auth")


@dataclass(frozen=True)
class RegistrationOutcome:
    """
    What a registration attempt actually did.

    Never returned to the client. The router uses it to decide which email to
    queue and then answers the same way regardless — if this ever reaches a
    response body, the enumeration oracle is back.
    """

    #: None when the address was already taken. The existing account is
    #: deliberately not returned: nothing downstream has any business acting on
    #: an account the requester has not authenticated as.
    user: Union[User, None]
    created: bool
    email: str


def register_new_user(
    db: Session, *, user_in: UserRegister
) -> RegistrationOutcome:
    """
    Creates an account, or reports that the address was already taken.

    Does not raise on a duplicate. The caller cannot distinguish the two
    outcomes in what it returns to the client, only in which background email
    it queues.

    The password is hashed before the existence check and in both branches, so
    the two paths do the same work. Moving the check first would be the obvious
    optimisation and would reintroduce the timing oracle.
    """
    normalized = (user_in.email or "").strip().lower()

    # Computed first, and unconditionally. See the module docstring.
    hashed_password: str = security.get_password_hash(user_in.password)

    existing_user = crud.get_user_by_email(db, email=normalized)
    if existing_user is not None:
        # The hash is discarded. That is the point: the work happened.
        logger.info(
            "REGISTRATION_DUPLICATE | user=%s | notice queued", existing_user.id
        )
        return RegistrationOutcome(user=None, created=False, email=normalized)

    new_user: User = crud.create_user(
        db, email=normalized, hashed_password=hashed_password
    )
    logger.info("REGISTRATION_CREATED | user=%s", new_user.id)
    return RegistrationOutcome(user=new_user, created=True, email=normalized)


def authenticate_user(
    db: Session, *, email: str, password: str
) -> Union[User, None]:
    """
    Verifies user credentials during sign-in attempts.

    Returns the loaded User record if validation succeeds, or None if the
    credentials are mismatched, expired, or incorrect.

    Unlike registration, this endpoint is *expected* to distinguish success
    from failure — that is what signing in means. What it must not distinguish
    is "no such account" from "wrong password", and returning None for both is
    what keeps those together. The dummy verify below makes them take the same
    time as well: without it, a missing account returns before bcrypt runs.
    """
    normalized = (email or "").strip().lower()
    user = crud.get_user_by_email(db, email=normalized)

    if not user:
        # Verify against a throwaway hash so a missing account costs the same
        # as a wrong password. The result is ignored.
        security.verify_password(password, _DUMMY_HASH)
        return None

    if not security.verify_password(password, user.hashed_password):
        return None

    return user


#: A real bcrypt hash of a value nothing will ever submit, used to equalise the
#: cost of a failed lookup in authenticate_user. Computed once at import.
_DUMMY_HASH: str = security.get_password_hash(
    "flowpilot-timing-equaliser-not-a-real-password"
)
"""N-017 — the signed-in user's two-factor sign-in settings (`/me/mfa`).

Turning it on is two calls: `setup` (password) returns the secret and the `otpauth://`
URI for the QR code; `confirm` (a code from the app) switches it on and returns ten
recovery codes, shown once. Turning it off needs the password and a code, so a
borrowed session cannot remove it. The sign-in side is in auth.py (`/auth/login/mfa`).
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Response, status

from app.api import deps
from app.core import security
from app.schemas.mfa import (
    MfaCodeRequest,
    MfaDisableRequest,
    MfaRecoveryCodesResponse,
    MfaSetupRequest,
    MfaSetupResponse,
    MfaStatusResponse,
)
from app.services import mfa_service

router = APIRouter(tags=["Me"])


def _raise(exc: mfa_service.MfaError) -> NoReturn:
    raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


def _check_password(user: Any, password: str) -> None:
    if not user.hashed_password or not security.verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PASSWORD_INCORRECT", "message": "Your current password is not right."},
        )


@router.get("/me/mfa", response_model=MfaStatusResponse, summary="Two-factor sign-in status")
def get_mfa_status(db: deps.DbSession, current_user: deps.CurrentUser) -> Any:
    return mfa_service.status(db, user=current_user)


@router.post("/me/mfa/setup", response_model=MfaSetupResponse, summary="Start turning on two-factor sign-in")
def start_mfa_setup(payload: MfaSetupRequest, db: deps.DbSession, current_user: deps.CurrentUser) -> Any:
    _check_password(current_user, payload.password)
    try:
        enrolment = mfa_service.start(db, user=current_user)
    except mfa_service.MfaError as exc:
        _raise(exc)
    db.commit()
    return {"secret": enrolment.secret, "otpauth_uri": enrolment.otpauth_uri}


@router.post("/me/mfa/confirm", response_model=MfaRecoveryCodesResponse, summary="Turn on two-factor sign-in")
def confirm_mfa(payload: MfaCodeRequest, db: deps.DbSession, current_user: deps.CurrentUser) -> Any:
    try:
        codes = mfa_service.confirm(db, user=current_user, code=payload.code)
    except mfa_service.MfaError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return {"recovery_codes": codes}


@router.post(
    "/me/mfa/recovery-codes",
    response_model=MfaRecoveryCodesResponse,
    summary="Replace the recovery codes",
)
def regenerate_recovery_codes(
    payload: MfaCodeRequest, db: deps.DbSession, current_user: deps.CurrentUser
) -> Any:
    try:
        codes = mfa_service.regenerate_recovery_codes(db, user=current_user, code=payload.code)
    except mfa_service.MfaError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return {"recovery_codes": codes}


@router.post("/me/mfa/disable", status_code=status.HTTP_204_NO_CONTENT, summary="Turn off two-factor sign-in")
def disable_mfa(payload: MfaDisableRequest, db: deps.DbSession, current_user: deps.CurrentUser) -> Response:
    _check_password(current_user, payload.password)
    try:
        mfa_service.disable(db, user=current_user, code=payload.code)
    except mfa_service.MfaError as exc:
        db.rollback()
        _raise(exc)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

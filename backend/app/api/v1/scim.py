"""ARCH-16 — SCIM 2.0 router (RFC 7644)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api import deps
from app.services.identity import scim_service
from app.services.identity.errors import ScimError, ScimNotFound

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scim/v2", tags=["scim"])

SCIM_CONTENT_TYPE = "application/scim+json"

_SEAT_CONSUMING = frozenset({"create_user", "activate_user", "create_group",
                             "add_group_member"})


def _scim_response(payload: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=payload, status_code=status_code,
                        media_type=SCIM_CONTENT_TYPE)


def _error(exc: ScimError) -> JSONResponse:
    return JSONResponse(content=exc.to_body(), status_code=exc.status_code,
                        media_type=SCIM_CONTENT_TYPE)


def scim_key(request: Request, authorization: str | None = Header(None),
             db=Depends(deps.get_db)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ScimNotFound("Invalid credentials.")
    token = authorization.split(" ", 1)[1].strip()
    client_ip = request.client.host if request.client else None
    return scim_service.authenticate(db, bearer=token, source_ip=client_ip)


def assert_write_allowed(db, *, organization_id, operation: str) -> None:
    if operation not in _SEAT_CONSUMING:
        return
    try:
        from app.services.billing.dunning_service import access_state
        state = access_state(db, organization_id=organization_id)
        if getattr(state, "is_read_only", False):
            raise ScimError(
                403,
                "This organization is in a read-only billing state; new users "
                "cannot be provisioned until the account is brought current. "
                "Deactivation and removal remain available.",
                scim_type="mutability",
            )
    except ScimError:
        raise
    except Exception:
        return


# ==========================================================================
# Discovery
# ==========================================================================

@router.get("/ServiceProviderConfig")
def service_provider_config(key=Depends(scim_key)) -> JSONResponse:
    return _scim_response(scim_service.service_provider_config())


@router.get("/ResourceTypes")
def resource_types(key=Depends(scim_key)) -> JSONResponse:
    return _scim_response({
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 2,
        "startIndex": 1,
        "itemsPerPage": 2,
        "Resources": [
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
             "id": "User", "name": "User", "endpoint": "/Users",
             "schema": scim_service.USER_SCHEMA},
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
             "id": "Group", "name": "Group", "endpoint": "/Groups",
             "schema": scim_service.GROUP_SCHEMA},
        ],
    })


@router.get("/Schemas")
def schemas(key=Depends(scim_key)) -> JSONResponse:
    return _scim_response({
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": 2,
        "startIndex": 1,
        "itemsPerPage": 2,
        "Resources": [
            {"id": scim_service.USER_SCHEMA, "name": "User"},
            {"id": scim_service.GROUP_SCHEMA, "name": "Group"},
        ],
    })


# ==========================================================================
# Users
# ==========================================================================

@router.get("/Users")
def list_users(filter: str | None = Query(None),
               startIndex: int | None = Query(None),
               count: int | None = Query(None),
               key=Depends(scim_key), db=Depends(deps.get_db)) -> JSONResponse:
    try:
        query = scim_service.parse_query(filter_expr=filter,
                                         start_index=startIndex, count=count)
        return _scim_response(scim_service.list_users(db, key=key, query=query))
    except ScimError as exc:
        return _error(exc)


@router.get("/Users/{resource_id}")
def get_user(resource_id: str, key=Depends(scim_key),
             db=Depends(deps.get_db)) -> JSONResponse:
    try:
        identity = scim_service.get_user(db, key=key, resource_id=resource_id)
        return _scim_response(scim_service.user_to_scim(db, identity))
    except ScimError as exc:
        return _error(exc)


@router.post("/Users", status_code=201)
def create_user(payload: dict = Body(...), key=Depends(scim_key),
                db=Depends(deps.get_db)) -> JSONResponse:
    try:
        assert_write_allowed(db, organization_id=key.organization_id,
                             operation="create_user")
        identity = scim_service.create_user(db, key=key, payload=payload)
        return _scim_response(scim_service.user_to_scim(db, identity), 201)
    except ScimError as exc:
        return _error(exc)


@router.put("/Users/{resource_id}")
def replace_user(resource_id: str, payload: dict = Body(...),
                 key=Depends(scim_key), db=Depends(deps.get_db)) -> JSONResponse:
    try:
        active = payload.get("active")
        if active is True:
            assert_write_allowed(db, organization_id=key.organization_id,
                                 operation="activate_user")
        identity = scim_service.replace_user(db, key=key, resource_id=resource_id,
                                             payload=payload)
        return _scim_response(scim_service.user_to_scim(db, identity))
    except ScimError as exc:
        return _error(exc)


@router.patch("/Users/{resource_id}")
def patch_user(resource_id: str, payload: dict = Body(...),
               key=Depends(scim_key), db=Depends(deps.get_db)) -> JSONResponse:
    try:
        identity = scim_service.patch_user(db, key=key, resource_id=resource_id,
                                           payload=payload)
        return _scim_response(scim_service.user_to_scim(db, identity))
    except ScimError as exc:
        return _error(exc)


@router.delete("/Users/{resource_id}", status_code=204)
def delete_user(resource_id: str, key=Depends(scim_key),
                db=Depends(deps.get_db)):
    try:
        scim_service.delete_user(db, key=key, resource_id=resource_id)
        return Response(status_code=204)
    except ScimError as exc:
        return _error(exc)


# ==========================================================================
# Groups
# ==========================================================================

@router.get("/Groups")
def list_groups(filter: str | None = Query(None),
                startIndex: int | None = Query(None),
                count: int | None = Query(None),
                key=Depends(scim_key), db=Depends(deps.get_db)) -> JSONResponse:
    try:
        query = scim_service.parse_query(filter_expr=filter,
                                         start_index=startIndex, count=count)
        return _scim_response(scim_service.list_groups(db, key=key, query=query))
    except ScimError as exc:
        return _error(exc)


@router.get("/Groups/{resource_id}")
def get_group(resource_id: str, key=Depends(scim_key),
              db=Depends(deps.get_db)) -> JSONResponse:
    try:
        group = scim_service.get_group(db, key=key, resource_id=resource_id)
        return _scim_response(scim_service.group_to_scim(db, group))
    except ScimError as exc:
        return _error(exc)


@router.post("/Groups", status_code=201)
def create_group(payload: dict = Body(...), key=Depends(scim_key),
                 db=Depends(deps.get_db)) -> JSONResponse:
    try:
        assert_write_allowed(db, organization_id=key.organization_id,
                             operation="create_group")
        group = scim_service.create_group(db, key=key, payload=payload)
        return _scim_response(scim_service.group_to_scim(db, group), 201)
    except ScimError as exc:
        return _error(exc)


@router.patch("/Groups/{resource_id}")
def patch_group(resource_id: str, payload: dict = Body(...),
                key=Depends(scim_key), db=Depends(deps.get_db)) -> JSONResponse:
    try:
        group = scim_service.patch_group(db, key=key, resource_id=resource_id,
                                         payload=payload)
        return _scim_response(scim_service.group_to_scim(db, group))
    except ScimError as exc:
        return _error(exc)


@router.delete("/Groups/{resource_id}", status_code=204)
def delete_group(resource_id: str, key=Depends(scim_key), db=Depends(deps.get_db)):
    try:
        scim_service.delete_group(db, key=key, resource_id=resource_id)
        return Response(status_code=204)
    except ScimError as exc:
        return _error(exc)
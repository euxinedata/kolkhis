import jwt
from authlib.integrations.starlette_client import OAuth
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, JWT_SECRET, FRONTEND_URL
from app.database import async_session
from app.models import OrgMembership, User

router = APIRouter(prefix="/auth")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

_IS_PROD = "euxine.eu" in FRONTEND_URL
_COOKIE_NAME = "token"


def _cookie_kwargs() -> dict:
    if _IS_PROD:
        return dict(domain=".euxine.eu", secure=True, samesite="none")
    return dict(secure=False, samesite="lax")


def _make_token(user: User, org_id: str | None = None, org_role: str | None = None) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    if org_id:
        payload["org_id"] = org_id
    if org_role:
        payload["org_role"] = org_role
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@router.get("/login/google")
async def login_google(request: Request):
    redirect_uri = request.url_for("callback_google")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback/google")
async def callback_google(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token["userinfo"]

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.google_id == userinfo["sub"])
        )
        user = result.scalar_one_or_none()

        if user is None:
            user = User(
                google_id=userinfo["sub"],
                email=userinfo["email"],
                name=userinfo.get("name", ""),
                picture_url=userinfo.get("picture"),
            )
            session.add(user)
        else:
            user.name = userinfo.get("name", user.name)
            user.picture_url = userinfo.get("picture", user.picture_url)
            user.last_login = datetime.utcnow()

        await session.commit()
        await session.refresh(user)

        # Check for active org memberships
        result = await session.execute(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == "active",
            )
        )
        memberships = result.scalars().all()

    membership = memberships[0] if memberships else None
    org_id = membership.org_id if membership else None
    org_role = membership.role if membership else None
    redirect_url = FRONTEND_URL if org_id else f"{FRONTEND_URL}/onboarding"

    response = RedirectResponse(url=redirect_url)
    response.set_cookie(
        _COOKIE_NAME,
        _make_token(user, org_id, org_role),
        httponly=True,
        max_age=7 * 24 * 3600,
        **_cookie_kwargs(),
    )
    return response


def verify_token(request: Request) -> dict | None:
    tok = request.cookies.get(_COOKIE_NAME)
    if not tok:
        # Fall back to Authorization: Bearer header (for API clients like dbt)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            tok = auth_header[7:]
    if not tok:
        return None
    try:
        return jwt.decode(tok, JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


async def require_auth(request: Request) -> dict:
    payload = verify_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return payload


@router.get("/me")
async def me(request: Request):
    payload = verify_token(request)
    if payload is None:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return {
        "id": payload["sub"],
        "email": payload["email"],
        "name": payload["name"],
        "org_id": payload.get("org_id"),
        "org_role": payload.get("org_role"),
    }


@router.post("/switch-org")
async def switch_org(request: Request):
    payload = verify_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    body = await request.json()
    org_id = body.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id required")

    user_id = int(payload["sub"])
    async with async_session() as session:
        result = await session.execute(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == "active",
            )
        )
        membership = result.scalar_one_or_none()
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this organization")

        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one()

    response = JSONResponse({"detail": "Switched organization", "org_id": org_id})
    response.set_cookie(
        _COOKIE_NAME,
        _make_token(user, org_id, membership.role),
        httponly=True,
        max_age=7 * 24 * 3600,
        **_cookie_kwargs(),
    )
    return response


@router.post("/logout")
async def logout():
    response = JSONResponse({"detail": "Logged out"})
    response.delete_cookie(_COOKIE_NAME, **_cookie_kwargs())
    return response

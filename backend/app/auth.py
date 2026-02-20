import jwt
from authlib.integrations.starlette_client import OAuth
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, JWT_SECRET, FRONTEND_URL
from app.database import async_session
from app.models import User

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


def _make_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.name,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
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
            user.last_login = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(user)

    response = RedirectResponse(url=FRONTEND_URL)
    response.set_cookie(
        _COOKIE_NAME,
        _make_token(user),
        httponly=True,
        max_age=7 * 24 * 3600,
        **_cookie_kwargs(),
    )
    return response


def verify_token(request: Request) -> dict | None:
    tok = request.cookies.get(_COOKIE_NAME)
    if not tok:
        return None
    try:
        return jwt.decode(tok, JWT_SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


@router.get("/me")
async def me(request: Request):
    payload = verify_token(request)
    if payload is None:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return {"id": payload["sub"], "email": payload["email"], "name": payload["name"]}


@router.post("/logout")
async def logout():
    response = RedirectResponse(url=FRONTEND_URL, status_code=302)
    response.delete_cookie(_COOKIE_NAME, **_cookie_kwargs())
    return response

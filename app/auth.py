from fastapi import HTTPException, Depends, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import supabase
from .db import supabase



bearer = HTTPBearer(scheme_name="BearerAuth")  # biar namanya match
def ensure_profile(user: dict):
    """
    Ensure profiles row exists for authenticated user.
    Safe to call multiple times.
    """
    user_id = user["user_id"]
    email = user.get("email")

    res = (
        supabase.table("profiles")
        .select("user_id")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )

    if res.data:
        return

    supabase.table("profiles").insert({
        "user_id": user_id,
        "email": email,
        # role, level, preferred_language handled by DB defaults
    }).execute()


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Security(bearer),
):
    token = creds.credentials

    # 1. verify auth user
    res = supabase.auth.get_user(token)
    if not res or not res.user:
        raise HTTPException(401, "Invalid token")

    user_id = res.user.id
    email = res.user.email

    # 2. fetch profile
    resp = (
        supabase.table("profiles")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )

    rows = resp.data or []
    prof = rows[0] if rows else None

    # 3. create profile if missing
    if not prof:
        supabase.table("profiles").insert({
            "user_id": user_id,
            "email": email,
            "role": "student",
            "level": "autre",
            "preferred_language": "fr",
        }).execute()

        resp2 = (
            supabase.table("profiles")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        prof = (resp2.data or [None])[0]

    if not prof:
        raise HTTPException(500, "Profile creation failed")

    return prof


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from ..db import get_db
from ..models import LoginRequest, RegisterRequest, TokenOut, UserOut
from ..security import create_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db=Depends(get_db)):
    hashed = hash_password(body.password)
    try:
        cur = await db.execute(
            "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
            (body.username, body.email.lower(), hashed),
        )
        await db.commit()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="El usuario o email ya existe") from exc
    row = await (await db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,))).fetchone()
    return UserOut.model_validate(dict(row))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginRequest, db=Depends(get_db)):
    identifier = body.identifier.lower()
    cur = await db.execute(
        "SELECT * FROM users WHERE username = ? OR email = ?",
        (identifier, identifier),
    )
    user = await cur.fetchone()
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")
    await db.execute("UPDATE users SET last_login = ? WHERE id = ?",
                     (datetime.now(timezone.utc).isoformat(), user["id"]))
    await db.commit()
    return TokenOut(access_token=create_token(user["id"], user["username"]))


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(get_current_user)):
    return UserOut.model_validate(user)

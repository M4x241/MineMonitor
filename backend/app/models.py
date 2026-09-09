import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# --- Auth ---------------------------------------------------------------
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    identifier: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    email: str
    created_at: datetime | None = None


# --- Mines ---------------------------------------------------------------
class MineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    seed: str | None = None
    world_name: str = "mine"
    spawn_x: int = 0
    spawn_y: int = 64
    spawn_z: int = 0
    difficulty: int = Field(default=1, ge=0, le=3)
    description: str | None = None


class MinePatch(BaseModel):
    name: str | None = None
    seed: str | None = None
    world_name: str | None = None
    spawn_x: int | None = None
    spawn_y: int | None = None
    spawn_z: int | None = None
    difficulty: int | None = None
    description: str | None = None


class MineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    seed: str
    world_name: str
    spawn_x: int
    spawn_y: int
    spawn_z: int
    difficulty: int
    description: str | None = None
    created_at: datetime | None = None


# --- Missions ------------------------------------------------------------
class MissionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    mine_id: int
    config_json: dict[str, Any] | None = None


class MissionPatch(BaseModel):
    name: str | None = None
    status: str | None = Field(
        default=None, pattern=r"^(pending|in_progress|paused|completed|canceled)$"
    )
    config_json: dict[str, Any] | None = None


class MissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    user_id: int
    mine_id: int
    mine_name: str | None = None
    map_path: str | None = None
    config_json: dict[str, Any] | None = None
    status: str
    coverage_percent: float | None = None
    final_entropy: float | None = None
    risk_detections: int | None = None
    register_date: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None

    @field_validator("config_json", mode="before")
    @classmethod
    def _parse_config(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v


# --- Minecraft bridge ----------------------------------------------------
class Pos(BaseModel):
    x: float
    y: float
    z: float


class Vec(BaseModel):
    x: float
    y: float
    z: float


class RaycastRequest(BaseModel):
    origin: Pos
    direction: Vec | None = None
    yaw: float | None = None
    pitch: float | None = None
    range: float = Field(default=16, gt=0, le=128)
    rays: int = Field(default=0, ge=0, le=4096)


class MoveRequest(BaseModel):
    target: Pos
    speed: float = Field(default=0.5, gt=0, le=5)


class TeleportRequest(BaseModel):
    position: Pos


class BlocksRequest(BaseModel):
    center: Pos
    radius: int = Field(default=8, ge=1, le=32)


class GroundTruthRequest(BlocksRequest):
    pass

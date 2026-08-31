from fastapi import APIRouter, Depends, HTTPException, status

from ..db import get_db
from ..models import MineCreate, MineOut, MinePatch
from ..security import get_current_user

router = APIRouter(prefix="/mines", tags=["mines"])


async def _get_mine_or_404(db, mine_id: int) -> dict:
    cur = await db.execute("SELECT * FROM mines WHERE id = ?", (mine_id,))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Mina no encontrada")
    return dict(row)


@router.get("", response_model=list[MineOut])
async def list_mines(db=Depends(get_db), _=Depends(get_current_user)):
    rows = await (await db.execute("SELECT * FROM mines ORDER BY created_at DESC")).fetchall()
    return [MineOut.model_validate(dict(r)) for r in rows]


@router.post("", response_model=MineOut, status_code=status.HTTP_201_CREATED)
async def create_mine(body: MineCreate, user=Depends(get_current_user), db=Depends(get_db)):
    cur = await db.execute(
        """INSERT INTO mines (name, seed, world_name, spawn_x, spawn_y, spawn_z,
           difficulty, description, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.name, body.seed, body.world_name, body.spawn_x, body.spawn_y,
         body.spawn_z, body.difficulty, body.description, user["id"]),
    )
    await db.commit()
    return MineOut.model_validate(await _get_mine_or_404(db, cur.lastrowid))


@router.get("/{mine_id}", response_model=MineOut)
async def get_mine(mine_id: int, db=Depends(get_db), _=Depends(get_current_user)):
    return MineOut.model_validate(await _get_mine_or_404(db, mine_id))


@router.patch("/{mine_id}", response_model=MineOut)
async def patch_mine(mine_id: int, body: MinePatch, db=Depends(get_db),
                     _=Depends(get_current_user)):
    await _get_mine_or_404(db, mine_id)
    fields, values = [], []
    for key, value in body.model_dump(exclude_unset=True).items():
        fields.append(f"{key} = ?")
        values.append(value)
    if fields:
        values.append(mine_id)
        await db.execute(f"UPDATE mines SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
    return MineOut.model_validate(await _get_mine_or_404(db, mine_id))


@router.delete("/{mine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mine(mine_id: int, db=Depends(get_db), _=Depends(get_current_user)):
    await _get_mine_or_404(db, mine_id)
    await db.execute("DELETE FROM mines WHERE id = ?", (mine_id,))
    await db.commit()

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from web import db_inventory as db
from web.auth import get_current_user

router = APIRouter()


@router.get("")
async def get_watchlist(user: dict = Depends(get_current_user)):
    entries = await db.get_watchlist(user["id"])
    return {"entries": entries}


class AddWatchRequest(BaseModel):
    card_name:    str
    pc_url:       str
    target_price: float
    current_price: Optional[float] = None


@router.post("")
async def add_watch(req: AddWatchRequest, user: dict = Depends(get_current_user)):
    watch_id = await db.add_watch(
        user["id"], req.card_name, req.pc_url, req.target_price, req.current_price
    )
    return {"watch_id": watch_id, "message": "Watch entry added"}


@router.delete("/{watch_id}")
async def remove_watch(watch_id: int, user: dict = Depends(get_current_user)):
    try:
        await db.remove_watch(user["id"], watch_id)
        return {"message": "Watch entry removed"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

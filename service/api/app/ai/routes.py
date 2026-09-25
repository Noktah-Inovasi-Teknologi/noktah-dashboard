"""The Hub's Biaya AI page: AI spend per case and month (Owner and Brand Managers only)."""
from fastapi import APIRouter, Depends

from .. import db
from ..auth import Caller, current_caller
from ..errors import Forbidden
from . import costs

router = APIRouter(prefix="/v1")


@router.get("/ai/costs")
async def ai_costs(caller: Caller = Depends(current_caller)) -> dict:
    if not costs.may_view(caller):
        raise Forbidden("Biaya AI hanya bisa dilihat Owner dan Brand Manager.")
    async with db.pool().acquire() as conn:
        return await costs.monthly(conn)

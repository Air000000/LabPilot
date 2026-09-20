from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE_PATH = Path(__file__).parent.parent / "operator.html"


@router.get("/operator", include_in_schema=False, response_class=HTMLResponse)
async def operator() -> HTMLResponse:
    return HTMLResponse(_PAGE_PATH.read_text(encoding="utf-8"))

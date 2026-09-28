from fastapi import APIRouter

from .auth import router as auth_router
from .documents import router as documents_router
from .internal import router as internal_router
from .qa import router as qa_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(documents_router)
router.include_router(internal_router)
router.include_router(qa_router)

__all__ = ["router"]

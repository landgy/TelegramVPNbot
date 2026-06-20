# src/handlers/__init__.py

from aiogram import Router

from .admin import router as admin_router
from .payment import router as payment_router
from .user import router as user_router
from .referral import router as referral_router

def get_handlers_router() -> Router:
    main_router = Router()
    main_router.include_routers(admin_router, payment_router, user_router, referral_router)
    return main_router

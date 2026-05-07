from .auth import router as auth_router
from .shops import router as shops_router
from .faqs import router as faqs_router
from .bot_settings import router as bot_settings_router
from .simulate import router as simulate_router
from .connect import router as connect_router

from routes.conversations import router as conversations_router
from routes.admin import router as admin_router
ALL_ROUTERS = [
    admin_router,
    conversations_router,
    auth_router,
    shops_router,
    faqs_router,
    bot_settings_router,
    simulate_router,
    connect_router,
]

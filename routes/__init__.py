from .auth import router as auth_router
from .shops import router as shops_router
from .faqs import router as faqs_router
from .bot_settings import router as bot_settings_router
from .simulate import router as simulate_router
from .connect import router as connect_router

from routes.conversations import router as conversations_router
from routes.admin import router as admin_router
from routes.provider_readiness import router as provider_readiness_router
from routes.provider_mock import router as provider_mock_router
from routes.provider_waha import router as provider_waha_router
from routes.provider_webchat import router as provider_webchat_router
from routes.provider_spacecraft_sync import router as provider_spacecraft_sync_router
from routes.provider_credentials import router as provider_credentials_router
from routes.provider_meta import router as provider_meta_router
ALL_ROUTERS = [
    provider_meta_router,
    provider_credentials_router,
    provider_mock_router,
    provider_waha_router,
    provider_webchat_router,
    provider_spacecraft_sync_router,
    provider_readiness_router,
    admin_router,
    conversations_router,
    auth_router,
    shops_router,
    faqs_router,
    bot_settings_router,
    simulate_router,
    connect_router,
]

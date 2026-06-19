"""Main entry point: start bot polling + webhook server + scheduler."""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.bot.admin_handlers import admin_router
from app.bot.dispatcher import bot, dp
from app.bot.join_handlers import join_router
from app.bot.payment_handlers import payment_router
from app.bot.user_handlers import user_router
from app.config import settings
from app.payment_config import init_config as init_payment_config
from app.scheduler import start_scheduler
from app.webhook import webhook_app

logger = logging.getLogger(__name__)

# Import payment providers to register them
import app.payments.stars     # noqa: F401
import app.payments.crypto    # noqa: F401
import app.payments.stripe    # noqa: F401
import app.payments.alipay    # noqa: F401
import app.payments.wechat    # noqa: F401


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from alembic.config import Config as AlembicConfig
    from alembic import command

    alembic_cfg = AlembicConfig("alembic.ini")
    command.upgrade(alembic_cfg, "head")
    logger.info("Database migrations applied")

    dp.include_router(user_router)
    dp.include_router(payment_router)
    dp.include_router(admin_router)
    dp.include_router(join_router)

    async def startup():
        init_payment_config()

        from app.bot_config import init_bot_config
        init_bot_config()

        start_scheduler()

        await bot.delete_webhook(drop_pending_updates=True)

        server = uvicorn.Server(
            config=uvicorn.Config(
                webhook_app,
                host=settings.webhook_host,
                port=settings.webhook_port,
                log_level="info",
            )
        )
        await asyncio.gather(
            dp.start_polling(bot),
            server.serve(),
        )

    logger.info("Starting bot...")
    asyncio.run(startup())


if __name__ == "__main__":
    main()

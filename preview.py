"""Standalone admin panel preview."""
import asyncio
import uvicorn
from fastapi import FastAPI


async def ensure_tables():
    """Create DB tables without Alembic (idempotent, safe for preview)."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.config import settings
    from app.models.models import Base

    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def main():
    await ensure_tables()
    print("DB ready.")

    from app.payment_config import init_config
    init_config()

    from app.bot_config import init_bot_config
    init_bot_config()

    from app.admin_panel import admin_panel_router

    app = FastAPI()
    app.include_router(admin_panel_router)

    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

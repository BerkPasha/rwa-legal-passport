import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# Statement parameters can hold confidential document text (page texts, quotations): they are kept
# out of error messages (hide_parameters) and out of the driver's DEBUG log of every statement
# (Stage 10A-2)
engine = create_async_engine(settings.database_url, echo=False, hide_parameters=True)
logging.getLogger("aiosqlite").setLevel(logging.INFO)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

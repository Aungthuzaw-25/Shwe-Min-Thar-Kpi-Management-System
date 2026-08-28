import asyncio
from sqlalchemy import text
from app.database.connection import engine

async def main():
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version;"))
        print("✅ Successfully dropped alembic_version table!")

if __name__ == "__main__":
    asyncio.run(main())
import asyncio
import os

from sqlalchemy import text

from labpilot.db.session import Database


async def main() -> None:
    database = Database(os.environ["LABPILOT_DATABASE_URL"])
    try:
        for attempt in range(60):
            try:
                async with database.session() as session:
                    await session.execute(text("SELECT 1"))
                return
            except Exception:
                if attempt == 59:
                    raise
                await asyncio.sleep(1)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())

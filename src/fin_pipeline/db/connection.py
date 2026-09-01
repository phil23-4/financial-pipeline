from surrealdb import Surreal
from fin_pipeline.config.settings import DB_ENDPOINT, DB_AUTH

class SurrealConnection:
    """Async Context Manager context wrapper providing secure session connections."""
    def __init__(self):
        self.db = None

    async def __aenter__(self):
        self.db = Surreal(DB_ENDPOINT)
        await self.db.connect()
        await self.db.signin(DB_AUTH)
        await self.db.use(namespace=DB_AUTH["namespace"], database=DB_AUTH["database"])
        return self.db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            await self.db.close()

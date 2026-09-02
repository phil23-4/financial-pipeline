"""Connection pooling for SurrealDB to optimize resource usage."""

from typing import Optional
from surrealdb import Surreal
from fin_pipeline.config.settings import DB_ENDPOINT, DB_AUTH
from loguru import logger as log


class SurrealConnectionPool:
    """Singleton connection pool for SurrealDB to avoid creating new connections for each request."""
    
    _instance: Optional['SurrealConnectionPool'] = None
    _pool: Optional[Surreal] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def get_connection(self) -> Surreal:
        """Get or create a connection from the pool.
        
        Returns:
            SurrealDB connection object
        """
        if self._pool is None:
            log.info("Initializing SurrealDB connection pool")
            self._pool = await self._create_connection()
        return self._pool
    
    async def _create_connection(self) -> Surreal:
        """Create a new SurrealDB connection."""
        db = Surreal()
        try:
            await db.connect(DB_ENDPOINT)
            await db.signin(DB_AUTH)
            await db.use(
                namespace=DB_AUTH["namespace"],
                database=DB_AUTH["database"]
            )
            log.debug("SurrealDB connection established")
        except Exception as e:
            log.error(f"Failed to establish SurrealDB connection: {e}")
            raise
        return db
    
    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            try:
                await self._pool.close()
                log.info("SurrealDB connection pool closed")
            except Exception as e:
                log.warning(f"Error closing SurrealDB connection: {e}")
            finally:
                self._pool = None
    
    async def reset(self) -> None:
        """Reset the connection pool by closing and reopening."""
        await self.close()
        self._pool = None

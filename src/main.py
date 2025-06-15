import asyncio
from api import GameAPI
import logging

logger = logging.getLogger(__name__)

async def main():
    """Example usage of MarketDataCache"""
    EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
    TEAM_SECRET = "5c440ac1-b111-405b-8c3d-a35bfe99933e"
    
    logger.info("Starting Market Data Cache application")
    
    # Create and connect to market data cache
    cache = GameAPI(
        EXCHANGE_URI,
        TEAM_SECRET # Set to True to see all raw updates in logs
    )
    
    await cache.connect()


if __name__ == '__main__':
    logger.info("Market Data Cache starting...")
    asyncio.run(main()) 
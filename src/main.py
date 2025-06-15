import asyncio
from api import GameAPI
import logging
import os 
from test_bot import TradingBot
logging.basicConfig(level=logging.INFO)
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

    testbots = []
    for i in range(10):
        testbots.append(TradingBot(cache))

        
    for bot in testbots:
        await bot.run()


    # while True:
    #     await asyncio.sleep(10)
    #     for asset in ['$JUMP', '$GARR', '$CARD', '$HEST', '$LOGN', '$SIMP']:
    #         os.makedirs("data/"+asset, exist_ok=True)
    #     logger.info("Dumping data")
    #     cache.csv_dump_dfs("data")
    #     logger.info("Data dumped")







if __name__ == '__main__':
    logger.info("Market Data Cache starting...")
    asyncio.run(main())



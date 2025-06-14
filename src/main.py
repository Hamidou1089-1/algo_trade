import asyncio
import logging
from typing import List, Optional
from market_data_cache import MarketDataCache
from test_bot import TestBot

# Global market cache instance that will be shared across all bots
global_market_cache: Optional[MarketDataCache] = None

# Configuration
EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
TEAM_SECRET = "5c440ac1-b111-405b-8c3d-a35bfe99933e"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('algo_trade.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class BotManager:
    """Manages the lifecycle of market data cache and trading bots"""
    
    def __init__(self):
        self.market_cache = None
        self.trading_bots = []
        self.running = False
    
    async def initialize_market_cache(self):
        """Initialize the global market data cache"""
        global global_market_cache
        
        logger.info("Initializing market data cache...")
        self.market_cache = MarketDataCache(
            EXCHANGE_URI,
            TEAM_SECRET,
            print_updates=False  # Set to True for debugging
        )
        
        # Make it globally accessible
        global_market_cache = self.market_cache
        
        # Connect to the market
        await self.market_cache.connect()
        logger.info("Market data cache initialized and connected")
        
        return self.market_cache
    
    def register_bot(self, bot_class, *args, **kwargs):
        """Register a trading bot to be launched"""
        if global_market_cache is None:
            raise RuntimeError("Market cache not initialized. Call initialize_market_cache() first.")
        
        # Pass the market cache as the first argument to the bot
        bot_instance = bot_class(global_market_cache, *args, **kwargs)
        self.trading_bots.append(bot_instance)
        logger.info(f"Registered bot: {bot_class.__name__}")
        
        return bot_instance
    
    async def start_all_bots(self):
        """Start all registered trading bots"""
        if not self.trading_bots:
            logger.warning("No trading bots registered")
            return
        
        logger.info(f"Starting {len(self.trading_bots)} trading bots...")
        
        # Create tasks for all bots
        bot_tasks = []
        for i, bot in enumerate(self.trading_bots):
            if hasattr(bot, 'start'):
                task = asyncio.create_task(bot.start(), name=f"Bot-{i}-{bot.__class__.__name__}")
                bot_tasks.append(task)
            else:
                logger.error(f"Bot {bot.__class__.__name__} does not have a start() method")
        
        if bot_tasks:
            logger.info("All trading bots started")
            # Wait for all bots to complete (they should run indefinitely)
            await asyncio.gather(*bot_tasks, return_exceptions=True)
    
    async def monitor_market_cache(self):
        """Monitor and log market cache statistics"""
        while self.running:
            try:
                if self.market_cache:
                    self.market_cache.log_market_summary()
                await asyncio.sleep(30)  # Log every 30 seconds
            except Exception as e:
                logger.error(f"Error in market cache monitoring: {e}")
                await asyncio.sleep(5)
    
    async def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down bot manager...")
        self.running = False
        
        # Close WebSocket connection if exists
        if self.market_cache and self.market_cache.ws:
            await self.market_cache.ws.close()
        
        logger.info("Bot manager shutdown complete")


# Global bot manager instance
bot_manager = BotManager()


async def launch_trading_system():
    """
    Main function to launch the entire trading system
    
    This function:
    1. Initializes the market data cache
    2. Registers trading bots
    3. Starts all registered trading bots
    4. Monitors the system
    """
    global bot_manager
    
    try:
        logger.info("=== STARTING ALGORITHMIC TRADING SYSTEM ===")
        
        # Initialize market data cache FIRST
        await bot_manager.initialize_market_cache()
        
        # NOW register bots (after cache is initialized)
        logger.info("Registering trading bots...")
        register_trading_bot(TestBot, config={'max_position': 3})
        
        # You can register multiple bots here:
        # register_trading_bot(TestBot, config={'max_position': 5})  # Second instance
        # register_trading_bot(AnotherBot, config={'param': 'value'})
        
        # Set running flag
        bot_manager.running = True
        
        # Start monitoring task
        monitor_task = asyncio.create_task(bot_manager.monitor_market_cache())
        
        # Start all registered bots
        bots_task = asyncio.create_task(bot_manager.start_all_bots())
        
        # Wait for all tasks (this will run indefinitely)
        await asyncio.gather(monitor_task, bots_task, return_exceptions=True)
        
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Unexpected error in trading system: {e}")
    finally:
        await bot_manager.shutdown()


def get_market_cache() -> Optional[MarketDataCache]:
    """
    Get the global market cache instance
    
    Returns:
        MarketDataCache: The global market cache instance, or None if not initialized
    """
    return global_market_cache


def register_trading_bot(bot_class, *args, **kwargs):
    """
    Register a trading bot to be launched with the system
    
    Args:
        bot_class: The trading bot class to instantiate
        *args: Additional arguments to pass to the bot constructor
        **kwargs: Additional keyword arguments to pass to the bot constructor
    
    Returns:
        The bot instance
    """
    return bot_manager.register_bot(bot_class, *args, **kwargs)


# Example usage and testing
async def main():
    """
    Example main function showing how to use the trading system
    """
    
    # Launch the trading system (bot registration happens inside launch_trading_system)
    await launch_trading_system()


if __name__ == "__main__":
    # Run the trading system
    asyncio.run(main())

import asyncio
import json
import websockets
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from collections import defaultdict
import time
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('market_data.log'),
        logging.StreamHandler()  # Also log to console
    ]
)
logger = logging.getLogger(__name__)

# Type definitions
InstrumentID_t = str
Price_t = int
Time_t = int
Quantity_t = int

@dataclass
class WelcomeMessage:
    type: str
    message: str

@dataclass
class OrderbookDepth:
    bids: Dict[Price_t, Quantity_t]
    asks: Dict[Price_t, Quantity_t]

@dataclass
class CandleDataResponse:
    tradeable: Dict[InstrumentID_t, List[Dict[str, Any]]]
    untradeable: Dict[InstrumentID_t, List[Dict[str, Any]]]

@dataclass
class MarketDataResponse:
    type: str
    time: Time_t
    candles: CandleDataResponse
    orderbook_depths: Dict[InstrumentID_t, OrderbookDepth]
    events: List[Dict[str, Any]]
    user_request_id: Optional[str] = None

@dataclass
class InstrumentInfo:
    instrument_id: InstrumentID_t
    first_seen: Time_t
    last_updated: Time_t
    best_bid: Optional[Price_t] = None
    best_ask: Optional[Price_t] = None
    bid_volume: Quantity_t = 0
    ask_volume: Quantity_t = 0

class MarketDataCache:
    def __init__(self, uri: str, team_secret: str, print_updates: bool = False):
        self.uri = f"{uri}?team_secret={team_secret}"
        self.ws = None
        self.print_updates = print_updates
        
        # Market data storage
        self.current_orderbooks: Dict[InstrumentID_t, OrderbookDepth] = {}
        self.current_candles: Dict[InstrumentID_t, List[Dict[str, Any]]] = defaultdict(list)
        self.instrument_info: Dict[InstrumentID_t, InstrumentInfo] = {}
        self.market_events: List[Dict[str, Any]] = []
        self.last_market_time: Optional[Time_t] = None
        
        # Historical data
        self.orderbook_history: Dict[InstrumentID_t, List[tuple]] = defaultdict(list)  # (timestamp, orderbook)
        self.event_history: List[tuple] = []  # (timestamp, event)
        
        # Statistics
        self.total_updates_received = 0
        self.instruments_discovered = set()
        self.connection_start_time: Optional[float] = None
        
    async def connect(self):
        """Connect to the WebSocket and start receiving market data"""
        try:
            logger.info(f"Attempting to connect to market at {self.uri}")
            self.ws = await websockets.connect(self.uri)
            self.connection_start_time = time.time()
            
            # Receive welcome message
            welcome_data = json.loads(await self.ws.recv())
            welcome_message = WelcomeMessage(**welcome_data)
            
            logger.info(f"Connected to market successfully. Welcome message: {welcome_message.message}")
            
            # Start the receive loop
            asyncio.create_task(self._receive_loop())
            
        except Exception as e:
            logger.error(f"Failed to connect to market: {e}")
            raise

    async def _receive_loop(self):
        """Main loop for receiving and processing market data"""
        assert self.ws, "WebSocket connection not established."
        
        try:
            logger.debug("Starting market data receive loop")
            async for msg in self.ws:
                data = json.loads(msg)
                
                if self.print_updates:
                    logger.debug(f"Received market data: {json.dumps(data, indent=2)}")
                
                msg_type = data.get("type")
                if msg_type == "market_data_update":
                    self._process_market_data_update(data)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error in receive loop: {e}")

    def _process_market_data_update(self, data: Dict[str, Any]):
        """Process incoming market data update"""
        try:
            # Parse orderbook depths
            parsed_orderbook_depths = {}
            for instr_id, depth_data in data.get("orderbook_depths", {}).items():
                parsed_orderbook_depths[instr_id] = OrderbookDepth(**depth_data)
            
            # Parse candles
            parsed_candles = CandleDataResponse(**data.get("candles", {}))
            
            # Create market data response
            market_data = MarketDataResponse(
                type=data["type"],
                time=data["time"],
                candles=parsed_candles,
                orderbook_depths=parsed_orderbook_depths,
                events=data.get("events", []),
                user_request_id=data.get("user_request_id")
            )
            
            self._cache_market_data(market_data)
            self.total_updates_received += 1
            
            if self.total_updates_received % 100 == 0:  # Log every 100 updates
                logger.debug(f"Processed {self.total_updates_received} market data updates")
            
        except KeyError as e:
            logger.error(f"Missing expected key in market data: {e}")
        except Exception as e:
            logger.error(f"Error processing market data: {e}")

    def _cache_market_data(self, data: MarketDataResponse):
        """Cache the market data update"""
        current_time = data.time
        self.last_market_time = current_time
        
        # Update orderbooks and instrument info
        for instr_id, orderbook in data.orderbook_depths.items():
            # Cache current orderbook
            self.current_orderbooks[instr_id] = orderbook
            
            # Store in history (keep last 1000 entries per instrument)
            history = self.orderbook_history[instr_id]
            history.append((current_time, orderbook))
            if len(history) > 1000:
                history.pop(0)
            
            # Update instrument info
            if instr_id not in self.instrument_info:
                self.instrument_info[instr_id] = InstrumentInfo(
                    instrument_id=instr_id,
                    first_seen=current_time,
                    last_updated=current_time
                )
                self.instruments_discovered.add(instr_id)
                logger.info(f"New instrument discovered: {instr_id}")
                
                # Debug: Log the raw orderbook structure for the first discovered instrument
                logger.info(f"Raw orderbook for {instr_id}:")
                logger.info(f"  Bids: {orderbook.bids} (type: {type(orderbook.bids)})")
                if orderbook.bids:
                    first_bid_price = list(orderbook.bids.keys())[0]
                    first_bid_qty = list(orderbook.bids.values())[0]
                    logger.info(f"  First bid price: {first_bid_price} (type: {type(first_bid_price)})")
                    logger.info(f"  First bid quantity: {first_bid_qty} (type: {type(first_bid_qty)})")
                logger.info(f"  Asks: {orderbook.asks} (type: {type(orderbook.asks)})")
                if orderbook.asks:
                    first_ask_price = list(orderbook.asks.keys())[0]
                    first_ask_qty = list(orderbook.asks.values())[0]
                    logger.info(f"  First ask price: {first_ask_price} (type: {type(first_ask_price)})")
                    logger.info(f"  First ask quantity: {first_ask_qty} (type: {type(first_ask_qty)})")
            
            # Update best prices and volumes
            instrument = self.instrument_info[instr_id]
            instrument.last_updated = current_time
            
            if orderbook.bids:
                # Convert string keys to int for comparison
                bid_prices = [int(price) if isinstance(price, str) else price for price in orderbook.bids.keys()]
                best_bid_price = max(bid_prices)
                instrument.best_bid = best_bid_price
                instrument.bid_volume = sum(int(qty) if isinstance(qty, str) else qty for qty in orderbook.bids.values())
            
            if orderbook.asks:
                # Convert string keys to int for comparison
                ask_prices = [int(price) if isinstance(price, str) else price for price in orderbook.asks.keys()]
                best_ask_price = min(ask_prices)
                instrument.best_ask = best_ask_price
                instrument.ask_volume = sum(int(qty) if isinstance(qty, str) else qty for qty in orderbook.asks.values())
        
        # Cache candle data
        for category in ['tradeable', 'untradeable']:
            category_data = getattr(data.candles, category, {})
            for instr_id, candles in category_data.items():
                self.current_candles[instr_id] = candles
        
        # Cache events
        for event in data.events:
            self.market_events.append(event)
            self.event_history.append((current_time, event))
            
            # Log significant events
            if event.get('type') in ['trade', 'settlement']:
                logger.info(f"Market event: {event}")
            
            # Keep only last 10000 events
            if len(self.market_events) > 10000:
                self.market_events.pop(0)
            if len(self.event_history) > 10000:
                self.event_history.pop(0)

    # Public API methods for accessing cached data
    
    def get_current_orderbook(self, instrument_id: InstrumentID_t) -> Optional[OrderbookDepth]:
        """Get the current orderbook for an instrument"""
        return self.current_orderbooks.get(instrument_id)
    
    def get_instrument_info(self, instrument_id: InstrumentID_t) -> Optional[InstrumentInfo]:
        """Get information about an instrument"""
        return self.instrument_info.get(instrument_id)
    
    def get_all_instruments(self) -> List[InstrumentID_t]:
        """Get list of all discovered instruments"""
        return list(self.instruments_discovered)
    
    def get_best_prices(self, instrument_id: InstrumentID_t) -> tuple[Optional[Price_t], Optional[Price_t]]:
        """Get best bid and ask prices for an instrument"""
        info = self.instrument_info.get(instrument_id)
        if info:
            return info.best_bid, info.best_ask
        return None, None
    
    def get_current_spread(self, instrument_id: InstrumentID_t) -> Optional[Price_t]:
        """Get current bid-ask spread for an instrument"""
        bid, ask = self.get_best_prices(instrument_id)
        if bid is not None and ask is not None:
            # Convert to int if they are strings
            bid_int = int(bid) if isinstance(bid, str) else bid
            ask_int = int(ask) if isinstance(ask, str) else ask
            return ask_int - bid_int
        return None
    
    def get_recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent market events"""
        return self.market_events[-limit:] if self.market_events else []
    
    def get_orderbook_history(self, instrument_id: InstrumentID_t, limit: int = 100) -> List[tuple]:
        """Get historical orderbook data for an instrument"""
        history = self.orderbook_history.get(instrument_id, [])
        return history[-limit:] if history else []
    
    def get_market_statistics(self) -> Dict[str, Any]:
        """Get market statistics"""
        uptime = time.time() - self.connection_start_time if self.connection_start_time else 0
        
        return {
            "connection_uptime_seconds": uptime,
            "total_updates_received": self.total_updates_received,
            "instruments_discovered": len(self.instruments_discovered),
            "instruments_list": list(self.instruments_discovered),
            "last_market_time": self.last_market_time,
            "total_events_cached": len(self.market_events),
            "active_orderbooks": len(self.current_orderbooks)
        }
    
    def log_market_summary(self):
        """Log a summary of current market state"""
        logger.info("=== MARKET DATA SUMMARY ===")
        stats = self.get_market_statistics()
        
        logger.info(f"Connection uptime: {stats['connection_uptime_seconds']:.1f} seconds")
        logger.info(f"Total updates received: {stats['total_updates_received']}")
        logger.info(f"Instruments discovered: {stats['instruments_discovered']}")
        logger.info(f"Active orderbooks: {stats['active_orderbooks']}")
        logger.info(f"Total events cached: {stats['total_events_cached']}")
        
        logger.info("Instruments with current prices:")
        for instr_id in sorted(self.instruments_discovered):
            info = self.get_instrument_info(instr_id)
            if info:
                bid, ask = info.best_bid, info.best_ask
                spread = self.get_current_spread(instr_id)
                logger.info(f"  {instr_id}: Bid={bid}, Ask={ask}, Spread={spread}")
        
        logger.info("=" * 30)

async def main():
    """Example usage of MarketDataCache"""
    EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
    TEAM_SECRET = "5c440ac1-b111-405b-8c3d-a35bfe99933e"
    
    logger.info("Starting Market Data Cache application")
    
    # Create and connect to market data cache
    cache = MarketDataCache(
        EXCHANGE_URI,
        TEAM_SECRET,
        print_updates=False  # Set to True to see all raw updates in logs
    )
    
    await cache.connect()
    
    # Log market summary every 10 seconds
    while True:
        await asyncio.sleep(10)
        cache.log_market_summary()

if __name__ == '__main__':
    logger.info("Market Data Cache starting...")
    asyncio.run(main())


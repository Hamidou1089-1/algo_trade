import asyncio
import json
import websockets
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple, Any
import logging
import random

# Import types and classes from example.py
InstrumentID_t = str
Price_t = int
Time_t = int
Quantity_t = int
OrderID_t = str

@dataclass
class BaseMessage:
    type: str

@dataclass
class AddOrderRequest(BaseMessage):
    type: str = field(default="add_order", init=False)
    user_request_id: str
    instrument_id: InstrumentID_t
    price: Price_t
    expiry: Time_t
    side: str
    quantity: Quantity_t

@dataclass
class CancelOrderRequest(BaseMessage):
    type: str = field(default="cancel_order", init=False)
    user_request_id: str
    order_id: OrderID_t
    instrument_id: InstrumentID_t

@dataclass
class GetInventoryRequest(BaseMessage):
    type: str = field(default="get_inventory", init=False)
    user_request_id: str

@dataclass
class GetPendingOrdersRequest(BaseMessage):
    type: str = field(default="get_pending_orders", init=False)
    user_request_id: str

@dataclass
class AddOrderResponseData:
    order_id: Optional[OrderID_t] = None
    message: Optional[str] = None
    immediate_inventory_change: Optional[Quantity_t] = None
    immediate_balance_change: Optional[Quantity_t] = None

@dataclass
class AddOrderResponse(BaseMessage):
    type: str
    user_request_id: str
    success: bool
    data: AddOrderResponseData

@dataclass
class CancelOrderResponse(BaseMessage):
    type: str
    user_request_id: str
    success: bool
    message: Optional[str] = None

@dataclass
class ErrorResponse(BaseMessage):
    type: str
    user_request_id: str
    message: str

@dataclass
class GetInventoryResponse(BaseMessage):
    type: str
    user_request_id: str
    data: Dict[InstrumentID_t, Tuple[Quantity_t, Quantity_t]]

@dataclass
class OrderJSON:
    orderID: OrderID_t
    teamID: str
    price: Price_t
    time: Time_t
    expiry: Time_t
    side: str
    unfilled_quantity: Quantity_t
    total_quantity: Quantity_t
    live: bool

@dataclass
class GetPendingOrdersResponse:
    type: str
    user_request_id: str
    data: Dict[InstrumentID_t, Tuple[List[OrderJSON], List[OrderJSON]]]

# Global request ID counter
global_user_request_id = 0

logger = logging.getLogger(__name__)

class TestBot:
    """
    Simple test bot that implements basic market making strategy:
    1. Chooses one instrument
    2. Quotes best bid + 1 and best ask - 1
    3. If hit on one side, creates new order on the other side
    """
    
    def __init__(self, market_cache, config=None):
        self.market_cache = market_cache
        self.config = config or {}
        
        # Trading state
        self.active_instrument = None
        self.pending_orders = {}  # {order_id: order_info}
        self.position = 0  # Track our position
        self.max_position = self.config.get('max_position', 3)
        
        # WebSocket connection for trading
        self.trading_ws = None
        self.pending_requests = {}
        
        # Configuration
        self.exchange_uri = "ws://192.168.100.10:9001/trade"
        self.team_secret = "5c440ac1-b111-405b-8c3d-a35bfe99933e"
        
        logger.info("TestBot initialized")
    
    async def start(self):
        """Main bot loop"""
        try:
            logger.info("Starting TestBot...")
            
            # Connect to trading WebSocket
            await self.connect_trading()
            
            # Wait for market data to be available
            await self.wait_for_market_data()
            
            # Choose an instrument to trade
            self.choose_instrument()
            
            if not self.active_instrument:
                logger.error("No suitable instrument found for trading")
                return
            
            logger.info(f"TestBot will trade {self.active_instrument}")
            
            # Start main trading loop
            await self.trading_loop()
            
        except Exception as e:
            logger.error(f"Error in TestBot: {e}")
        finally:
            if self.trading_ws:
                await self.trading_ws.close()
            logger.info("TestBot stopped")
    
    async def connect_trading(self):
        """Connect to trading WebSocket"""
        try:
            uri = f"{self.exchange_uri}?team_secret={self.team_secret}"
            self.trading_ws = await websockets.connect(uri)
            
            # Receive welcome message
            welcome_data = json.loads(await self.trading_ws.recv())
            logger.info(f"Connected to trading WebSocket: {welcome_data.get('message', 'Connected')}")
            
            # Start response handler
            asyncio.create_task(self.handle_trading_responses())
            
        except Exception as e:
            logger.error(f"Failed to connect to trading WebSocket: {e}")
            raise
    
    async def handle_trading_responses(self):
        """Handle responses from trading WebSocket"""
        try:
            async for msg in self.trading_ws:
                data = json.loads(msg)
                
                # Skip market data updates
                if data.get("type") == "market_data_update":
                    continue
                
                user_request_id = data.get("user_request_id")
                if user_request_id and user_request_id in self.pending_requests:
                    future = self.pending_requests[user_request_id]
                    future.set_result(data)
                    del self.pending_requests[user_request_id]
                
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Trading WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error in trading response handler: {e}")
    
    async def send_trading_request(self, payload: BaseMessage, timeout: int = 3):
        """Send a trading request and wait for response"""
        global global_user_request_id
        
        request_id = str(global_user_request_id).zfill(10)
        global_user_request_id += 1
        
        payload.user_request_id = request_id
        payload_dict = asdict(payload)
        
        # Create future for response
        future = asyncio.get_event_loop().create_future()
        self.pending_requests[request_id] = future
        
        try:
            # Send request
            await self.trading_ws.send(json.dumps(payload_dict))
            logger.debug(f"Sent trading request: {payload_dict}")
            
            # Wait for response
            response = await asyncio.wait_for(future, timeout)
            
            # Parse response based on type
            response_type = response.get("type")
            if response_type == "add_order_response":
                response['data'] = AddOrderResponseData(**response.get('data', {}))
                return AddOrderResponse(**response)
            elif response_type == "cancel_order_response":
                return CancelOrderResponse(**response)
            elif response_type == "get_inventory_response":
                return GetInventoryResponse(**response)
            elif response_type == "get_pending_orders_response":
                parsed_data = {}
                for instr_id, (bids_raw, asks_raw) in response.get('data', {}).items():
                    parsed_bids = [OrderJSON(**order_data) for order_data in bids_raw]
                    parsed_asks = [OrderJSON(**order_data) for order_data in asks_raw]
                    parsed_data[instr_id] = (parsed_bids, parsed_asks)
                response['data'] = parsed_data
                return GetPendingOrdersResponse(**response)
            elif response_type == "error":
                return ErrorResponse(**response)
            else:
                return response
                
        except asyncio.TimeoutError:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            logger.warning(f"Trading request timeout: {request_id}")
            return {"success": False, "user_request_id": request_id, "message": "Request timed out"}
        except Exception as e:
            if request_id in self.pending_requests:
                del self.pending_requests[request_id]
            logger.error(f"Error in trading request: {e}")
            return {"success": False, "user_request_id": request_id, "message": str(e)}
    
    async def wait_for_market_data(self):
        """Wait for market data to be available"""
        logger.info("Waiting for market data...")
        
        for _ in range(30):  # Wait up to 30 seconds
            instruments = self.market_cache.get_all_instruments()
            if instruments:
                logger.info(f"Market data available: {len(instruments)} instruments")
                return
            await asyncio.sleep(1)
        
        raise RuntimeError("Timeout waiting for market data")
    
    def choose_instrument(self):
        """Choose an instrument to trade"""
        instruments = self.market_cache.get_all_instruments()
        
        if not instruments:
            return
        
        # Filter for instruments with good liquidity
        suitable_instruments = []
        
        for instrument in instruments:
            bid, ask = self.market_cache.get_best_prices(instrument)
            spread = self.market_cache.get_current_spread(instrument)
            
            if bid and ask and spread and spread >= 2:  # Need at least 2 tick spread
                suitable_instruments.append(instrument)
        
        if suitable_instruments:
            # Choose a random suitable instrument
            self.active_instrument = random.choice(suitable_instruments)
            logger.info(f"Selected instrument: {self.active_instrument}")
        else:
            # Fallback to any instrument with data
            for instrument in instruments:
                bid, ask = self.market_cache.get_best_prices(instrument)
                if bid and ask:
                    self.active_instrument = instrument
                    logger.info(f"Fallback selected instrument: {self.active_instrument}")
                    break
    
    async def trading_loop(self):
        """Main trading loop"""
        logger.info("Starting trading loop...")
        
        while True:
            try:
                # Check current market conditions
                bid, ask = self.market_cache.get_best_prices(self.active_instrument)
                spread = self.market_cache.get_current_spread(self.active_instrument)
                
                if not bid or not ask or spread is None:
                    logger.debug(f"No market data for {self.active_instrument}")
                    await asyncio.sleep(1)
                    continue
                
                # Check our pending orders
                await self.check_pending_orders()
                
                # Update position from inventory
                await self.update_position()
                
                # Place new quotes if we don't have too many orders
                if len(self.pending_orders) < 2 and abs(self.position) < self.max_position:
                    await self.place_quotes(bid, ask)
                
                await asyncio.sleep(2)  # Wait 2 seconds between iterations
                
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                await asyncio.sleep(5)
    
    async def place_quotes(self, best_bid: int, best_ask: int):
        """Place quotes: bid at best_bid + 1, ask at best_ask - 1"""
        try:
            our_bid_price = best_bid + 1
            our_ask_price = best_ask - 1
            
            # Only place quotes if there's room
            if our_ask_price <= our_bid_price:
                logger.debug("No room for quotes - spread too tight")
                return
            
            # Place bid order (if position allows)
            if self.position > -self.max_position:
                bid_order = await self.place_order(self.active_instrument, our_bid_price, "bid")
                if bid_order and isinstance(bid_order, AddOrderResponse) and bid_order.success:
                    self.pending_orders[bid_order.data.order_id] = {
                        'order_id': bid_order.data.order_id,
                        'side': 'bid',
                        'price': our_bid_price
                    }
                    logger.info(f"Placed bid order: {our_bid_price}")
            
            # Place ask order (if position allows)
            if self.position < self.max_position:
                ask_order = await self.place_order(self.active_instrument, our_ask_price, "ask")
                if ask_order and isinstance(ask_order, AddOrderResponse) and ask_order.success:
                    self.pending_orders[ask_order.data.order_id] = {
                        'order_id': ask_order.data.order_id,
                        'side': 'ask',
                        'price': our_ask_price
                    }
                    logger.info(f"Placed ask order: {our_ask_price}")
            
        except Exception as e:
            logger.error(f"Error placing quotes: {e}")
    
    async def place_order(self, instrument: str, price: int, side: str):
        """Place a single order"""
        try:
            # Calculate expiry (10 seconds from now, in milliseconds)
            expiry = int(asyncio.get_event_loop().time() * 1000) + 10000
            
            order_request = AddOrderRequest(
                user_request_id="",
                instrument_id=instrument,
                price=price,
                quantity=1,
                side=side,
                expiry=expiry
            )
            
            response = await self.send_trading_request(order_request)
            return response
            
        except Exception as e:
            logger.error(f"Error placing {side} order at {price}: {e}")
            return None
    
    async def check_pending_orders(self):
        """Check status of pending orders and clean up filled/expired ones"""
        try:
            if not self.pending_orders:
                return
            
            # Get current pending orders from exchange
            pending_request = GetPendingOrdersRequest(user_request_id="")
            response = await self.send_trading_request(pending_request)
            
            if not isinstance(response, GetPendingOrdersResponse):
                return
            
            # Get our orders for this instrument
            our_orders_on_exchange = set()
            if self.active_instrument in response.data:
                bids, asks = response.data[self.active_instrument]
                for order in bids + asks:
                    our_orders_on_exchange.add(order.orderID)
            
            # Remove orders that are no longer on exchange (filled or expired)
            filled_orders = []
            for order_id in list(self.pending_orders.keys()):
                if order_id not in our_orders_on_exchange:
                    order_info = self.pending_orders[order_id]
                    filled_orders.append(order_info)
                    del self.pending_orders[order_id]
                    logger.info(f"Order filled/expired: {order_id} ({order_info['side']} at {order_info['price']})")
            
            # Update position based on filled orders
            for order in filled_orders:
                if order['side'] == 'bid':
                    self.position += 1  # We bought
                else:
                    self.position -= 1  # We sold
            
        except Exception as e:
            logger.error(f"Error checking pending orders: {e}")
    
    async def update_position(self):
        """Update position from inventory"""
        try:
            inventory_request = GetInventoryRequest(user_request_id="")
            response = await self.send_trading_request(inventory_request)
            
            if isinstance(response, GetInventoryResponse) and self.active_instrument in response.data:
                reserved, owned = response.data[self.active_instrument]
                self.position = owned
                logger.debug(f"Position update: {self.position} (reserved: {reserved})")
            
        except Exception as e:
            logger.error(f"Error updating position: {e}")
    
    async def cancel_all_orders(self):
        """Cancel all pending orders (for cleanup)"""
        try:
            for order_id in list(self.pending_orders.keys()):
                cancel_request = CancelOrderRequest(
                    user_request_id="",
                    order_id=order_id,
                    instrument_id=self.active_instrument
                )
                
                response = await self.send_trading_request(cancel_request)
                if isinstance(response, CancelOrderResponse) and response.success:
                    del self.pending_orders[order_id]
                    logger.info(f"Cancelled order: {order_id}")
            
        except Exception as e:
            logger.error(f"Error cancelling orders: {e}") 
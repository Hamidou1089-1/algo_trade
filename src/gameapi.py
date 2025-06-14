import asyncio
import json
import websockets
import pandas as pd
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime
import logging

# Import market data cache (will be passed from BotManager)
from market_data_cache import MarketDataCache, InstrumentInfo

# Type definitions
InstrumentID_t = str
Price_t = int
Time_t = int
Quantity_t = int
OrderID_t = str
TeamID_t = str

logger = logging.getLogger(__name__)

# Message dataclasses (keeping all the existing ones)
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
class WelcomeMessage(BaseMessage):
    type: str
    message: str

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
    teamID: TeamID_t
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


class GameAPI:
    def __init__(self, uri: str, team_secret: str, market_cache: Optional[MarketDataCache] = None):
        self.uri = f"{uri}?team_secret={team_secret}"
        self.ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._user_request_id = 0

        # Use the shared market cache instead of maintaining our own
        self.market_cache = market_cache

    async def connect(self):
        """Connect to the AlgoTrade server for trading"""
        self.ws = await websockets.connect(self.uri)
        welcome_data = json.loads(await self.ws.recv())
        welcome_message = WelcomeMessage(**welcome_data)
        logger.info(f"GameAPI Connected: {welcome_message.message}")

        # Start receiving messages (only for trading responses)
        asyncio.create_task(self._receive_loop())
        return welcome_message

    async def disconnect(self):
        """Disconnect from the server"""
        if self.ws:
            await self.ws.close()
            logger.info("GameAPI disconnected")

    async def _receive_loop(self):
        """Internal loop to receive trading responses"""
        assert self.ws, "Websocket connection not established."

        try:
            async for msg in self.ws:
                data = json.loads(msg)

                # Only handle responses with request IDs (trading responses)
                rid = data.get("user_request_id")
                if rid and rid in self._pending:
                    self._pending[rid].set_result(data)
                    del self._pending[rid]

                # We don't process market data here - that's handled by MarketDataCache

        except websockets.exceptions.ConnectionClosed:
            logger.warning("GameAPI WebSocket connection closed")
        except Exception as e:
            logger.error(f"Error in GameAPI receive loop: {e}")

    async def _send(self, payload: BaseMessage, timeout: int = 3):
        """Internal method to send messages and wait for responses"""
        rid = str(self._user_request_id).zfill(10)
        self._user_request_id += 1

        payload.user_request_id = rid
        payload_dict = asdict(payload)

        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut

        await self.ws.send(json.dumps(payload_dict))
        logger.debug(f"Sent request {rid}: {payload.type}")

        try:
            resp = await asyncio.wait_for(fut, timeout)

            # Parse response based on type
            if resp.get("type") == "add_order_response":
                resp['data'] = AddOrderResponseData(**resp.get('data', {}))
                return AddOrderResponse(**resp)
            elif resp.get("type") == "cancel_order_response":
                return CancelOrderResponse(**resp)
            elif resp.get("type") == "get_inventory_response":
                return GetInventoryResponse(**resp)
            elif resp.get("type") == "get_pending_orders_response":
                parsed_data = {}
                for instr_id, (bids_raw, asks_raw) in resp.get('data', {}).items():
                    parsed_bids = [OrderJSON(**order_data) for order_data in bids_raw]
                    parsed_asks = [OrderJSON(**order_data) for order_data in asks_raw]
                    parsed_data[instr_id] = (parsed_bids, parsed_asks)
                resp['data'] = parsed_data
                return GetPendingOrdersResponse(**resp)
            elif resp.get("type") == "error":
                return ErrorResponse(**resp)
            else:
                return resp

        except asyncio.TimeoutError:
            if rid in self._pending:
                del self._pending[rid]
            raise TimeoutError(f"Request {rid} timed out")

    # ========== Trading Methods ==========

    async def buy_future(self, underlying: str, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Buy a future contract"""
        instrument_id = f"{underlying}_future_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000

        order = AddOrderRequest(
            user_request_id="",
            instrument_id=instrument_id,
            price=price,
            quantity=quantity,
            side="bid",
            expiry=expiry_ms
        )
        return await self._send(order)

    async def sell_future(self, underlying: str, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Sell a future contract"""
        instrument_id = f"{underlying}_future_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000

        order = AddOrderRequest(
            user_request_id="",
            instrument_id=instrument_id,
            price=price,
            quantity=quantity,
            side="ask",
            expiry=expiry_ms
        )
        return await self._send(order)

    async def buy_call(self, underlying: str, strike: Price_t, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Buy a call option"""
        instrument_id = f"{underlying}_call_{strike}_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000

        order = AddOrderRequest(
            user_request_id="",
            instrument_id=instrument_id,
            price=price,
            quantity=quantity,
            side="bid",
            expiry=expiry_ms
        )
        return await self._send(order)

    async def sell_call(self, underlying: str, strike: Price_t, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Sell a call option"""
        instrument_id = f"{underlying}_call_{strike}_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000

        order = AddOrderRequest(
            user_request_id="",
            instrument_id=instrument_id,
            price=price,
            quantity=quantity,
            side="ask",
            expiry=expiry_ms
        )
        return await self._send(order)

    async def buy_put(self, underlying: str, strike: Price_t, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Buy a put option"""
        instrument_id = f"{underlying}_put_{strike}_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000

        order = AddOrderRequest(
            user_request_id="",
            instrument_id=instrument_id,
            price=price,
            quantity=quantity,
            side="bid",
            expiry=expiry_ms
        )
        return await self._send(order)

    async def sell_put(self, underlying: str, strike: Price_t, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Sell a put option"""
        instrument_id = f"{underlying}_put_{strike}_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000

        order = AddOrderRequest(
            user_request_id="",
            instrument_id=instrument_id,
            price=price,
            quantity=quantity,
            side="ask",
            expiry=expiry_ms
        )
        return await self._send(order)

    async def cancel_order(self, instrument_id: InstrumentID_t, order_id: OrderID_t):
        """Cancel an existing order"""
        cancel_req = CancelOrderRequest(
            user_request_id="",
            order_id=order_id,
            instrument_id=instrument_id
        )
        return await self._send(cancel_req)

    async def get_inventory(self):
        """Get current inventory (cash and holdings)"""
        req = GetInventoryRequest(user_request_id="")
        return await self._send(req)

    async def get_pending_orders(self):
        """Get all pending orders"""
        req = GetPendingOrdersRequest(user_request_id="")
        return await self._send(req)

    # ========== Market Data Methods (using MarketDataCache) ==========

    def get_orderbook(self, instrument_id: InstrumentID_t) -> Optional[pd.DataFrame]:
        """Get orderbook for a specific instrument as DataFrame"""
        if not self.market_cache:
            return None

        orderbook = self.market_cache.get_current_orderbook(instrument_id)
        if not orderbook:
            return None

        # Convert to DataFrame
        bids_data = [(int(price)/100, int(qty), 'bid') for price, qty in orderbook.bids.items()]
        asks_data = [(int(price)/100, int(qty), 'ask') for price, qty in orderbook.asks.items()]

        if not bids_data and not asks_data:
            return pd.DataFrame(columns=['price', 'quantity', 'side'])

        df = pd.DataFrame(bids_data + asks_data, columns=['price', 'quantity', 'side'])
        df = df.sort_values('price', ascending=False)

        return df

    def get_all_orderbooks(self) -> Dict[str, pd.DataFrame]:
        """Get all orderbooks as DataFrames"""
        if not self.market_cache:
            return {}

        instruments = self.market_cache.get_all_instruments()
        return {instr: self.get_orderbook(instr)
                for instr in instruments
                if self.get_orderbook(instr) is not None}

    def get_best_bid_ask(self, instrument_id: InstrumentID_t) -> Optional[Dict[str, Any]]:
        """Get best bid and ask prices for an instrument"""
        if not self.market_cache:
            return None

        best_bid, best_ask = self.market_cache.get_best_prices(instrument_id)
        spread = self.market_cache.get_current_spread(instrument_id)

        return {
            'instrument': instrument_id,
            'best_bid': int(best_bid) / 100 if best_bid else None,
            'best_ask': int(best_ask) / 100 if best_ask else None,
            'spread': int(spread) / 100 if spread else None
        }

    def get_instrument_info(self, instrument_id: InstrumentID_t) -> Optional[InstrumentInfo]:
        """Get detailed information about an instrument"""
        if not self.market_cache:
            return None
        return self.market_cache.get_instrument_info(instrument_id)

    def list_instruments(self) -> List[str]:
        """List all available instruments"""
        if not self.market_cache:
            return []
        return self.market_cache.get_all_instruments()

    def get_recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent market events"""
        if not self.market_cache:
            return []
        return self.market_cache.get_recent_events(limit)

    def get_market_statistics(self) -> Dict[str, Any]:
        """Get market statistics"""
        if not self.market_cache:
            return {}
        return self.market_cache.get_market_statistics()
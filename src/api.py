import asyncio
import json
import websockets
import pandas as pd
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime
from collections import defaultdict
import logging

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


@dataclass
class InstrumentInfo:
    instrument_id: InstrumentID_t
    first_seen: Time_t
    last_updated: Time_t
    best_bid: Optional[Price_t] = None
    best_ask: Optional[Price_t] = None
    bid_volume: Quantity_t = 0
    ask_volume: Quantity_t = 0

class GameAPI:
    def __init__(self, uri: str, team_secret: str):
        self.uri = f"{uri}?team_secret={team_secret}"
        self.ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._user_request_id = 0

        self.current_orderbooks: Dict[InstrumentID_t, OrderbookDepth] = {}
        self.current_candles: Dict[InstrumentID_t, List[Dict[str, Any]]] = defaultdict(list)
        self.instrument_info: Dict[InstrumentID_t, InstrumentInfo] = {}
        self.market_events: List[Dict[str, Any]] = []
        self.last_market_time: Optional[Time_t] = None

        self.instruments_discovered = set()

        # Historical data
        self.orderbook_history: Dict[InstrumentID_t, List[tuple]] = defaultdict(list)  # (timestamp, orderbook)
        self.event_history: List[tuple] = []  # (timestamp, event)

        self.underlying_dfs: Dict[str, pd.DataFrame] = {}

    async def connect(self):

        try : 
            """Connect to the AlgoTrade server for trading"""
            logger.info(f"Connecting to {self.uri}")
            self.ws = await websockets.connect(self.uri)
            welcome_data = json.loads(await self.ws.recv())
            welcome_message = WelcomeMessage(**welcome_data)
            logger.info(f"GameAPI Connected: {welcome_message.message}")

        except Exception as e:
            logger.error(f"Failed to connect to market: {e}")
            raise
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
                msg_type = data.get("type")
                if msg_type == "market_data_update":
                    #logger.info(f"Market data update received: {data}")
                    self._process_market_data_update(data)

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
        
        except Exception as e:
            logger.error(f"Error processing market data update: {e}")


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
                
                # Only log basic info for first few instruments
                # if len(self.instruments_discovered) <= 3:
                #     logger.debug(f"Sample orderbook structure for {instr_id}: bids={len(orderbook.bids)}, asks={len(orderbook.asks)}")
            
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
        
        #logger.info(f"Market data update processed: {self.current_orderbooks}")
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

    def _get_instrument_info(self, instrument_id: InstrumentID_t) -> Optional[InstrumentInfo]:
        """Get information about an instrument"""
        return self.current_orderbooks[instrument_id]

    def update_underlying_dfs(self):
        """Update the underlying DataFrames"""
        for instr_id, candles in self.current_candles.items() :
            if instr_id in ['$JUMP', '$GARR', '$CARD', '$HEST', '$LOGN', '$SIMP']:

                logger.info(f"Updating underlying df for instrument {instr_id}")
                self.underlying_dfs[instr_id] = pd.concat([self.underlying_dfs[instr_id], pd.DataFrame([candles])], ignore_index=True)
        logger.info(f"Underlying DataFrames updated: {self.underlying_dfs}")

    def csv_dump_dfs(self, dir: str):
        for underlying, df in self.underlying_dfs.items():
            df.to_csv(f"{dir}/{underlying}.csv", index=False)


    # ========== Market Data Methods (using MarketDataCache) ==========

    # def get_orderbook(self, instrument_id: InstrumentID_t) -> Optional[pd.DataFrame]:
    #     """Get orderbook for a specific instrument as DataFrame"""
    #     if not self.market_cache:
    #         return None

    #     orderbook = self.market_cache.get_current_orderbook(instrument_id)
    #     if not orderbook:
    #         return None

    #     # Convert to DataFrame
    #     bids_data = [(int(price)/100, int(qty), 'bid') for price, qty in orderbook.bids.items()]
    #     asks_data = [(int(price)/100, int(qty), 'ask') for price, qty in orderbook.asks.items()]

    #     if not bids_data and not asks_data:
    #         return pd.DataFrame(columns=['price', 'quantity', 'side'])

    #     df = pd.DataFrame(bids_data + asks_data, columns=['price', 'quantity', 'side'])
    #     df = df.sort_values('price', ascending=False)

    #     return df

    # def get_all_orderbooks(self) -> Dict[str, pd.DataFrame]:
    #     """Get all orderbooks as DataFrames"""
    #     if not self.market_cache:
    #         return {}

    #     instruments = self.market_cache.get_all_instruments()
    #     return {instr: self.get_orderbook(instr)
    #             for instr in instruments
    #             if self.get_orderbook(instr) is not None}

    # def get_best_bid_ask(self, instrument_id: InstrumentID_t) -> Optional[Dict[str, Any]]:
    #     """Get best bid and ask prices for an instrument"""
    #     if not self.market_cache:
    #         return None

    #     best_bid, best_ask = self.market_cache.get_best_prices(instrument_id)
    #     spread = self.market_cache.get_current_spread(instrument_id)

    #     return {
    #         'instrument': instrument_id,
    #         'best_bid': int(best_bid) / 100 if best_bid else None,
    #         'best_ask': int(best_ask) / 100 if best_ask else None,
    #         'spread': int(spread) / 100 if spread else None
    #     }

    # def get_instrument_info(self, instrument_id: InstrumentID_t) -> Optional[InstrumentInfo]:
    #     """Get detailed information about an instrument"""
    #     if not self.market_cache:
    #         return None
    #     return self.market_cache.get_instrument_info(instrument_id)

    # def list_instruments(self) -> List[str]:
    #     """List all available instruments"""
    #     if not self.market_cache:
    #         return []
    #     return self.market_cache.get_all_instruments()

    # def get_recent_events(self, limit: int = 100) -> List[Dict[str, Any]]:
    #     """Get recent market events"""
    #     if not self.market_cache:
    #         return []
    #     return self.market_cache.get_recent_events(limit)

    # def get_market_statistics(self) -> Dict[str, Any]:
    #     """Get market statistics"""
    #     if not self.market_cache:
    #         return {}
    #     return self.market_cache.get_market_statistics()
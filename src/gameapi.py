import asyncio
import json
import websockets
import pandas as pd
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Tuple, Any
from datetime import datetime

# Type definitions
InstrumentID_t = str
Price_t = int
Time_t = int
Quantity_t = int
OrderID_t = str
TeamID_t = str

# Message dataclasses
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

@dataclass
class OrderbookDepth:
    bids: Dict[Price_t, Quantity_t]
    asks: Dict[Price_t, Quantity_t]

@dataclass
class CandleDataResponse:
    tradeable: Dict[InstrumentID_t, List[Dict[str, Any]]]
    untradeable: Dict[InstrumentID_t, List[Dict[str, Any]]]

@dataclass
class MarketDataResponse(BaseMessage):
    type: str
    time: Time_t
    candles: CandleDataResponse
    orderbook_depths: Dict[InstrumentID_t, OrderbookDepth]
    events: List[Dict[str, Any]]
    user_request_id: Optional[str] = None


class GameAPI:
    def __init__(self, uri: str, team_secret: str):
        self.uri = f"{uri}?team_secret={team_secret}"
        self.ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._user_request_id = 0
        self._market_data_cache = {}
        self._orderbook_cache = {}

    async def connect(self):
        """Connect to the AlgoTrade server"""
        self.ws = await websockets.connect(self.uri)
        welcome_data = json.loads(await self.ws.recv())
        welcome_message = WelcomeMessage(**welcome_data)
        print(f"Connected: {welcome_message.message}")

        # Start receiving messages
        asyncio.create_task(self._receive_loop())
        return welcome_message

    async def disconnect(self):
        """Disconnect from the server"""
        if self.ws:
            await self.ws.close()

    async def _receive_loop(self):
        """Internal loop to receive and process messages"""
        assert self.ws, "Websocket connection not established."
        async for msg in self.ws:
            data = json.loads(msg)

            # Handle responses with request IDs
            rid = data.get("user_request_id")
            if rid and rid in self._pending:
                self._pending[rid].set_result(data)
                del self._pending[rid]

            # Handle market data updates
            msg_type = data.get("type")
            if msg_type == "market_data_update":
                try:
                    parsed_orderbook_depths = {
                        instr_id: OrderbookDepth(**depth_data)
                        for instr_id, depth_data in data.get("orderbook_depths", {}).items()
                    }
                    parsed_candles = CandleDataResponse(**data.get("candles", {}))

                    market_data = MarketDataResponse(
                        type=data["type"],
                        time=data["time"],
                        candles=parsed_candles,
                        orderbook_depths=parsed_orderbook_depths,
                        events=data.get("events", []),
                        user_request_id=data.get("user_request_id")
                    )

                    # Cache the data
                    self._market_data_cache = market_data
                    self._orderbook_cache = parsed_orderbook_depths

                except Exception as e:
                    print(f"Error parsing market data: {e}")

    async def _send(self, payload: BaseMessage, timeout: int = 3):
        """Internal method to send messages and wait for responses"""
        rid = str(self._user_request_id).zfill(10)
        self._user_request_id += 1

        payload.user_request_id = rid
        payload_dict = asdict(payload)

        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut

        await self.ws.send(json.dumps(payload_dict))

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

    # High-level trading methods

    async def buy_future(self, underlying: str, expiry_seconds: int, price: Price_t, quantity: Quantity_t = 1):
        """Buy a future contract"""
        instrument_id = f"{underlying}_future_{expiry_seconds}"
        expiry_ms = (expiry_seconds + 10) * 1000  # Add 10s buffer, convert to ms

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

    def get_orderbook(self, instrument_id: InstrumentID_t) -> Optional[pd.DataFrame]:
        """Get orderbook for a specific instrument as DataFrame"""
        if instrument_id not in self._orderbook_cache:
            return None

        orderbook = self._orderbook_cache[instrument_id]

        # Convert to DataFrame
        bids_data = [(price, qty, 'bid') for price, qty in orderbook.bids.items()]
        asks_data = [(price, qty, 'ask') for price, qty in orderbook.asks.items()]

        df = pd.DataFrame(bids_data + asks_data, columns=['price', 'quantity', 'side'])
        df['price'] = df['price'] / 100  # Convert cents to dollars
        df = df.sort_values('price', ascending=False)

        return df

    def get_all_orderbooks(self) -> Dict[str, pd.DataFrame]:
        """Get all orderbooks as DataFrames"""
        return {instr: self.get_orderbook(instr)
                for instr in self._orderbook_cache.keys()}

    def get_best_bid_ask(self, instrument_id: InstrumentID_t) -> Optional[Dict[str, Any]]:
        """Get best bid and ask prices for an instrument"""
        if instrument_id not in self._orderbook_cache:
            return None

        orderbook = self._orderbook_cache[instrument_id]

        best_bid = max(orderbook.bids.keys()) if orderbook.bids else None
        best_ask = min(orderbook.asks.keys()) if orderbook.asks else None

        return {
            'instrument': instrument_id,
            'best_bid': best_bid / 100 if best_bid else None,
            'best_ask': best_ask / 100 if best_ask else None,
            'spread': (best_ask - best_bid) / 100 if best_bid and best_ask else None
        }

    def get_market_data(self) -> Optional[MarketDataResponse]:
        """Get cached market data"""
        return self._market_data_cache

    def list_instruments(self) -> List[str]:
        """List all available instruments"""
        return list(self._orderbook_cache.keys())


# Example usage
async def example_usage():
    # Initialize API
    api = GameAPI("ws://192.168.100.10:9001/trade", "5c440ac1-b111-405b-8c3d-a35bfe99933e")

    # Connect
    await api.connect()

    # Wait a bit for market data
    await asyncio.sleep(2)

    # List all instruments first to see what's available
    instruments = api.list_instruments()
    print(f"Available instruments: {instruments[:5]}...")  # Show first 5

    # Find a future instrument if available
    future_instrument = next((instr for instr in instruments if "_future_" in instr), None)
    if future_instrument:
        # Extract parameters from the instrument ID
        parts = future_instrument.split('_')
        underlying = parts[0]
        expiry_seconds = int(parts[2])

        # Buy a future using the available instrument
        resp = await api.buy_future(underlying, expiry_seconds, 39000, quantity=2)
        print(f"Buy future response: {resp}")

        # Get orderbook data for this instrument
        orderbook_df = api.get_orderbook(future_instrument)
        if orderbook_df is not None:
            print("\nOrderbook:")
            print(orderbook_df)

        # Get best bid/ask for this instrument
        best_prices = api.get_best_bid_ask(future_instrument)
        print(f"\nBest prices: {best_prices}")
    else:
        print("No future instruments available")

    # Find a call option instrument if available
    call_instrument = next((instr for instr in instruments if "_call_" in instr), None)
    if call_instrument:
        # Extract parameters from the instrument ID
        parts = call_instrument.split('_')
        underlying = parts[0]
        strike = int(parts[2])
        expiry_seconds = int(parts[3])

        # Buy a call option using the available instrument
        resp = await api.buy_call(underlying, strike, expiry_seconds, 1000)
        print(f"Buy call response: {resp}")
    else:
        print("No call option instruments available")

    # Get inventory
    inventory = await api.get_inventory()
    print(f"Inventory: {inventory}")

    # Disconnect
    await api.disconnect()


if __name__ == "__main__":
    # Run example
    asyncio.run(example_usage())

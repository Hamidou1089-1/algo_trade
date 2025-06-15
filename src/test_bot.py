import random
import time
import asyncio
from api import GameAPI, AddOrderRequest
import logging

logger = logging.getLogger(__name__)

class TradingBot(): 

    def __init__(self, api: GameAPI):
        self.api = api
        self.instrument_id = None
        self.fair_price = None
        self.mid_price = None
        self.orderbook = None

    def get_random_instrument(self):
        """Select a random instrument from available orderbooks"""
        if self.api.current_orderbooks:
            self.instrument_id = random.choice(list(self.api.current_orderbooks.keys()))

            self.orderbook = self.api.current_orderbooks[self.instrument_id]
            logger.info(f"Selected instrument: {self.instrument_id}")
        else:
            logger.info("No instruments available yet")

    def get_best_prices(self):
        """Get best bid and ask prices for the current instrument"""
        if not self.instrument_id or self.instrument_id not in self.api.current_orderbooks:
            return None, None
        
        orderbook = self.api.current_orderbooks[self.instrument_id]
        
        # Get best bid (highest bid price)
        best_bid = None
        if orderbook.bids:
            bid_prices = [int(price) for price in orderbook.bids.keys()]
            best_bid = max(bid_prices)
        
        # Get best ask (lowest ask price)
        best_ask = None
        if orderbook.asks:
            ask_prices = [int(price) for price in orderbook.asks.keys()]
            best_ask = min(ask_prices)
        
        return best_bid, best_ask

    def parse_instrument_id(self):
        """Parse instrument ID to extract type and parameters"""
        if not self.instrument_id:
            return None
        
        parts = self.instrument_id.split("_")
        if len(parts) < 3:
            return None
        
        underlying = parts[0]
        instrument_type = parts[1]
        
        if instrument_type == "future":
            # Format: underlying_future_expiry
            expiry_seconds = int(parts[2])
            return {
                "type": "future",
                "underlying": underlying,
                "expiry_seconds": expiry_seconds
            }
        elif instrument_type in ["call", "put"]:
            # Format: underlying_call/put_strike_expiry
            if len(parts) >= 4:
                strike = int(parts[2])
                expiry_seconds = int(parts[3])
                return {
                    "type": instrument_type,
                    "underlying": underlying,
                    "strike": strike,
                    "expiry_seconds": expiry_seconds
                }
        
        return None

    async def place_buy_order(self, price):
        """Place a buy order using the appropriate API method"""
        if not self.instrument_id:
            return None
        
        instrument_info = self.parse_instrument_id()
        if not instrument_info:
            logger.info(f"Could not parse instrument ID: {self.instrument_id}")
            return None
        
        try:
            if instrument_info["type"] == "future":
                result = await self.api.buy_future(
                    underlying=instrument_info["underlying"],
                    expiry_seconds=instrument_info["expiry_seconds"],
                    price=price,
                    quantity=1
                )
            elif instrument_info["type"] == "call":
                result = await self.api.buy_call(
                    underlying=instrument_info["underlying"],
                    strike=instrument_info["strike"],
                    expiry_seconds=instrument_info["expiry_seconds"],
                    price=price,
                    quantity=1
                )
            elif instrument_info["type"] == "put":
                result = await self.api.buy_put(
                    underlying=instrument_info["underlying"],
                    strike=instrument_info["strike"],
                    expiry_seconds=instrument_info["expiry_seconds"],
                    price=price,
                    quantity=1
                )
            else:
                logger.info(f"Unknown instrument type: {instrument_info['type']}")
                return None
            
            logger.info(f"Buy order placed: {price} for {self.instrument_id}")
            return result
            
        except Exception as e:
            logger.info(f"Failed to place buy order: {e}")
            return None

    async def place_sell_order(self, price):
        """Place a sell order using the appropriate API method"""
        if not self.instrument_id:
            return None
        
        instrument_info = self.parse_instrument_id()
        if not instrument_info:
            logger.info(f"Could not parse instrument ID: {self.instrument_id}")
            return None
        
        try:
            if instrument_info["type"] == "future":
                result = await self.api.sell_future(
                    underlying=instrument_info["underlying"],
                    expiry_seconds=instrument_info["expiry_seconds"],
                    price=price,
                    quantity=1
                )
            elif instrument_info["type"] == "call":
                result = await self.api.sell_call(
                    underlying=instrument_info["underlying"],
                    strike=instrument_info["strike"],
                    expiry_seconds=instrument_info["expiry_seconds"],
                    price=price,
                    quantity=1
                )
            elif instrument_info["type"] == "put":
                result = await self.api.sell_put(
                    underlying=instrument_info["underlying"],
                    strike=instrument_info["strike"],
                    expiry_seconds=instrument_info["expiry_seconds"],
                    price=price,
                    quantity=1
                )
            else:
                logger.info(f"Unknown instrument type: {instrument_info['type']}")
                return None
            
            logger.info(f"Sell order placed: {price} for {self.instrument_id}")
            return result
            
        except Exception as e:
            logger.info(f"Failed to place sell order: {e}")
            return None

    async def run(self):
        """Main trading loop"""
        logger.info("Starting trading bot...")


        while True:
            try:
                # Get random instrument if not selected
                if not self.instrument_id:
                    self.get_random_instrument()
                    if not self.instrument_id:
                        await asyncio.sleep(1)
                        continue
                
                # Get best bid and ask prices
                best_bid, best_ask = self.get_best_prices()

                self.compute_fair_mid_price()
                logger.info(f"Fair price: {self.fair_price}")
                
                if best_bid and best_ask:
                    logger.info(f"Best bid: {best_bid}, Best ask: {best_ask}")
                    
                    if self.mid_price < self.fair_price :
                        # Place buy order at best bid
                        await self.place_buy_order(best_bid)
                        
                    else :
                        # Place sell order at best ask  
                        await self.place_sell_order(best_ask)
                    
                else:
                    logger.info(f"No valid prices for {self.instrument_id}")
                    
                
                # Wait before next iteration
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.info(f"Error in trading loop: {e}")
                await asyncio.sleep(1)

    def compute_fair_mid_price(self): 
        logger.info(f"Computing fair and mid price for {self.instrument_id}")

        logger.info(f"Orderbook: {self.orderbook}")

        if not self.orderbook.bids or not self.orderbook.asks:

            logger.info("No orderbook found, getting random instrument")
            self.get_random_instrument()

            return
        
        bids = self.orderbook.bids
        asks = self.orderbook.asks

        best_bid = None
        if self.orderbook.bids:
            bid_prices = [int(price) for price in self.orderbook.bids.keys()]
            best_bid = max(bid_prices)
        
        # Get best ask (lowest ask price)
        best_ask = None
        if self.orderbook.asks:
            ask_prices = [int(price) for price in self.orderbook.asks.keys()]
            best_ask = min(ask_prices)
        
        best_bid_vol, best_ask_vol = bids[str(best_bid)], asks[str(best_ask)]
        logger.info("arriving here")

        best_bid = int(best_bid)
        best_ask = int(best_ask)
        best_bid_vol = int(best_bid_vol)
        best_ask_vol = int(best_ask_vol)

        self.fair_price = (best_ask*best_ask_vol + best_bid*best_bid_vol) / (best_bid_vol + best_ask_vol)
        self.mid_price = best_bid + ( best_ask - best_bid ) / 2 

        logger.info(f"Fair price: {self.fair_price}, Mid price: {self.mid_price}")
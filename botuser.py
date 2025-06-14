"""
Templates de stratégies pour AlgoTrade 2025
Copiez et modifiez ces templates pour créer vos propres stratégies
"""

import asyncio
import logging
from typing import Dict, List, Optional
import math
from gameapi import GameAPI, GetInventoryResponse, GetPendingOrdersResponse
from market_data_cache import MarketDataCache

logger = logging.getLogger(__name__)

# Configuration commune
EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
TEAM_SECRET = "5c440ac1-b111-405b-8c3d-a35bfe99933e"


class BaseBot:
"""Classe de base pour tous les bots"""

    def __init__(self, market_cache: MarketDataCache, config: Dict = None):
        self.market_cache = market_cache
        self.config = config or {}
        self.api = None
        self.running = False
        self.positions = {}
        self.cash = 0

    async def start(self):
        """Démarre le bot"""
        self.api = GameAPI(EXCHANGE_URI, TEAM_SECRET, self.market_cache)
        await self.api.connect()
        self.running = True
        await self.run()

    async def run(self):
        """À implémenter dans les sous-classes"""
        raise NotImplementedError

    async def stop(self):
        """Arrête le bot"""
        self.running = False
        if self.api:
            await self.api.disconnect()

    async def update_positions(self):
        """Met à jour cash et positions"""
        inventory = await self.api.get_inventory()
        if isinstance(inventory, GetInventoryResponse):
            self.positions = {}
            for instrument, (reserved, owned) in inventory.data.items():
                if instrument == "$":
                    self.cash = owned
                elif owned != 0:
                    self.positions[instrument] = owned


class SimpleMomentumBot(BaseBot):
"""
Bot de momentum trading
- Achète quand le prix monte rapidement
- Vend quand le prix baisse rapidement
"""

    def __init__(self, market_cache: MarketDataCache, config: Dict = None):
        default_config = {
            'underlying': '$CARD',
            'momentum_threshold': 0.02,  # 2% de changement
            'lookback_seconds': 30,      # Période d'observation
            'position_size': 2,          # Taille des positions
            'max_positions': 3,          # Nombre max de positions
            'update_interval': 5         # Intervalle de mise à jour
        }
        super().__init__(market_cache, {**default_config, **(config or {})})
        self.price_history = {}  # instrument -> [(time, price), ...]

    async def run(self):
        logger.info("SimpleMomentumBot started")

        while self.running:
            try:
                # Analyser le momentum
                signals = await self.analyze_momentum()

                # Exécuter les trades
                for instrument, signal in signals.items():
                    if signal == 'BUY' and len(self.positions) < self.config['max_positions']:
                        await self.execute_buy(instrument)
                    elif signal == 'SELL' and instrument in self.positions:
                        await self.execute_sell(instrument)

                await self.update_positions()
                await asyncio.sleep(self.config['update_interval'])

            except Exception as e:
                logger.error(f"Error in momentum bot: {e}")
                await asyncio.sleep(10)

    async def analyze_momentum(self) -> Dict[str, str]:
        """Analyse le momentum des prix"""
        signals = {}
        current_time = asyncio.get_event_loop().time()

        # Obtenir les futures
        instruments = [i for i in self.api.list_instruments()
                       if "_future_" in i and self.config['underlying'] in i]

        for instrument in instruments[:5]:  # Limiter aux 5 premiers
            # Obtenir le prix actuel
            best_prices = self.api.get_best_bid_ask(instrument)
            if not best_prices or not best_prices['best_bid'] or not best_prices['best_ask']:
                continue

            current_price = (best_prices['best_bid'] + best_prices['best_ask']) / 2

            # Mettre à jour l'historique
            if instrument not in self.price_history:
                self.price_history[instrument] = []

            history = self.price_history[instrument]
            history.append((current_time, current_price))

            # Garder seulement les données récentes
            cutoff_time = current_time - self.config['lookback_seconds']
            history[:] = [(t, p) for t, p in history if t > cutoff_time]

            # Calculer le momentum si on a assez de données
            if len(history) >= 5:
                old_price = history[0][1]
                price_change = (current_price - old_price) / old_price

                if price_change > self.config['momentum_threshold']:
                    signals[instrument] = 'BUY'
                    logger.info(f"📈 Momentum UP {instrument}: {price_change:.2%}")
                elif price_change < -self.config['momentum_threshold']:
                    signals[instrument] = 'SELL'
                    logger.info(f"📉 Momentum DOWN {instrument}: {price_change:.2%}")

        return signals

    async def execute_buy(self, instrument: str):
        """Exécute un achat"""
        best_prices = self.api.get_best_bid_ask(instrument)
        if best_prices and best_prices['best_ask']:
            # Acheter au ask + petit premium
            price = int(best_prices['best_ask'] * 1.001 * 100)

            parts = instrument.split('_')
            underlying = parts[0]
            expiry = int(parts[2])

            resp = await self.api.buy_future(
                underlying, expiry, price, self.config['position_size']
            )
            if resp.success:
                logger.info(f"✅ BOUGHT {instrument} @ ${price/100}")

    async def execute_sell(self, instrument: str):
        """Exécute une vente"""
        if instrument not in self.positions:
            return

        best_prices = self.api.get_best_bid_ask(instrument)
        if best_prices and best_prices['best_bid']:
            # Vendre au bid - petit discount
            price = int(best_prices['best_bid'] * 0.999 * 100)

            parts = instrument.split('_')
            underlying = parts[0]
            expiry = int(parts[2])

            resp = await self.api.sell_future(
                underlying, expiry, price, self.positions[instrument]
            )
            if resp.success:
                logger.info(f"✅ SOLD {instrument} @ ${price/100}")


class SimpleOptionsArbitrageBot(BaseBot):
"""
Bot d'arbitrage put-call parity
Cherche des opportunités où: Call - Put ≠ Future - Strike
"""

    def __init__(self, market_cache: MarketDataCache, config: Dict = None):
        default_config = {
            'underlying': '$CARD',
            'min_profit': 100,      # Profit minimum en cents
            'position_size': 1,     # Taille des positions
            'update_interval': 10   # Intervalle de scan
        }
        super().__init__(market_cache, {**default_config, **(config or {})})

    async def run(self):
        logger.info("SimpleOptionsArbitrageBot started")

        while self.running:
            try:
                # Chercher les opportunités d'arbitrage
                opportunities = await self.find_arbitrage_opportunities()

                # Exécuter les meilleures opportunités
                for opp in opportunities[:2]:  # Max 2 à la fois
                    await self.execute_arbitrage(opp)

                await self.update_positions()
                await asyncio.sleep(self.config['update_interval'])

            except Exception as e:
                logger.error(f"Error in arbitrage bot: {e}")
                await asyncio.sleep(10)

    async def find_arbitrage_opportunities(self) -> List[Dict]:
        """Trouve les opportunités d'arbitrage put-call"""
        opportunities = []
        instruments = self.api.list_instruments()

        # Grouper par expiry et strike
        options_by_expiry_strike = {}
        futures_by_expiry = {}

        for instr in instruments:
            if self.config['underlying'] not in instr:
                continue

            parts = instr.split('_')

            if '_future_' in instr:
                expiry = int(parts[2])
                futures_by_expiry[expiry] = instr

            elif '_call_' in instr or '_put_' in instr:
                strike = int(parts[2])
                expiry = int(parts[3])
                key = (expiry, strike)

                if key not in options_by_expiry_strike:
                    options_by_expiry_strike[key] = {}

                if '_call_' in instr:
                    options_by_expiry_strike[key]['call'] = instr
                else:
                    options_by_expiry_strike[key]['put'] = instr

        # Vérifier la parité put-call
        for (expiry, strike), options in options_by_expiry_strike.items():
            if 'call' not in options or 'put' not in options:
                continue
            if expiry not in futures_by_expiry:
                continue

            # Obtenir les prix
            call_prices = self.api.get_best_bid_ask(options['call'])
            put_prices = self.api.get_best_bid_ask(options['put'])
            future_prices = self.api.get_best_bid_ask(futures_by_expiry[expiry])

            if not all([call_prices, put_prices, future_prices]):
                continue

            # Vérifier les deux côtés de l'arbitrage
            # Théorie: C - P = F - K

            # Opportunité 1: Acheter synthétique (C - P trop bas)
            if (call_prices['best_ask'] and put_prices['best_bid'] and
                    future_prices['best_bid']):

                synthetic_cost = call_prices['best_ask'] - put_prices['best_bid']
                future_value = future_prices['best_bid'] - strike/100
                profit = (future_value - synthetic_cost) * 100  # En cents

                if profit > self.config['min_profit']:
                    opportunities.append({
                        'type': 'buy_synthetic',
                        'call': options['call'],
                        'put': options['put'],
                        'future': futures_by_expiry[expiry],
                        'profit': profit,
                        'call_price': call_prices['best_ask'],
                        'put_price': put_prices['best_bid'],
                        'future_price': future_prices['best_bid'],
                        'strike': strike,
                        'expiry': expiry
                    })

        # Trier par profit
        opportunities.sort(key=lambda x: x['profit'], reverse=True)

        if opportunities:
            logger.info(f"Found {len(opportunities)} arbitrage opportunities")
            for opp in opportunities[:3]:
                logger.info(f"  Profit: ${opp['profit']/100:.2f} on {opp['type']}")

        return opportunities

    async def execute_arbitrage(self, opportunity: Dict):
        """Exécute une opportunité d'arbitrage"""
        logger.info(f"Executing arbitrage: {opportunity['type']} for ${opportunity['profit']/100:.2f} profit")

        if opportunity['type'] == 'buy_synthetic':
            # Acheter call, vendre put, vendre future

            # Extraire les paramètres
            underlying = self.config['underlying']
            strike = opportunity['strike']
            expiry = opportunity['expiry']

            # Acheter call
            call_resp = await self.api.buy_call(
                underlying, strike, expiry,
                int(opportunity['call_price'] * 100),
                self.config['position_size']
            )

            # Vendre put
            put_resp = await self.api.sell_put(
                underlying, strike, expiry,
                int(opportunity['put_price'] * 100),
                self.config['position_size']
            )

            # Vendre future
            future_resp = await self.api.sell_future(
                underlying, expiry,
                int(opportunity['future_price'] * 100),
                self.config['position_size']
            )

            if all([call_resp.success, put_resp.success, future_resp.success]):
                logger.info("✅ Arbitrage executed successfully!")
            else:
                logger.warning("⚠️ Arbitrage partially failed")


class SimpleScalpingBot(BaseBot):
"""
Bot de scalping rapide
- Profite des petits écarts bid-ask
- Nécessite une faible latence (utiliser sur Raspberry Pi)
"""

    def __init__(self, market_cache: MarketDataCache, config: Dict = None):
        default_config = {
            'underlying': '$CARD',
            'min_spread': 0.002,    # Spread minimum de 0.2%
            'position_time': 10,    # Temps max pour tenir une position
            'position_size': 1,     # Taille des positions
            'update_interval': 0.5  # Très rapide!
        }
        super().__init__(market_cache, {**default_config, **(config or {})})
        self.active_positions = {}  # instrument -> {'time': t, 'price': p}

    async def run(self):
        logger.info("SimpleScalpingBot started - REQUIRES LOW LATENCY!")

        while self.running:
            try:
                # Chercher les opportunités de scalping
                instruments = [i for i in self.api.list_instruments()
                               if "_future_" in i and self.config['underlying'] in i]

                for instrument in instruments[:3]:  # Limiter pour la vitesse
                    await self.scalp_instrument(instrument)

                # Fermer les positions anciennes
                await self.close_old_positions()

                await asyncio.sleep(self.config['update_interval'])

            except Exception as e:
                logger.error(f"Error in scalping bot: {e}")
                await asyncio.sleep(2)

    async def scalp_instrument(self, instrument: str):
        """Tente de scalper un instrument"""
        orderbook = self.market_cache.get_current_orderbook(instrument)
        if not orderbook or not orderbook.bids or not orderbook.asks:
            return

        # Obtenir le meilleur bid/ask
        best_bid = max(int(p) for p in orderbook.bids.keys())
        best_ask = min(int(p) for p in orderbook.asks.keys())

        spread_pct = (best_ask - best_bid) / best_bid

        # Si le spread est assez large, essayer de le capturer
        if spread_pct > self.config['min_spread'] and instrument not in self.active_positions:
            # Placer un ordre au milieu du spread
            mid_price = (best_bid + best_ask) // 2

            parts = instrument.split('_')
            underlying = parts[0]
            expiry = int(parts[2])

            # Acheter légèrement en dessous du mid
            buy_price = mid_price - 1
            resp = await self.api.buy_future(
                underlying, expiry, buy_price, self.config['position_size']
            )

            if resp.success:
                self.active_positions[instrument] = {
                    'time': asyncio.get_event_loop().time(),
                    'price': buy_price,
                    'order_id': resp.data.order_id
                }
                logger.info(f"🎯 Scalp BUY {instrument} @ ${buy_price/100}")

    async def close_old_positions(self):
        """Ferme les positions trop anciennes"""
        current_time = asyncio.get_event_loop().time()
        to_close = []

        for instrument, pos_info in self.active_positions.items():
            age = current_time - pos_info['time']

            if age > self.config['position_time']:
                to_close.append(instrument)

        for instrument in to_close:
            # Vendre au meilleur bid disponible
            best_prices = self.api.get_best_bid_ask(instrument)
            if best_prices and best_prices['best_bid']:
                parts = instrument.split('_')
                underlying = parts[0]
                expiry = int(parts[2])

                sell_price = int(best_prices['best_bid'] * 100)
                resp = await self.api.sell_future(
                    underlying, expiry, sell_price, self.config['position_size']
                )

                if resp.success:
                    profit = sell_price - self.active_positions[instrument]['price']
                    logger.info(f"🎯 Scalp SELL {instrument} @ ${sell_price/100} (P&L: ${profit/100})")
                    del self.active_positions[instrument]


# Pour utiliser ces bots dans main.py:
"""
from strategy_templates import SimpleMomentumBot, SimpleOptionsArbitrageBot, SimpleScalpingBot

# Dans launch_trading_system():

# Bot de momentum
bot_manager.register_bot(SimpleMomentumBot, config={
'underlying': '$CARD',
'momentum_threshold': 0.015,  # 1.5%
'lookback_seconds': 20
})

# Bot d'arbitrage options
bot_manager.register_bot(SimpleOptionsArbitrageBot, config={
'underlying': '$CARD',
'min_profit': 50  # 50 cents minimum
})

# Bot de scalping (pour Raspberry Pi)
bot_manager.register_bot(SimpleScalpingBot, config={
'underlying': '$CARD',
'min_spread': 0.001,  # 0.1%
'update_interval': 0.2  # Super rapide!
})
"""
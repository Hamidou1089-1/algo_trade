import asyncio
import logging
from typing import Dict, List, Optional
from ..gameapi import GameAPI, GetInventoryResponse, GetPendingOrdersResponse
from market_data_cache import MarketDataCache

logger = logging.getLogger(__name__)

# Configuration
EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
TEAM_SECRET = "5c440ac1-b111-405b-8c3d-a35bfe99933e"


class SimpleMarketMakingBot:
    """
    Bot de market making simple pour les futures
    - Place des ordres bid/ask autour du prix moyen
    - Maintient un inventaire équilibré
    - Gère le risque avec des limites de position
    """

    def __init__(self, market_cache: MarketDataCache, config: Dict = None):
        """
        Args:
            market_cache: Instance partagée du cache de données de marché
            config: Configuration optionnelle du bot
        """
        self.market_cache = market_cache

        # Configuration par défaut
        default_config = {
            'underlying': '$CARD',
            'spread_percent': 0.5,  # 0.5% de spread
            'max_position': 10,     # Position max par instrument
            'order_size': 1,        # Taille des ordres
            'min_time_to_expiry': 60,  # Ne pas trader < 60s avant expiration
            'update_interval': 2    # Intervalle entre les mises à jour
        }

        # Merge avec la config fournie
        self.config = {**default_config, **(config or {})}

        # GameAPI instance (sera créée dans start())
        self.api = None

        # État
        self.positions = {}  # instrument_id -> net_position
        self.running = False

        logger.info(f"SimpleMarketMakingBot initialized with config: {self.config}")

    async def start(self):
        """Démarre le bot (appelé par BotManager)"""
        try:
            # Créer et connecter GameAPI
            self.api = GameAPI(EXCHANGE_URI, TEAM_SECRET, self.market_cache)
            await self.api.connect()
            logger.info("SimpleMarketMakingBot connected to trading API")

            # Attendre un peu pour s'assurer que le cache a des données
            await asyncio.sleep(3)

            self.running = True
            await self.run()

        except Exception as e:
            logger.error(f"Error starting SimpleMarketMakingBot: {e}")
            self.running = False
            raise

    async def run(self):
        """Boucle principale du bot"""
        logger.info("SimpleMarketMakingBot main loop started")

        while self.running:
            try:
                # 1. Analyser le marché
                market_analysis = await self.analyze_market()

                if market_analysis:
                    # 2. Annuler les anciens ordres
                    #await self.cancel_all_orders()

                    # 3. Placer de nouveaux ordres
                    await self.place_orders(market_analysis)

                    # 4. Gérer le risque et afficher l'état
                    await self.update_positions()
                else:
                    logger.debug("No market opportunities found")

                # Attendre avant la prochaine itération
                await asyncio.sleep(self.config['update_interval'])

            except Exception as e:
                logger.error(f"Error in bot main loop: {e}")
                await asyncio.sleep(5)

    async def analyze_market(self) -> Dict:
        """Analyser le marché pour identifier les opportunités"""
        analysis = {}

        # Obtenir tous les instruments
        instruments = self.api.list_instruments()

        # Filtrer les futures de notre underlying
        future_instruments = [
            i for i in instruments
            if "_future_" in i and self.config['underlying'] in i
        ]

        logger.debug(f"Found {len(future_instruments)} future instruments for {self.config['underlying']}")

        for instrument in future_instruments:
            try:
                # Extraire l'expiry
                parts = instrument.split("_")
                expiry_seconds = int(parts[-1])

                # Skip si trop proche de l'expiration
                if expiry_seconds < self.config['min_time_to_expiry']:
                    continue

                # Obtenir les meilleurs prix
                best_prices = self.api.get_best_bid_ask(instrument)

                if best_prices and best_prices['best_bid'] and best_prices['best_ask']:
                    # Calculer le mid price
                    mid_price = (best_prices['best_bid'] + best_prices['best_ask']) / 2

                    # Calculer nos prix avec notre spread
                    our_bid = int(mid_price * (1 - self.config['spread_percent']/100) * 100)
                    our_ask = int(mid_price * (1 + self.config['spread_percent']/100) * 100)

                    analysis[instrument] = {
                        'mid_price': mid_price,
                        'our_bid': our_bid,
                        'our_ask': our_ask,
                        'expiry': expiry_seconds,
                        'market_spread': best_prices['spread'],
                        'market_bid': best_prices['best_bid'],
                        'market_ask': best_prices['best_ask']
                    }

            except Exception as e:
                logger.error(f"Error analyzing {instrument}: {e}")
                continue

        logger.info(f"Market analysis complete: {len(analysis)} tradeable instruments")
        return analysis

    async def cancel_all_orders(self):
        """Annuler tous nos ordres en attente"""
        try:
            pending_orders = await self.api.get_pending_orders()

            if isinstance(pending_orders, GetPendingOrdersResponse):
                total_cancelled = 0

                for instrument_id, (bids, asks) in pending_orders.data.items():
                    # Annuler tous les bids
                    for bid_order in bids:
                        await self.api.cancel_order(instrument_id, bid_order.orderID)
                        total_cancelled += 1

                    # Annuler tous les asks
                    for ask_order in asks:
                        await self.api.cancel_order(instrument_id, ask_order.orderID)
                        total_cancelled += 1

                if total_cancelled > 0:
                    logger.info(f"Cancelled {total_cancelled} orders")

        except Exception as e:
            logger.error(f"Error cancelling orders: {e}")

    async def place_orders(self, market_analysis: Dict):
        """Placer des ordres basés sur l'analyse du marché"""
        orders_placed = 0

        for instrument, analysis in market_analysis.items():
            try:
                # Vérifier notre position actuelle
                current_position = self.positions.get(instrument, 0)

                # Vérifier les limites de position
                can_buy = current_position < self.config['max_position']
                can_sell = current_position > -self.config['max_position']

                # Ne trader que si le spread du marché est suffisant
                if analysis['market_spread'] and analysis['market_spread'] > 0.01:

                    # Extraire l'expiry pour les ordres
                    expiry = analysis['expiry']

                    # Placer un ordre bid si on peut acheter
                    if can_buy:
                        resp = await self.api.buy_future(
                            self.config['underlying'],
                            expiry,
                            analysis['our_bid'],
                            self.config['order_size']
                        )
                        if resp.success:
                            logger.info(f"✅ BID placed on {instrument} @ ${analysis['our_bid']/100:.2f}")
                            orders_placed += 1
                        else:
                            logger.warning(f"❌ BID failed on {instrument}: {resp.data.message if hasattr(resp, 'data') else 'Unknown error'}")

                    # Placer un ordre ask si on peut vendre
                    if can_sell:
                        resp = await self.api.sell_future(
                            self.config['underlying'],
                            expiry,
                            analysis['our_ask'],
                            self.config['order_size']
                        )
                        if resp.success:
                            logger.info(f"✅ ASK placed on {instrument} @ ${analysis['our_ask']/100:.2f}")
                            orders_placed += 1
                        else:
                            logger.warning(f"❌ ASK failed on {instrument}: {resp.data.message if hasattr(resp, 'data') else 'Unknown error'}")

            except Exception as e:
                logger.error(f"Error placing orders for {instrument}: {e}")
                continue

        logger.info(f"Order placement complete: {orders_placed} orders placed")

    async def update_positions(self):
        """Mettre à jour les positions et afficher l'état"""
        try:
            inventory = await self.api.get_inventory()

            if isinstance(inventory, GetInventoryResponse):
                # Mettre à jour nos positions
                self.positions = {}
                total_value = 0

                for instrument, (reserved, owned) in inventory.data.items():
                    if instrument == "$":
                        # Cash
                        cash = owned
                        total_value += cash
                    else:
                        # Positions
                        if owned != 0:
                            self.positions[instrument] = owned
                            # Estimer la valeur (utiliser le mid price actuel)
                            best_prices = self.api.get_best_bid_ask(instrument)
                            if best_prices and best_prices['best_bid'] and best_prices['best_ask']:
                                mid_price = (best_prices['best_bid'] + best_prices['best_ask']) / 2
                                total_value += owned * mid_price * 100  # Convertir en cents

                # Afficher l'état du portfolio
                logger.info("=== Portfolio Status ===")
                logger.info(f"💰 Cash: ${cash/100:.2f}")
                logger.info(f"📊 Positions: {len(self.positions)}")
                logger.info(f"💎 Total Value (est.): ${total_value/100:.2f}")

                # Afficher les positions
                if self.positions:
                    logger.info("Current positions:")
                    for instr, qty in self.positions.items():
                        logger.info(f"  {instr}: {qty}")

                # Alerter si position excessive
                for instrument, position in self.positions.items():
                    if abs(position) > self.config['max_position'] * 1.5:
                        logger.warning(f"⚠️ Excessive position on {instrument}: {position}")

        except Exception as e:
            logger.error(f"Error updating positions: {e}")

    async def stop(self):
        """Arrêter le bot proprement"""
        logger.info("Stopping SimpleMarketMakingBot...")
        self.running = False

        # Annuler tous les ordres
        await self.cancel_all_orders()

        # Déconnecter l'API
        if self.api:
            await self.api.disconnect()

        logger.info("SimpleMarketMakingBot stopped")
import asyncio

from src import gameapi
from gameapi import GameAPI
import pandas as pd
from typing import Dict, List

class SimpleMarketMakingStrategy:
    """
    Stratégie simple de market making sur les futures
    - Place des ordres bid/ask autour du prix moyen du marché
    - Maintient un inventaire équilibré
    - Gère le risque avec des limites de position
    """

    def __init__(self, api: GameAPI, underlying: str = "$CARD"):
        self.api = api
        self.underlying = underlying

        # Paramètres de la stratégie
        self.spread_percent = 0.5  # 0.5% de spread
        self.max_position = 10     # Position max par instrument
        self.order_size = 1        # Taille des ordres
        self.min_time_to_expiry = 60  # Ne pas trader les futures qui expirent dans moins de 60s

        # État
        self.active_orders = {}    # order_id -> (instrument_id, side, price)
        self.positions = {}        # instrument_id -> net_position

    async def run(self):
        """Boucle principale de la stratégie"""
        while True:
            try:
                # 1. Analyser le marché
                market_analysis = await self.analyze_market()

                # 2. Annuler les anciens ordres
                await self.cancel_stale_orders()

                # 3. Placer de nouveaux ordres
                await self.place_orders(market_analysis)

                # 4. Gérer le risque
                await self.manage_risk()

                # Attendre avant la prochaine itération
                await asyncio.sleep(1)

            except Exception as e:
                print(f"Erreur dans la stratégie: {e}")
                await asyncio.sleep(5)

    async def analyze_market(self) -> Dict:
        """Analyser le marché pour identifier les opportunités"""
        analysis = {}

        # Lister tous les futures disponibles
        instruments = self.api.list_instruments()
        future_instruments = [i for i in instruments if "_future_" in i and self.underlying in i]

        for instrument in future_instruments:
            # Extraire l'expiry
            expiry_seconds = int(instrument.split("_")[-1])

            # Skip si trop proche de l'expiration
            if expiry_seconds < self.min_time_to_expiry:
                continue

            # Obtenir les meilleurs prix
            best_prices = self.api.get_best_bid_ask(instrument)

            if best_prices and best_prices['best_bid'] and best_prices['best_ask']:
                # Calculer le mid price
                mid_price = (best_prices['best_bid'] + best_prices['best_ask']) / 2

                # Calculer nos prix bid/ask avec notre spread
                our_bid = int(mid_price * (1 - self.spread_percent/100) * 100)  # Convertir en cents
                our_ask = int(mid_price * (1 + self.spread_percent/100) * 100)

                analysis[instrument] = {
                    'mid_price': mid_price,
                    'our_bid': our_bid,
                    'our_ask': our_ask,
                    'expiry': expiry_seconds,
                    'spread': best_prices['spread']
                }

        return analysis

    async def cancel_stale_orders(self):
        """Annuler les ordres qui ne sont plus pertinents"""
        pending_orders = await self.api.get_pending_orders()

        if isinstance(pending_orders, GetPendingOrdersResponse):
            for instrument_id, (bids, asks) in pending_orders.data.items():
                # Annuler tous nos ordres pour refaire le pricing
                for bid_order in bids:
                    await self.api.cancel_order(instrument_id, bid_order.orderID)

                for ask_order in asks:
                    await self.api.cancel_order(instrument_id, ask_order.orderID)

    async def place_orders(self, market_analysis: Dict):
        """Placer des ordres basés sur l'analyse du marché"""

        for instrument, analysis in market_analysis.items():
            # Vérifier notre position actuelle
            current_position = self.positions.get(instrument, 0)

            # Ne pas placer d'ordres si on est à la limite de position
            can_buy = current_position < self.max_position
            can_sell = current_position > -self.max_position

            # Placer un ordre bid si on peut acheter
            if can_buy and analysis['spread'] > 0.01:  # Spread minimum requis
                resp = await self.api.buy_future(
                    self.underlying,
                    analysis['expiry'],
                    analysis['our_bid'],
                    self.order_size
                )
                print(f"Placed BID on {instrument} @ ${analysis['our_bid']/100}")

            # Placer un ordre ask si on peut vendre
            if can_sell and analysis['spread'] > 0.01:
                resp = await self.api.sell_future(
                    self.underlying,
                    analysis['expiry'],
                    analysis['our_ask'],
                    self.order_size
                )
                print(f"Placed ASK on {instrument} @ ${analysis['our_ask']/100}")

    async def manage_risk(self):
        """Gérer le risque et l'inventaire"""
        # Mettre à jour les positions depuis l'inventaire
        inventory = await self.api.get_inventory()

        if isinstance(inventory, GetInventoryResponse):
            # Mettre à jour nos positions
            for instrument, (reserved, owned) in inventory.data.items():
                if instrument != "$":  # Skip cash
                    self.positions[instrument] = owned

            # Afficher l'état
            cash = inventory.data.get("$", (0, 0))[1]
            print(f"\n=== État du Portfolio ===")
            print(f"Cash: ${cash/100}")
            print(f"Positions: {len(self.positions)}")

            # Si on a trop de positions sur un instrument, essayer de réduire
            for instrument, position in self.positions.items():
                if abs(position) > self.max_position * 1.5:
                    print(f"⚠️  Position excessive sur {instrument}: {position}")
                    # Ici on pourrait implémenter une logique pour réduire la position


# Stratégie alternative : Arbitrage simple entre futures
class SimpleFuturesArbitrageStrategy:
    """
    Stratégie d'arbitrage entre futures de différentes maturités
    Si future court terme > future long terme + seuil → vendre court, acheter long
    """

    def __init__(self, api: GameAPI, underlying: str = "$CARD"):
        self.api = api
        self.underlying = underlying
        self.min_profit_threshold = 50  # Profit minimum en cents

    async def find_arbitrage_opportunities(self):
        """Chercher des opportunités d'arbitrage"""
        instruments = self.api.list_instruments()
        futures = sorted([i for i in instruments if "_future_" in i and self.underlying in i])

        opportunities = []

        # Comparer toutes les paires de futures
        for i in range(len(futures)):
            for j in range(i+1, len(futures)):
                future1 = futures[i]
                future2 = futures[j]

                # Obtenir les prix
                prices1 = self.api.get_best_bid_ask(future1)
                prices2 = self.api.get_best_bid_ask(future2)

                if prices1 and prices2:
                    # Vérifier s'il y a une opportunité d'arbitrage
                    # (Simplification: on ignore le time value pour cet exemple)
                    if prices1['best_ask'] and prices2['best_bid']:
                        profit = prices2['best_bid'] - prices1['best_ask']

                        if profit > self.min_profit_threshold / 100:
                            opportunities.append({
                                'buy': future1,
                                'sell': future2,
                                'profit': profit,
                                'buy_price': prices1['best_ask'],
                                'sell_price': prices2['best_bid']
                            })

        return opportunities


# Fonction principale pour lancer une stratégie
async def main():
    # Configuration
    EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
    TEAM_SECRET = "YOUR_TEAM_SECRET"

    # Initialiser l'API
    api = GameAPI(EXCHANGE_URI, TEAM_SECRET)
    await api.connect()

    # Attendre un peu pour recevoir les données de marché
    await asyncio.sleep(2)

    # Choisir et lancer une stratégie
    strategy = SimpleMarketMakingStrategy(api)
    # ou strategy = SimpleFuturesArbitrageStrategy(api)

    try:
        await strategy.run()
    except KeyboardInterrupt:
        print("\nArrêt de la stratégie...")
    finally:
        await api.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
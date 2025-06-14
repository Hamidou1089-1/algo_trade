import asyncio
import logging
from market_data_cache import MarketDataCache
from gameapi import GameAPI

# Configuration
EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
TEAM_SECRET = "5c440ac1-b111-405b-8c3d-a35bfe99933e"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_market_data_cache():
    """Test de connexion au MarketDataCache"""
    logger.info("=== TEST MARKET DATA CACHE ===")

    cache = MarketDataCache(EXCHANGE_URI, TEAM_SECRET, print_updates=False)
    await cache.connect()

    # Attendre pour recevoir des données
    await asyncio.sleep(5)

    # Afficher les statistiques
    stats = cache.get_market_statistics()
    logger.info(f"Updates reçus: {stats['total_updates_received']}")
    logger.info(f"Instruments découverts: {stats['instruments_discovered']}")

    # Afficher quelques instruments
    instruments = cache.get_all_instruments()
    if instruments:
        logger.info(f"Premiers instruments: {instruments[:5]}")

        # Tester les prix
        for instr in instruments[:3]:
            best_bid, best_ask = cache.get_best_prices(instr)
            spread = cache.get_current_spread(instr)
            logger.info(f"{instr}: Bid={best_bid}, Ask={best_ask}, Spread={spread}")

    return cache


async def test_game_api(market_cache):
    """Test de connexion GameAPI et passage d'ordres"""
    logger.info("\n=== TEST GAME API ===")

    api = GameAPI(EXCHANGE_URI, TEAM_SECRET, market_cache)
    await api.connect()

    # Attendre un peu
    await asyncio.sleep(2)

    # Tester l'inventaire
    logger.info("Test get_inventory()...")
    inventory = await api.get_inventory()
    logger.info(f"Inventory: {inventory}")

    # Lister les instruments
    instruments = api.list_instruments()
    logger.info(f"Nombre d'instruments: {len(instruments)}")

    # Chercher un future pour tester
    future_instrument = next((i for i in instruments if "_future_" in i), None)

    if future_instrument:
        logger.info(f"\nTest avec instrument: {future_instrument}")

        # Obtenir les prix
        best_prices = api.get_best_bid_ask(future_instrument)
        logger.info(f"Meilleurs prix: {best_prices}")

        # Obtenir le carnet d'ordres
        orderbook = api.get_orderbook(future_instrument)
        if orderbook is not None and not orderbook.empty:
            logger.info(f"Carnet d'ordres (top 5):")
            logger.info(orderbook.head())

        # Tester un ordre (très petit pour ne pas impacter)
        if best_prices and best_prices['best_bid']:
            # Placer un bid très bas pour tester
            test_price = int(best_prices['best_bid'] * 0.8 * 100)  # 20% en dessous

            parts = future_instrument.split('_')
            underlying = parts[0]
            expiry = int(parts[2])

            logger.info(f"\nTest ordre: BUY {underlying} @ {test_price/100}")
            resp = await api.buy_future(underlying, expiry, test_price, 1)
            logger.info(f"Réponse: {resp}")

            if resp.success:
                # Annuler immédiatement
                await asyncio.sleep(1)
                logger.info("Annulation de l'ordre test...")
                cancel_resp = await api.cancel_order(future_instrument, resp.data.order_id)
                logger.info(f"Annulation: {cancel_resp}")

    await api.disconnect()


async def main():
    """Test complet du système"""
    try:
        # Test 1: MarketDataCache
        cache = await test_market_data_cache()

        # Test 2: GameAPI
        await test_game_api(cache)

        logger.info("\n✅ TOUS LES TESTS PASSÉS AVEC SUCCÈS!")
        logger.info("Vous pouvez maintenant lancer main.py pour démarrer le trading")

    except Exception as e:
        logger.error(f"❌ ERREUR DANS LES TESTS: {e}", exc_info=True)
    finally:
        # Fermer la connexion
        if 'cache' in locals() and cache.ws:
            await cache.ws.close()


if __name__ == "__main__":
    asyncio.run(main())
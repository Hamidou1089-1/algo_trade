# AlgoTrade 2025 - Système de Trading Automatisé

## Structure du Projet

```
.
├── market_data_cache.py   # Cache partagé pour les données de marché
├── gameapi.py            # API pour les opérations de trading
├── simple_strategy.py    # Bot de market making simple
├── main.py              # Point d'entrée principal avec BotManager
└── README.md           # Ce fichier
```

## Architecture

1. **MarketDataCache** : Une seule connexion WebSocket partagée qui reçoit toutes les données de marché
2. **GameAPI** : Interface pour passer des ordres de trading (utilise le cache partagé pour les données)
3. **BotManager** : Gestionnaire central qui coordonne le cache et les bots
4. **Bots de Trading** : Stratégies individuelles qui utilisent GameAPI pour trader

## Démarrage Rapide

### 1. Installation des dépendances

```bash
pip install websockets pandas asyncio
```

### 2. Configuration

Modifiez les constantes dans `main.py` :
```python
EXCHANGE_URI = "ws://192.168.100.10:9001/trade"
TEAM_SECRET = "YOUR_TEAM_SECRET_HERE"
```

### 3. Lancer le système

```bash
python main.py
```

## Créer Votre Propre Bot

### Structure de base d'un bot :

```python
class MyTradingBot:
    def __init__(self, market_cache: MarketDataCache, config: Dict = None):
        self.market_cache = market_cache
        self.config = config or {}
        self.api = None
        self.running = False
        
    async def start(self):
        """Méthode appelée par BotManager pour démarrer le bot"""
        # Créer et connecter GameAPI
        self.api = GameAPI(EXCHANGE_URI, TEAM_SECRET, self.market_cache)
        await self.api.connect()
        
        self.running = True
        await self.run()
        
    async def run(self):
        """Logique principale du bot"""
        while self.running:
            # Votre stratégie ici
            instruments = self.api.list_instruments()
            # ...
            await asyncio.sleep(1)
            
    async def stop(self):
        """Arrêt propre du bot"""
        self.running = False
        if self.api:
            await self.api.disconnect()
```

### Enregistrer votre bot dans main.py :

```python
bot_manager.register_bot(
    MyTradingBot,
    config={
        'param1': 'value1',
        'param2': 'value2'
    }
)
```

## Fonctionnalités Disponibles

### GameAPI - Méthodes de Trading

- `buy_future(underlying, expiry, price, quantity)`
- `sell_future(underlying, expiry, price, quantity)`
- `buy_call(underlying, strike, expiry, price, quantity)`
- `sell_call(underlying, strike, expiry, price, quantity)`
- `buy_put(underlying, strike, expiry, price, quantity)`
- `sell_put(underlying, strike, expiry, price, quantity)`
- `cancel_order(instrument_id, order_id)`
- `get_inventory()` - Récupère cash et positions
- `get_pending_orders()` - Liste tous les ordres en attente

### GameAPI - Méthodes de Données de Marché

- `list_instruments()` - Liste tous les instruments disponibles
- `get_orderbook(instrument_id)` - Carnet d'ordres en DataFrame
- `get_best_bid_ask(instrument_id)` - Meilleurs prix bid/ask
- `get_instrument_info(instrument_id)` - Infos détaillées
- `get_recent_events(limit)` - Événements récents du marché
- `get_market_statistics()` - Statistiques globales

## Logs et Monitoring

Les logs sont écrits dans :
- Console (stdout)
- Fichier `algo_trade.log`

Le MarketDataCache affiche un résumé toutes les 30 secondes avec :
- Instruments découverts
- Meilleurs prix actuels
- Statistiques de connexion

## Exemples de Stratégies

### 1. Market Making Simple (inclus)
- Place des ordres bid/ask avec un spread fixe
- Gère les limites de position
- S'adapte aux prix du marché

### 2. Idées de Stratégies à Implémenter

**Arbitrage de Futures** :
```python
# Chercher des différences de prix entre futures de différentes maturités
if future_court_terme.ask < future_long_terme.bid - frais:
    acheter(future_court_terme)
    vendre(future_long_terme)
```

**Options Delta Neutral** :
```python
# Maintenir un delta neutre en hedgeant les options avec des futures
delta_total = sum(option.delta * option.position)
futures_needed = -delta_total
```

**Momentum Trading** :
```python
# Suivre la tendance sur les changements de prix rapides
if prix_actuel > prix_il_y_a_10s * 1.01:  # +1% en 10s
    acheter()
```


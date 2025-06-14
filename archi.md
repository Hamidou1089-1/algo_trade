# Structure du Projet AlgoTrade 2025

## 🏗️ Architecture Simplifiée

```
┌─────────────────────────────────────────────────────────────┐
│                        BotManager (main.py)                   │
│  - Initialise MarketDataCache                                │
│  - Crée et gère les bots                                     │
│  - Monitoring global                                         │
└──────────────────┬────────────────┬─────────────────────────┘
                   │                │
                   ▼                ▼
┌──────────────────────────┐  ┌─────────────────────────────┐
│   MarketDataCache        │  │   Trading Bots              │
│   (market_data_cache.py) │  │   (simple_strategy.py, ...) │
│                          │  │                             │
│  - 1 connexion WebSocket │  │  - Utilisent GameAPI        │
│  - Reçoit market data    │  │  - Implémentent stratégies │
│  - Cache partagé         │  │  - Passent les ordres      │
└──────────────────────────┘  └──────────────┬──────────────┘
           │                                  │
           │                                  ▼
           │                   ┌─────────────────────────────┐
           └──────────────────▶│      GameAPI                │
                               │      (gameapi.py)           │
                               │                             │
                               │  - Interface de trading     │
                               │  - Utilise MarketDataCache  │
                               │  - Gère les ordres          │
                               └─────────────────────────────┘
```

## 📁 Fichiers du Projet

### Fichiers Core
- **`market_data_cache.py`** - Gestion centralisée des données de marché
- **`gameapi.py`** - API pour les opérations de trading
- **`main.py`** - Point d'entrée avec BotManager

### Stratégies
- **`simple_strategy.py`** - Bot de market making simple
- **`strategy_templates.py`** - Templates pour créer vos stratégies

### Utilitaires
- **`test_connection.py`** - Test de connexion et validation
- **`README.md`** - Documentation

## 🚀 Flux d'Exécution

1. **Démarrage** (`main.py`)
   ```python
   # 1. Créer BotManager
   bot_manager = BotManager()
   
   # 2. Initialiser MarketDataCache (1 seule instance)
   await bot_manager.initialize_market_cache()
   
   # 3. Enregistrer les bots
   bot_manager.register_bot(SimpleMarketMakingBot, config={...})
   
   # 4. Démarrer tous les bots
   await bot_manager.start_all_bots()
   ```

2. **Dans chaque Bot**
   ```python
   def __init__(self, market_cache, config):
       self.market_cache = market_cache  # Reçu du BotManager
       
   async def start(self):
       self.api = GameAPI(uri, secret, self.market_cache)
       await self.api.connect()
       await self.run()
   ```

3. **Trading**
   ```python
   # Le bot utilise GameAPI pour trader
   await self.api.buy_future(underlying, expiry, price, qty)
   
   # Et MarketDataCache pour les données
   best_prices = self.api.get_best_bid_ask(instrument)
   ```

## 🔑 Points Clés

### 1. **Une seule connexion Market Data**
- `MarketDataCache` maintient LA connexion WebSocket pour les données
- Tous les bots partagent ce cache
- Évite la duplication et les limites de connexion

### 2. **GameAPI par Bot**
- Chaque bot a sa propre instance `GameAPI`
- Permet à chaque bot de trader indépendamment
- Utilise le `MarketDataCache` partagé pour les données

### 3. **Modularité**
- Facile d'ajouter de nouveaux bots
- Chaque bot est indépendant
- Configuration flexible par bot

## 📝 Créer un Nouveau Bot

1. **Créer le fichier** `my_bot.py`:
```python
from gameapi import GameAPI
from market_data_cache import MarketDataCache

class MyBot:
    def __init__(self, market_cache: MarketDataCache, config: Dict):
        self.market_cache = market_cache
        self.config = config
        self.api = None
        
    async def start(self):
        self.api = GameAPI(EXCHANGE_URI, TEAM_SECRET, self.market_cache)
        await self.api.connect()
        await self.run()
        
    async def run(self):
        while True:
            # Votre stratégie ici
            instruments = self.api.list_instruments()
            # ...
            await asyncio.sleep(1)
```

2. **L'enregistrer dans** `main.py`:
```python
from my_bot import MyBot

# Dans launch_trading_system():
bot_manager.register_bot(MyBot, config={'param': 'value'})
```

## 🎯 Workflow de Développement

1. **Tester la connexion**
   ```bash
   python test_connection.py
   ```

2. **Développer votre stratégie**
    - Utiliser `strategy_templates.py` comme base
    - Tester avec de petites positions

3. **Lancer le système complet**
   ```bash
   python main.py
   ```

4. **Monitorer**
    - Logs dans la console et `algo_trade.log`
    - Résumé MarketDataCache toutes les 30s
    - État de chaque bot dans ses logs


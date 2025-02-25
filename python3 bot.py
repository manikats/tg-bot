import asyncio
import logging
import numba
import re
from datetime import datetime, timedelta
from aiohttp import ClientSession, ClientWebSocketResponse
from aioredis import Redis
from telegram import Update
from telegram.ext import Updater, MessageHandler, Filters, CallbackContext

# Configuration
TELEGRAM_BOT_TOKEN = '7818808601:AAHRDtP5JuWy60n63zee7Ss1SbB39q73wNw
'
SOLANA_RPC_URL = 'https://api.mainnet-beta.solana.com'
DEXSCREENER_WS_URL = 'wss://io.dexscreener.com/dex/screener/pairs'
CACHE_TTL = 600  # 10 minutes

class OptimizedSolanaTradingBot:
    def __init__(self):
        self.session = None
        self.redis = None
        self.ws = None
        self.jit_analyze = numba.jit(nopython=True)(self._analyze_trends_numba)
        
    async def initialize(self):
        self.session = ClientSession()
        self.redis = await Redis.from_url('redis://localhost:6379', decode_responses=True)
        self.ws = await self.session.ws_connect(DEXSCREENER_WS_URL)
        asyncio.create_task(self._ws_listener())

    async def _ws_listener(self):
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = msg.json()
                await self._process_ws_update(data)

    async def _process_ws_update(self, data):
        pair = data.get('pair')
        if pair and pair['chainId'] == 'solana':
            await self.redis.set(
                f"solana:pair:{pair['address']}",
                json.dumps(pair),
                ex=CACHE_TTL
            )

    @rate_limited(30, 10)  # 30 calls per 10 seconds
    async def _get_cached_pair(self, token_address):
        cached = await self.redis.get(f"solana:pair:{token_address}")
        if cached:
            return json.loads(cached)
        
        async with self.session.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        ) as resp:
            data = await resp.json()
            pair = next((p for p in data['pairs'] if p['dexId'] in {'raydium', 'orca'}), None)
            if pair:
                await self.redis.set(
                    f"solana:pair:{token_address}",
                    json.dumps(pair),
                    ex=CACHE_TTL
                )
            return pair

    async def process_message(self, update: Update, context: CallbackContext):
        text = update.message.text
        tokens = re.findall(r'[1-9A-HJ-NP-Za-km-z]{32,44}', text)
        
        for token in tokens[:5]:  # Process max 5 tokens per message
            asyncio.create_task(self._process_token(token))

    async def _process_token(self, token_address):
        cached_data = await self._get_cached_pair(token_address)
        if not cached_data:
            return

        if not await self._safety_checks(token_address):
            return

        analysis = await self._perform_analysis(cached_data)
        if analysis['score'] > 0.8:
            await self._send_alert(token_address, analysis)

    async def _safety_checks(self, token_address):
        checks = await asyncio.gather(
            self._check_holder_distribution(token_address),
            self._check_lp_lock(token_address),
            self._check_slerf_protection(token_address),
            self._simulate_swap(token_address)
        )
        return all(checks)

    async def _check_holder_distribution(self, token_address):
        holders = await self._get_holders(token_address)
        total = sum(h['amount'] for h in holders)
        return all((h['amount']/total) <= 0.1 for h in holders[:3])

    async def _check_lp_lock(self, token_address):
        async with self.session.get(
            f"https://api.raydium.io/v2/main/pool/liquidity/{token_address}"
        ) as resp:
            data = await resp.json()
            return data.get('liquidity_locked', 0) > 0

    async def _check_slerf_protection(self, token_address):
        async with self.session.get(
            f"https://api.solscan.io/token/meta?tokenAddress={token_address}"
        ) as resp:
            data = await resp.json()
            return not data.get('isSlerf', False)

    async def _simulate_swap(self, token_address):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "simulateTransaction",
            "params": [
                "<SIGNED_TRANSACTION>",
                {"encoding": "jsonParsed"}
            ]
        }
        async with self.session.post(SOLANA_RPC_URL, json=payload) as resp:
            result = await resp.json()
            return not result.get('error')

    async def _perform_analysis(self, pair_data):
        prices = [p['priceUsd'] for p in pair_data['priceHistory'][-10:]]
        volumes = [v['volume'] for v in pair_data['volumeHistory'][-10:]]
        
        return {
            'score': self.jit_analyze(prices, volumes),
            'price': pair_data['priceUsd'],
            'liquidity': pair_data['liquidity']['usd']
        }

    @staticmethod
    def _analyze_trends_numba(prices, volumes):
        # Numba-optimized calculation
        price_change = (prices[-1] - prices[0]) / prices[0]
        volume_change = (volumes[-1] - volumes[0]) / volumes[0]
        return (price_change * 0.7) + (volume_change * 0.3)

    async def _send_alert(self, token_address, analysis):
        message = (
            f"🚨 SOLANA ALERT 🚨\n"
            f"🔗 Address: `{token_address}`\n"
            f"💰 Price: ${analysis['price']:.4f}\n"
            f"📈 Score: {analysis['score']:.2f}/1.0\n"
            f"💧 Liquidity: ${analysis['liquidity']:,.0f}\n"
            f"[DEX Screener](https://dexscreener.com/solana/{token_address})"
        )
        
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=message,
            parse_mode='Markdown'
        )

async def main():
    bot = OptimizedSolanaTradingBot()
    await bot.initialize()
    
    updater = Updater(TELEGRAM_BOT_TOKEN)
    updater.dispatcher.add_handler(MessageHandler(Filters.text, bot.process_message))
    
    await updater.start_polling()
    await updater.idle()
    await bot.close()

if _name_ == '_main_':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())
api.mainnet-beta.solana.com

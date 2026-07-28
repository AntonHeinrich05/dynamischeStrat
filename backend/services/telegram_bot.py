import os
from telegram import Bot
from telegram.constants import ParseMode
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Send trading signals via Telegram"""
    
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.frontend_url = os.getenv('FRONTEND_URL', 'https://crypto-scanner-frontend-a98r.onrender.com')
        self.bot = None
        
        if self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
                logger.info("Telegram bot initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
    
    def format_signal_message(self, signal: Dict) -> str:
        """Format signal data into Telegram message with better SL/TP context and website link"""
        signal_type = signal['type']
        is_pre_signal = signal.get('signal_class') == 'PRE_SIGNAL'
        strategy_name = signal.get('strategy_name', 'Scalping')
        
        if is_pre_signal:
            emoji = "🟡"
            title = f"⚠️ *PRE-{signal_type} WARNING*"
            action = "🔔 *Trade vorbereiten - 4. Regel steht bevor!*"
        else:
            emoji = "🟢" if signal_type == "LONG" else "🔴"
            title = f"{emoji} *{signal_type} SIGNAL* {emoji}"
            action = "🎯 *ACTION: Enter trade within 2 candles!*"
        
        # Calculate percentage moves for better context
        entry = signal['entry_price']
        sl = signal['stop_loss']
        tp1 = signal['take_profit_1']
        tp_full = signal['take_profit_full']
        
        sl_pct = abs((sl - entry) / entry * 100)
        tp1_pct = abs((tp1 - entry) / entry * 100)
        tp_full_pct = abs((tp_full - entry) / entry * 100)

        # Dynamic partial-close percent from the signal (falls back to 50%,
        # which is what DEFAULT_COIN_CFG.tp1_close_percent uses now).
        tp1_close_pct = int(signal.get('tp1_close_percent') or 50)

        message = f"""{title}

💰 *{signal['symbol']}* · {strategy_name}
🕐 Session: {signal.get('session', 'N/A')} | Rules: {signal.get('rules_met_count', 4)}/4

━━━━━━━━━━━━━━━━━━━
💵 *ENTRY:* `${entry}`
🛑 *STOP LOSS:* `${sl}` (-{sl_pct:.2f}%)
🎯 *TP1 ({tp1_close_pct}%):* `${tp1}` (+{tp1_pct:.2f}%)
🚀 *TP FULL:* `${tp_full}` (+{tp_full_pct:.2f}%)
━━━━━━━━━━━━━━━━━━━

📊 *CRV:* {signal['crv']} | RSI: {signal['rsi']}

{action}

🔗 [Open Live Dashboard]({self.frontend_url})
"""
        return message
    
    async def send_signal(self, signal: Dict) -> bool:
        """Send signal notification via Telegram"""
        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, skipping notification")
            return False
        
        try:
            message = self.format_signal_message(signal)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            
            logger.info(f"Telegram notification sent for {signal['symbol']} {signal['type']}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
    
    async def send_rejection(self, symbol: str, side: str, reason: str) -> bool:
        """Notify that a live order was rejected by Bitunix and the trade
        was NOT opened locally (no ghost position)."""
        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, skipping rejection alert")
            return False
        try:
            message = (
                f"⛔ *ORDER ABGEBROCHEN*\n\n"
                f"💰 *{symbol}* · {side}\n"
                f"❌ Bitunix hat die Order abgelehnt:\n"
                f"`{reason}`\n\n"
                f"⚠️ Es wurde *kein* Trade lokal geöffnet."
            )
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send rejection notification: {e}")
            return False

    async def send_test_message(self) -> bool:
        """Send test message to verify bot setup"""
        if not self.bot or not self.chat_id:
            return False
        
        try:
            message = f"""✅ *Crypto Scanner Bot Connected!*

Bot ist bereit und wartet auf Signale.

🔗 [Open Dashboard]({self.frontend_url})
"""
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send test message: {e}")
            return False

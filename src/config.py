import os
import logging
from logging.handlers import TimedRotatingFileHandler
from dotenv import load_dotenv
from aiogram import Bot

load_dotenv()

# ─── Telegram ────────────────────────────────────────────────────────────────
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не задан в .env — бот не может запуститься.")

BOT_USERNAME = os.getenv("BOT_USERNAME", "")
if not BOT_USERNAME:
    raise ValueError("BOT_USERNAME не задан в .env — реферальные ссылки не будут работать.")

ADMIN_IDS: list[int] = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
]
if not ADMIN_IDS:
    raise ValueError("ADMIN_IDS не заданы в .env — доступ в панель управления невозможен.")

# ─── Цены ────────────────────────────────────────────────────────────────────
PRICE_RUB: float = float(os.getenv("PRICE_RUB", "100"))
PRICE_USDT: float = float(os.getenv("PRICE_USDT", "1.5"))

# ─── Подписка ────────────────────────────────────────────────────────────────
TRIAL_DAYS: int = int(os.getenv("TRIAL_DAYS", "3"))
SUBSCRIPTION_DAYS: int = int(os.getenv("SUBSCRIPTION_DAYS", "30"))

# ─── Реферальная программа ───────────────────────────────────────────────────
REFERRAL_TRIAL_BONUS: int = int(os.getenv("REFERRAL_TRIAL_BONUS", "3"))
REFERRAL_PAID_BONUS: int = int(os.getenv("REFERRAL_PAID_BONUS", "7"))

# ─── Напоминания ─────────────────────────────────────────────────────────────
REMINDER_DAYS: list[int] = [
    int(x) for x in os.getenv("REMINDER_DAYS", "3,1").split(",")
    if x.strip().isdigit()
]

# ─── Реквизиты СБП ───────────────────────────────────────────────────────────
_SBP_NUMBER = os.getenv("SBP_NUMBER", "")
_SBP_BANK   = os.getenv("SBP_BANK", "")
_SBP_NAME   = os.getenv("SBP_NAME", "")

_sbp_lines = ["📲 " + _SBP_NUMBER] if _SBP_NUMBER else []
if _SBP_BANK: _sbp_lines.append("🏦 Банк: " + _SBP_BANK)
if _SBP_NAME: _sbp_lines.append("👤 Получатель: " + _SBP_NAME)
REKVIZITY_SBP = "Реквизиты СБП:\n" + "\n".join(_sbp_lines) if _sbp_lines else "⚠️ Реквизиты СБП не настроены."

# ─── Криптокошельки ──────────────────────────────────────────────────────────
WALLETS: dict[str, str] = {
    "trc20": os.getenv("WALLET_TRC20", ""),
    "erc20": os.getenv("WALLET_ERC20", ""),
    "bep20": os.getenv("WALLET_BEP20", ""),
    "ton":   os.getenv("WALLET_TON",   ""),
}
WALLETS = {net: addr for net, addr in WALLETS.items() if addr}

# ─── Пути ────────────────────────────────────────────────────────────────────
SRC_DIR  = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SRC_DIR)

LOGS_DIR = os.path.join(BASE_DIR, "logs")
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

INFO_LOG_PATH  = os.path.join(LOGS_DIR, "info.log")
ERROR_LOG_PATH = os.path.join(LOGS_DIR, "errors.log")

DATA_DIR = os.path.join(BASE_DIR, "data")
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

HISTORY_FILE   = os.path.join(DATA_DIR, "history.json")
SUPPORT_FILE   = os.path.join(DATA_DIR, "support.json")
REFERRAL_FILE  = os.path.join(DATA_DIR, "referrals.json")
COUNTRIES_FILE = os.path.join(DATA_DIR, "countries.json")

# ─── Логирование ─────────────────────────────────────────────────────────────
LOG_FORMAT = "%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
formatter  = logging.Formatter(LOG_FORMAT)

def _make_namer(base_path: str):
    import re
    def namer(default_name: str) -> str:
        return re.sub(r"^(.*?)(\.[^.]+)(\.\d{4}-\d{2}-\d{2})$",
                      lambda m: f"{m.group(1)}_{m.group(3)[1:]}{m.group(2)}",
                      default_name)
    return namer

info_handler = TimedRotatingFileHandler(INFO_LOG_PATH, when="midnight", interval=1, backupCount=14, encoding="utf-8")
info_handler.suffix = "%Y-%m-%d"
info_handler.namer = _make_namer(INFO_LOG_PATH)
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(formatter)

error_handler = TimedRotatingFileHandler(ERROR_LOG_PATH, when="midnight", interval=1, backupCount=30, encoding="utf-8")
error_handler.suffix = "%Y-%m-%d"
error_handler.namer = _make_namer(ERROR_LOG_PATH)
error_handler.setLevel(logging.ERROR)
error_handler.setFormatter(formatter)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
if logger.hasHandlers():
    logger.handlers.clear()
logger.addHandler(info_handler)
logger.addHandler(error_handler)
logger.addHandler(console_handler)

logging.getLogger("aiogram").setLevel(logging.INFO)

bot = Bot(token=TOKEN)
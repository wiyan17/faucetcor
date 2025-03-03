import json
import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    ConversationHandler,
    MessageHandler,
    Filters,
)
from web3 import Web3

# Load environment variables from .env file
load_dotenv()

# Configuration from .env
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
# Use your Sepolia RPC endpoint (e.g., via Infura)
ETH_RPC_URL = os.getenv('ETH_RPC_URL')
FAUCET_ADDRESS = os.getenv('FAUCET_ADDRESS')
FAUCET_PRIVATE_KEY = os.getenv('FAUCET_PRIVATE_KEY')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

# Faucet settings for Sepolia
CHAIN_ID = 11155111  # Sepolia testnet chain ID
FAUCET_AMOUNT = 0.001  # ETH amount to send per claim

# Setup logging (console and file)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
file_handler = logging.FileHandler('bot.log')
file_handler.setFormatter(formatter)
logger.addHandler(console_handler)
logger.addHandler(file_handler)

logger.info("Initializing Sepolia ETH Faucet Bot...")

# Whitelist storage (JSON file)
WHITELIST_FILE = 'whitelist.json'
# Global whitelist: { "telegram_user_id_as_string": [wallet_address1, wallet_address2, ...] }
WHITELIST = {}

def load_whitelist():
    global WHITELIST
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, 'r') as f:
                data = json.load(f)
                WHITELIST = data.get("users", {})
                logger.info("Whitelist loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading whitelist: {e}")
            WHITELIST = {}
    else:
        # Initialize whitelist from .env (each user with an empty wallet list)
        users_env = os.getenv('WHITELISTED_USER_IDS', '')
        if users_env.strip():
            WHITELIST = { str(int(x.strip())): [] for x in users_env.split(',') }
            logger.info("Whitelist initialized from .env.")
        else:
            WHITELIST = {}
        save_whitelist()

def save_whitelist():
    data = {"users": WHITELIST}
    try:
        with open(WHITELIST_FILE, 'w') as f:
            json.dump(data, f, indent=4)
            logger.info("Whitelist saved successfully.")
    except Exception as e:
        logger.error(f"Error saving whitelist: {e}")

load_whitelist()

# Initialize Web3 (sending ETH, so no ERC20 contract needed)
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
if not w3.is_connected():
    logger.error("Failed to connect to the Ethereum network.")
else:
    logger.info("Connected to the Ethereum network.")

# Rate limiting: track last claim time per Telegram user (by user id)
last_claim = {}

# --- Conversation State ---
FAUCET_WAIT_ADDRESS = 1

# --- Reply Keyboard Main Menu ---
def main_menu_keyboard(user_id: int):
    # To simulate right-aligned buttons, add an empty cell on the left.
    keyboard = [
        ["", "💧 Claim Faucet"],
        ["", "⏰ Check Status"],
        ["", "❓ Help"]
    ]
    if user_id == ADMIN_ID:
        keyboard.append(["", "⚙️ Admin Panel"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# --- Standard Command Handlers ---
def start_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    text = "Welcome to the Sepolia ETH Faucet Bot!\n\nPlease use the buttons below to navigate:"
    update.message.reply_text(text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} started the bot.")

def help_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    help_text = (
        "Sepolia ETH Faucet Bot Help:\n\n"
        "• Tap 'Claim Faucet' to request 0.001 ETH (if eligible).\n"
        "• Tap 'Check Status' to view your claim cooldown.\n"
        "• Only whitelisted users (with approved wallet addresses) can claim ETH.\n"
        "• Each user may have up to 10 wallet addresses.\n"
        "• You can claim only once every 24 hours.\n\n"
        "Admin functions are available via the Admin Panel (if you're an admin)."
    )
    update.message.reply_text(help_text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} requested help.")

def status_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    user_key = str(user_id)
    if user_key not in WHITELIST:
        update.message.reply_text("Sorry, you are not authorized to claim ETH.", reply_markup=main_menu_keyboard(user_id))
        logger.info(f"Unauthorized status check by user {user_id}.")
        return
    now = datetime.now()
    if user_id in last_claim:
        elapsed = now - last_claim[user_id]
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            update.message.reply_text(f"You're on cooldown. Try again in {str(remaining).split('.')[0]}.", reply_markup=main_menu_keyboard(user_id))
            logger.info(f"User {user_id} is on cooldown: {str(remaining).split('.')[0]}.")
            return
    update.message.reply_text("Great news! You are eligible for a claim.", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} is eligible for a claim.")

# --- Faucet Claim Conversation ---
def faucet_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    text = "You've chosen to claim ETH.\nPlease type your Ethereum address (or send /cancel to abort):"
    update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())
    logger.info(f"User {user_id} initiated a faucet claim.")
    return FAUCET_WAIT_ADDRESS

def faucet_receive_address(update: Update, context: CallbackContext)

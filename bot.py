import json
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    Filters,
)
from web3 import Web3

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ETH_RPC_URL = os.getenv('ETH_RPC_URL')
FAUCET_ADDRESS = os.getenv('FAUCET_ADDRESS')
FAUCET_PRIVATE_KEY = os.getenv('FAUCET_PRIVATE_KEY')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

FAUCET_AMOUNT = 0.001  # Default faucet amount
WHITELIST_FILE = 'whitelist.json'
whitelist = {}

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load Web3
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
if not w3.is_connected():
    logger.error("Failed to connect to Ethereum network.")
else:
    logger.info("Connected to Ethereum network.")

# Load whitelist
def load_whitelist():
    global whitelist
    if os.path.exists(WHITELIST_FILE):
        with open(WHITELIST_FILE, 'r') as f:
            whitelist = json.load(f)
    else:
        whitelist = {}
        save_whitelist()

def save_whitelist():
    with open(WHITELIST_FILE, 'w') as f:
        json.dump(whitelist, f, indent=4)

load_whitelist()
last_claim = {}

# Interactive Keyboard
def main_menu():
    keyboard = [
        [InlineKeyboardButton("💧 Claim Faucet", callback_data='claim')],
        [InlineKeyboardButton("⏰ Check Status", callback_data='status')],
        [InlineKeyboardButton("❓ Help", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Start Command
def start(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    update.message.reply_text("Welcome to the Sepolia ETH Faucet Bot! Choose an option:", reply_markup=main_menu())
    logger.info(f"User {user_id} started the bot.")

# Admin Commands
def add_user(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if len(context.args) != 1:
        update.message.reply_text("Usage: /adduser <telegram_user_id>")
        return

    user_id = context.args[0]
    if user_id not in whitelist:
        whitelist[user_id] = []
        save_whitelist()
        update.message.reply_text(f"✅ User {user_id} added to the whitelist.")
        logger.info(f"User {user_id} added to the whitelist.")
    else:
        update.message.reply_text("⚠️ User is already whitelisted.")

def remove_user(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if len(context.args) != 1:
        update.message.reply_text("Usage: /removeuser <telegram_user_id>")
        return

    user_id = context.args[0]
    if user_id in whitelist:
        del whitelist[user_id]
        save_whitelist()
        update.message.reply_text(f"✅ User {user_id} removed from the whitelist.")
        logger.info(f"User {user_id} removed from the whitelist.")
    else:
        update.message.reply_text("⚠️ User not found.")

def add_wallet(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if len(context.args) != 2:
        update.message.reply_text("Usage: /addwallet <wallet_address> <telegram_user_id>")
        return

    wallet, user_id = context.args
    if user_id in whitelist:
        if len(whitelist[user_id]) < 10:
            if wallet not in whitelist[user_id]:
                whitelist[user_id].append(wallet)
                save_whitelist()
                update.message.reply_text(f"✅ Wallet {wallet} added for user {user_id}.")
                logger.info(f"Wallet {wallet} added for user {user_id}.")
            else:
                update.message.reply_text("⚠️ Wallet already whitelisted.")
        else:
            update.message.reply_text("❌ User already has 10 wallets.")
    else:
        update.message.reply_text("❌ User is not whitelisted.")

def remove_wallet(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if len(context.args) != 1:
        update.message.reply_text("Usage: /removewallet <wallet_address>")
        return

    wallet = context.args[0]
    for user_id, wallets in whitelist.items():
        if wallet in wallets:
            whitelist[user_id].remove(wallet)
            save_whitelist()
            update.message.reply_text(f"✅ Wallet {wallet} removed.")
            logger.info(f"Wallet {wallet} removed from user {user_id}.")
            return

    update.message.reply_text("⚠️ Wallet not found.")

def list_whitelist(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_ID:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return

    whitelist_text = "📝 **Whitelist Users**\n"
    for user_id, wallets in whitelist.items():
        whitelist_text += f"🆔 {user_id}: {wallets}\n"

    update.message.reply_text(whitelist_text if whitelist else "⚠️ No users in whitelist.")

# Faucet Claim
def claim(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if user_id not in whitelist:
        update.message.reply_text("❌ You are not whitelisted.")
        return

    if user_id in last_claim and (datetime.now() - last_claim[user_id]) < timedelta(hours=24):
        update.message.reply_text("⏳ Please wait 24 hours before claiming again.")
        return

    update.message.reply_text("Send your whitelisted wallet address:")
    context.user_data['waiting_for_address'] = True

def receive_wallet(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if 'waiting_for_address' in context.user_data and context.user_data['waiting_for_address']:
        wallet = update.message.text.strip()
        if wallet in whitelist[user_id]:
            update.message.reply_text(f"✅ Transaction sent to {wallet}.")
            last_claim[user_id] = datetime.now()
        else:
            update.message.reply_text("❌ Address not in whitelist.")

# Start Bot
updater = Updater(TELEGRAM_TOKEN, use_context=True)
dp = updater.dispatcher

dp.add_handler(CommandHandler("start", start))
dp.add_handler(CommandHandler("adduser", add_user))
dp.add_handler(CommandHandler("removeuser", remove_user))
dp.add_handler(CommandHandler("addwallet", add_wallet))
dp.add_handler(CommandHandler("removewallet", remove_wallet))
dp.add_handler(CommandHandler("whitelist", list_whitelist))
dp.add_handler(MessageHandler(Filters.text & ~Filters.command, receive_wallet))

updater.start_polling()
logger.info("Bot started!")

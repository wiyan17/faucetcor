import json
import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)
from web3 import Web3

# ------------------------------
# Load environment variables
# ------------------------------
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
ETH_RPC_URL = os.getenv('ETH_RPC_URL')  # ARB ETH RPC endpoint (e.g., via Alchemy or Infura)
FAUCET_ADDRESS = os.getenv('FAUCET_ADDRESS')
FAUCET_PRIVATE_KEY = os.getenv('FAUCET_PRIVATE_KEY')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))  # Not used now except for /setamount
FAUCET_AMOUNT = 0.001  # ETH to send per claim
CHAIN_ID = 421614     # ARB ETH chain ID
WHITELIST_FILE = 'whitelist.json'

# ------------------------------
# Setup logging
# ------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("Starting ARB ETH Faucet Bot...")

# ------------------------------
# Whitelist storage functions
# ------------------------------
# Structure: { "telegram_user_id": [wallet_address1, wallet_address2, ...] }
whitelist = {}

def load_whitelist():
    global whitelist
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, 'r') as f:
                data = json.load(f)
                whitelist = data.get("users", {})
            logger.info("Whitelist loaded successfully.")
        except Exception as e:
            logger.error(f"Error loading whitelist: {e}")
            whitelist = {}
    else:
        # Optionally initialize from env variable if desired
        users_env = os.getenv('WHITELISTED_USER_IDS', '')
        if users_env.strip():
            whitelist = {str(int(x.strip())): [] for x in users_env.split(',')}
            logger.info("Whitelist initialized from .env.")
        else:
            whitelist = {}
        save_whitelist()

def save_whitelist():
    data = {"users": whitelist}
    try:
        with open(WHITELIST_FILE, 'w') as f:
            json.dump(data, f, indent=4)
        logger.info("Whitelist saved successfully.")
    except Exception as e:
        logger.error(f"Error saving whitelist: {e}")

load_whitelist()

# ------------------------------
# Initialize Web3
# ------------------------------
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
if not w3.is_connected():
    logger.error("Failed to connect to the Ethereum network.")
else:
    logger.info("Connected to the Ethereum network.")

# ------------------------------
# Rate limiting
# ------------------------------
last_claim = {}  # { telegram_user_id (int): datetime of last claim }

# ------------------------------
# Conversation State for Faucet Claim
# ------------------------------
FAUCET_WAIT_ADDRESS = 1

# ------------------------------
# Main Menu Reply Keyboard (for all users)
# ------------------------------
def main_menu_keyboard(user_id: int):
    keyboard = [
        ["💧 Claim Faucet"],
        ["⏰ Check Status"],
        ["❓ Help"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# ------------------------------
# User Command Handlers
# ------------------------------
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    update.message.reply_text(
        "Welcome to the ARB ETH Faucet Bot!\n\nPlease use the buttons below to navigate:",
        reply_markup=main_menu_keyboard(user_id)
    )
    logger.info(f"User {user_id} started the bot.")

def help_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    help_text = (
        "ARB ETH Faucet Bot Help:\n\n"
        "• Tap 'Claim Faucet' to request 0.001 ETH (if eligible).\n"
        "• Tap 'Check Status' to view your claim cooldown.\n"
        "• Use /balance to check the faucet wallet’s ETH balance.\n"
        "• Admins can update the faucet amount using /setamount.\n"
        "• Use /checkwhitelist <address> to check if an address is whitelisted in the ACL contract."
    )
    update.message.reply_text(help_text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {update.effective_user.id} requested help.")

def status(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    user_key = str(user_id)
    if user_key not in whitelist:
        update.message.reply_text("Sorry, you are not authorized to claim ETH.", reply_markup=main_menu_keyboard(user_id))
        logger.info(f"Unauthorized status check by user {user_id}.")
        return
    now = datetime.now()
    if user_id in last_claim:
        elapsed = now - last_claim[user_id]
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            update.message.reply_text(
                f"You're on cooldown. Try again in {str(remaining).split('.')[0]}.",
                reply_markup=main_menu_keyboard(user_id)
            )
            logger.info(f"User {user_id} is on cooldown: {str(remaining).split('.')[0]}.")
            return
    update.message.reply_text("Great news! You are eligible for a claim.", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} is eligible for a claim.")

def balance(update: Update, context: CallbackContext) -> None:
    try:
        bal = w3.eth.get_balance(FAUCET_ADDRESS)
        balance_eth = w3.from_wei(bal, 'ether')  # Use from_wei (new method name)
        update.message.reply_text(f"Faucet wallet balance: {balance_eth} ETH")
        logger.info(f"Faucet balance: {balance_eth} ETH")
    except Exception as e:
        update.message.reply_text(f"Error fetching balance: {str(e)}")
        logger.error(f"Error fetching faucet balance: {e}")

# ------------------------------
# New Command: Check Contract Whitelist
# ------------------------------
def check_whitelist_contract(update: Update, context: CallbackContext) -> None:
    if len(context.args) < 1:
        update.message.reply_text("Usage: /checkwhitelist <address>")
        return
    address = context.args[0]
    try:
        # Load the ACL contract ABI from file
        with open("abi_acl.json", "r") as f:
            acl_abi = json.load(f)
        acl_addr = "0x6Dbc02BD4adbb34caeFb081fe60eDC41e393521B"
        acl_contract = w3.eth.contract(address=acl_addr, abi=acl_abi)
        result = acl_contract.functions.isAlphaTester(w3.to_checksum_address(address)).call()
        if result:
            update.message.reply_text(f"Address {address} is whitelisted (isAlphaTester = True).")
        else:
            update.message.reply_text(f"Address {address} is NOT whitelisted (isAlphaTester = False).")
        logger.info(f"Checked whitelist for {address}: {result}")
    except Exception as e:
        update.message.reply_text(f"Error checking contract: {str(e)}")
        logger.error(f"Error in check_whitelist_contract: {e}")

# ------------------------------
# Faucet Claim Conversation Handlers
# ------------------------------
def faucet_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text(
        "You've chosen to claim ETH.\nPlease type your Ethereum address (or send /cancel to abort):",
        reply_markup=ReplyKeyboardRemove()
    )
    logger.info(f"User {user_id} initiated faucet claim.")
    return FAUCET_WAIT_ADDRESS

def faucet_receive_address(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    user_key = str(user_id)
    eth_address = update.message.text.strip().lower()

    if user_key not in whitelist:
        update.message.reply_text("Sorry, you are not authorized to use this faucet.")
        logger.info(f"Unauthorized faucet claim attempt by user {user_id}.")
        return ConversationHandler.END
    if not w3.is_address(eth_address):
        update.message.reply_text("That doesn't seem like a valid Ethereum address. Please try again (or send /cancel to abort):")
        return FAUCET_WAIT_ADDRESS
    if eth_address not in whitelist[user_key]:
        update.message.reply_text("This wallet address is not authorized for faucet claims.")
        logger.info(f"User {user_id} provided unapproved wallet address: {eth_address}.")
        return ConversationHandler.END
    now = datetime.now()
    if user_id in last_claim:
        elapsed = now - last_claim[user_id]
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            update.message.reply_text(f"Oops! You can only claim once every 24 hours. Try again in {str(remaining).split('.')[0]}.")
            logger.info(f"User {user_id} attempted claim during cooldown.")
            return ConversationHandler.END
    try:
        to_address = w3.to_checksum_address(eth_address)
        faucet_addr = w3.to_checksum_address(FAUCET_ADDRESS)
    except Exception as e:
        update.message.reply_text("An error occurred while processing addresses.")
        logger.error(f"Error converting addresses for user {user_id}: {e}")
        return ConversationHandler.END

    tx = {
        'nonce': w3.eth.get_transaction_count(faucet_addr),
        'to': to_address,
        'value': w3.to_wei(FAUCET_AMOUNT, 'ether'),
        'gas': 25000,  # Increased gas limit
        'gasPrice': w3.eth.gas_price,
        'chainId': CHAIN_ID
    }
    try:
        signed_tx = w3.eth.account.sign_transaction(tx, FAUCET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        last_claim[user_id] = now
        hash_str = tx_hash.hex()
        if not hash_str.startswith("0x"):
            hash_str = "0x" + hash_str
        etherscan_link = f"https://sepolia.arbiscan.io/tx/{hash_str}"
        update.message.reply_text(
            f"Your transaction was successful!\nTx Hash: {hash_str}\nView on Arbiscan: {etherscan_link}"
        )
        logger.info(f"User {user_id} claimed faucet. Tx: {hash_str}")
    except Exception as e:
        update.message.reply_text(f"An error occurred: {str(e)}")
        logger.error(f"Error during faucet claim for user {user_id}: {e}")
    update.message.reply_text("Returning to main menu.", reply_markup=main_menu_keyboard(user_id))
    return ConversationHandler.END

def faucet_cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text("Faucet claim canceled.", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} canceled faucet claim.")
    return ConversationHandler.END

# ------------------------------
# Dispatcher Registration and Main
# ------------------------------
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Basic commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("setamount", set_amount))
    dp.add_handler(CommandHandler("checkwhitelist", check_whitelist_contract))
    
    # Faucet conversation
    dp.add_handler(faucet_conv_handler := ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^💧 Claim Faucet$"), faucet_start)],
        states={
            FAUCET_WAIT_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, faucet_receive_address)]
        },
        fallbacks=[CommandHandler("cancel", faucet_cancel)],
        per_user=True,
    ))
    
    # Other message-based commands (Check Status)
    dp.add_handler(MessageHandler(Filters.regex("^(⏰ Check Status)$"), lambda u, c: status(u, c)))
    
    updater.start_polling()
    logger.info("Bot started!")
    updater.idle()

if __name__ == '__main__':
    main()
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ETH_RPC_URL = os.getenv("ETH_RPC_URL")  # ARB ETH RPC endpoint
FAUCET_ADDRESS = os.getenv("FAUCET_ADDRESS")
FAUCET_PRIVATE_KEY = os.getenv("FAUCET_PRIVATE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FAUCET_AMOUNT = 0.001  # ETH per claim
CHAIN_ID = 421614     # ARB ETH chain ID

# ------------------------------
# Setup logging
# ------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.info("Starting ARB ETH Faucet Bot...")

# ------------------------------
# Initialize Web3
# ------------------------------
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
if not w3.is_connected():
    logger.error("Failed to connect to the Ethereum network.")
else:
    logger.info("Connected to the Ethereum network.")

# ------------------------------
# Claim settings and rate limiting
# ------------------------------
CLAIM_COOLDOWN = timedelta(hours=48)
MAX_ADDRESSES_PER_USER = 15

# Instead of a single last_claim, we track:
# user_claims: {telegram_user_id: list of (address, claim_time)}
user_claims = {}  # Tracks each claim made by a Telegram user in the last 48 hours.
# address_claims: {ethereum_address: claim_time}
address_claims = {}  # Tracks last claim time per Ethereum address.

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
        ["❓ Help"],
        ["⏰ Check Balance"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# ------------------------------
# /start Command Handler
# ------------------------------
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    update.message.reply_text(
        "Welcome to the ARB ETH Faucet Bot!\n\nPlease use the menu below:",
        reply_markup=main_menu_keyboard(user_id)
    )
    logger.info(f"User {user_id} started the bot.")

# ------------------------------
# /help Command Handler
# ------------------------------
def help_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    help_text = (
        "Process:\n"
        "1. Tap 'Claim Faucet' and enter your Ethereum address.\n"
        "   - The bot checks your address against the ACL contract.\n"
        "   - If approved, you receive 0.001 ETH (claimable once every 48 hrs).\n"
        "2. Each Telegram user can claim using up to 15 addresses within 48 hrs.\n"
        "3. Each address can claim only once every 48 hrs.\n"
        "4. Use /balance to check the faucet wallet’s balance.\n"
        "5. Use /checkwhitelist <address> to verify an address."
    )
    update.message.reply_text(help_text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} requested help.")

# ------------------------------
# /balance Command Handler
# ------------------------------
def balance(update: Update, context: CallbackContext) -> None:
    try:
        bal = w3.eth.get_balance(FAUCET_ADDRESS)
        balance_eth = w3.from_wei(bal, "ether")
        update.message.reply_text(f"Faucet balance: {balance_eth} ETH")
        logger.info(f"Faucet balance: {balance_eth} ETH")
    except Exception as e:
        update.message.reply_text(f"Error fetching balance: {str(e)}")
        logger.error(f"Error fetching balance: {e}")

# ------------------------------
# /checkwhitelist Command Handler
# ------------------------------
def check_whitelist_contract(update: Update, context: CallbackContext) -> None:
    if len(context.args) < 1:
        update.message.reply_text("Usage: /checkwhitelist <address>")
        return
    address = context.args[0]
    try:
        to_address = w3.to_checksum_address(address)
    except Exception as e:
        update.message.reply_text("Invalid Ethereum address.")
        logger.error(f"Invalid address: {address}, error: {e}")
        return
    try:
        with open("abi_acl.json", "r") as f:
            acl_abi = json.load(f)
        acl_addr = "0x6Dbc02BD4adbb34caeFb081fe60eDC41e393521B"
        acl_contract = w3.eth.contract(address=acl_addr, abi=acl_abi)
        result = acl_contract.functions.isAlphaTester(to_address).call()
        if result:
            update.message.reply_text(f"Address {address} is whitelisted.")
        else:
            update.message.reply_text(f"Address {address} is NOT whitelisted.")
        logger.info(f"Checked whitelist for {address}: {result}")
    except Exception as e:
        update.message.reply_text(f"Error checking whitelist: {str(e)}")
        logger.error(f"Error in check_whitelist_contract: {e}")

# ------------------------------
# Faucet Claim Conversation Handlers
# ------------------------------
def faucet_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text(
        "Enter your Ethereum address to claim 0.001 ETH (or send /cancel to abort):",
        reply_markup=ReplyKeyboardRemove()
    )
    logger.info(f"User {user_id} initiated faucet claim.")
    return FAUCET_WAIT_ADDRESS

def faucet_receive_address(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    eth_address = update.message.text.strip().lower()
    
    try:
        to_address = w3.to_checksum_address(eth_address)
    except Exception as e:
        update.message.reply_text("Invalid Ethereum address. Please try again:")
        logger.error(f"Error converting address for user {user_id}: {e}")
        return FAUCET_WAIT_ADDRESS

    # Check whitelist via contract call
    try:
        with open("abi_acl.json", "r") as f:
            acl_abi = json.load(f)
        acl_addr = "0x6Dbc02BD4adbb34caeFb081fe60eDC41e393521B"
        acl_contract = w3.eth.contract(address=acl_addr, abi=acl_abi)
        is_whitelisted = acl_contract.functions.isAlphaTester(to_address).call()
    except Exception as e:
        update.message.reply_text(f"Error checking whitelist: {str(e)}")
        logger.error(f"Error calling isAlphaTester for user {user_id}: {e}")
        return ConversationHandler.END

    if not is_whitelisted:
        update.message.reply_text("This address is not whitelisted.")
        logger.info(f"Address {eth_address} not whitelisted.")
        return ConversationHandler.END

    now = datetime.now()

    # Check if the address was used in the last 48 hours
    if to_address in address_claims:
        last_time = address_claims[to_address]
        if now - last_time < CLAIM_COOLDOWN:
            update.message.reply_text("This address has already claimed in the last 48 hours.")
            logger.info(f"Address {eth_address} already claimed.")
            return ConversationHandler.END

    # Check if the user has claimed with 15 addresses in the last 48 hours
    user_history = user_claims.get(user_id, [])
    # Filter out claims older than 48 hours
    user_history = [ (addr, t) for addr, t in user_history if now - t < CLAIM_COOLDOWN ]
    if len(user_history) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("You have reached the maximum number of address claims (15) in 48 hours.")
        logger.info(f"User {user_id} has reached maximum claim addresses.")
        return ConversationHandler.END

    # Proceed with claim
    try:
        faucet_addr = w3.to_checksum_address(FAUCET_ADDRESS)
    except Exception as e:
        update.message.reply_text("Error processing faucet address.")
        logger.error(f"Error converting faucet address for user {user_id}: {e}")
        return ConversationHandler.END

    tx = {
        'nonce': w3.eth.get_transaction_count(faucet_addr),
        'to': to_address,
        'value': w3.to_wei(FAUCET_AMOUNT, "ether"),
        'gasPrice': w3.eth.gas_price,
        'chainId': CHAIN_ID
    }
    try:
        estimated_gas = w3.eth.estimate_gas(tx)
        tx['gas'] = int(estimated_gas * 1.2)
    except Exception as e:
        update.message.reply_text("Error estimating gas. Using fallback gas limit.")
        logger.error(f"Error estimating gas for user {user_id}: {e}")
        tx['gas'] = 35000

    try:
        signed_tx = w3.eth.account.sign_transaction(tx, FAUCET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        hash_str = tx_hash.hex()
        if not hash_str.startswith("0x"):
            hash_str = "0x" + hash_str
        etherscan_link = f"https://sepolia.arbiscan.io/tx/{hash_str}"
        update.message.reply_text(f"Success! Tx Hash: {hash_str}\nView on Arbiscan: {etherscan_link}")
        logger.info(f"User {user_id} claimed faucet. Tx: {hash_str}")
    except Exception as e:
        update.message.reply_text(f"Error during claim: {str(e)}")
        logger.error(f"Error during faucet claim for user {user_id}: {e}")
        return ConversationHandler.END

    # Record claim: update both address_claims and user_claims
    address_claims[to_address] = now
    user_history.append((to_address, now))
    user_claims[user_id] = user_history

    update.message.reply_text("Returning to main menu.", reply_markup=main_menu_keyboard(user_id))
    return ConversationHandler.END

def faucet_cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text("Faucet claim canceled.", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} canceled faucet claim.")
    return ConversationHandler.END

# ------------------------------
# Additional Data Structures for 48-Hour Limits
# ------------------------------
CLAIM_COOLDOWN = timedelta(hours=48)
MAX_ADDRESSES_PER_USER = 15
user_claims = {}      # {telegram_user_id: [(address, claim_time), ...]}
address_claims = {}   # {ethereum_address: claim_time}

# ------------------------------
# Dispatcher Registration and Main
# ------------------------------
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("setamount", set_amount))
    dp.add_handler(CommandHandler("checkwhitelist", check_whitelist_contract))
    
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^💧 Claim Faucet$"), faucet_start)],
        states={
            FAUCET_WAIT_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, faucet_receive_address)]
        },
        fallbacks=[CommandHandler("cancel", faucet_cancel)],
        per_user=True,
    )
    dp.add_handler(conv_handler)
    
    dp.add_handler(MessageHandler(Filters.regex("^(⏰ Check Balance)$"), balance))
    
    updater.start_polling()
    logger.info("Bot started!")
    updater.idle()

if __name__ == '__main__':
    main()
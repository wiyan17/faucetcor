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
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))  # For /setamount command
FAUCET_AMOUNT = 0.001  # ETH to send per claim
CHAIN_ID = 421614     # ARB ETH chain ID

# ------------------------------
# Setup logging
# ------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logger.info("Starting ARB ETH Faucet Bot...")

# ------------------------------
# Set Faucet Amount (Admin Command)
# ------------------------------
def set_amount(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return
    if len(context.args) != 1:
        update.message.reply_text("Usage: /setamount <amount>")
        return
    try:
        new_amount = float(context.args[0])
        global FAUCET_AMOUNT
        FAUCET_AMOUNT = new_amount
        update.message.reply_text(f"✅ Faucet amount set to {new_amount} ETH.")
        logger.info(f"Admin set faucet amount to {new_amount} ETH.")
    except ValueError:
        update.message.reply_text("❌ Invalid amount. Please enter a valid number.")

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
        ["❓ Help"],
        ["⏰ Check Balance"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# ------------------------------
# User Command Handlers
# ------------------------------
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    update.message.reply_text(
        "Welcome to the ARB ETH Faucet Bot!\n\nPlease use the menu below to interact:",
        reply_markup=main_menu_keyboard(user_id)
    )
    logger.info(f"User {user_id} started the bot.")

def help_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    help_text = (
        "ARB ETH Faucet Bot Help:\n\n"
        "Tutorial for Claiming ETH:\n"
        "1. Tap 'Claim Faucet' and enter your Ethereum address when prompted.\n"
        "   - The bot will call the ACL contract's isAlphaTester(address) function to verify your address.\n"
        "   - If approved (true), you'll receive 0.001 ETH (once every 24 hours).\n"
        "   - If not, you'll be informed that the address is not whitelisted.\n\n"
        "2. Check Balance:\n"
        "   - Use /balance or tap 'Check Balance' to view the faucet wallet's ETH balance.\n\n"
        "3. Admin Command:\n"
        "   - Admins can update the faucet amount using /setamount <amount>.\n\n"
        "4. Contract Whitelist Check:\n"
        "   - Use /checkwhitelist <address> to see if an address is whitelisted via the ACL contract."
    )
    update.message.reply_text(help_text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} requested help.")

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

    # Check whitelist status using the ACL contract
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
        logger.info(f"Address {eth_address} not whitelisted according to ACL.")
        return ConversationHandler.END

    now = datetime.now()
    if user_id in last_claim:
        elapsed = now - last_claim[user_id]
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            update.message.reply_text(f"Please wait {str(remaining).split('.')[0]} before claiming again.")
            logger.info(f"User {user_id} attempted claim during cooldown.")
            return ConversationHandler.END

    try:
        faucet_addr = w3.to_checksum_address(FAUCET_ADDRESS)
    except Exception as e:
        update.message.reply_text("Error processing faucet address.")
        logger.error(f"Error converting faucet address: {e}")
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
        update.message.reply_text(f"Success! Tx Hash: {hash_str}\nView on Arbiscan: {etherscan_link}")
        logger.info(f"User {user_id} claimed faucet. Tx: {hash_str}")
    except Exception as e:
        update.message.reply_text(f"Error during claim: {str(e)}")
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
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

# Load environment variables
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ETH_RPC_URL = os.getenv("ETH_RPC_URL")
FAUCET_ADDRESS = os.getenv("FAUCET_ADDRESS")
FAUCET_PRIVATE_KEY = os.getenv("FAUCET_PRIVATE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FAUCET_AMOUNT = 0.1        # 0.1 ETH per claim
CHAIN_ID = 421614

# Setup logging (errors & warnings will be logged)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.info("Starting ARB ETH Faucet Bot...")

# Initialize Web3
w3 = Web3(Web3.HTTPProvider(ETH_RPC_URL))
if not w3.is_connected():
    logger.error("Connection to Ethereum network failed.")
else:
    logger.info("Connected to Ethereum network.")

# 48-hour claim rules and limits
CLAIM_COOLDOWN = timedelta(hours=48)
MAX_ADDRESSES_PER_USER = 16

# Track claim times
address_claims = {}   # {ethereum_address: claim_time}
user_claims = {}      # {telegram_user_id: [(address, claim_time), ...]}

# User address storage for auto-claim (stored via JSON)
USER_ADDRESSES_FILE = "user_addresses.json"
user_addresses = {}

def load_user_addresses():
    global user_addresses
    if os.path.exists(USER_ADDRESSES_FILE):
        try:
            with open(USER_ADDRESSES_FILE, "r") as f:
                user_addresses = json.load(f)
            logger.info("User addresses loaded.")
        except Exception as e:
            logger.error(f"Error loading user addresses: {e}")
            user_addresses = {}
    else:
        user_addresses = {}
        save_user_addresses()

def save_user_addresses():
    try:
        with open(USER_ADDRESSES_FILE, "w") as f:
            json.dump(user_addresses, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving user addresses: {e}")

load_user_addresses()

# Conversation state for faucet claim
FAUCET_WAIT_ADDRESS = 1

# Main menu keyboard (only essential buttons)
def main_menu_keyboard(user_id: int):
    keyboard = [
        ["💧 Claim Faucet"],
        ["❓ Help"],
        ["⏰ Check Balance"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# /start command – minimal output
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    update.message.reply_text("Welcome to the ARB ETH Faucet Bot!", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} started the bot.")

# /help command – concise process description
def help_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    help_text = (
        "Process:\n"
        "1. Tap 'Claim Faucet' and enter your ETH address.\n"
        "2. If approved (whitelisted), you receive 0.1 ETH (once every 48 hrs).\n"
        "3. /balance – Check faucet balance.\n"
        "4. /checkwhitelist <address> – Verify whitelist status.\n"
        "5. /addaddress, /removeaddress, /checkaddress, /claimmanual – Manage addresses & auto claim."
    )
    update.message.reply_text(help_text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} requested help.")

# /balance command – minimal output
def balance(update: Update, context: CallbackContext) -> None:
    try:
        bal = w3.eth.get_balance(FAUCET_ADDRESS)
        balance_eth = w3.from_wei(bal, "ether")
        update.message.reply_text(f"Faucet balance: {balance_eth} ETH")
        logger.info(f"Faucet balance: {balance_eth} ETH")
    except Exception as e:
        update.message.reply_text(f"Error: {str(e)}")
        logger.error(f"Error fetching balance: {e}")

# /checkwhitelist command – minimal output
def check_whitelist_contract(update: Update, context: CallbackContext) -> None:
    if len(context.args) < 1:
        update.message.reply_text("Usage: /checkwhitelist <address>")
        return
    address = context.args[0]
    try:
        to_address = w3.to_checksum_address(address)
    except Exception:
        update.message.reply_text("Invalid address.")
        return
    try:
        with open("abi_acl.json", "r") as f:
            acl_abi = json.load(f)
        acl_addr = "0x6Dbc02BD4adbb34caeFb081fe60eDC41e393521B"
        acl_contract = w3.eth.contract(address=acl_addr, abi=acl_abi)
        result = acl_contract.functions.isAlphaTester(to_address).call()
        update.message.reply_text(f"{address} is {'whitelisted' if result else 'NOT whitelisted'}.")
        logger.info(f"Whitelist check for {address}: {result}")
    except Exception as e:
        update.message.reply_text(f"Error: {str(e)}")
        logger.error(f"Error checking whitelist: {e}")

# Faucet claim conversation (triggered by button)
def faucet_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text("Enter your ETH address to claim 0.1 ETH:", reply_markup=ReplyKeyboardRemove())
    logger.info(f"User {user_id} started claim.")
    return FAUCET_WAIT_ADDRESS

def faucet_receive_address(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    eth_address = update.message.text.strip().lower()
    try:
        to_address = w3.to_checksum_address(eth_address)
    except Exception:
        update.message.reply_text("Invalid address. Try again:")
        return FAUCET_WAIT_ADDRESS
    try:
        with open("abi_acl.json", "r") as f:
            acl_abi = json.load(f)
        acl_addr = "0x6Dbc02BD4adbb34caeFb081fe60eDC41e393521B"
        acl_contract = w3.eth.contract(address=acl_addr, abi=acl_abi)
        if not acl_contract.functions.isAlphaTester(to_address).call():
            update.message.reply_text("Address not whitelisted.")
            return ConversationHandler.END
    except Exception as e:
        update.message.reply_text(f"Whitelist error: {str(e)}")
        return ConversationHandler.END
    now = datetime.now()
    if to_address in address_claims and (now - address_claims[to_address] < CLAIM_COOLDOWN):
        update.message.reply_text("Address already claimed in 48 hrs.")
        return ConversationHandler.END
    user_hist = user_claims.get(user_id, [])
    user_hist = [(addr, t) for addr, t in user_hist if now - t < CLAIM_COOLDOWN]
    if len(user_hist) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("Claim limit reached (16 addresses) in 48 hrs.")
        return ConversationHandler.END
    try:
        faucet_addr = w3.to_checksum_address(FAUCET_ADDRESS)
    except Exception:
        update.message.reply_text("Faucet address error.")
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
    except Exception:
        tx['gas'] = 35000
    try:
        signed_tx = w3.eth.account.sign_transaction(tx, FAUCET_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        hash_str = tx_hash.hex()
        if not hash_str.startswith("0x"):
            hash_str = "0x" + hash_str
        update.message.reply_text(f"Success! Tx: {hash_str}")
        logger.info(f"Claim success for user {user_id}, Tx: {hash_str}")
    except Exception as e:
        update.message.reply_text(f"Claim error: {str(e)}")
        logger.error(f"Claim error for user {user_id}: {e}")
        return ConversationHandler.END
    address_claims[to_address] = now
    user_hist.append((to_address, now))
    user_claims[user_id] = user_hist
    update.message.reply_text("", reply_markup=main_menu_keyboard(user_id))
    return ConversationHandler.END

def faucet_cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text("Claim canceled.", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} canceled claim.")
    return ConversationHandler.END

# Slash command: /addaddress (for manual use)
def add_address(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if len(context.args) < 1:
        update.message.reply_text("Usage: /addaddress <address>")
        return
    addr = context.args[0].strip().lower()
    try:
        to_address = w3.to_checksum_address(addr)
    except Exception:
        update.message.reply_text("Invalid address.")
        return
    current = user_addresses.get(user_id, [])
    if len(current) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("Maximum addresses (16) reached.")
        return
    if to_address in current:
        update.message.reply_text("Address already saved.")
        return
    current.append(to_address)
    user_addresses[user_id] = current
    save_user_addresses()
    update.message.reply_text(f"Address {to_address} added.")
    
# Slash command: /removeaddress
def remove_address(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if len(context.args) < 1:
        update.message.reply_text("Usage: /removeaddress <address>")
        return
    addr = context.args[0].strip().lower()
    try:
        to_address = w3.to_checksum_address(addr)
    except Exception:
        update.message.reply_text("Invalid address.")
        return
    current = user_addresses.get(user_id, [])
    if to_address not in current:
        update.message.reply_text("Address not found.")
        return
    current.remove(to_address)
    user_addresses[user_id] = current
    save_user_addresses()
    update.message.reply_text(f"Address {to_address} removed.")

# Slash command: /checkaddress
def check_address(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    current = user_addresses.get(user_id, [])
    if not current:
        update.message.reply_text("No saved addresses.")
    else:
        update.message.reply_text("Saved addresses:\n" + "\n".join(current))

# Slash command: /claimmanual
def claim_manual(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        update.message.reply_text("No saved addresses. Use /addaddress to add one.")
        return
    results = []
    now = datetime.now()
    hist = user_claims.get(int(user_id), [])
    hist = [(addr, t) for addr, t in hist if now - t < CLAIM_COOLDOWN]
    if len(hist) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("Claim limit reached in 48 hours.")
        return
    for addr in addresses:
        try:
            to_address = w3.to_checksum_address(addr)
        except Exception:
            results.append(f"{addr}: Invalid format.")
            continue
        if to_address in address_claims and (now - address_claims[to_address] < CLAIM_COOLDOWN):
            results.append(f"{addr}: Already claimed.")
            continue
        try:
            with open("abi_acl.json", "r") as f:
                acl_abi = json.load(f)
            acl_addr = "0x6Dbc02BD4adbb34caeFb081fe60eDC41e393521B"
            acl_contract = w3.eth.contract(address=acl_addr, abi=acl_abi)
            if not acl_contract.functions.isAlphaTester(to_address).call():
                results.append(f"{addr}: Not whitelisted.")
                continue
        except Exception:
            results.append(f"{addr}: Whitelist error.")
            continue
        try:
            faucet_addr = w3.to_checksum_address(FAUCET_ADDRESS)
        except Exception:
            results.append(f"{addr}: Faucet error.")
            continue
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
        except Exception:
            tx['gas'] = 35000
        try:
            signed_tx = w3.eth.account.sign_transaction(tx, FAUCET_PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            hash_str = tx_hash.hex()
            if not hash_str.startswith("0x"):
                hash_str = "0x" + hash_str
            results.append(f"{addr}: Success, Tx: {hash_str}")
            address_claims[to_address] = now
            hist.append((to_address, now))
        except Exception:
            results.append(f"{addr}: Claim error.")
    user_claims.setdefault(int(user_id), []).extend(hist)
    update.message.reply_text("\n".join(results))

# Dispatcher registration and main
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Slash commands
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("checkwhitelist", check_whitelist_contract))
    dp.add_handler(CommandHandler("addaddress", add_address))
    dp.add_handler(CommandHandler("removeaddress", remove_address))
    dp.add_handler(CommandHandler("checkaddress", check_address))
    dp.add_handler(CommandHandler("claimmanual", claim_manual))
    
    # Conversation for claim faucet (triggered by button)
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^💧 Claim Faucet$"), faucet_start)],
        states={
            FAUCET_WAIT_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, faucet_receive_address)]
        },
        fallbacks=[CommandHandler("cancel", faucet_cancel)],
        per_user=True,
    )
    dp.add_handler(conv_handler)
    
    # Button triggers for static commands
    dp.add_handler(MessageHandler(Filters.regex("^(⏰ Check Balance)$"), balance))
    dp.add_handler(MessageHandler(Filters.regex("^(❓ Help)$"), help_command))
    
    updater.start_polling()
    logger.info("Bot started!")
    updater.idle()

if __name__ == '__main__':
    main()
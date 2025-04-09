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
ETH_RPC_URL = os.getenv("ETH_RPC_URL")  # e.g., via Infura or Alchemy
FAUCET_ADDRESS = os.getenv("FAUCET_ADDRESS")
FAUCET_PRIVATE_KEY = os.getenv("FAUCET_PRIVATE_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
FAUCET_AMOUNT = 0.1  # Claim amount: 0.1 ETH per claim
CHAIN_ID = 421614    # ARB ETH chain ID

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
# 48-hour claim rules
# ------------------------------
CLAIM_COOLDOWN = timedelta(hours=48)
MAX_ADDRESSES_PER_USER = 16

# Track claims per address and per user
address_claims = {}   # {ethereum_address: claim_time}
user_claims = {}      # {telegram_user_id: [(address, claim_time), ...]}

# ------------------------------
# User Address Storage
# ------------------------------
USER_ADDRESSES_FILE = "user_addresses.json"
# Structure: { "telegram_user_id": [address1, address2, ...] }
user_addresses = {}

def load_user_addresses():
    global user_addresses
    if os.path.exists(USER_ADDRESSES_FILE):
        try:
            with open(USER_ADDRESSES_FILE, "r") as f:
                user_addresses = json.load(f)
            logger.info("User addresses loaded successfully.")
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
        logger.info("User addresses saved successfully.")
    except Exception as e:
        logger.error(f"Error saving user addresses: {e}")

load_user_addresses()

# ------------------------------
# Conversation States
# ------------------------------
FAUCET_WAIT_ADDRESS = 1
ADD_ADDRESS_WAIT = 2
REMOVE_ADDRESS_WAIT = 3

# ------------------------------
# Main Menu Reply Keyboard
# ------------------------------
def main_menu_keyboard(user_id: int):
    keyboard = [
        ["💧 Claim Faucet"],
        ["📥 Add Address"],
        ["🗑️ Remove Address"],
        ["📋 Check Addresses"],
        ["🤖 Manual Claim"],
        ["⏰ Check Balance"],
        ["❓ Help"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

# ------------------------------
# Command Handlers - Main Menu Options
# ------------------------------
def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    update.message.reply_text(
        "Welcome to the ARB ETH Faucet Bot!\n\nPlease use the menu below:",
        reply_markup=main_menu_keyboard(user_id)
    )
    logger.info(f"User {user_id} started the bot.")

def help_command(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    help_text = (
        "Process:\n"
        "1. Tap 'Claim Faucet' and enter your Ethereum address.\n"
        "   - The bot verifies your address on-chain.\n"
        "   - If approved, you receive 0.1 ETH (once every 48 hrs).\n"
        "2. Tap '📥 Add Address' to store an address for auto-claim.\n"
        "3. Tap '🗑️ Remove Address' to delete a stored address.\n"
        "4. Tap '📋 Check Addresses' to view your stored addresses.\n"
        "5. Tap '🤖 Manual Claim' to claim for all your stored addresses (subject to limits).\n"
        "6. Tap '⏰ Check Balance' to view the faucet wallet’s balance.\n"
        "7. Tap '❓ Help' to see this message.\n"
        "8. Use /checkwhitelist <address> to verify an address's whitelist status.\n"
        "9. Admins can update the faucet amount using /setamount <amount>."
    )
    update.message.reply_text(help_text, reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} requested help.")

def balance(update: Update, context: CallbackContext) -> None:
    try:
        bal = w3.eth.get_balance(FAUCET_ADDRESS)
        balance_eth = w3.from_wei(bal, "ether")
        update.message.reply_text(f"Faucet balance: {balance_eth} ETH")
        logger.info(f"Faucet balance: {balance_eth} ETH")
    except Exception as e:
        update.message.reply_text(f"Error fetching balance: {str(e)}")
        logger.error(f"Error fetching balance: {e}")

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
        "Enter your Ethereum address to claim 0.1 ETH (or send /cancel to abort):",
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
    if to_address in address_claims and (now - address_claims[to_address] < CLAIM_COOLDOWN):
        update.message.reply_text("This address has already claimed in the last 48 hours.")
        logger.info(f"Address {eth_address} already claimed.")
        return ConversationHandler.END

    user_hist = user_claims.get(user_id, [])
    user_hist = [(addr, t) for addr, t in user_hist if now - t < CLAIM_COOLDOWN]
    if len(user_hist) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("You have reached the maximum number of claims (16 addresses) in 48 hours.")
        logger.info(f"User {user_id} reached max claims.")
        return ConversationHandler.END

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

    address_claims[to_address] = now
    user_hist.append((to_address, now))
    user_claims[user_id] = user_hist

    update.message.reply_text("Returning to main menu.", reply_markup=main_menu_keyboard(user_id))
    return ConversationHandler.END

def faucet_cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    update.message.reply_text("Faucet claim canceled.", reply_markup=main_menu_keyboard(user_id))
    logger.info(f"User {user_id} canceled faucet claim.")
    return ConversationHandler.END

# ------------------------------
# Manual Claim: Process all stored addresses
# ------------------------------
def claim_manual(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    addresses = user_addresses.get(user_id, [])
    if not addresses:
        update.message.reply_text("You have no saved addresses. Use /addaddress to add one.")
        return
    results = []
    now = datetime.now()
    hist = user_claims.get(int(user_id), [])
    hist = [(addr, t) for addr, t in hist if now - t < CLAIM_COOLDOWN]
    if len(hist) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("You have reached the maximum number of claims in 48 hours.")
        return
    for addr in addresses:
        try:
            to_address = w3.to_checksum_address(addr)
        except Exception as e:
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
        except Exception as e:
            results.append(f"{addr}: Error checking whitelist.")
            continue
        try:
            faucet_addr = w3.to_checksum_address(FAUCET_ADDRESS)
        except Exception as e:
            results.append(f"{addr}: Error processing faucet address.")
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
        except Exception as e:
            tx['gas'] = 35000
        try:
            signed_tx = w3.eth.account.sign_transaction(tx, FAUCET_PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            hash_str = tx_hash.hex()
            if not hash_str.startswith("0x"):
                hash_str = "0x" + hash_str
            results.append(f"{addr}: Success, Tx Hash: {hash_str}")
            address_claims[to_address] = now
            hist.append((to_address, now))
        except Exception as e:
            results.append(f"{addr}: Error during claim.")
    user_claims.setdefault(int(user_id), []).extend(hist)
    update.message.reply_text("\n".join(results))
    logger.info(f"User {user_id} manual claim results: {results}")

# ------------------------------
# Address Management Commands via Buttons
# ------------------------------
def add_address(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if len(context.args) < 1:
        update.message.reply_text("Usage: /addaddress <ethereum_address>")
        return
    addr = context.args[0].strip().lower()
    try:
        to_address = w3.to_checksum_address(addr)
    except Exception as e:
        update.message.reply_text("Invalid Ethereum address.")
        logger.error(f"Invalid address by user {user_id}: {addr}, error: {e}")
        return
    current = user_addresses.get(user_id, [])
    if len(current) >= MAX_ADDRESSES_PER_USER:
        update.message.reply_text("You have reached the maximum number of addresses (16).")
        return
    if to_address in current:
        update.message.reply_text("Address already saved.")
        return
    current.append(to_address)
    user_addresses[user_id] = current
    save_user_addresses()
    update.message.reply_text(f"Address {to_address} added.")
    logger.info(f"User {user_id} added address {to_address}.")

def remove_address(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    if len(context.args) < 1:
        update.message.reply_text("Usage: /removeaddress <ethereum_address>")
        return
    addr = context.args[0].strip().lower()
    try:
        to_address = w3.to_checksum_address(addr)
    except Exception as e:
        update.message.reply_text("Invalid Ethereum address.")
        logger.error(f"Invalid address by user {user_id}: {addr}, error: {e}")
        return
    current = user_addresses.get(user_id, [])
    if to_address not in current:
        update.message.reply_text("Address not found in your list.")
        return
    current.remove(to_address)
    user_addresses[user_id] = current
    save_user_addresses()
    update.message.reply_text(f"Address {to_address} removed.")
    logger.info(f"User {user_id} removed address {to_address}.")

def check_address(update: Update, context: CallbackContext) -> None:
    user_id = str(update.effective_user.id)
    current = user_addresses.get(user_id, [])
    if not current:
        update.message.reply_text("You have no saved addresses.")
    else:
        update.message.reply_text("Your saved addresses:\n" + "\n".join(current))
    logger.info(f"User {user_id} checked addresses.")

# ------------------------------
# Dispatcher Registration and Main
# ------------------------------
def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Main commands (accessible via buttons)
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("balance", balance))
    dp.add_handler(CommandHandler("setamount", set_amount))
    dp.add_handler(CommandHandler("checkwhitelist", check_whitelist_contract))
    dp.add_handler(CommandHandler("addaddress", add_address))
    dp.add_handler(CommandHandler("removeaddress", remove_address))
    dp.add_handler(CommandHandler("checkaddress", check_address))
    dp.add_handler(CommandHandler("claimmanual", claim_manual))
    
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
    dp.add_handler(MessageHandler(Filters.regex("^(❓ Help)$"), help_command))
    
    updater.start_polling()
    logger.info("Bot started!")
    updater.idle()

if __name__ == '__main__':
    main()
#!/usr/bin/env python3
"""
Zebra SMS Telegram Bot — bottom keyboard UI + admin service/country manager + Auto SMS Earnings & Withdraw System.
"""

import os
import re
import json
import time
import asyncio
import logging
import threading
import traceback
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("zebra_bot")

BASE_URL = "https://api.zebrasms.com/api/v1"
API_KEY = "6U3G3DDZ6GB"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

HEADERS = {
    "MAuth": API_KEY,
    "Content-Type": "application/json",
    "User-Agent": "curl/8.4.0",
    "Accept": "*/*",
}

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
SERVICES_FILE = os.path.join(DATA_DIR, "services.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
USERS_FILE = os.path.join(DATA_DIR, "users_balance.json")

CONFIG = {
    "max_per_user": 1, 
    "range_hits": {},
    "sms_rate": 0.05,       # ডিফল্ট প্রতি সফল SMS-এর রেট ($0.05)
    "min_withdraw": 1.0     # ডিফল্ট মিনিমাম উইথড্র অ্যামাউন্ট ($1.00)
}

USER_DATA = {}  # {str(chat_id): {"balance": float, "payment_info": str}}

def load_config():
    global CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                CONFIG.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2)
    except OSError as e:
        print(f"Could not save config.json: {e}")

def load_users_data():
    global USER_DATA
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                USER_DATA = json.load(f)
        except (json.JSONDecodeError, OSError):
            USER_DATA = {}

def save_users_data():
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_DATA, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"Could not save users_balance.json: {e}")

def get_user_info(chat_id):
    load_users_data()
    cid = str(chat_id)
    if cid not in USER_DATA:
        USER_DATA[cid] = {"balance": 0.0, "payment_info": "Not Set"}
        save_users_data()
    # Backward compatibility if data structure was simple float
    if isinstance(USER_DATA[cid], (int, float)):
        USER_DATA[cid] = {"balance": float(USER_DATA[cid]), "payment_info": "Not Set"}
        save_users_data()
    return USER_DATA[cid]

def update_user_balance(chat_id, amount):
    info = get_user_info(chat_id)
    info["balance"] = round(info["balance"] + amount, 2)
    USER_DATA[str(chat_id)] = info
    save_users_data()
    return info["balance"]

def set_user_payment_info(chat_id, details):
    info = get_user_info(chat_id)
    info["payment_info"] = details
    USER_DATA[str(chat_id)] = info
    save_users_data()

ACTIVE_BATCH = {}          # chat_id -> {"message_id":int, "rng":str, "label":str|None, "numbers": {number: {...}}}
KNOWN_USERS = set()        # chat_ids that have started the bot
NUMBERS_ALLOCATED = 0
ADMIN_STATE = {}           # admin chat_id -> ("action_name", extra_data)
USER_STATE = {}            # regular chat_id -> state
SEEN_FEED_KEYS = set()     
FEED_POLL_SECONDS = 5      

GROUP_USERNAME = os.environ.get("GROUP_USERNAME", "-1004415108815")
GROUP_LINK = os.environ.get("GROUP_LINK", "https://t.me/otpmastersgrp")
CHANNEL_USERNAME = os.environ.get("METHOD_CHANNEL_USERNAME", os.environ.get("CHANNEL_USERNAME", ""))
CHANNEL_LINK = os.environ.get("METHOD_CHANNEL_LINK", os.environ.get("CHANNEL_LINK", ""))
BACKUP_CHANNEL_USERNAME = os.environ.get("BACKUP_CHANNEL_USERNAME", "")
BACKUP_CHANNEL_LINK = os.environ.get("BACKUP_CHANNEL_LINK", "")

services = {}
AUTO_SERVICE_NAME = "🔎 Auto-Detected"
AUTO_POLL_SECONDS = 10  

CALLING_CODES = {
    "1": "USA/Canada", "7": "Russia/Kazakhstan", "20": "Egypt", "27": "South Africa",
    "30": "Greece", "31": "Netherlands", "32": "Belgium", "33": "France", "34": "Spain",
    "36": "Hungary", "39": "Italy", "40": "Romania", "41": "Switzerland", "43": "Austria",
    "44": "United Kingdom", "45": "Denmark", "46": "Sweden", "47": "Norway", "48": "Poland",
    "49": "Germany", "51": "Peru", "52": "Mexico", "53": "Cuba", "54": "Argentina",
    "55": "Brazil", "56": "Chile", "57": "Colombia", "58": "Venezuela", "60": "Malaysia",
    "61": "Australia", "62": "Indonesia", "63": "Philippines", "64": "New Zealand",
    "65": "Singapore", "66": "Thailand", "81": "Japan", "82": "South Korea", "84": "Vietnam",
    "86": "China", "90": "Turkey", "91": "India", "92": "Pakistan", "93": "Afghanistan",
    "94": "Sri Lanka", "95": "Myanmar", "98": "Iran",
    "211": "South Sudan", "212": "Morocco", "213": "Algeria", "216": "Tunisia",
    "218": "Libya", "220": "Gambia", "221": "Senegal", "222": "Mauritania", "223": "Mali",
    "224": "Guinea", "225": "Ivory Coast", "226": "Burkina Faso", "227": "Niger",
    "228": "Togo", "229": "Benin", "230": "Mauritius", "231": "Liberia", "232": "Sierra Leone",
    "233": "Ghana", "234": "Nigeria", "235": "Chad", "236": "Central African Republic",
    "237": "Cameroon", "238": "Cape Verde", "239": "Sao Tome and Principe",
    "240": "Equatorial Guinea", "241": "Gabon", "242": "Republic of the Congo",
    "243": "DR Congo", "244": "Angola", "245": "Guinea-Bissau", "248": "Seychelles",
    "249": "Sudan", "250": "Rwanda", "251": "Ethiopia", "252": "Somalia", "253": "Djibouti",
    "254": "Kenya", "255": "Tanzania", "256": "Uganda", "257": "Burundi", "258": "Mozambique",
    "260": "Zambia", "261": "Madagascar", "262": "Reunion/Mayotte", "263": "Zimbabwe",
    "264": "Namibia", "265": "Malawi", "266": "Lesotho", "267": "Botswana", "268": "Eswatini",
    "269": "Comoros", "290": "Saint Helena", "291": "Eritrea",
    "297": "Aruba", "298": "Faroe Islands", "299": "Greenland",
    "350": "Gibraltar", "351": "Portugal", "352": "Luxembourg", "353": "Ireland",
    "354": "Iceland", "355": "Albania", "356": "Malta", "357": "Cyprus", "358": "Finland",
    "359": "Bulgaria", "370": "Lithuania", "371": "Latvia", "372": "Estonia",
    "373": "Moldova", "374": "Armenia", "375": "Belarus", "376": "Andorra",
    "377": "Monaco", "378": "San Marino", "380": "Ukraine", "381": "Serbia",
    "382": "Montenegro", "383": "Kosovo", "385": "Croatia", "386": "Slovenia",
    "387": "Bosnia and Herzegovina", "389": "North Macedonia", "420": "Czech Republic",
    "421": "Slovakia", "423": "Liechtenstein",
    "500": "Falkland Islands", "501": "Belize", "502": "Guatemala", "503": "El Salvador",
    "504": "Honduras", "505": "Nicaragua", "506": "Costa Rica", "507": "Panama",
    "508": "Saint Pierre and Miquelon", "509": "Haiti", "590": "Guadeloupe",
    "591": "Bolivia", "592": "Guyana", "593": "Ecuador", "594": "French Guiana",
    "595": "Paraguay", "596": "Martinique", "597": "Suriname", "598": "Uruguay",
    "599": "Curacao",
    "670": "East Timor", "672": "Norfolk Island", "673": "Brunei", "674": "Nauru",
    "675": "Papua New Guinea", "676": "Tonga", "677": "Solomon Islands", "678": "Vanuatu",
    "679": "Fiji", "680": "Palau", "681": "Wallis and Futuna", "682": "Cook Islands",
    "683": "Niue", "685": "Samoa", "686": "Kiribati", "687": "New Caledonia",
    "688": "Tuvalu", "689": "French Polynesia", "690": "Tokelau", "691": "Micronesia",
    "692": "Marshall Islands",
    "850": "North Korea", "852": "Hong Kong", "853": "Macau", "855": "Cambodia",
    "856": "Laos", "870": "Inmarsat", "880": "Bangladesh", "886": "Taiwan",
    "960": "Maldives", "961": "Lebanon", "962": "Jordan", "963": "Syria", "964": "Iraq",
    "965": "Kuwait", "966": "Saudi Arabia", "967": "Yemen", "968": "Oman",
    "970": "Palestine", "971": "United Arab Emirates", "972": "Israel", "973": "Bahrain",
    "974": "Qatar", "975": "Bhutan", "976": "Mongolia", "977": "Nepal", "992": "Tajikistan",
    "993": "Turkmenistan", "994": "Azerbaijan", "995": "Georgia", "996": "Kyrgyzstan",
    "998": "Uzbekistan",
}

def guess_country_from_range(rng):
    digits = "".join(ch for ch in rng if ch.isdigit())
    for length in (3, 2, 1):
        prefix = digits[:length]
        if prefix in CALLING_CODES:
            return CALLING_CODES[prefix]
    return None

COUNTRY_TO_ISO2 = {
    "USA/Canada": "US", "Russia/Kazakhstan": "RU", "Egypt": "EG", "South Africa": "ZA",
    "Greece": "GR", "Netherlands": "NL", "Belgium": "BE", "France": "FR", "Spain": "ES",
    "Hungary": "HU", "Italy": "IT", "Romania": "RO", "Switzerland": "CH", "Austria": "AT",
    "United Kingdom": "GB", "Denmark": "DK", "Sweden": "SE", "Norway": "NO", "Poland": "PL",
    "Germany": "DE", "Peru": "PE", "Mexico": "MX", "Cuba": "CU", "Argentina": "AR",
    "Brazil": "BR", "Chile": "CL", "Colombia": "CO", "Venezuela": "VE", "Malaysia": "MY",
    "Australia": "AU", "Indonesia": "ID", "Philippines": "PH", "New Zealand": "NZ",
    "Singapore": "SG", "Thailand": "TH", "Japan": "JP", "South Korea": "KR", "Vietnam": "VN",
    "China": "CN", "Turkey": "TR", "India": "IN", "Pakistan": "PK", "Afghanistan": "AF",
    "Sri Lanka": "LK", "Myanmar": "MM", "Iran": "IR",
    "South Sudan": "SS", "Morocco": "MA", "Algeria": "DZ", "Tunisia": "TN", "Libya": "LY",
    "Gambia": "GM", "Senegal": "SN", "Mauritania": "MR", "Mali": "ML", "Guinea": "GN",
    "Ivory Coast": "CI", "Burkina Faso": "BF", "Niger": "NE", "Togo": "TG", "Benin": "BJ",
    "Mauritius": "MU", "Liberia": "LR", "Sierra Leone": "SL", "Ghana": "GH", "Nigeria": "NG",
    "Chad": "TD", "Central African Republic": "CF", "Cameroon": "CM", "Cape Verde": "CV",
    "Sao Tome and Principe": "ST", "Equatorial Guinea": "GQ", "Gabon": "GA",
    "Republic of the Congo": "CG", "DR Congo": "CD", "Angola": "AO", "Guinea-Bissau": "GW",
    "Seychelles": "SC", "Sudan": "SD", "Rwanda": "RW", "Ethiopia": "ET", "Somalia": "SO",
    "Djibouti": "DJ", "Kenya": "KE", "Tanzania": "TZ", "Uganda": "UG", "Burundi": "BI",
    "Mozambique": "MZ", "Zambia": "ZM", "Madagascar": "MG", "Reunion/Mayotte": "RE",
    "Zimbabwe": "ZW", "Namibia": "NA", "Malawi": "MW", "Lesotho": "LS", "Botswana": "BW",
    "Eswatini": "SZ", "Comoros": "KM", "Saint Helena": "SH", "Eritrea": "ER",
    "Aruba": "AW", "Faroe Islands": "FO", "Greenland": "GL",
    "Gibraltar": "GI", "Portugal": "PT", "Luxembourg": "LU", "Ireland": "IE",
    "Iceland": "IS", "Albania": "AL", "Malta": "MT", "Cyprus": "CY", "Finland": "FI",
    "Bulgaria": "BG", "Lithuania": "LT", "Latvia": "LV", "Estonia": "EE",
    "Moldova": "MD", "Armenia": "AM", "Belarus": "BY", "Andorra": "AD",
    "Monaco": "MC", "San Marino": "SM", "Ukraine": "UA", "Serbia": "RS",
    "Montenegro": "ME", "Kosovo": "XK", "Croatia": "HR", "Slovenia": "SI",
    "Bosnia and Herzegovina": "BA", "North Macedonia": "MK", "Czech Republic": "CZ",
    "Slovakia": "SK", "Liechtenstein": "LI",
    "Falkland Islands": "FK", "Belize": "BZ", "Guatemala": "GT", "El Salvador": "SV",
    "Honduras": "HN", "Nicaragua": "NI", "Costa Rica": "CR", "Panama": "PA",
    "Saint Pierre and Miquelon": "PM", "Haiti": "HT", "Guadeloupe": "GP",
    "Bolivia": "BO", "Guyana": "GY", "Ecuador": "EC", "French Guiana": "GF",
    "Paraguay": "PY", "Martinique": "MQ", "Suriname": "SR", "Uruguay": "UY",
    "Curacao": "CW",
    "East Timor": "TL", "Norfolk Island": "NF", "Brunei": "BN", "Nauru": "NR",
    "Papua New Guinea": "PG", "Tonga": "TO", "Solomon Islands": "SB", "Vanuatu": "VU",
    "Fiji": "FJ", "Palau": "PW", "Wallis and Futuna": "WF", "Cook Islands": "CK",
    "Niue": "NU", "Samoa": "WS", "Kiribati": "KI", "New Caledonia": "NC",
    "Tuvalu": "TV", "French Polynesia": "PF", "Tokelau": "TK", "Micronesia": "FM",
    "Marshall Islands": "MH",
    "North Korea": "KP", "Hong Kong": "HK", "Macau": "MO", "Cambodia": "KH",
    "Laos": "LA", "Bangladesh": "BD", "Taiwan": "TW",
    "Maldives": "MV", "Lebanon": "LB", "Jordan": "JO", "Syria": "SY", "Iraq": "IQ",
    "Kuwait": "KW", "Saudi Arabia": "SA", "Yemen": "YE", "Oman": "OM",
    "Palestine": "PS", "United Arab Emirates": "AE", "Israel": "IL", "Bahrain": "BH",
    "Qatar": "QA", "Bhutan": "BT", "Mongolia": "MN", "Nepal": "NP", "Tajikistan": "TJ",
    "Turkmenistan": "TM", "Azerbaijan": "AZ", "Georgia": "GE", "Kyrgyzstan": "KG",
    "Uzbekistan": "UZ",
}

def flag_emoji(country_name):
    iso2 = COUNTRY_TO_ISO2.get(country_name)
    if not iso2 or len(iso2) != 2:
        return ""
    return "".join(chr(127397 + ord(ch)) for ch in iso2.upper())

SERVICE_KEYWORDS = [
    "facebook", "instagram", "whatsapp", "telegram", "google", "tiktok", "twitter",
    "snapchat", "viber", "wechat", "uber", "paypal", "amazon", "netflix",
    "microsoft", "apple", "spotify", "discord", "tinder", "line", "grab", "gojek",
]

def detect_service(message):
    lower = message.lower()
    for kw in SERVICE_KEYWORDS:
        if kw in lower:
            return kw.capitalize()
    return None

def find_known_range_for_country(country):
    for countries in services.values():
        for name, rng in countries.items():
            if name.split(" (")[0] == country:
                return rng
    return None

def mask_code_in_message(message, code):
    if not code:
        return message
    return message.replace(code, "•" * len(code))

def mask_number_middle(number, visible_start=4, visible_end=3, mask_len=4):
    digits_only = number.lstrip("+")
    prefix = "+" if number.startswith("+") else ""
    if len(digits_only) <= visible_start + visible_end:
        return number
    start = digits_only[:visible_start]
    end = digits_only[-visible_end:]
    return f"{prefix}{start}{'•' * mask_len}{end}"

_MD_SPECIAL = ("\\", "_", "*", "`", "[")

def md_escape(text):
    text = str(text)
    for ch in _MD_SPECIAL:
        text = text.replace(ch, "\\" + ch)
    return text

def load_services():
    global services
    if os.path.exists(SERVICES_FILE):
        try:
            with open(SERVICES_FILE, "r", encoding="utf-8") as f:
                services = json.load(f)
        except (json.JSONDecodeError, OSError):
            services = {}
    else:
        services = {}

def save_services():
    try:
        with open(SERVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(services, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"Could not save services.json: {e}")

load_services()
load_config()
load_users_data()

# ── Health server ────────────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()

    def log_message(self, *args):
        pass

def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server listening on port %s", port)

# ── API helpers ──────────────────────────────────────────────────────────
def _call_sync(method, path, **kwargs):
    url = f"{BASE_URL}{path}"
    resp = requests.request(method, url, headers=HEADERS, timeout=15, **kwargs)
    logger.info(
        "Zebra API call: %s %s | body=%s | HTTP %s | response=%s",
        method, url, kwargs.get("json") or kwargs.get("params"), resp.status_code, resp.text[:500],
    )
    try:
        data = resp.json()
    except ValueError:
        snippet = resp.text[:200].strip() or "(empty response)"
        raise RuntimeError(f"API returned a non-JSON response (HTTP {resp.status_code}): {snippet}")
    meta = data.get("meta", {})
    if meta.get("code") != 0:
        raise RuntimeError(data.get("message") or meta.get("error") or f"API error {meta.get('code')}")
    return data.get("data")

async def api_call(method, path, **kwargs):
    return await asyncio.to_thread(_call_sync, method, path, **kwargs)

def is_admin(user_id):
    return user_id in ADMIN_IDS

def extract_code(message):
    match = re.search(r"\b\d{3,8}\b", message)
    if match:
        return match.group(0)
    match = re.search(r"\b[A-Za-z0-9]{4,8}\b", message)
    return match.group(0) if match else None

FORCE_JOIN_CHATS = [
    (label, chat_id, link)
    for label, chat_id, link in [
        ("OTP/Range Group", GROUP_USERNAME, GROUP_LINK),
        ("Method Channel", CHANNEL_USERNAME, CHANNEL_LINK),
        ("Backup Channel", BACKUP_CHANNEL_USERNAME, BACKUP_CHANNEL_LINK),
    ]
    if chat_id and link
]

async def missing_required_joins(context, user_id):
    missing = []
    for label, chat_id, link in FORCE_JOIN_CHATS:
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked"):
                missing.append((label, chat_id, link))
        except Exception as e:
            logger.info("membership check failed for %s / %s: %s", chat_id, user_id, e)
    return missing

def join_required_kb(missing):
    rows = [[InlineKeyboardButton(f"➡️ Join {label}", url=link)] for label, _cid, link in missing]
    rows.append([InlineKeyboardButton("✅ I've Joined", callback_data="checkjoin")])
    return InlineKeyboardMarkup(rows)

async def enforce_join_or_prompt(chat_id, context, edit_query=None, user_id=None):
    if is_admin(user_id if user_id is not None else chat_id):
        return True
    if not FORCE_JOIN_CHATS:
        return True

    missing = await missing_required_joins(context, chat_id)
    if not missing:
        return True

    msg = "🔒 *Access Locked*\n\nJoin *all* of the following to use this bot, then tap *I've Joined*:"
    kb = join_required_kb(missing)
    if edit_query:
        try:
            await edit_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except Exception:
            await context.bot.send_message(chat_id, msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id, msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return False

async def post_to_group(context, text):
    if not GROUP_USERNAME:
        return
    try:
        await context.bot.send_message(GROUP_USERNAME, text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.info("post_to_group failed: %s", e)

# ── Bottom Keyboards ────────────────────────────────────────────────────
USER_ROWS = [
    ["💰 My Balance", "💳 Payment Details"],
    ["💸 Withdraw", "📱 Get Number"], 
    ["📡 Browse Ranges", "📨 My Codes"], 
    ["🔴 Live Feed", "ℹ️ Help"]
]

CANCEL_KB = ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)

def main_kb(user_id):
    rows = [row[:] for row in USER_ROWS]
    if is_admin(user_id):
        rows.append(["🛠 Admin"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def back_inline(target="noop"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=target)]])

# ── /start ───────────────────────────────────────────────────────────────
WELCOME = (
    "🤖✨ *OTP MASTER BOT* ✨🤖\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "📲 Grab a virtual number and get its verification code — instantly, right here.\n\n"
    "👇 Use the buttons below to get started 👇"
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    KNOWN_USERS.add(chat_id)
    get_user_info(chat_id)

    if not await enforce_join_or_prompt(chat_id, context, user_id=update.effective_user.id):
        return
    await update.message.reply_text(
        WELCOME, parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb(update.effective_user.id)
    )

# ── Service / Country UI ─────────────────────────────────────────────────
async def show_services(update_or_query, edit=False, user_id=None):
    custom_btn = [InlineKeyboardButton("✍️ Custom Range", callback_data="customrange")]
    visible_services = {
        name: c for name, c in services.items() if name != AUTO_SERVICE_NAME or is_admin(user_id)
    }

    if not visible_services:
        text = "No services configured yet. Enter a custom range yourself, or ask an admin to add a service."
        kb = InlineKeyboardMarkup([custom_btn])
        if edit:
            await update_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        else:
            await update_or_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    buttons = [[InlineKeyboardButton(name, callback_data=f"svc:{name}")] for name in visible_services.keys()]
    buttons.append(custom_btn)
    text = "📱 *Choose a service:*"
    if edit:
        await update_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update_or_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))

async def show_countries(query, service):
    countries = services.get(service, {})
    back_btn = InlineKeyboardButton("⬅️ Back", callback_data="svcback")
    if not countries:
        await query.edit_message_text(
            f"No countries configured for {service} yet.", reply_markup=InlineKeyboardMarkup([[back_btn]])
        )
        return

    last_seen = CONFIG.get("range_last_seen", {})

    def base_name(country):
        return country.split(" (")[0]

    groups = {}
    for country, rng in countries.items():
        groups.setdefault(base_name(country), []).append((country, rng))
    for group in groups.values():
        group.sort(key=lambda cr: last_seen.get(cr[1], 0), reverse=True)
    ordered_groups = sorted(groups.items(), key=lambda kv: max(last_seen.get(r, 0) for _, r in kv[1]), reverse=True)
    ordered = [cr for _, group in ordered_groups for cr in group]

    buttons = [
        [InlineKeyboardButton(country, callback_data=f"svccountry:{service}:{country}")]
        for country, _rng in ordered
    ]
    buttons.append([back_btn])
    await query.edit_message_text(
        f"🌍 *{service}* — choose a country:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def service_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    KNOWN_USERS.add(chat_id)

    if not await enforce_join_or_prompt(chat_id, context, edit_query=query, user_id=query.from_user.id):
        return

    if data == "customrange":
        USER_STATE[chat_id] = "awaiting_custom_range"
        await query.edit_message_text(
            "✍️ Send the range you want a number from, e.g. `22501XXX`.\nSend /cancel to abort.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "svcback":
        await show_services(query, edit=True, user_id=query.from_user.id)

    elif data.startswith("svc:"):
        service = data.split(":", 1)[1]
        await show_countries(query, service)

    elif data.startswith("svccountry:"):
        _, service, country = data.split(":", 2)
        rng = services.get(service, {}).get(country)
        if not rng:
            await query.edit_message_text("That option no longer exists.", reply_markup=back_inline())
            return
        await start_getnum_flow(chat_id, rng, context, edit_query=query, label=f"{service} / {country}")

# ── Raw range browsing ──────────────────────────────────────────────────
async def show_sender_list(update_or_query, edit=False):
    try:
        data = await api_call("GET", "/publicapi/liveaccess")
    except Exception as e:
        text = f"⚠️ Error: {e}"
        if edit:
            await update_or_query.edit_message_text(text)
        else:
            await update_or_query.message.reply_text(text)
        return

    rows = data.get("rows", [])
    if not rows:
        text = "No senders delivering right now."
        if edit:
            await update_or_query.edit_message_text(text)
        else:
            await update_or_query.message.reply_text(text)
        return

    buttons = [[InlineKeyboardButton(f"{r['sender']} ({len(r['ranges'])})", callback_data=f"sender:{r['sender']}")]
               for r in rows[:30]]
    kb = InlineKeyboardMarkup(buttons)
    text = "📡 *Senders delivering right now*\nTap one to see its raw ranges:"
    if edit:
        await update_or_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await update_or_query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

async def show_ranges_for_sender(query, sender):
    back_btn = InlineKeyboardButton("⬅️ Back", callback_data="backtosenders")
    try:
        data = await api_call("GET", "/publicapi/liveaccess", params={"sender": sender})
    except Exception as e:
        await query.edit_message_text(f"⚠️ Error: {e}", reply_markup=InlineKeyboardMarkup([[back_btn]]))
        return
    rows = data.get("rows", [])
    ranges = rows[0]["ranges"] if rows else []
    if not ranges:
        await query.edit_message_text(f"No ranges found for {sender}.", reply_markup=InlineKeyboardMarkup([[back_btn]]))
        return
    buttons = [[InlineKeyboardButton(rng, callback_data=f"getnum:{rng}")] for rng in ranges]
    buttons.append([back_btn])
    await query.edit_message_text(
        f"📡 *{sender}* — tap a range to allocate a number:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

async def raw_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    KNOWN_USERS.add(chat_id)

    if data == "checkjoin":
        missing = await missing_required_joins(context, chat_id)
        if missing:
            await query.answer("Still missing one or more — join them and try again.", show_alert=True)
            return
        await query.edit_message_text("✅ Thanks! Tap 📱 Get Number below to continue.")
        return

    if not await enforce_join_or_prompt(chat_id, context, edit_query=query, user_id=query.from_user.id):
        return

    if data.startswith("sender:"):
        await show_ranges_for_sender(query, data.split(":", 1)[1])

    elif data == "backtosenders":
        await show_sender_list(query, edit=True)

    elif data.startswith("getnum:"):
        payload = data.split(":", 1)[1]
        if "|" in payload:
            rng, label = payload.split("|", 1)
        else:
            rng, label = payload, None
        await start_getnum_flow(chat_id, rng, context, edit_query=query, label=label)

    elif data == "delbatch":
        ACTIVE_BATCH.pop(chat_id, None)
        try:
            await query.edit_message_text("🗑 Deleted.")
        except Exception:
            pass

# ── Get number + wait for code ──────────────────────────────────────────
async def getnum_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    KNOWN_USERS.add(chat_id)
    if not context.args:
        await update.message.reply_text("Usage: /getnum 22501XXX")
        return
    await start_getnum_flow(chat_id, context.args[0], context)

def render_batch_text(batch):
    label = batch.get("label")
    header = f"*{label}*\n\n" if label else ""
    lines = [f"{header}📱 *Numbers Assigned*\n"]
    for i, (number, info) in enumerate(batch["numbers"].items(), start=1):
        if info["status"] == "waiting":
            lines.append(f"{i}. `{number}` — ⌛ waiting")
        elif info["status"] == "delivered":
            code_part = f" — 🔑 `{info['code']}`" if info.get("code") else " — ✅ delivered"
            lines.append(f"{i}. `{number}`{code_part}")
        elif info["status"] == "expired":
            lines.append(f"{i}. `{number}` — ⌛ expired")
    return "\n".join(lines)

def batch_kb(rng, label, service=None):
    change_data = f"getnum:{rng}|{label}" if label else f"getnum:{rng}"
    rows = [[
        InlineKeyboardButton("🔄 Get New Batch (same range)", callback_data=change_data),
        InlineKeyboardButton("🗑 Delete All", callback_data="delbatch"),
    ]]
    if service and service in services:
        rows.append([InlineKeyboardButton("🌍 Change Country", callback_data=f"svc:{service}")])
    if GROUP_LINK:
        rows.append([InlineKeyboardButton("🔍 OTP GRUP 🔍", url=GROUP_LINK)])
    return InlineKeyboardMarkup(rows)

async def _render_batch(chat_id, context):
    batch = ACTIVE_BATCH.get(chat_id)
    if not batch:
        return
    service = batch["label"].split(" / ")[0] if batch.get("label") else None
    text = render_batch_text(batch)
    kb = batch_kb(batch["rng"], batch.get("label"), service)

    old_message_id = batch.get("message_id")
    try:
        sent = await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        batch["message_id"] = getattr(sent, "message_id", old_message_id)
        if old_message_id is not None:
            try:
                await context.bot.delete_message(chat_id, old_message_id)
            except Exception:
                pass
    except Exception as e:
        logger.error("Failed to re-send batch card for %s: %s", chat_id, e)

async def start_getnum_flow(chat_id, rng, context, edit_query=None, label=None):
    global NUMBERS_ALLOCATED

    if not await enforce_join_or_prompt(chat_id, context, edit_query=edit_query):
        return

    ACTIVE_BATCH.pop(chat_id, None)

    count = max(1, CONFIG.get("max_per_user", 1))
    allocated = []
    last_error = None
    for _ in range(count):
        try:
            data = await api_call("POST", "/publicapi/getnum", json={"range": rng})
        except Exception as e:
            last_error = str(e)
            break
        if not data or not data.get("rows"):
            last_error = "No number returned for that range."
            break
        allocated.append(data["rows"][0])

    if not allocated:
        msg = f"⚠️ Error: {last_error or 'could not allocate a number.'}"
        if edit_query:
            await edit_query.edit_message_text(msg)
        else:
            await context.bot.send_message(chat_id, msg)
        return

    NUMBERS_ALLOCATED += len(allocated)
    numbers = {
        row["number"]: {"status": "waiting", "code": None, "expires_ms": row["expires_ms"]}
        for row in allocated
    }
    batch = {"rng": rng, "label": label, "numbers": numbers}

    service = label.split(" / ")[0] if label else None
    text = render_batch_text(batch)
    kb = batch_kb(rng, label, service)
    if edit_query:
        sent = await edit_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        sent = await context.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

    batch["message_id"] = getattr(sent, "message_id", None)
    ACTIVE_BATCH[chat_id] = batch

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    USER_STATE.pop(chat_id, None)
    ADMIN_STATE.pop(chat_id, None)
    ACTIVE_BATCH.pop(chat_id, None)
    await update.message.reply_text("🛑 অ্যাকশন বাতিল করা হয়েছে।", reply_markup=main_kb(update.effective_user.id))

# ── Codes & Live Feed ────────────────────────────────────────────────────
async def codes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    KNOWN_USERS.add(chat_id)
    if not await enforce_join_or_prompt(chat_id, context, user_id=update.effective_user.id):
        return
    try:
        data = await api_call("GET", "/publicapi/getupdate")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")
        return
    rows = data.get("rows", [])[:15]
    if not rows:
        await update.message.reply_text("No codes received yet.")
        return
    lines = [f"{r['number']} ({r['sender']}): {r['message']}" for r in rows]
    await update.message.reply_text("\n".join(lines))

async def live_feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    KNOWN_USERS.add(update.effective_chat.id)

    if not services:
        text = "No verified ranges saved yet."
    else:
        lines = []
        for name, countries in services.items():
            if not countries:
                continue
            lines.append(f"*{name}*")
            for country, rng in countries.items():
                flag = flag_emoji(country.split(" (")[0])
                lines.append(f"  • {country} {flag} — `{rng}`".rstrip())
        text = "📡 *Known working ranges:*\n\n" + "\n".join(lines) if lines else "No verified ranges saved yet."

    text += "\n\n🔴 Live incoming codes (masked) are posted in real time in our group."
    buttons = InlineKeyboardMarkup([[InlineKeyboardButton("Open Group", url=GROUP_LINK)]]) if GROUP_LINK else None
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=buttons)

HELP_TEXT = (
    "ℹ️✨ *HOW IT WORKS* ✨\n"
    "━━━━━━━━━━━━━━━━━━\n\n"
    "📱 *Get Number* — pick a service & country to allocate a number\n"
    "📡 *Browse Ranges* — see raw ranges by sender id\n"
    "📨 *My Codes* — view last received codes\n"
    "💰 *My Balance & Withdraw* — check earnings & withdraw\n"
    "💳 *Payment Details* — set/update your withdraw account details\n\n"
    " You can cancel any action anytime by tapping *❌ Cancel*."
)

# ── Admin Panel ──────────────────────────────────────────────────────────
def admin_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="adm:stats")],
        [InlineKeyboardButton("🧩 Manage Services", callback_data="adm:services")],
        [InlineKeyboardButton(f"🔢 Max Numbers: {CONFIG.get('max_per_user', 1)}", callback_data="adm:setlimit")],
        [InlineKeyboardButton(f"💵 SMS Rate: ${CONFIG.get('sms_rate', 0.05)}", callback_data="adm:setrate")],
        [InlineKeyboardButton(f"📉 Min Withdraw: ${CONFIG.get('min_withdraw', 1.0)}", callback_data="adm:setminwithdraw")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="adm:broadcast")],
        [InlineKeyboardButton("⬅️ Close", callback_data="adm:close")],
    ])

async def show_admin_sender_list(query, service):
    try:
        data = await api_call("GET", "/publicapi/liveaccess")
    except Exception as e:
        await query.edit_message_text(f"⚠️ Error: {e}", reply_markup=services_menu_kb())
        return
    rows = data.get("rows", [])
    manual_btn = [InlineKeyboardButton("✍️ Enter Range Manually", callback_data=f"adm:acrmanual:{service}")]
    if not rows:
        buttons = [manual_btn, [InlineKeyboardButton("⬅️ Back", callback_data="adm:services")]]
        await query.edit_message_text(
            "No senders delivering right now, but you can still add a range you already know:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return
    buttons = [
        [InlineKeyboardButton(f"{r['sender']} ({len(r['ranges'])})", callback_data=f"adm:acrsender:{service}:{r['sender']}")]
        for r in rows[:30]
    ]
    buttons.append(manual_btn)
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:services")])
    await query.edit_message_text(
        f"📡 Pick a sender for *{service}*:", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_admin_ranges_for_sender(query, service, sender):
    try:
        data = await api_call("GET", "/publicapi/liveaccess", params={"sender": sender})
    except Exception as e:
        await query.edit_message_text(f"⚠️ Error: {e}", reply_markup=services_menu_kb())
        return
    rows = data.get("rows", [])
    ranges = rows[0]["ranges"] if rows else []
    if not ranges:
        await query.edit_message_text(f"No ranges found for {sender}.", reply_markup=services_menu_kb())
        return
    buttons = [
        [InlineKeyboardButton(f"{rng} → {guess_country_from_range(rng) or '?'}", callback_data=f"adm:acrrange:{service}:{rng}")]
        for rng in ranges
    ]
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"adm:svcpick:{service}")])
    await query.edit_message_text(
        f"📡 *{sender}* — tap a range to auto-add it to *{service}*:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )

def _range_already_known(rng):
    for countries in services.values():
        if rng in countries.values():
            return True
    return False

# ── Central Updates Poller ───────────────────────────────────────────────
async def central_updates_poller(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await api_call("GET", "/publicapi/getupdate")
    except Exception as e:
        logger.info("central_updates_poller: getupdate call failed (%s) — will retry next cycle.", e)
        data = None

    rows = data.get("rows", []) if data else []
    first_run = not SEEN_FEED_KEYS

    number_index = {}
    for chat_id, batch in ACTIVE_BATCH.items():
        for number, info in batch["numbers"].items():
            if info["status"] == "waiting":
                number_index[number] = chat_id

    changed_chats = set()

    for r in reversed(rows):
        key = (r["number"], r["message"], r["at_ms"])
        if key in SEEN_FEED_KEYS:
            continue
        SEEN_FEED_KEYS.add(key)

        chat_id = number_index.get(r["number"])
        delivered = False
        if chat_id is not None:
            batch = ACTIVE_BATCH.get(chat_id)
            info = batch["numbers"].get(r["number"]) if batch else None
            if info and info["status"] == "waiting":
                code = extract_code(r["message"])
                info["status"] = "delivered"
                info["code"] = code
                delivered = True
                changed_chats.add(chat_id)

                sms_rate = CONFIG.get("sms_rate", 0.05)
                new_bal = update_user_balance(chat_id, sms_rate)

                rng, label = batch["rng"], batch.get("label")
                again_data = f"getnum:{rng}|{label}" if label else f"getnum:{rng}"
                notify_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Get Same Country New Number", callback_data=again_data)
                ]])

                safe_message = md_escape(r["message"])
                notify_text = (
                    f"🎉 *Code Received & Balance Credited!* 💰\n\n"
                    f"📞 *Number:*\n`{r['number']}`\n\n"
                    f"💬 *Message:*\n{safe_message}\n\n"
                    f"🔑 *Code:*\n```\n{code or '—'}\n```\n"
                    f"💵 আপনার অ্যাকাউন্টে **+${sms_rate}** যোগ করা হয়েছে। বর্তমান ব্যালেন্স: **${new_bal}**"
                )
                try:
                    await context.bot.send_message(
                        chat_id, notify_text, parse_mode=ParseMode.MARKDOWN, reply_markup=notify_kb
                    )
                except Exception as e:
                    logger.error("Failed to send code-received notification to %s: %s", chat_id, e)

                group_text = (
                    "🎉 *Code Delivered!* 🎉\n\n"
                    f"📡 Sender: *{md_escape(r['sender'])}*\n"
                    f"📞 Number: `{mask_number_middle(r['number'])}`\n"
                )
                if code:
                    group_text += f"🔑 Code: `{code}`\n"
                group_text += f"💬 {safe_message}"
                await post_to_group(context, group_text)

        if not delivered:
            if first_run:
                continue
            code = extract_code(r["message"])
            masked = mask_code_in_message(r["message"], code)
            service = detect_service(r["message"]) or "Unknown"
            country = r.get("country") or "Unknown"
            flag = flag_emoji(country)
            known_range = find_known_range_for_country(country)

            lines = [
                "🟢 *NEW ACTIVE RANGE* ✅",
                "",
                f"⚙️ Service: *{md_escape(service)}*",
                f"🌍 Country: *{md_escape(country)}* {flag}".strip(),
            ]
            if known_range:
                lines.append(f"📱 Range: `{known_range}`")
            lines.append(f"✉️ Full SMS:\n{md_escape(masked)}")
            if GROUP_LINK:
                lines.append(f"\n🔍 [Join / Number Bot]({GROUP_LINK})")
            await post_to_group(context, "\n".join(lines))

    if len(SEEN_FEED_KEYS) > 5000:
        SEEN_FEED_KEYS.clear()

    now_ms = int(time.time() * 1000)
    for chat_id, batch in ACTIVE_BATCH.items():
        for number, info in batch["numbers"].items():
            if info["status"] == "waiting" and now_ms > info["expires_ms"]:
                info["status"] = "expired"
                changed_chats.add(chat_id)

    for chat_id in changed_chats:
        await _render_batch(chat_id, context)
        batch = ACTIVE_BATCH.get(chat_id)
        if batch and all(info["status"] != "waiting" for info in batch["numbers"].values()):
            ACTIVE_BATCH.pop(chat_id, None)

STALE_RANGE_MS = 30 * 60 * 1000

async def auto_poll_liveaccess(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await api_call("GET", "/publicapi/liveaccess")
    except Exception as e:
        logger.info("auto_poll_liveaccess: liveaccess call failed (%s) — retry next cycle.", e)
        return

    rows = data.get("rows", []) if data else []
    newly_added = []
    services.setdefault(AUTO_SERVICE_NAME, {})
    hits = CONFIG.setdefault("range_hits", {})
    last_seen = CONFIG.setdefault("range_last_seen", {})
    now_ms = int(time.time() * 1000)

    for row in rows:
        for rng in row.get("ranges", []):
            hits[rng] = hits.get(rng, 0) + 1
            last_seen[rng] = now_ms
            if _range_already_known(rng):
                continue
            country = guess_country_from_range(rng) or rng
            base_country = country
            n = 2
            while country in services[AUTO_SERVICE_NAME]:
                country = f"{base_country} ({n})"
                n += 1
            services[AUTO_SERVICE_NAME][country] = rng
            newly_added.append(f"{country} → {rng}")

    pruned = []
    auto_countries = services.get(AUTO_SERVICE_NAME, {})
    for country, rng in list(auto_countries.items()):
        if now_ms - last_seen.get(rng, 0) > STALE_RANGE_MS:
            auto_countries.pop(country, None)
            pruned.append(f"{country} ({rng})")

    save_config()
    if newly_added or pruned:
        save_services()
    if newly_added:
        text = "🟢 *NEW RANGE(S) DETECTED* 🔎\n\n" + "\n".join(f"✅ {line}" for line in newly_added)
        await post_to_group(context, text)

def services_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Service", callback_data="adm:svc_add")],
        [InlineKeyboardButton("🌍 Add Country to Service", callback_data="adm:svc_addcountry")],
        [InlineKeyboardButton("📋 List Services", callback_data="adm:svc_list")],
        [InlineKeyboardButton("🗑 Remove Service", callback_data="adm:svc_remove")],
        [InlineKeyboardButton("🗑 Remove Country", callback_data="adm:svc_remcountry")],
        [InlineKeyboardButton("⬅️ Back", callback_data="adm:home")],
    ])

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Not authorized.")
        return
    await update.message.reply_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())

async def admin_callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.answer("Not authorized.", show_alert=True)
        return
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "adm:home":
        ADMIN_STATE.pop(chat_id, None)
        await query.edit_message_text("🛠 *Admin Panel*", parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())

    elif data == "adm:close":
        ADMIN_STATE.pop(chat_id, None)
        await query.edit_message_text("Admin panel closed.")

    elif data == "adm:stats":
        load_users_data()
        total_money = sum(u.get("balance", 0) if isinstance(u, dict) else u for u in USER_DATA.values())
        text = (
            "📊 *Stats*\n\n"
            f"👥 Known users: {len(KNOWN_USERS)}\n"
            f"💰 Total User Balances: ${round(total_money, 2)}\n"
            f"⌛ Active waits: {sum(len(b['numbers']) for b in ACTIVE_BATCH.values())}\n"
            f"📱 Numbers allocated this run: {NUMBERS_ALLOCATED}\n"
            f"🧩 Services configured: {len(services)}\n"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu_kb())

    elif data == "adm:broadcast":
        ADMIN_STATE[chat_id] = ("broadcast", None)
        await query.edit_message_text("📢 Send the message to broadcast to all known users.\nSend /cancel to abort.")

    elif data == "adm:setlimit":
        ADMIN_STATE[chat_id] = ("set_limit", None)
        await query.edit_message_text(f"🔢 Send new number of numbers per request (Current: {CONFIG.get('max_per_user', 1)}):")

    elif data == "adm:setrate":
        ADMIN_STATE[chat_id] = ("set_rate", None)
        await query.edit_message_text(f"💵 বর্তমান প্রতি SMS রেট: ${CONFIG.get('sms_rate', 0.05)}\n\nনতুন রেট লিখে পাঠান (যেমন: 0.10):")

    elif data == "adm:setminwithdraw":
        ADMIN_STATE[chat_id] = ("set_min_withdraw", None)
        await query.edit_message_text(f"📉 বর্তমান মিনিমাম উইথড্র সীমা: ${CONFIG.get('min_withdraw', 1.0)}\n\nনতুন সীমা লিখে পাঠান (যেমন: 2.0 বা 5.0):")

    elif data == "adm:services":
        await query.edit_message_text("🧩 *Manage Services*", parse_mode=ParseMode.MARKDOWN, reply_markup=services_menu_kb())

    elif data == "adm:svc_add":
        ADMIN_STATE[chat_id] = ("add_service", None)
        await query.edit_message_text("➕ Send new service name (e.g. Facebook):")

    elif data == "adm:svc_addcountry":
        if not services:
            await query.edit_message_text("No services yet — add one first.", reply_markup=services_menu_kb())
            return
        buttons = [[InlineKeyboardButton(name, callback_data=f"adm:svcpick:{name}")] for name in services.keys()]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:services")])
        await query.edit_message_text("Pick service to add country:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm:svcpick:"):
        service = data.split(":", 2)[2]
        await show_admin_sender_list(query, service)

    elif data.startswith("adm:acrsender:"):
        _, _, service, sender = data.split(":", 3)
        await show_admin_ranges_for_sender(query, service, sender)

    elif data.startswith("adm:acrmanual:"):
        service = data.split(":", 2)[2]
        ADMIN_STATE[chat_id] = ("add_country_manual", service)
        await query.edit_message_text(f"✍️ Send country & range for *{service}* (e.g. `Ivory Coast 22501XXX`):", parse_mode=ParseMode.MARKDOWN)

    elif data.startswith("adm:acrrange:"):
        _, _, service, rng = data.split(":", 3)
        if service not in services:
            await query.edit_message_text("That service no longer exists.", reply_markup=services_menu_kb())
            return
        country = guess_country_from_range(rng) or rng
        base_country = country
        n = 2
        while country in services[service] and services[service][country] != rng:
            country = f"{base_country} ({n})"
            n += 1
        services[service][country] = rng
        save_services()
        await query.edit_message_text(f"✅ Auto-added *{country}* → `{rng}` under *{service}*.", parse_mode=ParseMode.MARKDOWN, reply_markup=services_menu_kb())

    elif data == "adm:svc_list":
        if not services:
            text = "No services configured yet."
        else:
            lines = []
            for name, countries in services.items():
                lines.append(f"*{name}*")
                if countries:
                    for country, rng in countries.items():
                        lines.append(f"  • {country} — `{rng}`")
                else:
                    lines.append("  _(no countries yet)_")
            text = "📋 *Services*\n\n" + "\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=services_menu_kb())

    elif data == "adm:svc_remove":
        if not services:
            await query.edit_message_text("No services to remove.", reply_markup=services_menu_kb())
            return
        buttons = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"adm:svcdel:{name}")] for name in services.keys()]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:services")])
        await query.edit_message_text("Pick service to remove:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm:svcdel:"):
        service = data.split(":", 2)[2]
        services.pop(service, None)
        save_services()
        await query.edit_message_text(f"🗑 Removed *{service}*.", parse_mode=ParseMode.MARKDOWN, reply_markup=services_menu_kb())

    elif data == "adm:svc_remcountry":
        if not services:
            await query.edit_message_text("No services yet.", reply_markup=services_menu_kb())
            return
        buttons = [[InlineKeyboardButton(name, callback_data=f"adm:remcountrysvc:{name}")] for name in services.keys()]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:services")])
        await query.edit_message_text("Pick service to remove country:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm:remcountrysvc:"):
        service = data.split(":", 2)[2]
        countries = services.get(service, {})
        if not countries:
            await query.edit_message_text(f"No countries under {service}.", reply_markup=services_menu_kb())
            return
        buttons = [
            [InlineKeyboardButton(f"🗑 {country}", callback_data=f"adm:remcountry:{service}:{country}")]
            for country in countries.keys()
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="adm:svc_remcountry")])
        await query.edit_message_text(f"Pick country to remove from *{service}*:", parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("adm:remcountry:"):
        _, _, service, country = data.split(":", 3)
        services.get(service, {}).pop(country, None)
        save_services()
        await query.edit_message_text(f"🗑 Removed *{country}* from *{service}*.", parse_mode=ParseMode.MARKDOWN, reply_markup=services_menu_kb())

async def admin_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_STATE:
        return False

    action, extra = ADMIN_STATE.pop(chat_id)
    text = update.message.text.strip()

    if text == "❌ Cancel":
        await update.message.reply_text("🛑 অ্যাডমিন অ্যাকশন বাতিল করা হয়েছে।", reply_markup=main_kb(update.effective_user.id))
        return True

    if action == "broadcast":
        sent, failed = 0, 0
        for uid in list(KNOWN_USERS):
            try:
                await context.bot.send_message(uid, f"📢 {text}")
                sent += 1
            except Exception:
                failed += 1
        await update.message.reply_text(f"✅ Broadcast sent to {sent} user(s). Failed: {failed}.")

    elif action == "add_service":
        if text in services:
            await update.message.reply_text(f"'{text}' already exists.")
        else:
            services[text] = {}
            save_services()
            await update.message.reply_text(f"✅ Service '{text}' added.")

    elif action == "set_limit":
        if not text.isdigit() or int(text) < 1:
            await update.message.reply_text("Send a whole number of 1 or more.")
            return True
        CONFIG["max_per_user"] = int(text)
        save_config()
        await update.message.reply_text(f"✅ Per-user limit set to {text}.")

    elif action == "set_rate":
        try:
            rate = float(text)
            if rate < 0:
                raise ValueError()
            CONFIG["sms_rate"] = rate
            save_config()
            await update.message.reply_text(f"✅ সফলভাবে প্রতি SMS রেট **${rate}** সেট করা হয়েছে।", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন (যেমন: 0.05)")
            return True

    elif action == "set_min_withdraw":
        try:
            min_w = float(text)
            if min_w < 0:
                raise ValueError()
            CONFIG["min_withdraw"] = min_w
            save_config()
            await update.message.reply_text(f"✅ সফলভাবে মিনিমাম উইথড্র সীমা **${min_w}** সেট করা হয়েছে।", parse_mode=ParseMode.MARKDOWN)
        except ValueError:
            await update.message.reply_text("❌ সঠিক সংখ্যা লিখুন (যেমন: 1.0)")
            return True

    elif action == "add_country_manual":
        service = extra
        if service not in services:
            await update.message.reply_text("That service no longer exists.")
            return True
        if " " not in text:
            await update.message.reply_text("Format: `Country Name RANGECODE`", parse_mode=ParseMode.MARKDOWN)
            return True
        country, rng = text.rsplit(" ", 1)
        services[service][country.strip()] = rng.strip()
        save_services()
        await update.message.reply_text(f"✅ Added {country.strip()} → `{rng.strip()}` under {service}.", parse_mode=ParseMode.MARKDOWN)

    return True

# ── Bottom Keyboard Router ───────────────────────────────────────────────
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    KNOWN_USERS.add(chat_id)
    text = update.message.text.strip()

    # Universal Cancel Option
    if text == "❌ Cancel":
        USER_STATE.pop(chat_id, None)
        ADMIN_STATE.pop(chat_id, None)
        await update.message.reply_text("🛑 প্রসেস বাতিল করা হয়েছে।", reply_markup=main_kb(update.effective_user.id))
        return

    if await admin_text_handler(update, context):
        return

    if not await enforce_join_or_prompt(chat_id, context, user_id=update.effective_user.id):
        return

    # Handle Payment Info Setting State
    if USER_STATE.get(chat_id) == "awaiting_payment_details":
        USER_STATE.pop(chat_id, None)
        set_user_payment_info(chat_id, text)
        await update.message.reply_text(f"✅ আপনার পেমেন্ট মেথড সফলভাবে সেভ করা হয়েছে:\n\n`{text}`", parse_mode=ParseMode.MARKDOWN, reply_markup=main_kb(update.effective_user.id))
        return

    # Handle Withdraw Amount State
    if USER_STATE.get(chat_id) == "awaiting_withdraw_amount":
        try:
            amount = float(text)
            user_info = get_user_info(chat_id)
            min_withdraw = CONFIG.get("min_withdraw", 1.0)

            if amount <= 0:
                await update.message.reply_text("❌ পরিমাণ অবশ্যই শূন্যের বেশি হতে হবে।")
            elif amount < min_withdraw:
                await update.message.reply_text(f"❌ মিনিমাম উইথড্র অ্যামাউন্ট **${min_withdraw}**। আপনি এর কম তুলবেন না।", parse_mode=ParseMode.MARKDOWN)
            elif amount > user_info["balance"]:
                await update.message.reply_text(f"❌ পর্যাপ্ত ব্যালেন্স নেই! আপনার বর্তমান ব্যালেন্স: **${user_info['balance']}**", parse_mode=ParseMode.MARKDOWN)
            else:
                USER_STATE.pop(chat_id, None)
                update_user_balance(chat_id, -amount)
                
                await update.message.reply_text(
                    f"✅ **${amount}** উইথড্র রিকোয়েস্ট জমা দেওয়া হয়েছে!\n\n"
                    f"💳 পেমেন্ট অ্যাকাউন্ট: `{user_info['payment_info']}`\n"
                    f"অ্যাডমিন শীঘ্রই চেক করে আপনার পেমেন্ট পাঠিয়ে দেবেন।", 
                    parse_mode=ParseMode.MARKDOWN, 
                    reply_markup=main_kb(update.effective_user.id)
                )
                
                # Notify Admins with full details
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(
                            admin_id,
                            f"🔔 **নতুন উইথড্র রিকোয়েস্ট!**\n\n"
                            f"👤 ইউজার আইডি: `{chat_id}`\n"
                            f"💵 পরিমাণ: `${amount}`\n"
                            f"💳 পেমেন্ট বিবরণ: `{user_info['payment_info']}`",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception:
                        pass
        except ValueError:
            await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যা লিখুন (যেমন: 2.5)।")
        return

    if USER_STATE.get(chat_id) == "awaiting_custom_range":
        USER_STATE.pop(chat_id, None)
        await start_getnum_flow(chat_id, text, context)
        return

    # Menu Commands
    if text == "💰 My Balance":
        info = get_user_info(chat_id)
        await update.message.reply_text(
            f"💳 **আপনার অ্যাকাউন্ট বিবরণী:**\n\n"
            f"💵 বর্তমান ব্যালেন্স: **${info['balance']}**\n"
            f"💳 পেমেন্ট অ্যাকাউন্ট: `{info['payment_info']}`\n\n"
            f"_পেমেন্ট মেথড পরিবর্তন করতে '💳 Payment Details' বাটনে চাপ দিন।_", 
            parse_mode=ParseMode.MARKDOWN
        )

    elif text == "💳 Payment Details":
        USER_STATE[chat_id] = "awaiting_payment_details"
        await update.message.reply_text(
            "✍️ আপনার টাকা নেওয়ার অ্যাকাউন্ট বিবরণী লিখে পাঠান:\n\n"
            "উদাহরণ:\n"
            "• `Bkash Personal: 01700000000`\n"
            "• `Nagad Personal: 01800000000`\n"
            "• `Binance Pay ID: 12345678`\n\n"
            "বাতিল করতে চাইলে নিচে **❌ Cancel** চাপুন।",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=CANCEL_KB
        )

    elif text == "💸 Withdraw":
        info = get_user_info(chat_id)
        min_w = CONFIG.get("min_withdraw", 1.0)

        if info["payment_info"] == "Not Set":
            await update.message.reply_text(
                "⚠️ আপনার কোনো পেমেন্ট মেথড সেভ করা নেই!\n\n"
                "উইথড্র করার আগে **💳 Payment Details** বাটনে চাপ দিয়ে আপনার বিকাশ/নগদ/বাইনান্স নাম্বার সেভ করে নিন।"
            )
        elif info["balance"] < min_w:
            await update.message.reply_text(
                f"❌ উইথড্র করার মতো পর্যাপ্ত ব্যালেন্স নেই।\n\n"
                f"💵 আপনার ব্যালেন্স: **${info['balance']}**\n"
                f"📉 মিনিমাম উইথড্র লিমিট: **${min_w}**", 
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            USER_STATE[chat_id] = "awaiting_withdraw_amount"
            await update.message.reply_text(
                f"💵 বর্তমান ব্যালেন্স: **${info['balance']}**\n"
                f"📉 মিনিমাম উইথড্র: **${min_w}**\n"
                f"💳 পেমেন্ট দেওয়া হবে: `{info['payment_info']}`\n\n"
                f"আপনি কত ডলার উইথড্র করতে চান? পরিমাণ লিখে পাঠান (যেমন: {min_w}):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=CANCEL_KB
            )

    elif text == "📱 Get Number":
        await show_services(update, user_id=update.effective_user.id)
    elif text == "📡 Browse Ranges":
        await show_sender_list(update)
    elif text == "📨 My Codes":
        await codes_command(update, context)
    elif text == "🔴 Live Feed":
        await live_feed_command(update, context)
    elif text == "ℹ️ Help":
        await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)
    elif text == "🛠 Admin":
        await admin_command(update, context)

# ── Wiring ───────────────────────────────────────────────────────────────
async def global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception:\n%s", "".join(traceback.format_exception(None, context.error, context.error.__traceback__)))
    chat_id = None
    try:
        if isinstance(update, Update) and update.effective_chat:
            chat_id = update.effective_chat.id
    except Exception:
        pass

    if chat_id is not None:
        try:
            await context.bot.send_message(chat_id, "⚠️ Something went wrong handling that. Please try again.")
        except Exception:
            pass

def main():
    if not BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first.")
    if not ADMIN_IDS:
        print("⚠️ No ADMIN_IDS set.")

    load_services()
    load_config()
    load_users_data()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getnum", getnum_command))
    app.add_handler(CommandHandler("codes", codes_command))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("admin", admin_command))

    app.add_handler(CallbackQueryHandler(admin_callback_router, pattern=r"^adm:"))
    app.add_handler(CallbackQueryHandler(service_callback_router, pattern=r"^(svc|customrange)"))
    app.add_handler(CallbackQueryHandler(raw_callback_router, pattern=r"^(sender:|getnum:|delbatch|backtosenders|checkjoin)"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(global_error_handler)

    if app.job_queue is not None:
        app.job_queue.run_repeating(auto_poll_liveaccess, interval=AUTO_POLL_SECONDS, first=15)
        app.job_queue.run_repeating(central_updates_poller, interval=FEED_POLL_SECONDS, first=10)
    else:
        print("⚠️ JobQueue not available.")

    start_health_server()
    print("Bot running with full Withdraw, Payment Method & Min Limits...")
    app.run_polling()

if __name__ == "__main__":
    main()

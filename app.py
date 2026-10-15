import difflib
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import sqlite3
import zlib
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="SmartCart | Smart Shopping Assistant",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

CATALOGUE_FILE = Path(__file__).with_name("smartcart_catalogue.csv")
PAGE_SIZE = 20
COLUMNS = 4
AVAILABLE = ("in stock", "low stock")

# ---------- Fallback demo catalogue (used only if smartcart_catalogue.csv is missing) ----------
DEFAULT_PRODUCTS = [
    {"product_id":"MILK001","product_name":"Taaza Toned Milk","brand":"Amul","category":"Dairy and Eggs","pack_size":"1 L","price":68.00,"stock_status":"In stock","aisle":"B1"},
    {"product_id":"MILK002","product_name":"Toned Milk","brand":"Milma","category":"Dairy and Eggs","pack_size":"1 L","price":56.00,"stock_status":"In stock","aisle":"B1"},
    {"product_id":"BISC001","product_name":"Marie Gold Biscuits","brand":"Britannia","category":"Snacks and Packaged Foods","pack_size":"250 g","price":38.00,"stock_status":"In stock","aisle":"E2"},
    {"product_id":"BISC002","product_name":"Marie Biscuits","brand":"Parle","category":"Snacks and Packaged Foods","pack_size":"250 g","price":32.00,"stock_status":"In stock","aisle":"E2"},
    {"product_id":"RICE001","product_name":"Raw Rice","brand":"Store Brand","category":"Rice, Flour and Pulses","pack_size":"5 kg","price":310.00,"stock_status":"In stock","aisle":"C1"},
]

REQUIRED_COLUMNS = [
    "product_id", "product_name", "brand", "category",
    "pack_size", "price", "stock_status", "aisle",
]


# ---------- Offers ----------
# Offers live in an optional "offer" column of the catalogue CSV: "BOGO" or "10% off".
# If the column is missing, a few demo offers are assigned so the feature can be seen.
BOGO_TEXT = "Buy 1 Get 1 Free"


def demo_offer(pid):
    h = zlib.crc32(str(pid).encode()) % 100
    if h < 8:
        return "BOGO"
    if h < 20:
        return "10% off"
    if h < 28:
        return "20% off"
    return ""


def parse_offer(text):
    t = str(text or "").strip().lower()
    if not t or t == "nan":
        return None
    if t in ("bogo", "b1g1", "buy 1 get 1", "buy one get one", "buy 1 get 1 free", "buy one get one free"):
        return ("bogo", 0.0)
    m = re.match(r"(\d+(?:\.\d+)?)\s*%", t)
    if m and 0 < float(m.group(1)) < 100:
        return ("pct", float(m.group(1)))
    return None


def offer_label(text):
    o = parse_offer(text)
    if o is None:
        return ""
    return BOGO_TEXT if o[0] == "bogo" else f"{o[1]:g}% off"


# ---------- Snack and sweet calories (estimates) ----------
# (keyword, kcal per 100 g or 100 ml). Only snacks, sweets and sugary drinks are tracked.
JUNK_KCAL = [
    ("biscuit", 450), ("cookie", 480), ("wafer", 520), ("chip", 540), ("namkeen", 500), ("bhujia", 500),
    ("mixture", 480), ("popcorn", 450), ("nacho", 480), ("rusk", 410), ("cake", 380), ("chocolate", 540),
    ("toffee", 400), ("candy", 390), ("noodle", 450), ("ice cream", 210), ("cola", 42), ("soda", 40),
    ("soft drink", 42), ("energy drink", 45),
]
JUNK_EXCLUDE = ("chocolate drink", "drinking chocolate", "cocoa", "cake mix", "baking soda", "club soda", "soda water")


def junk_info(name):
    low = str(name).lower()
    if any(x in low for x in JUNK_EXCLUDE):
        return False, 0.0
    for kw, per100 in JUNK_KCAL:
        if re.search(rf"(?<!\w){re.escape(kw)}s?(?!\w)", low):
            return True, float(per100)
    return False, 0.0


def pack_grams(pack):
    m = re.search(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*(kg|gms|gm|g|ltr|l|ml)\b", str(pack).lower())
    if not m:
        return 100.0
    mult = int(m.group(1)) if m.group(1) else 1
    n = float(m.group(2)) * mult
    return n * 1000 if m.group(3) in ("kg", "l", "ltr") else n


def prepare_catalogue(df):
    df = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    for col in ["product_id", "product_name", "brand", "category", "pack_size", "stock_status", "aisle"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["stock_status"] = df["stock_status"].replace("", "In stock")
    df["aisle"] = df["aisle"].replace("", "-")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])
    df = df[(df["price"] >= 0) & (df["product_id"] != "") & (df["product_name"] != "")]
    df = df.drop_duplicates(subset=["product_id"], keep="first")
    if "offer" in df.columns:
        df["offer"] = df["offer"].fillna("").astype(str).str.strip()
    else:
        df["offer"] = [demo_offer(pid) for pid in df["product_id"]]
    flags = [junk_info(n) for n in df["product_name"]]
    df["junk"] = [f[0] for f in flags]
    est = pd.Series([f[1] * pack_grams(ps) / 100 for f, ps in zip(flags, df["pack_size"])], index=df.index, dtype=float)
    if "calories" in df.columns:
        given = pd.to_numeric(df["calories"], errors="coerce")
        df["kcal"] = given.where(given > 0, est)
    else:
        df["kcal"] = est
    df["unit_price"] = df.apply(
        lambda r: f"₹{r['price']:.2f} / {r['pack_size']}" if r["pack_size"] else f"₹{r['price']:.2f} / pack",
        axis=1,
    )
    return df.reset_index(drop=True)


def load_default_catalogue():
    st.session_state.catalogue_note = ""
    problems = []
    others = sorted(q for q in CATALOGUE_FILE.parent.glob("*.csv") if q != CATALOGUE_FILE)
    for path in [CATALOGUE_FILE] + others:
        if not path.exists():
            problems.append(f"{path.name} was not found")
            continue
        try:
            df = prepare_catalogue(pd.read_csv(path))
        except Exception as exc:
            problems.append(f"{path.name} could not be read ({exc})")
            continue
        if df.empty:
            problems.append(f"{path.name} has no usable rows")
            continue
        if path != CATALOGUE_FILE:
            st.session_state.catalogue_note = f"{CATALOGUE_FILE.name} was not found, so {path.name} is being used instead."
            st.session_state.catalogue_note_n = len(df)
        return df
    df = prepare_catalogue(pd.DataFrame(DEFAULT_PRODUCTS))
    st.session_state.catalogue_note = ("Showing the small demo list because " + "; ".join(problems) +
                                       f". Put {CATALOGUE_FILE.name} in the same GitHub folder as app.py.")
    st.session_state.catalogue_note_n = len(df)
    return df


def rupees(amount):
    return f"₹{amount:,.2f}"


def price_label(amount):
    amount = float(amount)
    return f"₹{amount:,.0f}" if amount == int(amount) else f"₹{amount:,.2f}"


# ---------- State ----------
if "catalogue" not in st.session_state:
    st.session_state.catalogue = load_default_catalogue()
if "cart" not in st.session_state:
    st.session_state.cart = {}
if "budget" not in st.session_state:
    st.session_state.budget = 2000.0
if "page" not in st.session_state:
    st.session_state.page = 1
for _k, _v in {
    "user": None, "family": None, "scope": "me", "scope_choice": "Personal",
    "wallet": 0.0, "cal_limit": 5000.0, "orders": [], "txns": [], "chat": [], "last_saved": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


def product_by_id(pid):
    cat = st.session_state.catalogue
    rows = cat[cat["product_id"] == pid]
    return None if rows.empty else rows.iloc[0]


def line_cost(p, qty):
    """Cost of qty units of product p after its offer (BOGO or percentage off)."""
    price, qty = float(p["price"]), int(qty)
    o = parse_offer(p.get("offer", ""))
    if o is None:
        return price * qty
    if o[0] == "bogo":
        return price * math.ceil(qty / 2)
    return price * qty * (1 - o[1] / 100)


def cart_total():
    total = 0.0
    for pid, qty in st.session_state.cart.items():
        p = product_by_id(pid)
        if p is not None:
            total += line_cost(p, qty)
    return total


def cart_savings():
    saved = 0.0
    for pid, qty in st.session_state.cart.items():
        p = product_by_id(pid)
        if p is not None:
            saved += float(p["price"]) * int(qty) - line_cost(p, qty)
    return saved


def junk_breakdown():
    rows = []
    for pid, qty in st.session_state.cart.items():
        p = product_by_id(pid)
        if p is not None and bool(p["junk"]) and float(p["kcal"]) > 0:
            rows.append((p["product_name"], float(p["kcal"]) * int(qty)))
    rows.sort(key=lambda r: -r[1])
    return rows


def junk_kcal():
    return sum(k for _, k in junk_breakdown())


STOP_WORDS = {"and", "the", "with", "pack", "fresh", "powder", "small", "large", "whole", "regular"}


def name_words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2 and w not in STOP_WORDS}


def cheaper_alternatives(p, qty=1):
    cat = st.session_state.catalogue
    pool = cat[
        (cat["category"] == p["category"])
        & (cat["price"] < p["price"])
        & (cat["stock_status"].str.lower().isin(AVAILABLE))
    ]
    words = name_words(p["product_name"])
    pool = pool[pool["product_name"].apply(lambda n: bool(name_words(n) & words))]
    room = st.session_state.budget - cart_total()
    pool = pool[pool["price"] * qty <= room]
    return pool.sort_values("price").head(3)


def add_item(pid, qty=1):
    p = product_by_id(pid)
    if p is None:
        return False
    cur = st.session_state.cart.get(pid, 0)
    new_qty = min(cur + qty, 99)
    proposed = cart_total() - line_cost(p, cur) + line_cost(p, new_qty)
    budget = st.session_state.budget
    if proposed <= budget + 1e-9:
        before = junk_kcal()
        st.session_state.cart[pid] = new_qty
        st.session_state.last_added = pid
        after = junk_kcal()
        limit = float(st.session_state.get("cal_limit", 0) or 0)
        if limit > 0 and before < limit <= after:
            st.toast(f"Calorie limit reached: snacks and sweets now add up to about {after:,.0f} kcal. That is enough.", icon="🍟")
        elif limit > 0 and before < 0.8 * limit <= after:
            st.toast("You are close to your snack and sweet calorie limit.", icon="⚠️")
        return True
    alts = cheaper_alternatives(p, qty)
    st.session_state.notice = {
        "msg": f"Adding {p['product_name']} would make your bill {rupees(proposed)}, "
               f"which is {rupees(proposed - budget)} over your budget.",
        "alts": [
            f"{a['brand']} · {a['product_name']} ({a['pack_size']}): {rupees(float(a['price']))}, "
            f"saves {rupees(float(p['price']) - float(a['price']))} per item"
            for _, a in alts.iterrows()
        ],
    }
    return False


def change_qty(pid, delta):
    qty = st.session_state.cart.get(pid, 0) + delta
    if qty <= 0:
        st.session_state.cart.pop(pid, None)
    else:
        st.session_state.cart[pid] = min(qty, 99)


def add_by_code():
    code = st.session_state.get("code_input", "").strip().upper()
    st.session_state.code_input = ""
    if not code:
        return
    cat = st.session_state.catalogue
    match = cat[cat["product_id"].str.upper() == code]
    if match.empty:
        st.session_state.code_msg = ("err", f"No product has the code {code}.")
        return
    p = match.iloc[0]
    if str(p["stock_status"]).lower() not in AVAILABLE:
        st.session_state.code_msg = ("err", f"{p['product_name']} is not available right now.")
        return
    if add_item(p["product_id"], 1):
        st.session_state.code_msg = ("ok", f"Added {p['product_name']} ({p['pack_size']}).")


def reset_page():
    st.session_state.page = 1


# ---------- Smart suggestions ----------
# The first keyword that matches a product name decides its pairing list, so specific names come first.
PAIRINGS = {
    "peanut butter": ["bread", "honey", "oats", "banana"],
    "chocolate spread": ["bread", "croissant", "strawberry", "milk"],
    "bread": ["butter", "peanut butter", "chocolate spread", "mixed fruit jam", "cheese slices", "egg", "honey"],
    "pav": ["butter", "tomato ketchup", "onion", "potato"],
    "burger buns": ["cheese slices", "tomato ketchup", "mayonnaise", "onion"],
    "cookies": ["milk", "tea powder", "coffee powder"],
    "biscuits": ["tea powder", "milk", "coffee powder"],
    "butter": ["bread", "egg", "pav", "cheese slices"],
    "jam": ["bread", "butter", "cheese slices"],
    "instant noodles": ["egg", "tomato ketchup", "chilli sauce", "mixed vegetables"],
    "pasta": ["tomato", "cheese slices", "olive oil", "capsicum"],
    "macaroni": ["tomato", "cheese slices", "capsicum", "mayonnaise"],
    "cornflakes": ["milk", "honey", "banana"],
    "oats": ["milk", "honey", "banana", "almonds"],
    "egg": ["bread", "butter", "onion", "green chilli", "tomato"],
    "tea powder": ["milk", "sugar", "biscuits", "rusk"],
    "coffee": ["milk", "sugar", "biscuits", "cookies"],
    "paneer": ["capsicum", "butter", "fresh cream", "garam masala", "onion"],
    "idli rice": ["urad dal", "fresh coconut", "sambar powder", "curry leaves"],
    "toor dal": ["rice", "sambar powder", "ghee", "turmeric powder"],
    "atta": ["ghee", "toor dal", "paneer", "curd"],
    "rice": ["toor dal", "sambar powder", "coconut oil", "mango pickle", "papad", "curd"],
    "chicken": ["onion", "ginger", "garlic", "chicken masala", "curry leaves", "lemon"],
    "mutton": ["onion", "ginger", "garlic", "meat masala", "coconut oil"],
    "sardine": ["fish curry masala", "tamarind", "curry leaves", "small onion", "coconut oil"],
    "mackerel": ["fish curry masala", "tamarind", "curry leaves", "small onion", "coconut oil"],
    "seer fish": ["fish curry masala", "tamarind", "curry leaves", "small onion", "coconut oil"],
    "pomfret": ["fish curry masala", "lemon", "curry leaves", "coconut oil"],
    "tuna": ["fish curry masala", "lemon", "curry leaves", "coconut oil"],
    "prawns": ["onion", "garlic", "chilli powder", "coconut oil", "curry leaves"],
    "onion": ["tomato", "green chilli", "ginger", "garlic"],
    "tomato": ["onion", "green chilli", "coriander leaves", "ginger"],
    "notebook": ["pen", "pencil", "eraser", "sharpener", "ruler", "highlighter", "geometry box"],
    "pen": ["notebook", "pencil", "eraser", "correction pen", "highlighter"],
    "colour pencils": ["drawing book", "sharpener", "eraser", "crayons"],
    "pencil": ["eraser", "sharpener", "notebook", "ruler"],
    "crayons": ["drawing book", "colour pencils", "sketch pens", "sharpener"],
    "washing powder": ["bucket", "mug", "fabric softener", "detergent bar", "scrub pad"],
    "liquid detergent": ["fabric softener", "clothes hanger", "laundry basket", "bucket"],
    "bucket": ["mug", "washing powder", "scrub pad", "phenyl"],
    "dishwash": ["scrub pad", "cleaning brush", "kitchen towel"],
    "toothpaste": ["toothbrush", "mouthwash"],
    "toothbrush": ["toothpaste", "mouthwash"],
    "shampoo": ["conditioner", "hair oil", "body wash", "comb"],
    "soap": ["shampoo", "body wash", "handwash liquid"],
    "diapers": ["baby wipes", "baby powder", "baby lotion", "baby soap"],
    "razor": ["shaving foam", "deodorant"],
    "potato chips": ["cola", "lemon soda", "tomato ketchup"],
    "cola": ["potato chips", "banana chips", "popcorn"],
    "ice cream": ["ice cream cone", "chocolate bar"],
    "milk": ["tea powder", "coffee powder", "sugar", "cornflakes", "oats", "chocolate drink", "biscuits"],
    "sugar": ["tea powder", "milk", "coffee powder"],
}


def _word_pattern(kw):
    return re.compile(rf"(?<!\w){re.escape(kw)}s?(?!\w)")


def trigger_for(name):
    low = name.lower()
    for kw in PAIRINGS:
        if _word_pattern(kw).search(low):
            return kw
    return None


def find_product(kw, exclude):
    cat = st.session_state.catalogue
    pattern = _word_pattern(kw)
    best = None
    for row in cat.itertuples(index=False):
        if row.product_id in exclude or str(row.stock_status).lower() not in AVAILABLE:
            continue
        name = row.product_name.lower()
        if not pattern.search(name):
            continue
        tier = 0 if name in (kw, kw + "s") else (1 if name.startswith(kw) else 2)
        key = (tier, float(row.price))
        if best is None or key < best[0]:
            best = (key, row.product_id)
    return product_by_id(best[1]) if best else None


def rule_suggestions(pids, exclude):
    scores = {}
    for pid in pids:
        p = product_by_id(pid)
        if p is None:
            continue
        trig = trigger_for(p["product_name"])
        if not trig:
            continue
        for rank, kw in enumerate(PAIRINGS[trig]):
            match = find_product(kw, exclude)
            if match is None:
                continue
            entry = scores.setdefault(match["product_id"], {"score": 0, "with": []})
            entry["score"] += 10 - rank
            if p["product_name"] not in entry["with"]:
                entry["with"].append(p["product_name"])
    ranked = sorted(scores.items(), key=lambda kv: -kv[1]["score"])
    return [(product_by_id(i), "Goes well with " + ", ".join(e["with"][:2])) for i, e in ranked]


def get_api_key():
    key = None
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY")


@st.cache_data(show_spinner=False, ttl=3600)
def ai_pick(cart_names, catalogue_lines, limit, _api_key):
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key)
    prompt = (
        "You are a supermarket shopping assistant. The customer's cart has: "
        + ", ".join(cart_names)
        + f".\nFrom the catalogue below, choose up to {limit} products they are most likely to need "
        "together with these items, the way a helpful shop assistant would "
        "(for example bread goes with butter, peanut butter, chocolate spread and jam; "
        "a notebook goes with pens and pencils). Use only product ids from the catalogue and do not repeat cart items.\n"
        'Reply with a JSON array only, like [{"id": "VEG-001", "reason": "short reason, 8 words max"}].\n\n'
        "Catalogue (id|name|pack|price):\n" + "\n".join(catalogue_lines)
    )
    msg = client.messages.create(
        model="claude-haiku-5-5", max_tokens=700, messages=[{"role": "user", "content": prompt}]
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    found = re.search(r"\[.*\]", text, re.S)
    data = json.loads(found.group(0)) if found else []
    return [(str(d["id"]), str(d.get("reason", ""))[:80]) for d in data if isinstance(d, dict) and "id" in d]


def get_suggestions(pids, limit=4):
    exclude = set(st.session_state.cart) | set(pids)
    key = get_api_key()
    if key and st.session_state.get("use_ai"):
        try:
            cat = st.session_state.catalogue
            avail = cat[cat["stock_status"].str.lower().isin(AVAILABLE)]
            lines = tuple(
                f"{r.product_id}|{r.product_name}|{r.pack_size}|{r.price:g}"
                for r in avail.itertuples(index=False)
            )
            names = tuple(product_by_id(pid)["product_name"] for pid in pids if product_by_id(pid) is not None)
            out = []
            for pid, reason in ai_pick(names, lines, limit, key):
                p = product_by_id(pid)
                if p is not None and pid not in exclude and str(p["stock_status"]).lower() in AVAILABLE:
                    out.append((p, reason or "Suggested by AI"))
            if out:
                return out[:limit], "ai"
        except Exception:
            pass
    return rule_suggestions(pids, exclude)[:limit], "rules"


def render_card(p, prefix, note=None):
    pid = p["product_id"]
    status = str(p["stock_status"])
    available = status.lower() in AVAILABLE
    flag = ""
    if status.lower() == "low stock":
        flag = '<span class="low">Low stock</span>'
    elif not available:
        flag = f'<span class="out">{html.escape(status)}</span>'
    hue = zlib.crc32(str(p["category"]).encode()) % 360
    note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
    off = parse_offer(p.get("offer", ""))
    if off and off[0] == "pct":
        price_html = (f'<span class="price">{price_label(float(p["price"]) * (1 - off[1] / 100))}</span>'
                      f'<span class="was">{price_label(p["price"])}</span>')
    else:
        price_html = f'<span class="price">{price_label(p["price"])}</span>'
    offer_html = f'<span class="offer">{html.escape(offer_label(p.get("offer", "")))}</span>' if off else ""
    kcal_html = (f'<div class="kcal">🍟 about {float(p["kcal"]):,.0f} kcal per pack</div>'
                 if bool(p["junk"]) and float(p["kcal"]) > 0 else "")
    with st.container(border=True):
        st.markdown(
            f'<div class="tile" style="--h:{hue}">'
            f'<b>{html.escape(p["product_name"][:1].upper())}</b><span>{html.escape(p["aisle"])}</span></div>'
            f'{note_html}'
            f'<div class="pname">{html.escape(p["product_name"])}</div>'
            f'<div class="pmeta">{html.escape(p["brand"])} · {html.escape(p["pack_size"])}</div>'
            f'<div class="pcode">{html.escape(pid)}</div>'
            f'{kcal_html}'
            f'{price_html}{offer_html}{flag}',
            unsafe_allow_html=True,
        )
        qty = st.session_state.cart.get(pid, 0)
        if qty == 0:
            st.button("Add", key=f"{prefix}_add_{pid}", on_click=add_item, args=(pid, 1),
                      disabled=not available, use_container_width=True)
        else:
            a, b, c = st.columns(3)
            a.button("−", key=f"{prefix}_dec_{pid}", on_click=change_qty, args=(pid, -1), use_container_width=True)
            b.markdown(f'<div class="qty">{qty}</div>', unsafe_allow_html=True)
            c.button("+", key=f"{prefix}_inc_{pid}", on_click=add_item, args=(pid, 1), use_container_width=True)


def render_suggestions(pids, title, prefix, limit=4, show_reason=False):
    items, source = get_suggestions(pids, limit)
    if not items:
        return
    badge = "AI suggestions" if source == "ai" else "Smart suggestions"
    st.markdown(f'<div class="aisle">{html.escape(title)} · {badge}</div>', unsafe_allow_html=True)
    cols = st.columns(COLUMNS)
    for col, (p, reason) in zip(cols, items):
        with col:
            render_card(p, prefix, note=reason if show_reason else None)


# ---------- Accounts, wallet and saved progress ----------
# Progress is saved in a small SQLite file. On Streamlit Community Cloud the disk is wiped when the app
# restarts or redeploys, so use a hosted database (Supabase, Neon, Turso) for a real launch.
DB_FILE = Path(os.environ.get("SMARTCART_DB", str(Path(__file__).with_name("smartcart.db"))))
IST = timezone(timedelta(hours=5, minutes=30))


def _conn():
    con = sqlite3.connect(DB_FILE, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt BLOB, pw BLOB, family TEXT, data TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS families (code TEXT PRIMARY KEY, owner TEXT, data TEXT)")
    return con


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


def _now():
    return datetime.now(IST).strftime("%d %b %Y %H:%M")


def _msg(kind, text):
    st.session_state.auth_msg = (kind, text)


def db_user(username):
    with closing(_conn()) as con:
        return con.execute("SELECT username, salt, pw, family FROM users WHERE username=?", (username,)).fetchone()


def db_members(code):
    with closing(_conn()) as con:
        return [r[0] for r in con.execute("SELECT username FROM users WHERE family=? ORDER BY username", (code,))]


def db_load(scope):
    ss = st.session_state
    with closing(_conn()) as con:
        if scope == "family" and ss.family:
            row = con.execute("SELECT data FROM families WHERE code=?", (ss.family,)).fetchone()
        else:
            row = con.execute("SELECT data FROM users WHERE username=?", (ss.user,)).fetchone()
    try:
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:
        return {}


def db_save(scope, data):
    ss = st.session_state
    payload = json.dumps(data)
    with closing(_conn()) as con, con:
        if scope == "family" and ss.family:
            con.execute("UPDATE families SET data=? WHERE code=?", (payload, ss.family))
        else:
            con.execute("UPDATE users SET data=? WHERE username=?", (payload, ss.user))


def snapshot():
    ss = st.session_state
    return {
        "cart": {str(k): int(v) for k, v in ss.cart.items()},
        "budget": float(ss.budget),
        "wallet": round(float(ss.wallet), 2),
        "cal_limit": float(ss.cal_limit),
        "orders": ss.orders[-50:],
        "txns": ss.txns[-100:],
    }


def persist():
    if not st.session_state.user:
        return
    try:
        data = snapshot()
        blob = json.dumps(data, sort_keys=True)
        if blob != st.session_state.get("last_saved"):
            db_save(st.session_state.scope, data)
            st.session_state.last_saved = blob
    except Exception as exc:
        st.sidebar.warning(f"Could not save your progress: {exc}")


def load_scope(keep_guest_cart=False):
    ss = st.session_state
    data = db_load(ss.scope)
    guest_cart = dict(ss.cart) if keep_guest_cart else {}
    cart = {}
    for k, v in data.get("cart", {}).items():
        try:
            cart[str(k)] = max(1, min(int(v), 99))
        except (TypeError, ValueError):
            pass
    ss.cart = cart or guest_cart
    ss.budget = max(1.0, float(data.get("budget", ss.budget)))
    ss.wallet = float(data.get("wallet", 0.0))
    ss.cal_limit = max(100.0, float(data.get("cal_limit", 5000.0)))
    ss.orders = list(data.get("orders", []))
    ss.txns = list(data.get("txns", []))
    ss.last_saved = None


def do_login():
    ss = st.session_state
    u = ss.get("auth_user", "").strip().lower()
    pw = ss.get("auth_pw", "")
    row = db_user(u)
    if not row or not hmac.compare_digest(_hash(pw, row[1]), row[2]):
        return _msg("err", "Wrong username or password.")
    ss.user, ss.family, ss.scope, ss.scope_choice, ss.auth_pw = u, row[3], "me", "Personal", ""
    load_scope(keep_guest_cart=True)
    _msg("ok", f"Welcome, {u}. Your saved progress is loaded.")


def do_register():
    ss = st.session_state
    u = ss.get("auth_user", "").strip().lower()
    pw = ss.get("auth_pw", "")
    if not re.fullmatch(r"[a-z0-9_]{3,20}", u):
        return _msg("err", "Username must be 3 to 20 letters, numbers or underscores.")
    if len(pw) < 6:
        return _msg("err", "Password needs at least 6 characters.")
    salt = os.urandom(16)
    try:
        with closing(_conn()) as con, con:
            con.execute("INSERT INTO users (username, salt, pw, family, data) VALUES (?,?,?,?,?)",
                        (u, salt, _hash(pw, salt), None, "{}"))
    except sqlite3.IntegrityError:
        return _msg("err", "That username is taken.")
    do_login()


def do_logout():
    persist()
    ss = st.session_state
    ss.user, ss.family, ss.scope, ss.scope_choice = None, None, "me", "Personal"
    ss.cart, ss.budget, ss.wallet, ss.cal_limit = {}, 2000.0, 0.0, 5000.0
    ss.orders, ss.txns, ss.last_saved, ss.receipt = [], [], None, None


def create_family():
    ss = st.session_state
    code = secrets.token_hex(3).upper()
    with closing(_conn()) as con, con:
        con.execute("INSERT INTO families (code, owner, data) VALUES (?,?,?)",
                    (code, ss.user, json.dumps({"budget": float(ss.budget)})))
        con.execute("UPDATE users SET family=? WHERE username=?", (code, ss.user))
    ss.family = code
    _msg("ok", f"Family created. Share the code {code} with your family.")


def join_family():
    ss = st.session_state
    code = ss.get("family_code_in", "").strip().upper()
    with closing(_conn()) as con, con:
        found = con.execute("SELECT code FROM families WHERE code=?", (code,)).fetchone()
        if found:
            con.execute("UPDATE users SET family=? WHERE username=?", (code, ss.user))
    if not found:
        return _msg("err", "No family has that code.")
    ss.family, ss.family_code_in = code, ""
    _msg("ok", "You joined the family. Switch to Family (shared) to use the shared cart and wallet.")


def leave_family():
    persist()
    ss = st.session_state
    with closing(_conn()) as con, con:
        con.execute("UPDATE users SET family=NULL WHERE username=?", (ss.user,))
    ss.family, ss.scope_choice = None, "Personal"
    if ss.scope == "family":
        ss.scope = "me"
        load_scope()


def switch_scope():
    persist()
    ss = st.session_state
    ss.scope = "family" if ss.get("scope_choice") == "Family (shared)" else "me"
    load_scope()


def topup():
    ss = st.session_state
    amount = float(ss.get("topup_amt", 0) or 0)
    if not ss.user or amount <= 0:
        return
    ss.wallet = round(float(ss.wallet) + amount, 2)
    ss.txns.append({"when": _now(), "type": "top-up", "amount": amount, "by": ss.user, "note": "Money added"})
    persist()


def build_receipt(order):
    line = "-" * 34
    out = ["SMARTCART BILL", f"Order: {order['id']}", f"Date: {order['when']}", f"Paid by: {order['method']}", line]
    for pid, qty in order["items"].items():
        p = product_by_id(pid)
        if p is not None:
            out.append(f"{p['product_name']} ({p['pack_size']}) x{qty}")
            offer = offer_label(p.get("offer", ""))
            out.append(f"    {rupees(line_cost(p, qty))}" + (f"  [{offer}]" if offer else ""))
    out += [line, f"You saved: {rupees(order['saved'])}", f"TOTAL: {rupees(order['total'])}", line,
            "Demo bill. No real payment was made."]
    return "\n".join(out)


def pay_now():
    ss = st.session_state
    total = cart_total()
    method = ss.get("pay_method", "Wallet")
    if not ss.cart:
        return
    if method == "Wallet":
        if not ss.user:
            ss.pay_msg = ("err", "Log in to pay from your wallet.")
            return
        if total > float(ss.wallet) + 1e-9:
            ss.pay_msg = ("err", "Not enough money in the wallet.")
            return
    if method == "UPI" and not re.fullmatch(r"[\w.\-]{2,}@[A-Za-z]{2,}", ss.get("upi_id", "").strip()):
        ss.pay_msg = ("err", "Enter a valid UPI ID such as name@bank.")
        return
    saved = cart_savings()
    n = sum(ss.cart.values())
    order = {"id": "SC" + datetime.now(IST).strftime("%y%m%d%H%M%S"), "when": _now(), "method": method,
             "total": round(total, 2), "saved": round(saved, 2), "by": ss.user or "guest",
             "items": {str(k): int(v) for k, v in ss.cart.items()}}
    msg = f"Payment of {rupees(total)} received by {method}. Order {order['id']}."
    if method == "Wallet":
        ss.wallet = round(float(ss.wallet) - total, 2)
        ss.txns.append({"when": _now(), "type": "purchase", "amount": -round(total, 2), "by": ss.user,
                        "note": f"{n} items, order {order['id']}"})
        msg += f" Wallet balance left: {rupees(ss.wallet)}."
    ss.orders.append(order)
    ss.receipt = build_receipt(order)
    ss.cart = {}
    ss.pay_msg = ("ok", msg)
    persist()


# ---------- Items you usually buy ----------
def regular_items(limit=4):
    ss = st.session_state
    recent = ss.orders[-10:]
    if not recent:
        return []
    counts = {}
    for o in recent:
        for pid in o.get("items", {}):
            counts[pid] = counts.get(pid, 0) + 1
    need = min(2, len(recent))
    out = []
    for pid, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        if c < need or pid in ss.cart:
            continue
        p = product_by_id(pid)
        if p is None or str(p["stock_status"]).lower() not in AVAILABLE:
            continue
        note = "From your last order" if len(recent) == 1 else f"Bought in {c} of your last {len(recent)} orders"
        out.append((p, note))
    return out[:limit]


def render_regulars(prefix):
    items = regular_items()
    if not items:
        return
    st.markdown('<div class="aisle">You usually buy</div>', unsafe_allow_html=True)
    cols = st.columns(COLUMNS)
    for col, (p, note) in zip(cols, items):
        with col:
            render_card(p, prefix, note=note)


# ---------- Deals (Buy 1 Get 1 and discounts) ----------
def has_offer(o):
    return parse_offer(o) is not None


def deal_items(limit=4):
    ss = st.session_state
    cat = ss.catalogue
    pool = cat[cat["offer"].apply(has_offer) & cat["stock_status"].str.lower().isin(AVAILABLE)]
    pool = pool[~pool["product_id"].isin(ss.cart)]
    if pool.empty:
        return [], "none"
    ids = set(pool["product_id"])
    cart_pids = list(ss.cart)
    key = get_api_key()
    if cart_pids and key and ss.get("use_ai"):
        try:
            lines = tuple(f"{r.product_id}|{r.product_name}|{r.pack_size}|{r.price:g}" for r in pool.itertuples(index=False))
            names = tuple(product_by_id(pid)["product_name"] for pid in cart_pids if product_by_id(pid) is not None)
            out = []
            for pid, reason in ai_pick(names, lines, limit, key):
                p = product_by_id(pid)
                if p is not None and pid in ids:
                    out.append((p, f"{offer_label(p['offer'])} · {reason}" if reason else offer_label(p["offer"])))
            if out:
                return out[:limit], "ai"
        except Exception:
            pass
    if cart_pids:
        pairs = [(p, f"{offer_label(p['offer'])} · {r.lower()}") for p, r in rule_suggestions(cart_pids, set(cart_pids))
                 if p["product_id"] in ids]
        if pairs:
            return pairs[:limit], "rules"

    def strength(o):
        kind, val = parse_offer(o)
        return 50.0 if kind == "bogo" else val

    best = pool.assign(_d=pool["offer"].apply(strength)).sort_values(["_d", "price"], ascending=[False, True]).head(limit)
    return [(r, offer_label(r["offer"])) for _, r in best.iterrows()], "top"


def render_deals(prefix):
    items, source = deal_items()
    if not items:
        return
    badge = {"ai": "AI picks for your cart", "rules": "picked for your cart", "top": "biggest savings"}[source]
    st.markdown(f'<div class="aisle">Deals for you · {badge}</div>', unsafe_allow_html=True)
    cols = st.columns(COLUMNS)
    for col, (p, note) in zip(cols, items):
        with col:
            render_card(p, prefix, note=note)


# ---------- AI shopping assistant ----------
def chat_reply(history):
    key = get_api_key()
    if key and st.session_state.get("use_ai"):
        try:
            import anthropic

            cat = st.session_state.catalogue
            avail = cat[cat["stock_status"].str.lower().isin(AVAILABLE)]
            lines = [f"{r.product_id}|{r.product_name}|{r.pack_size}|{r.price:g}" for r in avail.itertuples(index=False)]
            system = (
                "You are SmartCart's friendly shopping assistant for an Indian supermarket. Answer briefly, "
                "under 120 words. When the customer asks what is needed for a dish, or wants product ideas, "
                "list the ingredients in your reply and put the matching product ids from the catalogue in "
                '"ids" (at most 15). Use only ids from the catalogue and mention any ingredient the store '
                'does not stock. Reply with JSON only: {"reply": "text", "ids": ["VEG-001"]}\n\n'
                "Catalogue (id|name|pack|price):\n" + "\n".join(lines)
            )
            msgs = [{"role": m["role"], "content": m["content"]} for m in history[-8:]]
            while msgs and msgs[0]["role"] != "user":
                msgs.pop(0)
            client = anthropic.Anthropic(api_key=key)
            msg = client.messages.create(model="claude-haiku-5-5", max_tokens=800, system=system, messages=msgs)
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            found = re.search(r"\{.*\}", text, re.S)
            data = json.loads(found.group(0)) if found else {}
            reply = str(data.get("reply") or text).strip()[:1500]
            pids = []
            for i in data.get("ids", []):
                if product_by_id(str(i)) is not None and str(i) not in pids:
                    pids.append(str(i))
            return reply, pids[:15]
        except Exception:
            pass
    name = match_recipe(history[-1]["content"])
    if name:
        plan = recipe_plan(name, 4)
        names = ", ".join(it["p"]["product_name"] for it in plan["items"])
        extra = f" Not stocked here: {', '.join(plan['missing'])}." if plan["missing"] else ""
        return f"For **{name}** (4 servings) this store has: {names}.{extra}", [it["p"]["product_id"] for it in plan["items"]]
    return ("I can only match dishes from the built-in list right now, such as pasta, biryani or sambar. "
            "Add an ANTHROPIC_API_KEY secret to let me answer any question."), []


def add_many(pids):
    ss = st.session_state
    todo = []
    for pid in pids:
        p = product_by_id(pid)
        if p is not None and pid not in ss.cart and str(p["stock_status"]).lower() in AVAILABLE:
            todo.append((pid, p))
    if not todo:
        ss.chat_note = ("err", "Those items are already in your cart or not available.")
        return
    cost = sum(line_cost(p, 1) for _, p in todo)
    room = float(ss.budget) - cart_total()
    if cost > room + 1e-9:
        ss.chat_note = ("err", f"These items cost {rupees(cost)}, which is {rupees(cost - max(room, 0))} more than your remaining budget.")
        return
    for pid, _ in todo:
        ss.cart[pid] = 1
    ss.chat_note = ("ok", f"Added {len(todo)} items to your cart.")


# ---------- Cook a dish ----------
# Each ingredient is (catalogue keyword, scales_with_servings). Main items need more packs for bigger groups.
RECIPES = {
    "Sambar": {"aliases": ["sambhar", "sambar rice"], "items": [
        ("toor dal", True), ("small onion", False), ("tomato", False), ("drumstick", True), ("carrot", False),
        ("brinjal", False), ("tamarind", False), ("sambar powder", False), ("turmeric powder", False),
        ("mustard seeds", False), ("curry leaves", False), ("dry red chilli", False), ("asafoetida", False),
        ("coriander leaves", False), ("coconut oil", False), ("salt", False)]},
    "Rasam": {"aliases": ["rasam rice"], "items": [
        ("tomato", True), ("tamarind", False), ("toor dal", False), ("rasam powder", False), ("garlic", False),
        ("curry leaves", False), ("mustard seeds", False), ("cumin seeds", False), ("black pepper", False),
        ("coriander leaves", False), ("dry red chilli", False), ("coconut oil", False), ("salt", False)]},
    "Idli with coconut chutney": {"aliases": ["idli", "idly"], "items": [
        ("idli rice", True), ("urad dal", True), ("fenugreek seeds", False), ("fresh coconut", False),
        ("green chilli", False), ("curry leaves", False), ("mustard seeds", False), ("coconut oil", False),
        ("ginger", False), ("salt", False)]},
    "Dosa with chutney": {"aliases": ["dosa", "dosai"], "items": [
        ("idli rice", True), ("urad dal", True), ("fenugreek seeds", False), ("sunflower oil", False),
        ("fresh coconut", False), ("green chilli", False), ("curry leaves", False), ("salt", False)]},
    "Avial": {"aliases": ["aviyal"], "items": [
        ("raw banana", False), ("drumstick", False), ("carrot", False), ("beans", False), ("pumpkin", False),
        ("curd", False), ("fresh coconut", False), ("green chilli", False), ("cumin seeds", False),
        ("curry leaves", False), ("coconut oil", False), ("turmeric powder", False), ("salt", False)]},
    "Kerala fish curry": {"aliases": ["fish curry", "meen curry", "meen", "fish"], "items": [
        ("sardine", True), ("small onion", False), ("tamarind", False), ("fish curry masala", False),
        ("ginger", False), ("garlic", False), ("green chilli", False), ("curry leaves", False),
        ("coconut oil", False), ("salt", False)]},
    "Chicken curry": {"aliases": ["chicken"], "items": [
        ("chicken (curry cut)", True), ("onion", True), ("tomato", False), ("ginger", False), ("garlic", False),
        ("green chilli", False), ("chicken masala", False), ("turmeric powder", False), ("chilli powder", False),
        ("coriander powder", False), ("curry leaves", False), ("coconut oil", False), ("coriander leaves", False),
        ("salt", False)]},
    "Chicken biryani": {"aliases": ["biryani", "biriyani", "chicken biriyani"], "items": [
        ("basmati rice", True), ("chicken (curry cut)", True), ("onion", True), ("tomato", False), ("curd", False),
        ("ginger", False), ("garlic", False), ("green chilli", False), ("biryani masala", False), ("ghee", False),
        ("mint leaves", False), ("coriander leaves", False), ("lemon", False), ("sunflower oil", False),
        ("salt", False)]},
    "Vegetable pulao": {"aliases": ["veg biryani", "veg pulao", "pulao", "vegetable biryani"], "items": [
        ("basmati rice", True), ("carrot", False), ("green peas", False), ("beans", False), ("potato", False),
        ("onion", False), ("ginger", False), ("garlic", False), ("biryani masala", False), ("ghee", False),
        ("mint leaves", False), ("salt", False)]},
    "Paneer butter masala": {"aliases": ["paneer", "butter paneer"], "items": [
        ("paneer", True), ("butter (salted)", False), ("tomato", True), ("onion", False), ("fresh cream", False),
        ("ginger", False), ("garlic", False), ("garam masala", False), ("chilli powder", False),
        ("kasuri methi", False), ("coriander leaves", False), ("salt", False)]},
    "Palak paneer": {"aliases": ["spinach paneer"], "items": [
        ("spinach", True), ("paneer", True), ("onion", False), ("tomato", False), ("ginger", False),
        ("garlic", False), ("green chilli", False), ("garam masala", False), ("cumin seeds", False),
        ("ghee", False), ("fresh cream", False), ("salt", False)]},
    "Dal tadka": {"aliases": ["dal fry", "dal", "parippu"], "items": [
        ("toor dal", True), ("onion", False), ("tomato", False), ("garlic", False), ("ginger", False),
        ("green chilli", False), ("cumin seeds", False), ("turmeric powder", False), ("chilli powder", False),
        ("ghee", False), ("coriander leaves", False), ("lemon", False), ("salt", False)]},
    "Rajma masala": {"aliases": ["rajma", "rajma chawal"], "items": [
        ("rajma", True), ("onion", False), ("tomato", False), ("ginger", False), ("garlic", False),
        ("green chilli", False), ("garam masala", False), ("chilli powder", False), ("coriander powder", False),
        ("cumin seeds", False), ("turmeric powder", False), ("sunflower oil", False),
        ("coriander leaves", False), ("salt", False)]},
    "Chana masala": {"aliases": ["chole", "chickpea curry"], "items": [
        ("kabuli chana", True), ("onion", False), ("tomato", False), ("ginger", False), ("garlic", False),
        ("chana masala", False), ("green chilli", False), ("turmeric powder", False), ("sunflower oil", False),
        ("lemon", False), ("coriander leaves", False), ("salt", False)]},
    "Chapati with potato curry": {"aliases": ["chapati", "roti", "chapathi"], "items": [
        ("wheat atta", True), ("potato", True), ("onion", False), ("tomato", False), ("green chilli", False),
        ("chilli powder", False), ("turmeric powder", False), ("cumin seeds", False), ("sunflower oil", False),
        ("coriander leaves", False), ("salt", False)]},
    "Egg curry": {"aliases": ["egg roast", "mutta curry"], "items": [
        ("eggs", True), ("onion", False), ("tomato", False), ("ginger", False), ("garlic", False),
        ("chilli powder", False), ("turmeric powder", False), ("coriander powder", False),
        ("garam masala", False), ("curry leaves", False), ("coconut oil", False), ("salt", False)]},
    "Masala omelette with toast": {"aliases": ["omelette", "omelet", "egg toast"], "items": [
        ("eggs", True), ("onion", False), ("green chilli", False), ("tomato", False), ("coriander leaves", False),
        ("white bread", True), ("butter (salted)", False), ("black pepper", False), ("sunflower oil", False),
        ("salt", False)]},
    "Upma": {"aliases": ["rava upma", "sooji upma"], "items": [
        ("sooji", True), ("onion", False), ("green chilli", False), ("ginger", False), ("carrot", False),
        ("mustard seeds", False), ("curry leaves", False), ("sunflower oil", False), ("lemon", False),
        ("salt", False)]},
    "Poha": {"aliases": ["aval upma", "kanda poha"], "items": [
        ("poha", True), ("onion", False), ("potato", False), ("green chilli", False), ("curry leaves", False),
        ("mustard seeds", False), ("turmeric powder", False), ("lemon", False), ("roasted peanuts", False),
        ("coriander leaves", False), ("sunflower oil", False), ("salt", False)]},
    "Pongal": {"aliases": ["ven pongal"], "items": [
        ("raw rice", True), ("moong dal", True), ("ghee", False), ("cumin seeds", False), ("black pepper", False),
        ("curry leaves", False), ("ginger", False), ("salt", False)]},
    "Semiya payasam": {"aliases": ["payasam", "kheer", "vermicelli payasam"], "items": [
        ("vermicelli", True), ("milk (full cream)", True), ("sugar", False), ("ghee", False),
        ("cashew nuts", False), ("raisins", False), ("cardamom", False)]},
    "Tomato pasta": {"aliases": ["pasta", "red sauce pasta"], "items": [
        ("pasta", True), ("tomato", False), ("onion", False), ("capsicum", False), ("garlic", False),
        ("olive oil", False), ("mozzarella cheese", False), ("black pepper", False), ("salt", False)]},
    "Veg sandwich": {"aliases": ["sandwich", "bread sandwich"], "items": [
        ("white bread", True), ("butter (salted)", False), ("cheese slices", False), ("cucumber", False),
        ("tomato", False), ("onion", False), ("mayonnaise", False), ("black pepper", False)]},
    "Pancakes": {"aliases": ["pancake", "crepe", "crepes"], "items": [
        ("maida", True), ("egg", True), ("milk (full cream)", True), ("sugar", False), ("butter (salted)", False),
        ("baking powder", False), ("honey", False)]},
    "Cake": {"aliases": ["vanilla cake", "plum cake", "sponge cake", "birthday cake", "chocolate cake", "cupcake"], "items": [
        ("maida", True), ("sugar", True), ("egg", True), ("butter (salted)", False), ("milk (full cream)", False),
        ("baking powder", False), ("vanilla essence", False), ("cocoa powder", False)]},
    "Fried rice": {"aliases": ["veg fried rice", "egg fried rice"], "items": [
        ("basmati rice", True), ("carrot", False), ("beans", False), ("onion", False), ("capsicum", False),
        ("spring onion", False), ("garlic", False), ("soy sauce", False), ("egg", False),
        ("sunflower oil", False), ("black pepper", False), ("salt", False)]},
    "Veg noodles": {"aliases": ["noodles", "hakka noodles", "chow mein", "maggi"], "items": [
        ("noodles", True), ("carrot", False), ("capsicum", False), ("cabbage", False), ("onion", False),
        ("garlic", False), ("soy sauce", False), ("vinegar", False), ("sunflower oil", False),
        ("black pepper", False), ("salt", False)]},
    "Curd rice": {"aliases": ["thayir sadam", "perugu annam"], "items": [
        ("raw rice", True), ("curd", True), ("milk (full cream)", False), ("green chilli", False), ("ginger", False),
        ("mustard seeds", False), ("curry leaves", False), ("coriander leaves", False), ("salt", False)]},
    "Lemon rice": {"aliases": ["chitranna"], "items": [
        ("raw rice", True), ("lemon", False), ("green chilli", False), ("curry leaves", False),
        ("mustard seeds", False), ("turmeric powder", False), ("roasted peanuts", False),
        ("sunflower oil", False), ("salt", False)]},
    "Khichdi": {"aliases": ["dal khichdi", "moong khichdi"], "items": [
        ("raw rice", True), ("moong dal", True), ("ghee", False), ("cumin seeds", False),
        ("turmeric powder", False), ("ginger", False), ("salt", False)]},
    "Aloo paratha": {"aliases": ["paratha", "stuffed paratha"], "items": [
        ("wheat flour", True), ("potato", True), ("green chilli", False), ("coriander leaves", False),
        ("cumin seeds", False), ("ghee", False), ("curd", False), ("salt", False)]},
    "Suji halwa": {"aliases": ["sooji halwa", "rava kesari", "kesari", "halwa"], "items": [
        ("rava", True), ("sugar", True), ("ghee", False), ("cashew nuts", False), ("raisins", False),
        ("cardamom", False), ("milk (full cream)", False)]},
    "Masala chai": {"aliases": ["chai", "tea"], "items": [
        ("tea", False), ("milk (full cream)", True), ("sugar", False), ("ginger", False), ("cardamom", False)]},
    "Banana milkshake": {"aliases": ["milkshake", "banana shake", "shake"], "items": [
        ("banana", True), ("milk (full cream)", True), ("sugar", False), ("ice cream", False)]},
    "Veg pizza": {"aliases": ["pizza", "margherita pizza"], "items": [
        ("pizza base", True), ("pizza sauce", False), ("mozzarella cheese", True), ("capsicum", False),
        ("onion", False), ("olive oil", False)]},
    "Veg burger": {"aliases": ["burger", "burgers"], "items": [
        ("burger bun", True), ("potato", False), ("cheese slices", False), ("tomato", False), ("onion", False),
        ("mayonnaise", False), ("tomato ketchup", False)]},
    "Green salad": {"aliases": ["salad", "veg salad"], "items": [
        ("cucumber", True), ("tomato", True), ("onion", False), ("carrot", False), ("lemon", False),
        ("black pepper", False), ("salt", False)]},
    "Medu vada": {"aliases": ["vada", "ulundu vada", "vadai"], "items": [
        ("urad dal", True), ("green chilli", False), ("curry leaves", False), ("ginger", False),
        ("black pepper", False), ("sunflower oil", False), ("salt", False)]},
}


def match_recipe(text):
    t = text.strip().lower()
    if len(t) < 3:
        return None
    names = {}
    for name, r in RECIPES.items():
        names[name.lower()] = name
        for a in r["aliases"]:
            names[a.lower()] = name
    if t in names:
        return names[t]
    hits = [k for k in names if len(k) >= 4 and (k in t or (len(t) >= 4 and t in k))]
    if hits:
        return names[max(hits, key=len)]
    close = difflib.get_close_matches(t, list(names), n=1, cutoff=0.82)
    return names[close[0]] if close else None


# Grams (or ml) needed per person. Main items default to 100, everything else to 20.
PER_SERVING_G = {
    "toor dal": 50, "moong dal": 40, "urad dal": 40, "rajma": 50, "chana": 50, "idli rice": 80, "raw rice": 70,
    "basmati rice": 80, "pasta": 80, "poha": 60, "vermicelli": 30, "rava": 50, "wheat flour": 80, "maida": 60,
    "paneer": 80, "chicken": 150, "sardine": 150, "bread": 60, "milk": 100, "curd": 80, "noodles": 80,
    "egg": 60, "sugar": 30, "banana": 120, "cucumber": 60, "tomato": 50, "onion": 40, "potato": 70,
    "carrot": 40, "beans": 40, "capsicum": 30, "brinjal": 40, "pumpkin": 50, "cabbage": 40, "drumstick": 30,
    "raw banana": 50, "coriander leaves": 5, "curry leaves": 3, "green chilli": 5,
}


def pack_weight(pack):
    """Return (amount, unit) with unit 'g' or 'ml' for packs like 500 g, 1 kg, 1 L, 6 x 70 g. None if not a weight."""
    m = re.search(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:\.\d+)?)\s*(kg|gms|gm|g|ltr|l|ml)\b", str(pack).lower())
    if not m:
        return None
    n = float(m.group(2)) * (int(m.group(1)) if m.group(1) else 1)
    unit = m.group(3)
    if unit == "kg":
        return n * 1000, "g"
    if unit in ("l", "ltr"):
        return n * 1000, "ml"
    return n, ("ml" if unit == "ml" else "g")


def fmt_amount(amount, unit):
    if amount >= 1000:
        return f"{amount / 1000:g} {'kg' if unit == 'g' else 'L'}"
    return f"{amount:g} {unit}"


def recipe_qty(p, kw, scales, servings):
    """Packs to buy for this many people, plus the amount needed. Bigger groups mean more weight."""
    per = next((v for k, v in PER_SERVING_G.items() if k in kw), 100 if scales else 20)
    need = per * servings
    pw = pack_weight(p["pack_size"])
    if pw:
        qty = math.ceil(need / pw[0])
        if not scales:
            qty = min(qty, max(1, math.ceil(servings / 6)))
        return max(1, min(qty, 10)), fmt_amount(need, pw[1])
    groups = max(1, math.ceil(servings / 4))
    return (groups if scales else 1), ""


def recipe_plan(name, servings):
    items, missing = [], []
    for kw, scales in RECIPES[name]["items"]:
        p = find_product(kw, set())
        if p is None:
            missing.append(kw)
        else:
            qty, need = recipe_qty(p, kw, scales, servings)
            items.append({"p": p, "qty": qty, "note": "", "need": need})
    return {"title": name, "items": items, "missing": missing, "source": "rules"}


def generic_plan(dish):
    """Offline fallback for dishes with no recipe: products whose names contain a word from the dish."""
    cat = st.session_state.catalogue
    words = [w for w in re.findall(r"[a-z]+", dish.lower()) if len(w) >= 3 and w not in ("and", "with", "the", "for")]
    items, seen = [], set()
    for w in words:
        pat = re.compile(rf"(?<!\w){re.escape(w)}(?:s|es)?(?!\w)")
        for row in cat.itertuples(index=False):
            if row.product_id in seen or str(row.stock_status).lower() not in AVAILABLE:
                continue
            if pat.search(row.product_name.lower()):
                seen.add(row.product_id)
                items.append({"p": product_by_id(row.product_id), "qty": 1, "note": "", "need": ""})
            if len(items) >= 8:
                break
    if not items:
        return None
    return {"title": dish, "items": items, "missing": [], "source": "search"}


@st.cache_data(show_spinner=False, ttl=3600)
def ai_recipe(dish, servings, catalogue_lines, _api_key):
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key)
    prompt = (
        f"A customer wants to cook: {dish}, for {servings} people. "
        "Using ONLY products from the catalogue below, list the ingredients they need to buy. "
        "Skip water. List ingredients that the store does not stock under \"missing\". "
        "Scale quantities with the number of people: roughly 100 g of each main ingredient per person and "
        "20 g of spices, oil and seasonings per person, choosing how many packs cover that weight (qty 1 to 10).\n"
        'Reply with JSON only: {"dish": "proper dish name", "items": [{"id": "VEG-001", "qty": 1, '
        '"note": "short role, 5 words max"}], "missing": ["ingredient name"]}\n\n'
        "Catalogue (id|name|pack|price):\n" + "\n".join(catalogue_lines)
    )
    msg = client.messages.create(
        model="claude-haiku-5-5", max_tokens=1200, messages=[{"role": "user", "content": prompt}]
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    found = re.search(r"\{.*\}", text, re.S)
    data = json.loads(found.group(0)) if found else {}
    items = []
    for d in data.get("items", []):
        if isinstance(d, dict) and "id" in d:
            items.append((str(d["id"]), max(1, min(int(d.get("qty", 1)), 10)), str(d.get("note", ""))[:60]))
    return str(data.get("dish", dish))[:60], items, [str(m)[:40] for m in data.get("missing", [])][:10]


def ai_plan(dish, servings):
    key = get_api_key()
    st.session_state.ai_error = None
    if not (key and st.session_state.get("use_ai")):
        return None
    cat = st.session_state.catalogue
    avail = cat[cat["stock_status"].str.lower().isin(AVAILABLE)]
    lines = tuple(f"{r.product_id}|{r.product_name}|{r.pack_size}|{r.price:g}" for r in avail.itertuples(index=False))
    try:
        title, raw, missing = ai_recipe(dish, servings, lines, key)
    except Exception as exc:
        st.session_state.ai_error = f"{type(exc).__name__}: {str(exc)[:150]}"
        return None
    items = []
    for pid, qty, note in raw:
        p = product_by_id(pid)
        if p is not None and str(p["stock_status"]).lower() in AVAILABLE:
            items.append({"p": p, "qty": qty, "note": note, "need": ""})
    if not items:
        return None
    return {"title": title, "items": items, "missing": missing, "source": "ai"}


# ---------- Styling ----------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&family=DM+Sans:wght@400;500;700&family=JetBrains+Mono:wght@500&display=swap');
.stApp { font-family: 'DM Sans', system-ui, sans-serif; }
h1, h2, h3, .brand { font-family: 'Bricolage Grotesque', 'Trebuchet MS', sans-serif !important; letter-spacing: -.02em; }
.head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: .5rem; }
.brand { font-weight: 800; font-size: 2rem; color: #17221b; }
.sub { color: #5d6b61; font-size: .9rem; }
div[data-testid="stMetric"] { background: #ffffff; border: 1px solid #dde3da; border-radius: 10px; padding: 10px 14px; }
.tile { height: 58px; border-radius: 6px; display: flex; align-items: flex-end; justify-content: space-between;
        padding: 6px 8px; margin-bottom: 8px;
        background: hsl(var(--h) 38% 88%); color: hsl(var(--h) 45% 28%); }
.tile b { font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.5rem; line-height: 1; }
.tile span, .pcode { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: .7rem; }
.pname { font-weight: 700; font-size: .95rem; line-height: 1.25; min-height: 2.4em; color: #17221b; overflow-wrap: anywhere; }
.pmeta { color: #5d6b61; font-size: .8rem; }
.pcode { color: #5d6b61; margin-bottom: 6px; }
.price { background: #ffd84a; color: #2a2200; font-family: 'Bricolage Grotesque', sans-serif; font-weight: 800;
         font-size: 1.1rem; padding: 2px 9px; border-radius: 4px; display: inline-block; }
.low { color: #b25b00; font-size: .78rem; font-weight: 700; margin-left: 6px; }
.out { color: #c0392b; font-size: .78rem; font-weight: 700; margin-left: 6px; }
.qty { text-align: center; font-weight: 700; padding-top: .35rem; }
.aisle { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: .78rem; letter-spacing: .06em;
         text-transform: uppercase; color: #5d6b61; border-bottom: 1px solid #dde3da; padding-bottom: 4px; margin: 14px 0 6px; }
.note { color: #1f6b45; font-size: .76rem; font-weight: 700; margin-bottom: 4px; }
.offer { background: #1f6b45; color: #fff; font-size: .72rem; font-weight: 700; padding: 2px 7px; border-radius: 4px; margin-left: 6px; }
.was { color: #8a948c; text-decoration: line-through; font-size: .8rem; margin-left: 6px; }
.kcal { color: #b25b00; font-size: .76rem; font-weight: 700; margin-bottom: 4px; }
</style>
""", unsafe_allow_html=True)

# ---------- Sidebar ----------
with st.sidebar:
    st.header("Account")
    _am = st.session_state.pop("auth_msg", None)
    if _am:
        (st.success if _am[0] == "ok" else st.error)(_am[1])
    if st.session_state.user:
        st.write(f"Signed in as **{st.session_state.user}**")
        st.button("Log out", on_click=do_logout, use_container_width=True)
        st.caption("Your cart, budget, wallet and orders are saved to your account.")

        st.subheader("Family plan")
        if st.session_state.family:
            st.radio("Shop with", ["Personal", "Family (shared)"], key="scope_choice", horizontal=True,
                     on_change=switch_scope)
            st.caption("Family code (share it so others can join):")
            st.code(st.session_state.family)
            st.caption("Members: " + ", ".join(db_members(st.session_state.family)))
            if st.session_state.scope == "family":
                st.caption("Shared cart and wallet. Others only see your changes after they press Refresh.")
                st.button("Refresh family data", on_click=load_scope, use_container_width=True)
            st.button("Leave family", on_click=leave_family, use_container_width=True)
        else:
            st.button("Create a family", on_click=create_family, use_container_width=True)
            st.text_input("Or join with a family code", key="family_code_in")
            st.button("Join family", on_click=join_family, use_container_width=True)

        st.subheader("Wallet" + (" (family)" if st.session_state.scope == "family" else ""))
        st.metric("Balance", rupees(float(st.session_state.wallet)))
        st.number_input("Add money (₹)", min_value=100.0, max_value=50000.0, value=500.0, step=100.0, key="topup_amt")
        st.button("Add to wallet", on_click=topup, use_container_width=True)
        st.caption("Demo wallet: no real money moves. A live version needs a payment gateway such as Razorpay or a UPI provider.")
    else:
        st.text_input("Username", key="auth_user")
        st.text_input("Password", type="password", key="auth_pw")
        _a, _b = st.columns(2)
        _a.button("Log in", on_click=do_login, use_container_width=True)
        _b.button("Sign up", on_click=do_register, use_container_width=True)
        st.caption("Log in to save your cart and use the wallet. Without an account, progress is lost when you leave.")
    st.divider()
    st.header("Shopping setup")
    st.number_input("Set your budget (₹)", min_value=1.0, max_value=1000000.0, step=100.0, key="budget")
    st.number_input("Snack and sweet calorie limit (kcal)", min_value=100.0, max_value=100000.0, step=500.0,
                    key="cal_limit")
    st.caption("Calories are estimates from pack size. Add a `calories` column (kcal per pack) to your CSV for exact values. "
               "Offers are demo data unless your CSV has an `offer` column such as BOGO or 10% off.")
    st.caption("Sample catalogue for demonstration. Prices are illustrative, not live store prices.")
    st.divider()
    st.subheader("Catalogue tools")
    uploaded = st.file_uploader("Upload your product CSV", type=["csv"])
    if uploaded is not None:
        sig = (uploaded.name, uploaded.size)
        if st.session_state.get("loaded_sig") != sig:
            try:
                incoming = prepare_catalogue(pd.read_csv(uploaded))
                if incoming.empty:
                    st.error("No valid product rows found. Check your CSV columns and prices.")
                else:
                    st.session_state.catalogue = incoming
                    st.session_state.loaded_sig = sig
                    reset_page()
                    st.success(f"Loaded {len(incoming)} products.")
            except Exception as exc:
                st.error(f"Could not read CSV: {exc}")
    st.divider()
    st.subheader("Smart suggestions")
    if get_api_key():
        st.toggle("Use AI (Claude) suggestions", value=True, key="use_ai")
        st.caption("AI picks items that go with your cart. If it is unavailable, built-in pairing rules are used.")
    else:
        st.caption("Suggestions use built-in pairing rules. Add an ANTHROPIC_API_KEY secret to switch on AI suggestions.")
    if st.button("Restore full catalogue", use_container_width=True):
        st.session_state.catalogue = load_default_catalogue()
        st.session_state.cart = {}
        st.session_state.pop("loaded_sig", None)
        reset_page()
        st.rerun()

catalogue = st.session_state.catalogue
valid_ids = set(catalogue["product_id"].tolist())
st.session_state.cart = {pid: q for pid, q in st.session_state.cart.items() if pid in valid_ids}

# ---------- Header and budget ----------
budget = float(st.session_state.budget)
total = cart_total()
remaining = budget - total
percent = min(total / budget, 1.0) if budget else 0.0
item_count = sum(st.session_state.cart.values())

st.markdown(
    f'<div class="head"><span class="brand">SmartCart</span>'
    f'<span class="sub">Budget-aware smart shopping · {len(catalogue)} products · '
    f'{catalogue["category"].nunique()} categories</span></div>',
    unsafe_allow_html=True,
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Shopping budget", rupees(budget))
m2.metric("Live bill", rupees(total))
m3.metric("Remaining" if remaining >= 0 else "Over budget by", rupees(abs(remaining)))
m4.metric("Items in cart", item_count)
st.progress(percent, text=f"{percent * 100:.0f}% of your budget used")

if total > budget:
    st.error(f"Your cart is {rupees(total - budget)} over budget. Remove items or choose suitable alternatives.")
elif item_count and remaining <= budget * 0.10:
    st.warning("You're close to your budget limit. Check the total before adding more.")

if st.session_state.get("catalogue_note") and len(st.session_state.catalogue) == st.session_state.get("catalogue_note_n"):
    st.warning(st.session_state.catalogue_note)

junk_rows = junk_breakdown()
junk = sum(k for _, k in junk_rows)
cal_limit = float(st.session_state.cal_limit)
if junk_rows and cal_limit > 0:
    top = ", ".join(n for n, _ in junk_rows[:3])
    if junk >= cal_limit:
        st.error(f"🍟 Snacks and sweets in your cart add up to about {junk:,.0f} kcal, over your limit of "
                 f"{cal_limit:,.0f} kcal. That is enough. Consider removing some: {top}.")
    elif junk >= 0.8 * cal_limit:
        st.warning(f"🍟 Snacks and sweets are at about {junk:,.0f} of your {cal_limit:,.0f} kcal limit. "
                   f"The biggest contributors: {top}.")

tab_shop, tab_cart, tab_cook, tab_chat, tab_catalogue, tab_how = st.tabs(
    ["Shop", f"My cart ({item_count})", "Cook a dish", "Ask AI", "Product catalogue", "How it works"]
)

# ---------- Shop ----------
with tab_shop:
    notice = st.session_state.pop("notice", None)
    if notice:
        st.warning(notice["msg"])
        if notice["alts"]:
            st.markdown("**Cheaper alternatives in the same category:**")
            for line in notice["alts"]:
                st.write("• " + line)
        else:
            st.info("No cheaper in-stock option fits your remaining budget. Lower the quantity or remove an item.")

    render_regulars("regs")
    render_deals("deals")

    last = st.session_state.get("last_added")
    if last and last in st.session_state.cart:
        lp = product_by_id(last)
        if lp is not None:
            render_suggestions([last], f"Goes well with {lp['product_name']}", "rec")

    s1, s2, s3 = st.columns([3, 2, 2])
    search = s1.text_input(
        "Search by product, brand, aisle or code", placeholder="onion, pen, Amul, VEG-001",
        key="search", on_change=reset_page,
    )
    sort_by = s2.selectbox(
        "Sort by", ["Aisle order", "Name A to Z", "Price low to high", "Price high to low"], key="sort_by",
    )
    s3.text_input(
        "Add by product code", placeholder="VEG-001", key="code_input", on_change=add_by_code,
    )
    code_msg = st.session_state.pop("code_msg", None)
    if code_msg:
        (st.success if code_msg[0] == "ok" else st.error)(code_msg[1])

    categories = ["All", "Offers"] + sorted(catalogue["category"].unique().tolist())
    if hasattr(st, "pills"):
        chosen = st.pills("Category", categories, default="All", key="cat", label_visibility="collapsed", on_change=reset_page)
        chosen = chosen or "All"
    else:
        chosen = st.selectbox("Category", categories, key="cat_select", on_change=reset_page)

    shown = catalogue.copy()
    if search.strip():
        q = search.strip().lower()
        hay = (shown["product_name"] + " " + shown["brand"] + " " + shown["category"] + " "
               + shown["product_id"] + " " + shown["aisle"]).str.lower()
        shown = shown[hay.str.contains(q, regex=False)]
    if chosen == "Offers":
        shown = shown[shown["offer"].apply(has_offer)]
    elif chosen != "All":
        shown = shown[shown["category"] == chosen]

    if sort_by == "Name A to Z":
        shown = shown.sort_values("product_name")
    elif sort_by == "Price low to high":
        shown = shown.sort_values(["price", "product_name"])
    elif sort_by == "Price high to low":
        shown = shown.sort_values(["price", "product_name"], ascending=[False, True])
    else:
        shown = shown.sort_values(["aisle", "product_id"])

    st.caption(f"{len(shown)} of {len(catalogue)} products")
    if shown.empty:
        st.info("No products match. Try a different word or choose All categories.")
    else:
        pages = max(1, math.ceil(len(shown) / PAGE_SIZE))
        st.session_state.page = max(1, min(int(st.session_state.page), pages))
        if pages > 1:
            st.number_input("Page", min_value=1, max_value=pages, step=1, key="page")
        start = (st.session_state.page - 1) * PAGE_SIZE
        chunk = shown.iloc[start:start + PAGE_SIZE]

        for row_start in range(0, len(chunk), COLUMNS):
            cols = st.columns(COLUMNS)
            for col, (_, p) in zip(cols, chunk.iloc[row_start:row_start + COLUMNS].iterrows()):
                with col:
                    render_card(p, "grid")

# ---------- Cart ----------
with tab_cart:
    st.subheader("Your cart, in aisle order")
    _pm = st.session_state.pop("pay_msg", None)
    if _pm:
        (st.success if _pm[0] == "ok" else st.error)(_pm[1])
    if st.session_state.get("receipt"):
        with st.expander("View and download your bill", expanded=bool(_pm and _pm[0] == "ok")):
            st.code(st.session_state.receipt, language=None)
            st.download_button("Download bill (.txt)", st.session_state.receipt, file_name="smartcart_bill.txt")
    if not st.session_state.cart:
        st.info("Your cart is empty. Add products from the Shop tab or enter a product code.")
    else:
        rows = []
        for pid, qty in st.session_state.cart.items():
            p = product_by_id(pid)
            if p is None:
                continue
            rows.append({
                "Aisle": p["aisle"], "Product ID": p["product_id"], "Product": p["product_name"],
                "Brand": p["brand"], "Category": p["category"], "Pack size": p["pack_size"],
                "Qty": int(qty), "Unit price": float(p["price"]), "Line total": line_cost(p, qty),
                "Offer": offer_label(p.get("offer", "")),
            })
        bill_df = pd.DataFrame(rows).sort_values(["Aisle", "Product"]).reset_index(drop=True)

        lines = ["Picking list (by aisle)"]
        for aisle, grp in bill_df.groupby("Aisle", sort=True):
            label = "Unsorted" if aisle == "-" else f"Aisle {aisle} · {grp['Category'].iloc[0]}"
            st.markdown(f'<div class="aisle">{html.escape(label)}</div>', unsafe_allow_html=True)
            lines.append("\n" + label)
            for _, r in grp.iterrows():
                pid = r["Product ID"]
                a, b, c = st.columns([5, 2, 3])
                a.markdown(f"**{r['Product']}**")
                a.caption(f"{r['Brand']} · {r['Pack size']} · {rupees(r['Unit price'])} each"
                          + (f" · {r['Offer']}" if r["Offer"] else ""))
                if r["Offer"] == BOGO_TEXT and r["Qty"] % 2 == 1:
                    a.caption("Add 1 more and it is free.")
                b.markdown(f"**{rupees(r['Line total'])}**")
                with c:
                    x, y, z = st.columns(3)
                    x.button("−", key=f"cdec_{pid}", on_click=change_qty, args=(pid, -1), use_container_width=True)
                    y.markdown(f'<div class="qty">{r["Qty"]}</div>', unsafe_allow_html=True)
                    z.button("+", key=f"cinc_{pid}", on_click=add_item, args=(pid, 1), use_container_width=True)
                lines.append(f"- {r['Product']} ({r['Pack size']}) x {r['Qty']}  Rs {r['Line total']:,.2f}")
        lines.append(f"\nTotal: Rs {total:,.2f}")

        st.divider()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total bill", rupees(total))
        c2.metric("Budget remaining" if remaining >= 0 else "Over budget by", rupees(abs(remaining)))
        c3.metric("You saved", rupees(cart_savings()))
        d1, d2, d3 = st.columns(3)
        d1.download_button("Download bill (CSV)", data=bill_df.to_csv(index=False).encode("utf-8"),
                           file_name="smartcart_bill.csv", mime="text/csv", use_container_width=True)
        d2.download_button("Download picking list", data="\n".join(lines), file_name="picking_list.txt",
                           use_container_width=True)
        if d3.button("Clear cart", use_container_width=True):
            st.session_state.cart = {}
            st.rerun()

        st.subheader("Pay your bill")
        st.radio("Payment method", ["Wallet", "UPI", "Card", "Cash on delivery"], key="pay_method", horizontal=True)
        method = st.session_state.pay_method
        can_pay = True
        if method == "Wallet":
            if not st.session_state.user:
                st.info("Log in from the sidebar to pay from your wallet.")
                can_pay = False
            else:
                wallet = float(st.session_state.wallet)
                w1, w2 = st.columns(2)
                w1.metric("Wallet balance", rupees(wallet))
                w2.metric("Balance after paying", rupees(wallet - total) if wallet >= total else "Not enough")
                if wallet < total:
                    st.warning(f"Your wallet is {rupees(total - wallet)} short. Add money from the sidebar.")
                    can_pay = False
        elif method == "UPI":
            st.text_input("Your UPI ID", placeholder="name@bank", key="upi_id")
            st.caption("Demo only: no money moves. A live version needs a payment gateway such as Razorpay.")
        elif method == "Card":
            st.caption("Demo only: no card details are collected and no money moves.")
        else:
            st.caption("You pay in cash when the order is delivered.")
        st.button(f"Pay {rupees(total)}" + (" from wallet" if method == "Wallet" else f" by {method}"),
                  type="primary", on_click=pay_now, disabled=not can_pay, use_container_width=True)

        if junk_rows and cal_limit > 0:
            st.progress(min(junk / cal_limit, 1.0),
                        text=f"Snack and sweet calories (estimated): {junk:,.0f} of {cal_limit:,.0f} kcal")

        render_regulars("regc")
        render_deals("dealc")
        render_suggestions(list(st.session_state.cart.keys()), "You may also need", "recc", show_reason=True)

    if st.session_state.orders:
        with st.expander("Order history"):
            _rows = [{k: o.get(k, "") for k in ("id", "when", "method", "total", "saved", "by")}
                     for o in st.session_state.orders[::-1]]
            st.dataframe(pd.DataFrame(_rows), hide_index=True, use_container_width=True)
    if st.session_state.user and st.session_state.txns:
        with st.expander("Wallet history"):
            st.dataframe(pd.DataFrame(st.session_state.txns[::-1]), hide_index=True, use_container_width=True)

# ---------- Cook a dish tab ----------
with tab_cook:
    st.subheader("Cook a dish")
    st.caption("Tell me what you want to make. I build the shopping list from this store's catalogue, "
               "you untick what you already have at home, and everything goes into your cart in one tap.")
    done = st.session_state.pop("cook_msg", None)
    if done:
        st.success(done)

    with st.form("cook_form"):
        dish_text = st.text_input("What are you cooking?", placeholder="sambar, chicken biryani, avial, payasam",
                                  key="dish_text")
        st.form_submit_button("Show ingredients", use_container_width=True)
    servings = int(st.number_input("Servings (more people means more weight)", min_value=1, max_value=20, value=4,
                                   step=1, key="servings"))
    pick = st.selectbox("Or pick from the list (not needed if you type a dish above)",
                        ["Choose a dish"] + sorted(RECIPES), key="dish_pick")

    plan = None
    if dish_text.strip():
        name = match_recipe(dish_text)
        if name:
            plan = recipe_plan(name, servings)
            if name.lower() != dish_text.strip().lower():
                st.caption(f'You typed "{dish_text.strip()}", so showing the recipe for {name}.')
        else:
            plan = ai_plan(dish_text.strip(), servings)
            if plan is None:
                if not get_api_key():
                    why = "AI is not set up (no ANTHROPIC_API_KEY secret), so only built-in dishes can be planned."
                elif not st.session_state.get("use_ai"):
                    why = "AI suggestions are switched off in the sidebar."
                else:
                    why = f"AI could not plan this dish ({st.session_state.get('ai_error') or 'no matching products'})."
                plan = generic_plan(dish_text.strip())
                if plan:
                    st.info(why + " Showing the closest products in the store instead.")
                else:
                    st.warning(why + " No products in the store match this dish. Try a dish from the list below.")
    elif pick != "Choose a dish":
        plan = recipe_plan(pick, servings)

    if plan:
        rnd = st.session_state.get("cook_round", 0)
        st.markdown(f'<div class="aisle">Ingredients for {html.escape(plan["title"])}, {servings} servings · '
                    f'{ {"ai": "AI plan", "rules": "Recipe plan", "search": "Closest products"}[plan["source"]] }</div>', unsafe_allow_html=True)
        chosen_lines = []
        for it in plan["items"]:
            p, qty = it["p"], it["qty"]
            pid = p["product_id"]
            in_cart = pid in st.session_state.cart
            pw = pack_weight(p["pack_size"])
            total = f" = {fmt_amount(pw[0] * qty, pw[1])}" if pw and qty > 1 else ""
            label = f"{p['product_name']} · {p['pack_size']} × {qty}{total} · {rupees(line_cost(p, qty))}"
            if it.get("need"):
                label += f" · need about {it['need']}"
            if it["note"]:
                label += f" · {it['note']}"
            if in_cart:
                label += " · already in cart"
            if st.checkbox(label, value=not in_cart, key=f"cook_{rnd}_{plan['title']}_{servings}_{pid}"):
                chosen_lines.append((pid, qty, line_cost(p, qty)))

        if plan["missing"]:
            st.caption("Not stocked here: " + ", ".join(plan["missing"]))

        cost = sum(c for _, _, c in chosen_lines)
        room = budget - cart_total()
        r1, r2, r3 = st.columns(3)
        r1.metric("Selected items", len(chosen_lines))
        r2.metric("Cost", rupees(cost))
        r3.metric("Per serving", rupees(cost / servings) if chosen_lines else rupees(0))

        if chosen_lines and cost > room:
            st.error(f"This list costs {rupees(cost - room)} more than your remaining budget of {rupees(max(room, 0))}. "
                     "Untick items you already have, or raise your budget in the sidebar.")
        elif chosen_lines:
            st.caption(f"You will have {rupees(room - cost)} left after adding these.")

        if st.button(f"Add {len(chosen_lines)} items to cart", type="primary", key="cook_add",
                     disabled=not chosen_lines or cost > room):
            for pid, qty, _ in chosen_lines:
                st.session_state.cart[pid] = min(st.session_state.cart.get(pid, 0) + qty, 99)
            st.session_state.cook_round = rnd + 1
            st.session_state.cook_msg = f"Added {len(chosen_lines)} ingredients for {plan['title']} to your cart."
            st.rerun()

# ---------- Ask AI ----------
with tab_chat:
    st.subheader("Ask the shopping assistant")
    st.caption("Ask what you need for a dish, or for product ideas. For example: what do I need to make pasta?")
    note = st.session_state.pop("chat_note", None)
    if note:
        (st.success if note[0] == "ok" else st.error)(note[1])
    for i, m in enumerate(st.session_state.chat):
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            todo = [pid for pid in m.get("pids", []) if pid not in st.session_state.cart and product_by_id(pid) is not None]
            if todo:
                cost = sum(line_cost(product_by_id(pid), 1) for pid in todo)
                st.button(f"Add {len(todo)} items to cart ({rupees(cost)})", key=f"chat_add_{i}",
                          on_click=add_many, args=(todo,))
    question = st.chat_input("Ask about a dish or a product", key="chat_in")
    if question:
        st.session_state.chat.append({"role": "user", "content": question})
        with st.spinner("Thinking..."):
            reply, pids = chat_reply(st.session_state.chat)
        st.session_state.chat.append({"role": "assistant", "content": reply, "pids": pids})
        st.rerun()
    if st.session_state.chat and st.button("Clear chat", key="chat_clear"):
        st.session_state.chat = []
        st.rerun()

# ---------- Catalogue ----------
with tab_catalogue:
    st.subheader("Product catalogue")
    display_cols = ["product_id", "product_name", "brand", "category", "aisle", "pack_size", "price", "unit_price", "stock_status"]
    st.dataframe(catalogue[display_cols], use_container_width=True, hide_index=True)
    st.download_button(
        "Download product catalogue (CSV)",
        data=catalogue[display_cols].drop(columns=["unit_price"]).to_csv(index=False).encode("utf-8"),
        file_name="smartcart_product_catalogue.csv",
        mime="text/csv",
    )
    st.markdown("**CSV columns:** `product_id`, `product_name`, `brand`, `category`, `pack_size`, `price`, `stock_status`, and optional `aisle`.")
    st.caption("For exact unit-price comparisons across different pack sizes, store a normalised pack quantity and unit in the database.")

# ---------- How it works ----------
with tab_how:
    st.subheader("Project overview")
    st.markdown("""
    **Problem:** Shoppers lose track of their total while choosing among many products, brands and pack sizes.

    **Solution:** SmartCart combines a full supermarket catalogue, budget monitoring, a live bill, stock visibility, lower-price alternatives and an aisle-ordered picking list.

    **Core workflow**
    1. Set a budget in the sidebar.
    2. Search, pick a category, or type a product code to find items.
    3. Add items with the Add and plus buttons.
    4. The live bill and remaining budget update straight away.
    5. If an item would exceed the budget, the app suggests cheaper in-stock options from the same category.
    6. Open My cart to see items grouped by aisle, then download the bill or picking list.

    **Cook a dish:** type a dish such as sambar or chicken biryani, choose the number of servings, and SmartCart builds the full ingredient list from the catalogue with the cost per serving. Untick what you already have, check it against your budget, and add everything to the cart in one tap. With an API key, AI can plan dishes that are not in the built-in list.

    **Smart suggestions:** when you add an item, SmartCart suggests what usually goes with it, for example butter, peanut butter, chocolate spread and jam for bread, or pens and pencils for a notebook. With an API key it uses Claude to choose from your catalogue. Without one it uses built-in pairing rules.

    **Bill payment:** choose Wallet, UPI, Card or Cash on delivery, pay, and get a bill you can view and download. Only the wallet and orders are saved; UPI and card are demo payments.

    **Accounts and wallet:** sign up to save your cart, budget, wallet and order history. Add money to the wallet in the sidebar and pay from it at checkout, so there is no separate payment step. The wallet in this demo is simulated.

    **Family plan:** create a family and share the code. Members can switch to a shared cart and wallet.

    **Calorie alerts:** snacks, sweets and sugary drinks are counted in kcal. You get an alert as your cart nears your limit and another when it passes it.

    **Offers:** Buy 1 Get 1 Free and percentage discounts are applied to the bill automatically. A "Deals for you" row shows discounted items that go with your cart (AI picks when an API key is set), and the Offers filter lists every deal.

    **Usual items:** after your first order, items you buy regularly are suggested again when they are missing from your cart.

    **Future scope:** barcode/RFID scanning, supermarket inventory integration, expiry-date alerts and demand prediction.

    **Limitations of this demo:** prices and stock levels are sample data, and it is not connected to a live supermarket or physical cart hardware. Cheaper alternatives are matched by similar product names, so check pack size and suitability before switching.
    """)
    st.info("For a real deployment, use verified store prices and update stock from a supermarket database or API.")

# ---------- Save progress ----------
persist()

  
        
               
   
    

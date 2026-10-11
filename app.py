import streamlit as st
import pandas as pd
from io import BytesIO

st.set_page_config(
    page_title="SmartCart | Smart Shopping Assistant",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------- Demo catalogue (sample prices; replace with verified store prices) ----------
DEFAULT_PRODUCTS = [
    {"product_id":"MILK001","product_name":"Taaza Toned Milk","brand":"Amul","category":"Milk & Dairy","pack_size":"1 L","price":68.00,"stock_status":"In stock"},
    {"product_id":"MILK002","product_name":"Toned Milk","brand":"Milma","category":"Milk & Dairy","pack_size":"1 L","price":56.00,"stock_status":"In stock"},
    {"product_id":"MILK003","product_name":"Full Cream Milk","brand":"Amul","category":"Milk & Dairy","pack_size":"1 L","price":76.00,"stock_status":"In stock"},
    {"product_id":"BISC001","product_name":"Marie Gold Biscuits","brand":"Britannia","category":"Biscuits & Cookies","pack_size":"250 g","price":38.00,"stock_status":"In stock"},
    {"product_id":"BISC002","product_name":"Marie Biscuits","brand":"Parle","category":"Biscuits & Cookies","pack_size":"250 g","price":32.00,"stock_status":"In stock"},
    {"product_id":"BISC003","product_name":"Dark Fantasy Cookies","brand":"Sunfeast","category":"Biscuits & Cookies","pack_size":"300 g","price":95.00,"stock_status":"Low stock"},
    {"product_id":"CHOC001","product_name":"Dairy Milk Chocolate","brand":"Cadbury","category":"Chocolates","pack_size":"80 g","price":50.00,"stock_status":"In stock"},
    {"product_id":"CHOC002","product_name":"KitKat","brand":"Nestlé","category":"Chocolates","pack_size":"75 g","price":45.00,"stock_status":"In stock"},
    {"product_id":"CHOC003","product_name":"Munch","brand":"Nestlé","category":"Chocolates","pack_size":"42 g","price":20.00,"stock_status":"In stock"},
    {"product_id":"ATTA001","product_name":"Whole Wheat Atta","brand":"Aashirvaad","category":"Flour & Baking","pack_size":"5 kg","price":285.00,"stock_status":"In stock"},
    {"product_id":"ATTA002","product_name":"Chakki Fresh Atta","brand":"Pillsbury","category":"Flour & Baking","pack_size":"5 kg","price":270.00,"stock_status":"In stock"},
    {"product_id":"MAID001","product_name":"Maida","brand":"Generic Store Brand","category":"Flour & Baking","pack_size":"1 kg","price":48.00,"stock_status":"In stock"},
    {"product_id":"COFF001","product_name":"Classic Coffee","brand":"Nescafé","category":"Coffee & Beverages","pack_size":"100 g","price":185.00,"stock_status":"In stock"},
    {"product_id":"COFF002","product_name":"Instant Coffee","brand":"Bru","category":"Coffee & Beverages","pack_size":"100 g","price":165.00,"stock_status":"In stock"},
    {"product_id":"PROT001","product_name":"Protein Bar - Chocolate","brand":"Yoga Bar","category":"Protein Bars & Snacks","pack_size":"60 g","price":60.00,"stock_status":"In stock"},
    {"product_id":"PROT002","product_name":"Protein Bar - Peanut","brand":"RiteBite","category":"Protein Bars & Snacks","pack_size":"50 g","price":55.00,"stock_status":"Low stock"},
    {"product_id":"RICE001","product_name":"Raw Rice","brand":"Store Brand","category":"Rice & Grains","pack_size":"5 kg","price":310.00,"stock_status":"In stock"},
    {"product_id":"RICE002","product_name":"Matta Rice","brand":"Store Brand","category":"Rice & Grains","pack_size":"5 kg","price":340.00,"stock_status":"In stock"},
    {"product_id":"SUGR001","product_name":"White Sugar","brand":"Store Brand","category":"Essentials","pack_size":"1 kg","price":48.00,"stock_status":"In stock"},
    {"product_id":"OIL001","product_name":"Sunflower Oil","brand":"Fortune","category":"Essentials","pack_size":"1 L","price":145.00,"stock_status":"In stock"},
]

REQUIRED_COLUMNS = [
    "product_id", "product_name", "brand", "category",
    "pack_size", "price", "stock_status"
]

def prepare_catalogue(df):
    df = df.copy()
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["product_id"] = df["product_id"].astype(str).str.strip()
    df["product_name"] = df["product_name"].astype(str).str.strip()
    df["brand"] = df["brand"].astype(str).str.strip()
    df["category"] = df["category"].astype(str).str.strip()
    df["pack_size"] = df["pack_size"].astype(str).str.strip()
    df["stock_status"] = df["stock_status"].astype(str).str.strip().replace("", "In stock")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["price"])
    df = df[(df["price"] >= 0) & (df["product_id"] != "") & (df["product_name"] != "")]
    df = df.drop_duplicates(subset=["product_id"], keep="first")
    # Unit price is informative; pack sizes remain visible because units vary by category.
    df["unit_price"] = df.apply(
        lambda r: f"₹{r['price']:.2f} / {r['pack_size']}" if r["pack_size"] else f"₹{r['price']:.2f} / pack",
        axis=1
    )
    return df.reset_index(drop=True)

def rupees(amount):
    return f"₹{amount:,.2f}"

if "catalogue" not in st.session_state:
    st.session_state.catalogue = prepare_catalogue(pd.DataFrame(DEFAULT_PRODUCTS))
if "cart" not in st.session_state:
    st.session_state.cart = {}
if "budget" not in st.session_state:
    st.session_state.budget = 2000.0

catalogue = st.session_state.catalogue

# ---------- Styling ----------
st.markdown("""
<style>
    .stApp { background: linear-gradient(180deg, #fff9fc 0%, #f7fbff 100%); }
    [data-testid="stSidebar"] { background: #fff0f6; }
    .hero {
        padding: 1.35rem 1.5rem; border-radius: 20px;
        background: linear-gradient(110deg, #ffe0ef 0%, #e4f4ff 100%);
        border: 1px solid #f0d7e5; margin-bottom: 1rem;
    }
    .hero h1 { margin: 0; color: #34243b; }
    .hero p { margin: .35rem 0 0 0; color: #5f5366; }
    div[data-testid="stMetric"] {
        background: rgba(255,255,255,.86); border: 1px solid #eadfe9;
        padding: 14px 16px; border-radius: 16px;
    }
    .small-note { color: #6b6470; font-size: .88rem; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <h1>🛒 SmartCart</h1>
  <p>Your budget-aware shopping assistant · Compare brands · Track every rupee</p>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Shopping setup")
    budget = st.number_input("Set your budget (₹)", min_value=1.0, max_value=1000000.0,
                             value=float(st.session_state.budget), step=100.0)
    st.session_state.budget = budget
    st.caption("Sample catalogue for demonstration. Prices are illustrative, not live store prices.")
    st.divider()
    st.subheader("📦 Catalogue tools")
    uploaded = st.file_uploader("Upload your product CSV", type=["csv"])
    if uploaded is not None:
        try:
            incoming = prepare_catalogue(pd.read_csv(uploaded))
            if incoming.empty:
                st.error("No valid product rows found. Check your CSV columns and prices.")
            else:
                st.session_state.catalogue = incoming
                catalogue = incoming
                st.success(f"Loaded {len(incoming)} products.")
        except Exception as exc:
            st.error(f"Could not read CSV: {exc}")
    if st.button("Restore demo products", use_container_width=True):
        st.session_state.catalogue = prepare_catalogue(pd.DataFrame(DEFAULT_PRODUCTS))
        st.session_state.cart = {}
        st.rerun()

# Ensure cart has only products still present in current catalogue.
valid_ids = set(catalogue["product_id"].tolist())
st.session_state.cart = {pid: qty for pid, qty in st.session_state.cart.items() if pid in valid_ids}

def product_by_id(pid):
    rows = catalogue[catalogue["product_id"] == pid]
    return None if rows.empty else rows.iloc[0]

def cart_total():
    total = 0.0
    for pid, qty in st.session_state.cart.items():
        p = product_by_id(pid)
        if p is not None:
            total += float(p["price"]) * int(qty)
    return total

total = cart_total()
remaining = budget - total
percent = min(total / budget, 1.0) if budget else 0.0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Shopping budget", rupees(budget))
m2.metric("Live bill", rupees(total))
m3.metric("Remaining", rupees(remaining), delta=rupees(remaining) + " left" if remaining >= 0 else rupees(abs(remaining)) + " over")
m4.metric("Items in cart", sum(st.session_state.cart.values()))
st.progress(percent, text=f"{percent*100:.0f}% of your budget used")

if total > budget:
    st.error(f"Your cart is {rupees(total-budget)} over budget. Remove items or choose suitable alternatives.")
elif remaining <= budget * 0.10:
    st.warning("You're close to your budget limit. Check the total before adding more.")
else:
    st.success("You're within your shopping budget.")

tab_shop, tab_cart, tab_catalogue, tab_how = st.tabs(
    ["🛍️ Shop & compare", "🧾 My live bill", "📋 Product catalogue", "💡 How it works"]
)

with tab_shop:
    st.subheader("Find your products")
    f1, f2, f3 = st.columns([2, 1, 1])
    with f1:
        search = st.text_input("Search by product, brand or product ID", placeholder="e.g. milk, Amul, COFF001")
    with f2:
        categories = ["All categories"] + sorted(catalogue["category"].dropna().astype(str).unique().tolist())
        chosen_category = st.selectbox("Category", categories)
    with f3:
        stock_filter = st.selectbox("Stock status", ["All", "In stock only", "Low stock only"])

    shown = catalogue.copy()
    if search:
        searchable = shown[["product_id", "product_name", "brand", "category"]].astype(str).agg(" ".join, axis=1)
        shown = shown[searchable.str.contains(search, case=False, na=False)]
    if chosen_category != "All categories":
        shown = shown[shown["category"] == chosen_category]
    if stock_filter == "In stock only":
        shown = shown[shown["stock_status"].str.lower().eq("in stock")]
    elif stock_filter == "Low stock only":
        shown = shown[shown["stock_status"].str.lower().eq("low stock")]

    st.caption(f"{len(shown)} product(s) found")
    if shown.empty:
        st.info("No products match these filters. Try another search.")
    else:
        for _, p in shown.iterrows():
            with st.container(border=True):
                left, mid, right = st.columns([3, 2, 1.5])
                with left:
                    st.markdown(f"**{p['product_name']}**")
                    st.caption(f"{p['brand']} · {p['category']}")
                    st.caption(f"ID: `{p['product_id']}` · Pack: {p['pack_size']}")
                with mid:
                    st.markdown(f"### {rupees(float(p['price']))}")
                    st.caption(f"Unit/pack price: {p['unit_price']}")
                    status = str(p["stock_status"])
                    if status.lower() == "in stock":
                        st.markdown("🟢 In stock")
                    elif status.lower() == "low stock":
                        st.markdown("🟠 Low stock")
                    else:
                        st.markdown(f"⚪ {status}")
                with right:
                    qty = st.number_input("Qty", min_value=1, max_value=99, value=1, key=f"qty_{p['product_id']}")
                    if st.button("Add to cart", key=f"add_{p['product_id']}", use_container_width=True,
                                  disabled=str(p["stock_status"]).lower() not in ("in stock", "low stock")):
                        proposed = cart_total() + float(p["price"]) * int(qty)
                        if proposed <= budget:
                            st.session_state.cart[p["product_id"]] = st.session_state.cart.get(p["product_id"], 0) + int(qty)
                            st.rerun()
                        else:
                            st.warning(f"Adding this would make your bill {rupees(proposed)}, exceeding your budget by {rupees(proposed-budget)}.")
                            same_cat = catalogue[
                                (catalogue["category"] == p["category"]) &
                                (catalogue["price"] < p["price"]) &
                                (catalogue["stock_status"].str.lower().isin(["in stock", "low stock"]))
                            ].sort_values("price")
                            affordable = same_cat[
                                same_cat.apply(lambda alt: cart_total() + float(alt["price"]) * int(qty) <= budget, axis=1)
                            ]
                            if not affordable.empty:
                                st.markdown("**Cheaper alternatives in the same category:**")
                                for _, alt in affordable.head(3).iterrows():
                                    st.write(f"• {alt['brand']} — {alt['product_name']} ({alt['pack_size']}): **{rupees(float(alt['price']))}** · save {rupees(float(p['price'])-float(alt['price']))} per item")
                            else:
                                st.info("No cheaper in-stock option in this category fits your remaining budget. You can lower the quantity or remove an item.")

with tab_cart:
    st.subheader("Your shopping bill")
    if not st.session_state.cart:
        st.info("Your cart is empty. Add products from the Shop & compare tab.")
    else:
        bill_rows = []
        for pid, qty in list(st.session_state.cart.items()):
            p = product_by_id(pid)
            if p is None:
                continue
            bill_rows.append({
                "Product ID": p["product_id"],
                "Product": p["product_name"],
                "Brand": p["brand"],
                "Pack size": p["pack_size"],
                "Qty": int(qty),
                "Unit price": float(p["price"]),
                "Line total": float(p["price"]) * int(qty),
            })
        bill_df = pd.DataFrame(bill_rows)
        st.dataframe(
            bill_df.style.format({"Unit price": "₹{:.2f}", "Line total": "₹{:.2f}"}),
            use_container_width=True, hide_index=True
        )
        controls = st.columns([2, 2, 2, 2])
        for idx, (pid, qty) in enumerate(list(st.session_state.cart.items())):
            p = product_by_id(pid)
            if p is None:
                continue
            with controls[idx % 4]:
                st.caption(f"{p['product_name']} · current qty {qty}")
                q_new = st.number_input("Change quantity", min_value=0, max_value=99, value=int(qty), key=f"edit_{pid}")
                if st.button("Update", key=f"update_{pid}", use_container_width=True):
                    proposed_cart = dict(st.session_state.cart)
                    if q_new == 0:
                        proposed_cart.pop(pid, None)
                    else:
                        proposed_cart[pid] = int(q_new)
                    proposed_total = sum(float(product_by_id(k)["price"]) * v for k, v in proposed_cart.items() if product_by_id(k) is not None)
                    # Allow cart edits even if over budget, so user can reduce it; warn if still over.
                    st.session_state.cart = proposed_cart
                    st.rerun()
        st.divider()
        total = cart_total()
        remaining = budget - total
        c1, c2 = st.columns(2)
        c1.metric("Total bill", rupees(total))
        c2.metric("Budget remaining", rupees(remaining))
        if remaining < 0:
            st.error(f"Over budget by {rupees(abs(remaining))}. Use alternatives or reduce quantities.")
        elif remaining == 0:
            st.success("Exactly within budget!")
        else:
            st.info(f"You can still spend {rupees(remaining)}.")
        csv_data = bill_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download bill as CSV", data=csv_data, file_name="smartcart_bill.csv", mime="text/csv")
        if st.button("Clear entire cart", type="secondary"):
            st.session_state.cart = {}
            st.rerun()

with tab_catalogue:
    st.subheader("Product catalogue")
    display_cols = ["product_id", "product_name", "brand", "category", "pack_size", "price", "unit_price", "stock_status"]
    st.dataframe(catalogue[display_cols], use_container_width=True, hide_index=True)
    st.download_button(
        "⬇️ Download product catalogue template",
        data=catalogue[display_cols].to_csv(index=False).encode("utf-8"),
        file_name="smartcart_product_catalogue.csv",
        mime="text/csv"
    )
    st.markdown("**Required CSV columns:** `product_id`, `product_name`, `brand`, `category`, `pack_size`, `price`, `stock_status`.")
    st.caption("The app calculates unit/pack price display from price and pack size. For exact unit-price comparisons across different pack sizes, store normalized pack quantity and measurement unit in the database.")

with tab_how:
    st.subheader("Presentation-ready project overview")
    st.markdown("""
    **Problem:** Shoppers may lose track of their total while choosing among brands and pack sizes.

    **Solution:** SmartCart combines budget monitoring, a live bill, product search, stock visibility and lower-price alternatives.

    **Core workflow**
    1. Set a budget.
    2. Search or filter products by category, brand or product ID.
    3. Add a product and quantity.
    4. The app calculates the bill and remaining budget.
    5. If a product would exceed the budget, the app suggests lower-priced options in the same category.
    6. Review, edit, remove or download the bill.

    **Future scope:** barcode/RFID scanning, supermarket inventory integration, expiry-date alerts, smart shopping sequence and demand prediction.

    **Limitations of this demo:** prices and stock levels are sample data. It is not connected to a live supermarket or physical cart hardware. Product comparisons should consider pack size and product suitability, not price alone.
    """)
    st.info("For a real deployment, use verified store prices and update stock from a supermarket database/API.")

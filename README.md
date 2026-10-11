# SmartCart — Smart Shopping Cart Web App

## Features
- Budget entry in Indian rupees
- Live bill and remaining-budget tracker
- Product catalogue with product ID, product name, brand, category, pack size, price, unit/pack price, and stock status
- Search by product, brand, or ID
- Filter by category and stock status
- Add products and quantities to cart
- Warns when a proposed addition exceeds the budget
- Suggests lower-priced alternatives in the same category
- Edit quantities, remove items, clear cart, and download bill as CSV
- Upload a custom product catalogue CSV
- Presentation-ready project overview tab

## Run locally
1. Install Python 3.10 or newer.
2. Open a terminal in this folder.
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run:
   ```bash
   streamlit run app.py
   ```

## Product CSV format
Required columns:
`product_id, product_name, brand, category, pack_size, price, stock_status`

Example:
```csv
product_id,product_name,brand,category,pack_size,price,stock_status
MILK001,Toned Milk,Amul,Milk & Dairy,1 L,68,In stock
MILK002,Toned Milk,Milma,Milk & Dairy,1 L,56,In stock
```

## Important
The included products, prices, and stock statuses are illustrative demo data only. Verify prices and availability before using the app in a real supermarket. This prototype is not connected to live inventory, barcode/RFID devices, or payment systems.

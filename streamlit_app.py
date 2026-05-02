import streamlit as st
import pandas as pd
import io
import os
import re
from apify_client import ApifyClient
from datetime import datetime

# -----------------------------
# CONFIG & STYLING
# -----------------------------
st.set_page_config(page_title="Stock Analyst", page_icon="📈", layout="wide")

# Premium Dark Mode CSS
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    .stButton>button {
        width: 100%;
        border-radius: 10px;
        height: 3em;
        background-color: #4CAF50;
        color: white;
        font-weight: bold;
        border: none;
        transition: 0.3s;
    }
    .stButton>button:hover {
        background-color: #45a049;
        box-shadow: 0 4px 15px rgba(76, 175, 80, 0.3);
    }
    .stTextInput>div>div>input {
        border-radius: 10px;
    }
    .header-text {
        font-family: 'Inter', sans-serif;
        color: #f0f2f6;
        text-align: center;
        padding-bottom: 20px;
    }
    .card {
        background: rgba(255, 255, 255, 0.05);
        padding: 20px;
        border-radius: 15px;
        border: 1px solid rgba(255, 255, 255, 0.1);
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

# -----------------------------
# CORE LOGIC (From app_v1.py)
# -----------------------------
# Use st.secrets for Streamlit Cloud, fallback to os.getenv for local .env
API_TOKEN = st.secrets.get("API_TOKEN", os.getenv("API_TOKEN"))
ACTOR_ID = st.secrets.get("ACTOR_ID", os.getenv("ACTOR_ID"))

# Reusable date parser for keys like "Mar 2025", "Dec 2021"
_MONTH_MAP = {"Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,"Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12}

def parse_date_key(key):
    """Parse 'Mar 2025' → (2025, 3) for sorting. Returns (0, 0) if unparseable."""
    match = re.match(r'(\w+)\s+(\d{4})', key)
    if match:
        month_str, year_str = match.groups()
        return (int(year_str), _MONTH_MAP.get(month_str, 0))
    return (0, 0)

def safe_div(a, b):
    try:
        if a is None or b in [0, None]:
            return None
        return round(a / b, 3)
    except:
        return None

def extract_metric(data_list, metric):
    for item in data_list:
        if item.get("Metric") == metric:
            return item
    return None

def clean_metric_dict(metric_dict):
    """Normalize keys like 'Mar 2023 \n 15m' to 'Mar 2023' so different tables align perfectly."""
    if not metric_dict:
        return metric_dict
    cleaned = {}
    for k, v in metric_dict.items():
        if k == "Metric":
            cleaned[k] = v
        else:
            match = re.match(r'\s*(\w+)\s+(\d{4})', k)
            if match:
                cleaned[f"{match.group(1)} {match.group(2)}"] = v
            else:
                cleaned[k] = v
    return cleaned

def fetch_data(url):
    client = ApifyClient(API_TOKEN)
    run_input = {"mode": "getstockdetails", "url": url}
    run = client.actor(ACTOR_ID).call(run_input=run_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return items[0]

def build_row(data):
    company = data.get("company_name", "Unknown")
    
    # Balance Sheet for DE Ratio
    bs = data.get("balance_sheet", [])
    borrow = clean_metric_dict(extract_metric(bs, "Borrowings"))
    equity_cap = clean_metric_dict(extract_metric(bs, "Equity Capital"))
    reserves = clean_metric_dict(extract_metric(bs, "Reserves"))

    de_ratio = None
    try:
        if borrow:
            year_keys = [k for k in borrow.keys() if k != "Metric"]
            latest_year_de = sorted(year_keys, key=parse_date_key, reverse=True)[0]
            debt = borrow.get(latest_year_de)
            equity = (equity_cap.get(latest_year_de) or 0) + (reserves.get(latest_year_de) or 0)
            de_ratio = safe_div(debt, equity)
    except:
        pass

    # Cash Flow & P&L
    cfo = clean_metric_dict(extract_metric(data.get("cash_flow", []), "Cash from Operating Activity"))
    op = clean_metric_dict(extract_metric(data.get("profit_and_loss", {}).get("annual_data", []), "Operating Profit"))
    np_ = clean_metric_dict(extract_metric(data.get("profit_and_loss", {}).get("annual_data", []), "Net Profit"))

    if not cfo:
        raise ValueError(f"Cash Flow metric not found for {company}")

    years = sorted([k for k in cfo.keys() if k != "Metric"], key=parse_date_key, reverse=True)[:5]
    
    cf_vals = [cfo.get(y) for y in years]
    op_vals = [op.get(y) for y in years] if op else [None]*5
    np_vals = [np_.get(y) for y in years] if np_ else [None]*5

    # Ratios — only calculate if ALL required values are available (not None)
    cf_np_1y = safe_div(cf_vals[0], np_vals[0]) if (len(cf_vals) > 0 and cf_vals[0] is not None and np_vals[0] is not None) else None
    cf_op_1y = safe_div(cf_vals[0], op_vals[0]) if (len(cf_vals) > 0 and cf_vals[0] is not None and op_vals[0] is not None) else None

    # Helper for sum-based ratios — ONLY when all values in the slice are present
    def get_avg_ratio(vals_a, vals_b, count):
        slice_a = vals_a[:count]
        slice_b = vals_b[:count]
        # Need exactly 'count' values and none of them should be None
        if len(slice_a) < count or len(slice_b) < count:
            return None
        if any(v is None for v in slice_a) or any(v is None for v in slice_b):
            return None
        a_avg = sum(slice_a) / count
        b_avg = sum(slice_b) / count
        return safe_div(a_avg, b_avg)

    cf_np_3y = get_avg_ratio(cf_vals, np_vals, 3)
    cf_op_3y = get_avg_ratio(cf_vals, op_vals, 3)
    cf_np_5y = get_avg_ratio(cf_vals, np_vals, 5)
    cf_op_5y = get_avg_ratio(cf_vals, op_vals, 5)

    # Quarterly — pick the LATEST Jun and Sep quarters
    q_op = clean_metric_dict(extract_metric(data.get("quarters", []), "Operating Profit"))
    q_np = clean_metric_dict(extract_metric(data.get("quarters", []), "Net Profit"))
    op_jun = op_sep = np_jun = np_sep = None

    if q_op:
        q_keys = [k for k in q_op.keys() if k != "Metric"]
        jun_keys = sorted([k for k in q_keys if "Jun" in k], key=parse_date_key, reverse=True)
        sep_keys = sorted([k for k in q_keys if "Sep" in k], key=parse_date_key, reverse=True)
        
        if jun_keys:
            latest_jun = jun_keys[0]
            op_jun = q_op[latest_jun]
            np_jun = q_np.get(latest_jun) if q_np else None
        if sep_keys:
            latest_sep = sep_keys[0]
            op_sep = q_op[latest_sep]
            np_sep = q_np.get(latest_sep) if q_np else None

    # Build Row
    row = {
        "DE_Ratio": de_ratio,
        "Stock": company,
        "CF/NP_1Y": cf_np_1y,
        "CF/NP_3Y": cf_np_3y,
        "CF/NP_5Y": cf_np_5y,
        "CF/OP_1Y": cf_op_1y,
        "CF/OP_3Y": cf_op_3y,
        "CF/OP_5Y": cf_op_5y,
    }

    for i, y in enumerate(years):
        row[f"CF_{y}"] = cf_vals[i]
    for i, y in enumerate(years):
        row[f"NP_{y}"] = np_vals[i]
    for i, y in enumerate(years):
        row[f"OP_{y}"] = op_vals[i]

    row.update({
        "OP_Jun": op_jun, "OP_Sep": op_sep,
        "NP_Jun": np_jun, "NP_Sep": np_sep
    })

    return row

# -----------------------------
# APP UI
# -----------------------------
def main():
    st.markdown("<h1 class='header-text'>🚀Stock Intelligence</h1>", unsafe_allow_html=True)
    
    # Initialize session state for the queue
    if 'url_queue' not in st.session_state:
        st.session_state.url_queue = []
    if 'processed_data' not in st.session_state:
        st.session_state.processed_data = pd.DataFrame()

    with st.sidebar:
        st.header("📂 Data Management")
        uploaded_file = st.file_uploader("1. Upload base Sheet (Optional)", type=["xlsx", "csv"])
        
        if uploaded_file and st.session_state.processed_data.empty:
            try:
                if uploaded_file.name.endswith(".csv"):
                    st.session_state.processed_data = pd.read_csv(uploaded_file)
                else:
                    st.session_state.processed_data = pd.read_excel(uploaded_file)
                st.success("Base sheet loaded!")
            except Exception as e:
                st.error(f"Error loading base sheet: {e}")

        st.divider()
        st.subheader("📊 Bulk Upload")
        stock_list_file = st.file_uploader("2. Upload Screener Stock List", type=["xlsx", "csv"])
        
        if stock_list_file:
            try:
                # Support both CSV and Excel
                if stock_list_file.name.endswith(".csv"):
                    df_stocks = pd.read_csv(stock_list_file)
                else:
                    df_stocks = pd.read_excel(stock_list_file)
                
                # Clean column names
                df_stocks.columns = [c.strip() for c in df_stocks.columns]
                
                if "NSE Code" in df_stocks.columns or "BSE Code" in df_stocks.columns:
                    st.write("---")
                    st.write("🎯 **Select Stocks to Add**")
                    
                    # Selection logic - robust against missing 'Name' column
                    if "Name" in df_stocks.columns:
                        display_col = "Name"
                    elif "NSE Code" in df_stocks.columns:
                        display_col = "NSE Code"
                    else:
                        display_col = "BSE Code"
                        
                    # Drop NA values to prevent selection errors
                    df_stocks[display_col] = df_stocks[display_col].fillna("Unknown").astype(str)
                    all_names = df_stocks[display_col].tolist()
                    
                    select_all = st.checkbox("Select All Stocks", value=True)
                    
                    if select_all:
                        selected_names = st.multiselect("Stocks", all_names, default=all_names)
                    else:
                        selected_names = st.multiselect("Stocks", all_names)

                    if st.button("➕ Add Selected to Queue"):
                        count = 0
                        for name in selected_names:
                            row = df_stocks[df_stocks[display_col] == name].iloc[0]
                            nse = str(row.get("NSE Code", "")).strip()
                            bse = str(row.get("BSE Code", "")).strip()
                            
                            # Build URL
                            if nse and nse.lower() != "nan" and nse != "":
                                url = f"https://www.screener.in/company/{nse}/consolidated/"
                            elif bse and bse.lower() != "nan" and bse != "":
                                url = f"https://www.screener.in/company/{bse}/"
                            else:
                                continue
                                
                            if url not in st.session_state.url_queue:
                                st.session_state.url_queue.append(url)
                                count += 1
                        st.success(f"Added {count} stocks to queue!")
                        st.rerun()
                else:
                    st.error("File must have 'NSE Code' or 'BSE Code' columns.")
            except Exception as e:
                st.error(f"Error reading stock list: {e}")

        st.divider()
        if st.button("🗑️ Clear All Data & Queue"):
            st.session_state.url_queue = []
            st.session_state.processed_data = pd.DataFrame()
            st.rerun()

    # URL Input Section (Using a form to allow clear_on_submit)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    with st.form("add_to_queue_form", clear_on_submit=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            new_url = st.text_input("Add Screener.in URL to queue", placeholder="https://www.screener.in/company/TCS/consolidated/")
        with col2:
            st.write(" ") # Padding
            add_btn = st.form_submit_button("➕ Add to List")
            
        if add_btn:
            if new_url.strip():
                st.session_state.url_queue.append(new_url.strip())
                st.rerun() # Refresh to show updated queue
            else:
                st.warning("Enter a URL first.")
    st.markdown("</div>", unsafe_allow_html=True)

    # Queue Display
    if st.session_state.url_queue:
        st.subheader(f"📋 Processing Queue ({len(st.session_state.url_queue)} companies)")
        cols = st.columns([4, 1])
        for idx, url in enumerate(st.session_state.url_queue):
            with st.container():
                c1, c2 = st.columns([9, 1])
                c1.code(url)
                if c2.button("❌", key=f"del_{idx}"):
                    st.session_state.url_queue.pop(idx)
                    st.rerun()
        
        st.divider()
        
        # Process Button
        if st.button("🚀 Start Sequential Processing", use_container_width=True):
            total_urls = len(st.session_state.url_queue)
            st.warning("⚠️ **Processing started... Please do not close the app or stop the process in between.**")
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            success_count = 0
            error_count = 0
            new_rows = []

            for i, url in enumerate(st.session_state.url_queue):
                current_count = i + 1
                status_text.markdown(f"🔍 **Processing ({current_count}/{total_urls}):** `{url}`")
                
                try:
                    data = fetch_data(url)
                    row = build_row(data)
                    # Check for duplicate stock
                    stock_name = row.get('Stock', '')
                    if not st.session_state.processed_data.empty and stock_name in st.session_state.processed_data['Stock'].values:
                        st.warning(f"⚠️ Skipped duplicate: **{stock_name}** (already exists)")
                    else:
                        new_rows.append(row)
                        success_count += 1
                        st.success(f"✅ Extracted: **{stock_name}**")
                except Exception as e:
                    error_count += 1
                    st.error(f"❌ Failed for `{url}`: {str(e)}")
                
                # Update progress
                progress_bar.progress(current_count / total_urls)

            # Finalize Data
            if new_rows:
                df_new = pd.DataFrame(new_rows)
                df_final = pd.concat([st.session_state.processed_data, df_new], ignore_index=True)
                
                # --- COLUMN REORDERING LOGIC ---
                # 1. Separate columns into categories
                all_cols = list(df_final.columns)
                static_cols = ["DE_Ratio", "Stock", "CF/NP_1Y", "CF/NP_3Y", "CF/NP_5Y", "CF/OP_1Y", "CF/OP_3Y", "CF/OP_5Y"]
                quarterly_cols = ["OP_Jun", "OP_Sep", "NP_Jun", "NP_Sep"]
                
                # 2. Identify dynamic year columns (CF_..., NP_..., OP_...)
                # IMPORTANT: Exclude quarterly columns to avoid duplicates
                cf_cols = [c for c in all_cols if c.startswith("CF_") and c not in static_cols and c not in quarterly_cols]
                np_cols = [c for c in all_cols if c.startswith("NP_") and c not in static_cols and c not in quarterly_cols]
                op_cols = [c for c in all_cols if c.startswith("OP_") and c not in static_cols and c not in quarterly_cols]
                
                # Smart date-based sorting using reusable helper
                def col_date_sort_key(col_name):
                    parts = col_name.split("_", 1)
                    return parse_date_key(parts[1]) if len(parts) >= 2 else (0, 0)
                
                cf_cols = sorted(cf_cols, key=col_date_sort_key, reverse=True)
                np_cols = sorted(np_cols, key=col_date_sort_key, reverse=True)
                op_cols = sorted(op_cols, key=col_date_sort_key, reverse=True)
                
                # 3. Handle any other columns that might have appeared
                all_dynamic = set(cf_cols + np_cols + op_cols)
                used_cols = set(static_cols) | all_dynamic | set(quarterly_cols)
                other_cols = [c for c in all_cols if c not in used_cols]
                
                # 4. Final Order
                final_column_order = [c for c in static_cols if c in all_cols] + \
                                     cf_cols + np_cols + op_cols + \
                                     [c for c in quarterly_cols if c in all_cols] + \
                                     other_cols
                
                df_final = df_final[final_column_order]
                # -------------------------------

                st.session_state.processed_data = df_final
            
            # Clear queue after processing
            st.session_state.url_queue = []
            
            st.divider()
            st.balloons()
            st.success(f"🎊 Finished! Successfully processed: {success_count} | Errors: {error_count}")
            
            # Show download if we have data
            if not st.session_state.processed_data.empty:
                st.subheader("📊 Final Data Preview")
                st.dataframe(st.session_state.processed_data.tail(10), use_container_width=True)

                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    st.session_state.processed_data.to_excel(writer, index=False)
                
                st.download_button(
                    label="📥 Download Final Consolidated Excel",
                    data=output.getvalue(),
                    file_name=f"stock_analysis_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
    else:
        if not st.session_state.processed_data.empty:
            st.info("Queue is empty. You can download the current data below or add more companies.")
            st.dataframe(st.session_state.processed_data.tail(5), use_container_width=True)
            
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                st.session_state.processed_data.to_excel(writer, index=False)
            
            st.download_button(
                label="📥 Download Current Excel",
                data=output.getvalue(),
                file_name=f"stock_analysis_current.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.info("👋 Start by adding a company URL above or uploading an existing Excel file in the sidebar.")

if __name__ == "__main__":
    main()

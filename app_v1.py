import pandas as pd
import os
from dotenv import load_dotenv
from apify_client import ApifyClient

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
ACTOR_ID = os.getenv("ACTOR_ID")

if not API_TOKEN or not ACTOR_ID:
    raise ValueError("API_TOKEN or ACTOR_ID not found in .env file")


# -----------------------------
# HELPERS
# -----------------------------
def safe_div(a, b):
    try:
        if a is None or b in [0, None]:
            return None
        return round(a / b, 3)
    except:
        return None


def extract_metric(data_list, metric):
    for item in data_list:
        if item["Metric"] == metric:
            return item
    return None


# -----------------------------
# FETCH DATA
# -----------------------------
def fetch_data(url):
    client = ApifyClient(API_TOKEN)

    run_input = {
        "mode": "getstockdetails",
        "url": url
    }

    run = client.actor(ACTOR_ID).call(run_input=run_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

    return items[0]


# -----------------------------
# CORE LOGIC → BUILD ONE ROW
# -----------------------------
def build_row(data):

    company = data["company_name"]

    cfo = extract_metric(data["cash_flow"], "Cash from Operating Activity")
    op = extract_metric(data["profit_and_loss"]["annual_data"], "Operating Profit")
    np_ = extract_metric(data["profit_and_loss"]["annual_data"], "Net Profit")

    # Get years (sorted latest first)
    years = [k for k in cfo.keys() if k != "Metric"]
    years = sorted(years, reverse=True)

    # Take last 5 years
    years = years[:5]

    cf_vals = []
    op_vals = []
    np_vals = []

    for y in years:
        cf_vals.append(cfo.get(y))
        op_vals.append(op.get(y))
        np_vals.append(np_.get(y))
    


    # -----------------------------
    # RATIOS
    # -----------------------------
    cf_np_1y = safe_div(cf_vals[0], np_vals[0])
    cf_op_1y = safe_div(cf_vals[0], op_vals[0])

    cf_np_3y = safe_div(sum(cf_vals[:3])/3, sum(np_vals[:3])/3)
    cf_op_3y = safe_div(sum(cf_vals[:3])/3, sum(op_vals[:3])/3)

    cf_np_5y = safe_div(sum(cf_vals)/5, sum(np_vals)/5)
    cf_op_5y = safe_div(sum(cf_vals)/5, sum(op_vals)/5)

    # -----------------------------
    # QUARTERLY (Jun & Sep)
    # -----------------------------
    q_op = extract_metric(data["quarters"], "Operating Profit")
    q_np = extract_metric(data["quarters"], "Net Profit")

    op_jun = op_sep = np_jun = np_sep = None

    for k in q_op.keys():
        if "Jun" in k:
            op_jun = q_op[k]
            np_jun = q_np.get(k)
        if "Sep" in k:
            op_sep = q_op[k]
            np_sep = q_np.get(k)

    # -----------------------------
    # DEBT TO EQUITY
    # -----------------------------
    bs = data.get("balance_sheet", [])
    borrow = extract_metric(bs, "Borrowings")
    equity_cap = extract_metric(bs, "Equity Capital")
    reserves = extract_metric(bs, "Reserves")

    try:
        latest_year_de = sorted([k for k in borrow.keys() if k != "Metric"], reverse=True)[0]
        debt = borrow.get(latest_year_de)
        equity = (equity_cap.get(latest_year_de) or 0) + (reserves.get(latest_year_de) or 0)
        de_ratio = safe_div(debt, equity)
    except:
        de_ratio = None

    # -----------------------------
    # BUILD FINAL ROW
    # -----------------------------
    row = {
        "Stock":company,
        "DE_Ratio": de_ratio,

        # Ratios
        "CF/NP_1Y": cf_np_1y,
        "CF/NP_3Y": cf_np_3y,
        "CF/NP_5Y": cf_np_5y,

        "CF/OP_1Y": cf_op_1y,
        "CF/OP_3Y": cf_op_3y,
        "CF/OP_5Y": cf_op_5y,
    }

    # Dynamic Annual Data (Actual Years)
    for i, y in enumerate(years):
        if i < len(cf_vals): row[f"CF_{y}"] = cf_vals[i]
    for i, y in enumerate(years):
        if i < len(np_vals): row[f"NP_{y}"] = np_vals[i]
    for i, y in enumerate(years):
        if i < len(op_vals): row[f"OP_{y}"] = op_vals[i]

    # Quarterly
    row["OP_Jun"] = op_jun
    row["OP_Sep"] = op_sep
    row["NP_Jun"] = np_jun
    row["NP_Sep"] = np_sep

    return row


# -----------------------------
# MAIN
# -----------------------------
def main():

    urls = [
        "https://www.screener.in/company/KANSAINER/consolidated/"
    ]

    all_rows = []

    for url in urls:
        try:
            data = fetch_data(url)
            row = build_row(data)
            all_rows.append(row)
            print(f"✅ Processed: {row['Stock']}")
        except Exception as e:
            print(f"❌ Error for {url}: {e}")

    if not all_rows:
        print("⚠️ No data to save.")
        return

    df_new = pd.DataFrame(all_rows)
    file_name = "final_stock_sheet.xlsx"

    if os.path.exists(file_name):
        try:
            df_old = pd.read_excel(file_name)
            df_final = pd.concat([df_old, df_new], ignore_index=True)
        except Exception as e:
            print(f"⚠️ Error reading existing file: {e}. Saving fresh data.")
            df_final = df_new
    else:
        df_final = df_new

    df_final.to_excel(file_name, index=False)

    print(f"🚀 Excel updated: {file_name}")


if __name__ == "__main__":
    main()
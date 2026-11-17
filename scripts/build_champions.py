"""
CEA Champions — rank Singapore property agents by number of transactions.

Data (free, data.gov.sg, Open Data Licence):
  * CEA Salespersons' Property Transaction Records (residential)  d_ee7e46d3c57f7865790704632b0aef71
    Jan 2017 -> now, one row per agent per deal side. No prices.
  * CEA Salesperson Information                                   d_07c63be0f37e6e59c07a4ddc2fd87fcb
    Currently registered agents: reg no, registration dates, current agency.

Raw files are cached in data/raw/*.csv.gz and only re-downloaded when
--refresh is passed (the monthly workflow passes it).
"""
import argparse
import gzip
import io
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "output"

DATASETS = {
    "transactions": "d_ee7e46d3c57f7865790704632b0aef71",
    "salespersons": "d_07c63be0f37e6e59c07a4ddc2fd87fcb",
}

SALE_TYPES = {"RESALE", "NEW SALE", "SUB-SALE", "SUB SALE"}
TOP_N = 100


# ---------------------------------------------------------------- download
def download(dataset_id: str) -> bytes:
    poll = f"https://api-open.data.gov.sg/v1/public/api/datasets/{dataset_id}/poll-download"
    headers = {}
    if os.environ.get("DATAGOV_API_KEY"):
        headers["x-api-key"] = os.environ["DATAGOV_API_KEY"]
    url = None
    for attempt in range(12):
        r = requests.get(poll, headers=headers, timeout=60)
        if r.status_code == 429:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        body = r.json()
        url = (body.get("data") or {}).get("url")
        if url:
            break
        # file still being prepared -> kick it off and wait
        requests.get(poll.replace("poll-download", "initiate-download"), headers=headers, timeout=60)
        time.sleep(10)
    if not url:
        sys.exit(f"Could not get download URL for {dataset_id}")
    r = requests.get(url, timeout=600)
    r.raise_for_status()
    return r.content


def load(name: str, refresh: bool) -> pd.DataFrame:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{name}.csv.gz"
    if refresh or not path.exists():
        print(f"Downloading {name} …", flush=True)
        content = download(DATASETS[name])
        with gzip.open(path, "wb") as f:
            f.write(content)
        print(f"  saved {path.name} ({len(content)/1e6:.1f} MB raw)", flush=True)
    else:
        print(f"Using cached {path.name}", flush=True)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ---------------------------------------------------------------- helpers
def clean(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    for c in df.columns:
        df[c] = df[c].str.strip()
    return df


def segment(row_ptype: pd.Series) -> pd.Series:
    return row_ptype.str.upper().str.contains("HDB").map({True: "HDB", False: "PRIVATE"})


def mode_or_blank(s: pd.Series) -> str:
    s = s[(s != "") & (s != "-")]
    return s.value_counts().index[0] if len(s) else ""


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download raw data")
    args = ap.parse_args()

    tx = clean(load("transactions", args.refresh))
    sp = clean(load("salespersons", args.refresh))
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- transactions
    tx = tx[tx["salesperson_reg_num"] != ""].copy()
    tx["reg"] = tx["salesperson_reg_num"].str.upper()
    tx["date"] = pd.to_datetime(tx["transaction_date"], format="%b-%Y", errors="coerce")
    tx = tx.dropna(subset=["date"])
    tx["year"] = tx["date"].dt.year
    tx["ttype"] = tx["transaction_type"].str.upper()
    tx["is_sale"] = tx["ttype"].isin(SALE_TYPES)
    tx["is_rental"] = tx["ttype"].str.contains("RENTAL")
    tx["seg"] = segment(tx["property_type"])
    tx["rep"] = tx["represented"].str.upper()
    tx["area"] = tx["town"].where(tx["town"].str.len() > 1, tx["district"].radd("D"))

    data_from = tx["date"].min().strftime("%b %Y")
    data_to = tx["date"].max().strftime("%b %Y")
    last_full_year = tx["year"].max() - (1 if tx[tx["year"] == tx["year"].max()]["date"].dt.month.max() < 12 else 0)

    # ---- salesperson register (current)
    sp = sp.rename(columns={"registration_no": "reg", "registration_number": "reg"})
    sp["reg"] = sp["reg"].str.upper()
    sp = sp.drop_duplicates("reg")
    sp_cols = ["reg", "registration_start_date", "registration_end_date",
               "estate_agent_name", "estate_agent_license_no"]
    sp = sp[[c for c in sp_cols if c in sp.columns]]

    # ---- per-agent profile
    g = tx.groupby("reg")
    prof = pd.DataFrame({
        "name": g["salesperson_name"].agg(mode_or_blank),
        "total_deals": g.size(),
        "sales_deals": g["is_sale"].sum(),
        "rental_deals": g["is_rental"].sum(),
        "hdb_deals": g["seg"].apply(lambda s: (s == "HDB").sum()),
        "private_deals": g["seg"].apply(lambda s: (s == "PRIVATE").sum()),
        "new_sale_deals": g["ttype"].apply(lambda s: (s == "NEW SALE").sum()),
        "first_deal": g["date"].min().dt.strftime("%Y-%m"),
        "last_deal": g["date"].max().dt.strftime("%Y-%m"),
        "active_years": g["year"].nunique(),
        "top_area": g["area"].agg(mode_or_blank),
    })
    for side in ["BUYER", "SELLER", "LANDLORD", "TENANT"]:
        prof[f"rep_{side.lower()}"] = g["rep"].apply(lambda s, side=side: (s == side).sum())

    per_year = tx.groupby(["reg", "year"]).agg(deals=("reg", "size"), sales=("is_sale", "sum")).reset_index()
    best = per_year.sort_values("deals", ascending=False).drop_duplicates("reg").set_index("reg")
    prof["peak_year"] = best["year"]
    prof["peak_year_deals"] = best["deals"]
    first_year = per_year.sort_values("year").drop_duplicates("reg").set_index("reg")
    prof["rookie_year_deals"] = first_year["deals"]
    # what they started out doing (first 12 months of recorded deals)
    tx = tx.join(g["date"].min().rename("first_date"), on="reg")
    early = tx[tx["date"] < tx["first_date"] + pd.DateOffset(months=12)]
    early_mix = (early["seg"] + " " + early["ttype"].where(~early["is_rental"], "RENTAL"))
    prof["started_in"] = early_mix.groupby(early["reg"]).agg(mode_or_blank)

    prof = prof.reset_index().merge(sp, on="reg", how="left")
    prof["status"] = prof["estate_agent_name"].notna().map({True: "Active", False: "Not currently registered"})
    prof["estate_agent_name"] = prof["estate_agent_name"].fillna("")
    prof["reg_start_year"] = pd.to_datetime(prof.get("registration_start_date"), errors="coerce").dt.year
    prof["sales_share_pct"] = (100 * prof["sales_deals"] / prof["total_deals"]).round(1)
    prof["deals_per_active_year"] = (prof["total_deals"] / prof["active_years"]).round(1)

    # ---- rankings
    prof["rank_all"] = prof["total_deals"].rank(method="min", ascending=False).astype(int)
    prof["rank_sales"] = prof["sales_deals"].rank(method="min", ascending=False).astype(int)
    prof["rank_rental"] = prof["rental_deals"].rank(method="min", ascending=False).astype(int)

    # years spent in each year's top 100 (consistency) + first year they broke in
    per_year["yr_rank"] = per_year.groupby("year")["deals"].rank(method="min", ascending=False)
    top_years = per_year[per_year["yr_rank"] <= TOP_N]
    prof = prof.merge(top_years.groupby("reg").size().rename("years_in_top100"), on="reg", how="left")
    prof = prof.merge(top_years.groupby("reg")["year"].min().rename("first_top100_year"), on="reg", how="left")
    prof["years_in_top100"] = prof["years_in_top100"].fillna(0).astype(int)
    prof["years_to_top100"] = prof["first_top100_year"] - pd.to_datetime(prof["first_deal"]).dt.year

    cols = ["rank_all", "rank_sales", "rank_rental", "name", "reg", "status", "estate_agent_name",
            "reg_start_year", "first_deal", "last_deal", "active_years",
            "total_deals", "sales_deals", "rental_deals", "sales_share_pct",
            "hdb_deals", "private_deals", "new_sale_deals",
            "rep_buyer", "rep_seller", "rep_landlord", "rep_tenant",
            "peak_year", "peak_year_deals", "deals_per_active_year",
            "rookie_year_deals", "started_in", "top_area",
            "years_in_top100", "first_top100_year", "years_to_top100"]
    prof = prof[cols]

    prof.sort_values("rank_all").head(500).to_csv(OUT / "champions_all_time_total.csv", index=False)
    prof.sort_values("rank_sales").head(500).to_csv(OUT / "champions_all_time_sales.csv", index=False)
    prof.sort_values("rank_rental").head(200).to_csv(OUT / "champions_all_time_rental.csv", index=False)
    prof.sort_values(["years_in_top100", "total_deals"], ascending=False).head(200) \
        .to_csv(OUT / "champions_consistency.csv", index=False)

    # ---- yearly champions (top 20 per year, total + sales)
    names = prof.set_index("reg")[["name", "estate_agent_name"]]
    yearly = []
    for metric in ["deals", "sales"]:
        py = per_year.copy()
        py["rank"] = py.groupby("year")[metric].rank(method="min", ascending=False)
        py = py[py["rank"] <= 20].join(names, on="reg")
        py["metric"] = "total" if metric == "deals" else "sales_only"
        py["value"] = py[metric]
        yearly.append(py[["year", "metric", "rank", "name", "reg", "estate_agent_name", "value"]])
    pd.concat(yearly).sort_values(["metric", "year", "rank"]).to_csv(OUT / "champions_by_year.csv", index=False)

    # ---- origins: where the top 100 came from
    top = prof[prof["rank_all"] <= TOP_N]
    everyone = prof[prof["total_deals"] > 0]

    def share(df, col):
        return df[col].replace("", "(not currently registered)").value_counts()

    agency = pd.DataFrame({
        "top100_agents": share(top, "estate_agent_name"),
        "all_agents_with_deals": share(everyone, "estate_agent_name"),
    }).fillna(0).astype(int)
    agency["top100_share_pct"] = (100 * agency["top100_agents"] / len(top)).round(1)
    agency["all_share_pct"] = (100 * agency["all_agents_with_deals"] / len(everyone)).round(1)
    agency["over_representation_x"] = (agency["top100_share_pct"] / agency["all_share_pct"]).round(2)
    agency.sort_values("top100_agents", ascending=False).to_csv(OUT / "origins_by_agency.csv", index_label="agency")

    cohort = top["reg_start_year"].value_counts().sort_index()
    cohort.rename("top100_agents").to_csv(OUT / "origins_by_registration_year.csv", index_label="reg_start_year")
    top["started_in"].value_counts().rename("top100_agents") \
        .to_csv(OUT / "origins_by_starting_segment.csv", index_label="started_in")

    summary = {
        "data_from": data_from,
        "data_to": data_to,
        "last_full_year": int(last_full_year),
        "transaction_rows": int(len(tx)),
        "agents_with_deals": int(len(everyone)),
        "agents_currently_registered": int(len(sp)),
        "top10_all_time": prof.sort_values("rank_all").head(10)[
            ["rank_all", "name", "estate_agent_name", "total_deals", "sales_deals", "rental_deals"]
        ].to_dict("records"),
        "top10_sales": prof.sort_values("rank_sales").head(10)[
            ["rank_sales", "name", "estate_agent_name", "sales_deals", "total_deals"]
        ].to_dict("records"),
        "top100_median": {
            "total_deals": float(top["total_deals"].median()),
            "rookie_year_deals": float(top["rookie_year_deals"].median()),
            "years_to_top100": float(top["years_to_top100"].median()),
            "sales_share_pct": float(top["sales_share_pct"].median()),
            "still_registered_pct": round(100 * (top["status"] == "Active").mean(), 1),
        },
        "all_agents_median": {
            "total_deals": float(everyone["total_deals"].median()),
            "rookie_year_deals": float(everyone["rookie_year_deals"].median()),
        },
        "top100_agencies": agency.sort_values("top100_agents", ascending=False).head(10)["top100_agents"].to_dict(),
        "top100_started_in": top["started_in"].value_counts().head(8).to_dict(),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()

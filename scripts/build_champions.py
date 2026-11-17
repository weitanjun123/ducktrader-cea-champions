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
# CEA data coverage: Jan 2017 - Dec 2019 has HDB resale ONLY.
# All deal types (private sales, new launches, rentals) start Jan 2020.
FULL_FROM = pd.Timestamp("2020-01-01")
NEWCOMER_FROM = pd.Timestamp("2021-01-01")  # first deal on/after this = genuine newcomer


def count(g, col, val):
    return g[col].apply(lambda s: int((s == val).sum()))


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
    tx["date"] = pd.to_datetime(tx["transaction_date"].str.title(), format="%b-%Y", errors="coerce")
    tx = tx.dropna(subset=["date"])
    tx["year"] = tx["date"].dt.year
    tx["ttype"] = tx["transaction_type"].str.upper()
    tx["is_sale"] = tx["ttype"].isin(SALE_TYPES)
    tx["is_rental"] = tx["ttype"].str.contains("RENTAL")
    tx["seg"] = segment(tx["property_type"])
    tx["is_hdb_resale"] = (tx["seg"] == "HDB") & (tx["ttype"] == "RESALE")
    tx["rep"] = tx["represented"].str.upper()
    tx["area"] = tx["town"].where(tx["town"].str.len() > 1, "D" + tx["district"])
    tx["kind"] = tx["seg"] + " " + tx["ttype"].where(~tx["is_rental"], "RENTAL")

    data_to = tx["date"].max()
    full = tx[tx["date"] >= FULL_FROM].copy()

    # ---- salesperson register (current snapshot)
    sp = sp.rename(columns={"registration_no": "reg", "registration_number": "reg"})
    sp["reg"] = sp["reg"].str.upper()
    sp = sp.drop_duplicates("reg")
    sp = sp[[c for c in ["reg", "registration_start_date", "estate_agent_name"] if c in sp.columns]]

    # ---- per-agent profile (all deal types, Jan 2020 ->)
    g = full.groupby("reg")
    prof = pd.DataFrame({
        "name": g["salesperson_name"].agg(mode_or_blank),
        "total_deals": g.size(),
        "sales_deals": g["is_sale"].sum(),
        "rental_deals": g["is_rental"].sum(),
        "hdb_deals": count(g, "seg", "HDB"),
        "private_deals": count(g, "seg", "PRIVATE"),
        "new_launch_deals": count(g, "ttype", "NEW SALE"),
        "rep_buyer": count(g, "rep", "BUYER"),
        "rep_seller": count(g, "rep", "SELLER"),
        "rep_landlord": count(g, "rep", "LANDLORD"),
        "rep_tenant": count(g, "rep", "TENANT"),
        "top_area": g["area"].agg(mode_or_blank),
        "main_business": g["kind"].agg(mode_or_blank),
        "last_deal": g["date"].max().dt.strftime("%Y-%m"),
        "active_years": g["year"].nunique(),
    })
    py = full.groupby(["reg", "year"]).agg(deals=("reg", "size"), sales=("is_sale", "sum")).reset_index()
    best = py.sort_values("deals", ascending=False).drop_duplicates("reg").set_index("reg")
    prof["peak_year"] = best["year"]
    prof["peak_year_deals"] = best["deals"]
    prof["deals_per_active_year"] = (prof["total_deals"] / prof["active_years"]).round(1)
    prof["sales_share_pct"] = (100 * prof["sales_deals"] / prof["total_deals"]).round(1)

    # ---- HDB resale since 2017 (the longest record CEA has) + debut date
    ga = tx.groupby("reg")
    longrun = pd.DataFrame({
        "hdb_resale_since_2017": ga["is_hdb_resale"].sum(),
        "first_deal_date": ga["date"].min(),
    })
    prof = prof.join(longrun, how="outer")
    prof["name"] = prof["name"].fillna(ga["salesperson_name"].agg(mode_or_blank))
    for c in ["total_deals", "sales_deals", "rental_deals", "hdb_deals", "private_deals", "new_launch_deals",
              "rep_buyer", "rep_seller", "rep_landlord", "rep_tenant", "active_years"]:
        prof[c] = prof[c].fillna(0).astype(int)
    prof["first_deal"] = prof["first_deal_date"].dt.strftime("%Y-%m")
    prof["first_deal"] = prof["first_deal"].where(prof["first_deal_date"] > pd.Timestamp("2017-01-31"),
                                                  "2017-01 or earlier")

    # ---- origins: newcomers (first deal of any kind from 2021) -> what they started doing
    prof["newcomer"] = prof["first_deal_date"] >= NEWCOMER_FROM
    tx = tx.join(prof["first_deal_date"], on="reg")
    early = tx[tx["date"] < tx["first_deal_date"] + pd.DateOffset(months=12)]
    eg = early.groupby("reg")
    prof["started_in"] = eg["kind"].agg(mode_or_blank)
    prof["rookie_12m_deals"] = eg.size()
    prof.loc[~prof["newcomer"], "started_in"] = "Already active before 2021"
    prof.loc[~prof["newcomer"], "rookie_12m_deals"] = pd.NA

    # ---- current agency + whether they moved agency after their first deal
    prof = prof.reset_index().rename(columns={"index": "reg"}).merge(sp, on="reg", how="left")
    prof["status"] = prof["estate_agent_name"].notna().map({True: "Active", False: "Not currently registered"})
    prof["estate_agent_name"] = prof["estate_agent_name"].fillna("")
    reg_since = pd.to_datetime(prof["registration_start_date"], errors="coerce")
    prof["current_agency_since"] = reg_since.dt.strftime("%Y-%m").fillna("")
    moved = reg_since > prof["first_deal_date"] + pd.DateOffset(months=1)
    prof["switched_agency"] = moved.map({True: "Yes", False: ""})

    # ---- rankings
    for col, rk in [("total_deals", "rank_total"), ("sales_deals", "rank_sales"),
                    ("rental_deals", "rank_rental"), ("hdb_resale_since_2017", "rank_hdb_resale")]:
        prof[rk] = prof[col].rank(method="min", ascending=False).astype(int)

    py["yr_rank"] = py.groupby("year")["deals"].rank(method="min", ascending=False)
    top_years = py[py["yr_rank"] <= TOP_N]
    prof = prof.merge(top_years.groupby("reg").size().rename("years_in_yearly_top100"), on="reg", how="left")
    prof = prof.merge(top_years.groupby("reg")["year"].min().rename("first_top100_year"), on="reg", how="left")
    prof["years_in_yearly_top100"] = prof["years_in_yearly_top100"].fillna(0).astype(int)
    prof["years_to_top100"] = (prof["first_top100_year"] - prof["first_deal_date"].dt.year).where(prof["newcomer"])

    cols = ["rank_total", "rank_sales", "rank_rental", "rank_hdb_resale", "name", "reg", "status",
            "estate_agent_name", "current_agency_since", "switched_agency",
            "total_deals", "sales_deals", "rental_deals", "sales_share_pct",
            "hdb_deals", "private_deals", "new_launch_deals", "hdb_resale_since_2017",
            "rep_buyer", "rep_seller", "rep_landlord", "rep_tenant",
            "main_business", "top_area", "peak_year", "peak_year_deals", "deals_per_active_year",
            "first_deal", "last_deal", "newcomer", "started_in", "rookie_12m_deals",
            "years_in_yearly_top100", "first_top100_year", "years_to_top100"]
    prof = prof[cols]

    prof.sort_values("rank_total").head(500).to_csv(OUT / "champions_total_since_2020.csv", index=False)
    prof.sort_values("rank_sales").head(500).to_csv(OUT / "champions_sales_since_2020.csv", index=False)
    prof.sort_values("rank_rental").head(200).to_csv(OUT / "champions_rental_since_2020.csv", index=False)
    prof.sort_values("rank_hdb_resale").head(200).to_csv(OUT / "champions_hdb_resale_since_2017.csv", index=False)
    prof[prof["newcomer"]].sort_values("total_deals", ascending=False).head(100) \
        .to_csv(OUT / "rising_stars_debut_2021_plus.csv", index=False)
    prof.sort_values(["years_in_yearly_top100", "total_deals"], ascending=False).head(200) \
        .to_csv(OUT / "champions_consistency.csv", index=False)

    # ---- yearly champions
    names = prof.set_index("reg")[["name", "estate_agent_name"]]
    out = []
    for metric, src, label in [("deals", py, "all_deals"), ("sales", py, "sales_only")]:
        d = src.copy()
        d["rank"] = d.groupby("year")[metric].rank(method="min", ascending=False)
        d = d[d["rank"] <= 20].join(names, on="reg")
        d["list"], d["deals_in_year"] = label, d[metric]
        out.append(d)
    hr = tx[tx["is_hdb_resale"]].groupby(["reg", "year"]).size().rename("deals_in_year").reset_index()
    hr["rank"] = hr.groupby("year")["deals_in_year"].rank(method="min", ascending=False)
    hr = hr[hr["rank"] <= 20].join(names, on="reg")
    hr["list"] = "hdb_resale"
    out.append(hr)
    yearly = pd.concat(out)[["list", "year", "rank", "name", "reg", "estate_agent_name", "deals_in_year"]]
    yearly["rank"] = yearly["rank"].astype(int)
    yearly.sort_values(["list", "year", "rank"]).to_csv(OUT / "champions_by_year.csv", index=False)

    # ---- origins summary for the champion groups
    everyone = prof[prof["total_deals"] > 0]
    groups = {
        "top100_total": prof[prof["rank_total"] <= TOP_N],
        "top100_sales": prof[prof["rank_sales"] <= TOP_N],
        "top100_hdb_resale": prof[prof["rank_hdb_resale"] <= TOP_N],
    }
    ag = pd.DataFrame({"all_agents_with_deals": everyone["estate_agent_name"].replace("", "(not registered now)").value_counts()})
    for k, df in groups.items():
        ag[k] = df["estate_agent_name"].replace("", "(not registered now)").value_counts()
    ag = ag.fillna(0).astype(int)
    ag["all_share_pct"] = (100 * ag["all_agents_with_deals"] / len(everyone)).round(1)
    ag["top100_total_share_pct"] = (100 * ag["top100_total"] / TOP_N).round(1)
    ag = ag[(ag[list(groups)].sum(axis=1) > 0) | (ag["all_agents_with_deals"] >= 200)]
    ag.sort_values(["top100_total", "top100_sales"], ascending=False).to_csv(OUT / "origins_by_agency.csv", index_label="agency")

    def describe(df):
        return {
            "count": int(len(df)),
            "median_deals_since_2020": float(df["total_deals"].median()),
            "median_sales_share_pct": float(df["sales_share_pct"].median()),
            "switched_agency_pct": round(100 * (df["switched_agency"] == "Yes").mean(), 1),
            "still_registered_pct": round(100 * (df["status"] == "Active").mean(), 1),
            "active_before_2017_pct": round(100 * (df["first_deal"] == "2017-01 or earlier").mean(), 1),
            "newcomers_2021_plus": int(df["newcomer"].sum()),
            "top_agencies": df["estate_agent_name"].replace("", "(not registered now)").value_counts().head(6).to_dict(),
            "main_business": df["main_business"].value_counts().head(5).to_dict(),
        }

    summary = {
        "data_to": data_to.strftime("%b %Y"),
        "coverage_note": "Jan 2017-Dec 2019 = HDB resale only; all deal types from Jan 2020",
        "transaction_rows": int(len(tx)),
        "agents_with_deals_since_2020": int(len(everyone)),
        "agents_currently_registered": int(len(sp)),
        "all_agents": {"median_deals_since_2020": float(everyone["total_deals"].median()),
                       "switched_agency_pct": round(100 * (everyone["switched_agency"] == "Yes").mean(), 1)},
        **{k: describe(v) for k, v in groups.items()},
        "rising_stars_started_in": prof[prof["newcomer"]].sort_values("total_deals", ascending=False)
            .head(100)["started_in"].value_counts().to_dict(),
        "all_newcomers_started_in": prof[prof["newcomer"]]["started_in"].value_counts().head(8).to_dict(),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()

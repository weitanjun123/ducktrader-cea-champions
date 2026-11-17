"""
Estimate how many currently registered CEA agents earn less than a typical
full-time income, from the deals CEA records (CEA publishes no prices).

Method (all assumptions in ASSUMPTIONS, written to output/income_assumptions.csv):
  * Window: the 12 most recent complete months in the CEA data.
  * Each CEA row = one agent on one side of one deal.
  * Gross commission per side = rate x price (sales) or months x rent (rentals).
      - HDB resale price / HDB whole-flat rent: medians over the window from HDB's
        own data on data.gov.sg (downloaded below).
      - Private prices / rents: fixed assumptions (no free official median).
  * Agent keeps AGENT_SPLIT of gross commission (rest to agency). GST excluded.
  * No business expenses deducted (ads, transport) -> estimates are generous.
"""
import json
from pathlib import Path

import pandas as pd

from build_champions import RAW, OUT, clean, download, gzip

HDB_RESALE = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"
HDB_RENT = "d_c9f57187485a850908655db0e8cfe651"

THRESHOLD = 5000          # S$/month: median gross income of full-time residents, excl. employer CPF (MOM 2025)
AGENT_SPLIT = 0.75        # share of gross commission the agent keeps
PRIVATE_RESALE_PRICE = 1_700_000   # assumption
NEW_LAUNCH_PRICE = 2_000_000       # assumption
PRIVATE_RENT = 4_300               # 2-bedroom RCR median rent, URA contracts Q4 2025-Q3 2026 (PropKaki)
ROOM_RENT = 1_550                  # median standard room rent (Cove 2026 guide)
BANDS = [0, 1, 2000, 4000, THRESHOLD, 8000, 15000, 10**9]
BAND_LABELS = ["S$0 (no deals)", "Under S$2k", "S$2k-4k", "S$4k-5k", "S$5k-8k", "S$8k-15k", "S$15k+"]


def load_extra(name, dsid, refresh):
    path = RAW / f"{name}.csv.gz"
    if refresh or not path.exists():
        content = download(dsid)
        with gzip.open(path, "wb") as f:
            f.write(content)
    return clean(pd.read_csv(path, dtype=str, keep_default_na=False))


def main(refresh=False):
    tx = clean(pd.read_csv(RAW / "transactions.csv.gz", dtype=str, keep_default_na=False))
    sp = clean(pd.read_csv(RAW / "salespersons.csv.gz", dtype=str, keep_default_na=False))
    sp = sp.rename(columns={"registration_no": "reg", "registration_number": "reg"})
    sp["reg"] = sp["reg"].str.upper()
    sp = sp.drop_duplicates("reg")

    tx["reg"] = tx["salesperson_reg_num"].str.upper()
    tx["date"] = pd.to_datetime(tx["transaction_date"].str.title(), format="%b-%Y", errors="coerce")
    tx = tx.dropna(subset=["date"])

    # window: 12 most recent months whose deal count looks complete (>= 70% of trailing median)
    monthly = tx.groupby("date").size()
    med = monthly.tail(24).median()
    complete = monthly[monthly >= 0.7 * med]
    end = complete.index.max()
    start = end - pd.DateOffset(months=11)
    w = tx[(tx["date"] >= start) & (tx["date"] <= end)].copy()

    # medians from HDB data over the same window
    hr = load_extra("hdb_resale_prices", HDB_RESALE, refresh)
    hr["date"] = pd.to_datetime(hr["month"], format="%Y-%m", errors="coerce")
    hr_w = hr[(hr["date"] >= start) & (hr["date"] <= end)]
    hdb_price = float(pd.to_numeric(hr_w["resale_price"], errors="coerce").median())
    rent = load_extra("hdb_rents", HDB_RENT, refresh)
    rent["date"] = pd.to_datetime(rent["rent_approval_date"], format="%Y-%m", errors="coerce")
    rent_w = rent[(rent["date"] >= start) & (rent["date"] <= end)]
    hdb_rent = float(pd.to_numeric(rent_w["monthly_rent"], errors="coerce").median())

    # commission per deal side, before agency split
    def comm(row_type, ptype, rep):
        hdb = "HDB" in ptype
        if row_type == "RESALE":
            if hdb:
                return (0.02 if rep == "SELLER" else 0.01) * hdb_price
            return (0.015 if rep == "SELLER" else 0.01) * PRIVATE_RESALE_PRICE
        if row_type in ("NEW SALE", "SUB-SALE"):
            return 0.015 * (NEW_LAUNCH_PRICE if row_type == "NEW SALE" else PRIVATE_RESALE_PRICE)
        if row_type == "WHOLE RENTAL":
            return 0.75 * (hdb_rent if hdb else PRIVATE_RENT)
        if row_type == "ROOM RENTAL":
            return 0.5 * ROOM_RENT
        return 0.0

    keys = w[["transaction_type", "property_type", "represented"]].drop_duplicates()
    keys["gross"] = [comm(t.upper(), p.upper(), r.upper()) for t, p, r in keys.itertuples(index=False)]
    w = w.merge(keys, on=["transaction_type", "property_type", "represented"])

    per = w.groupby("reg").agg(deals=("reg", "size"), gross=("gross", "sum"))
    agents = sp[["reg", "salesperson_name", "estate_agent_name"]].merge(per, on="reg", how="left")
    agents[["deals", "gross"]] = agents[["deals", "gross"]].fillna(0)
    agents["monthly_take_home"] = agents["gross"] * AGENT_SPLIT / 12
    agents["band"] = pd.cut(agents["monthly_take_home"], BANDS, right=False, labels=BAND_LABELS)

    n = len(agents)
    bands = agents["band"].value_counts().reindex(BAND_LABELS).fillna(0).astype(int).rename("agents").to_frame()
    bands["share_pct"] = (100 * bands["agents"] / n).round(1)
    bands["cumulative_pct"] = bands["share_pct"].cumsum().round(1)
    bands.to_csv(OUT / "income_bands.csv", index_label="est_monthly_take_home")

    deal_bands = pd.cut(agents["deals"], [0, 1, 2, 5, 12, 24, 48, 10**6], right=False,
                        labels=["0", "1", "2-4", "5-11", "12-23", "24-47", "48+"]).value_counts().sort_index()
    deal_bands.rename("agents").to_csv(OUT / "deal_bands_last12m.csv", index_label="deal_sides_in_12_months")

    # break-even: deal sides a year needed to clear the threshold, per deal type
    breakeven = {}
    for label, t, p, r in [("HDB resale (seller side)", "RESALE", "HDB", "SELLER"),
                           ("HDB resale (buyer side)", "RESALE", "HDB", "BUYER"),
                           ("Condo resale (seller side)", "RESALE", "CONDOMINIUM_APARTMENTS", "SELLER"),
                           ("New launch", "NEW SALE", "CONDOMINIUM_APARTMENTS", "BUYER"),
                           ("Condo whole rental", "WHOLE RENTAL", "CONDOMINIUM_APARTMENTS", "LANDLORD"),
                           ("HDB whole rental", "WHOLE RENTAL", "HDB", "LANDLORD"),
                           ("Room rental", "ROOM RENTAL", "HDB", "LANDLORD")]:
        take = comm(t, p, r) * AGENT_SPLIT
        breakeven[label] = {"take_home_per_deal": round(take), "deals_per_year_to_reach_threshold": round(THRESHOLD * 12 / take, 1)}

    below = agents["monthly_take_home"] < THRESHOLD
    summary = {
        "window": f"{start:%b %Y} - {end:%b %Y}",
        "threshold_sgd_per_month": THRESHOLD,
        "registered_agents": n,
        "pct_below_threshold": round(100 * below.mean(), 1),
        "pct_zero_deals": round(100 * (agents["deals"] == 0).mean(), 1),
        "pct_below_2000": round(100 * (agents["monthly_take_home"] < 2000).mean(), 1),
        "median_deal_sides": float(agents["deals"].median()),
        "median_monthly_take_home": round(float(agents["monthly_take_home"].median())),
        "agents_with_at_least_1_deal": {
            "count": int((agents["deals"] > 0).sum()),
            "pct_below_threshold": round(100 * below[agents["deals"] > 0].mean(), 1),
            "median_monthly_take_home": round(float(agents.loc[agents["deals"] > 0, "monthly_take_home"].median())),
        },
        "pct_of_all_commission_earned_by_top_10pct": round(
            100 * agents["gross"].nlargest(int(n * 0.1)).sum() / agents["gross"].sum(), 1),
        "inputs": {"hdb_median_resale_price": round(hdb_price), "hdb_median_rent": round(hdb_rent),
                   "private_resale_price": PRIVATE_RESALE_PRICE, "new_launch_price": NEW_LAUNCH_PRICE,
                   "private_rent": PRIVATE_RENT, "room_rent": ROOM_RENT, "agent_split": AGENT_SPLIT},
        "breakeven": breakeven,
    }
    (OUT / "income_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    import sys
    main(refresh="--refresh" in sys.argv)

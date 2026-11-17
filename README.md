# DuckTrader CEA Champions

Ranks Singapore property agents by number of residential transactions, using free CEA open data from data.gov.sg (Open Data Licence).

- **Transactions:** CEA Salespersons' Property Transaction Records (residential), `d_ee7e46d3c57f7865790704632b0aef71`, Jan 2017 to now
- **Agents:** CEA Salesperson Information, `d_07c63be0f37e6e59c07a4ddc2fd87fcb` (currently registered agents and their agency)

No prices are published by CEA, so rankings are by **deal count** only.

## Outputs (`output/`)
| File | What |
|---|---|
| `champions_all_time_total.csv` | Top 500 by all deals (sales + rentals) |
| `champions_all_time_sales.csv` | Top 500 by sales only (resale, new sale, sub-sale) |
| `champions_all_time_rental.csv` | Top 200 by rentals |
| `champions_consistency.csv` | Most years spent in a yearly top 100 |
| `champions_by_year.csv` | Top 20 per year, total and sales-only |
| `origins_*.csv` | Where the top 100 came from: agency, registration year, first-year segment |
| `summary.json` | Headline stats |

## Notes
- Each row in CEA's data is one agent on one side of a deal. A deal where the agent represents both sides counts twice.
- Agents active before 2017 appear to "start" in 2017, because that's where the data begins.
- `status = Not currently registered` means the agent is no longer on CEA's active register.
- Runs monthly on the 18th (after CEA's update on the 16th). Raw data is cached in `data/raw/`.

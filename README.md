# DuckTrader CEA Champions

Ranks Singapore property agents by number of residential transactions, using free CEA open data from data.gov.sg (Open Data Licence).

- **Transactions:** CEA Salespersons' Property Transaction Records (residential), `d_ee7e46d3c57f7865790704632b0aef71`, Jan 2017 to now
- **Agents:** CEA Salesperson Information, `d_07c63be0f37e6e59c07a4ddc2fd87fcb` (currently registered agents and their agency)

No prices are published by CEA, so rankings are by **deal count** only.

## Outputs (`output/`)
| File | What |
|---|---|
| `champions_total_since_2020.csv` | Top 500 by all deals since Jan 2020 (sales + rentals) |
| `champions_sales_since_2020.csv` | Top 500 by sales only (resale, new launch, sub-sale) |
| `champions_rental_since_2020.csv` | Top 200 by rentals |
| `champions_hdb_resale_since_2017.csv` | Top 200 by HDB resale deals since Jan 2017 (CEA's longest record) |
| `rising_stars_debut_2021_plus.csv` | Top 100 agents whose first deal was in 2021 or later |
| `champions_consistency.csv` | Most years spent in a yearly top 100 |
| `champions_by_year.csv` | Top 20 per year: all deals, sales only, HDB resale |
| `origins_by_agency.csv` | Which agencies the champions are with now |
| `summary.json` | Headline stats |

## Notes
- Each row in CEA's data is one agent on one side of a deal. A deal where the agent represents both sides counts twice.
- **Coverage:** Jan 2017–Dec 2019 has HDB resale only. All deal types start Jan 2020, so the main rankings use Jan 2020 onwards.
- `switched_agency = Yes` means the agent's current CEA registration started after their first recorded deal (most likely a move to their current agency).
- `status = Not currently registered` means the agent is no longer on CEA's active register.
- Runs monthly on the 18th (after CEA's update on the 16th). Raw data is cached in `data/raw/`.

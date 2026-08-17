# El Paso Sales & New Construction Tracker

**[View the live dashboard →](https://hoffmanap.github.io/housetrends/)**

Tracks recorded home sale prices and new-construction activity across the City of El Paso, combining MLS export data with the RentCast API. Built to support ongoing housing policy research alongside the rest of the [hoffmanap.github.io](https://hoffmanap.github.io) portfolio.

## What's here

- **Sales Prices tab** — median sale price, sales volume, and property characteristics (size, lot size, beds, baths) over time, filterable by date range and by drawing a boundary directly on the map. Includes a "New Construction vs. Resale" breakdown that classifies each sale using its own `yearBuilt` field.
- **New Construction tab** — listing volume, pricing, HOA prevalence, builder activity, and property type mix for active/inactive new-construction listings, with the same date and map filtering.

Both tabs link to the full underlying CSV for anyone who wants the raw data rather than the charts.

## Data sources

| Source | Coverage | Notes |
|---|---|---|
| MLS exports | 2019 – Aug 2025 | Comprehensive for the period covered; the authoritative base for sales data |
| Investor-platform export (`result (N).csv` files) | 2019 – present | MLS/RentCast-sourced records via a third-party analytics tool; tagged `Public Records` vs `Third Party` for quality filtering |
| RentCast API | Ongoing, quarterly (sales) / monthly (new construction) | Extends coverage past the MLS cutoff; Texas's non-disclosure rules mean only a fraction of RentCast-sourced sales carry a public price |
| [housinggrowth](https://github.com/hoffmanap/housinggrowth) parcel data | Four high-growth zip codes, two build-year windows | Used to spatially backfill missing `yearBuilt` values |

Known limitations are surfaced directly in the dashboard's copy rather than hidden — see the caveats under "Market Overview" and the property-characteristics sections for specifics (e.g., MLS-backup-sourced sales don't include `yearBuilt`, so new-construction share is likely undercounted, not overcounted).

## Repository structure

```
housetrends/
├── index.html                          # the dashboard itself
├── data/
│   ├── el_paso_sales.csv               # sales data the dashboard reads
│   └── el_paso_new_construction.csv    # new-construction data the dashboard reads
├── fetch_sales_and_construction.py     # ongoing RentCast pipeline (--mode sales / --mode construction)
├── merge_mls_sales.py                  # one-time: merges MLS_Geocoded_Backup.csv
├── merge_combined_sales.py             # one-time: merges the larger Combined Home Sales 2019-2025 export
├── merge_targeted_sales.py             # one-time: merges targeted historical sales pulls
├── merge_investor_export.py            # merges result (N).csv investor-platform exports (full history)
├── combine_2026_sales.py               # step 1: isolates recent sales from result (N).csv files for review
├── merge_cleaned_sales.py              # step 2: merges a cleaned review file (additive only)
├── replace_2026_investor_sales.py      # step 2 alt: replaces a cohort/year slice wholesale (use after re-cleaning)
├── retag_investor_source.py            # one-time: fixes source/cohort labeling on already-merged records
├── fix_date_format.py                  # one-time: repairs raw (non-ISO) dates in el_paso_sales.csv
├── crossref_housinggrowth.py           # backfills missing yearBuilt via spatial match against housinggrowth data
└── .github/workflows/                  # scheduled GitHub Actions pipeline runs
```

## Important: file location

The dashboard reads only from `data/el_paso_sales.csv` and `data/el_paso_new_construction.csv`. When updating either file, upload it **into the `data/` folder specifically** — a file placed at the repo root instead will not be picked up by the live site.

## Running the pipeline

All scripts are run locally (not through GitHub Actions) except the scheduled RentCast pulls. Each script's docstring (`python script_name.py --help`) explains its exact purpose, expected input, and safe-to-re-run behavior. Always run one-time merge scripts with `--dry-run` first and check the printed preview/counts before committing.

## Design system

Shares the visual language of the rest of the portfolio — Archivo Black display type, IBM Plex Sans/Mono body text, Fraunces for narrative copy, thick black borders with offset drop shadows, pastel stat cards. See [hoffmanap.github.io/EPmissingmiddle](https://hoffmanap.github.io/EPmissingmiddle) for the reference implementation.

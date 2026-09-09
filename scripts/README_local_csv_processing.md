# Local CSV Processing

`process_local_csv.py` rebuilds the main SRWI analysis tables from CSV files
already exported by the notebooks. It does not call Earth Engine, WEkEO, or
EUMETSAT services.

## Install

```bash
pip install pandas numpy
```

## Run

Put the notebook CSV exports in one folder, then run:

```bash
python scripts/process_local_csv.py ^
  --input-dir path\to\csv_exports ^
  --output-dir local_csv_outputs
```

Expected input files when available:

- `sentinel2_timeseries_by_site.csv`
- `climate_extremes_by_site.csv`

The script also works if one of these files is missing; it skips that part and
still writes a CSV profile.

## Outputs

- `local_csv_profile.csv`
- `local_sentinel2_timeseries_by_site.csv`
- `local_sentinel2_timeseries_summary_by_vegetation_role.csv`
- `local_mango_flowering_proxy_metrics_by_site.csv`
- `local_mango_flowering_proxy_smooth_curves.csv`
- `local_climate_summary_by_site_year.csv`
- `local_mango_flowering_climate_by_site_year.csv`
- `local_mango_flowering_rain_correlation.csv`

## Useful Options

```bash
python scripts/process_local_csv.py --profile-only --input-dir path\to\csv_exports
```

Change the phenological year start month:

```bash
python scripts/process_local_csv.py --pheno-year-start-month 7
```

Change simple local thresholds when the exported climate file does not already
contain anomaly columns:

```bash
python scripts/process_local_csv.py --heavy-rain-mm 20 --heat-c 32 --dry-roll-mm 5
```

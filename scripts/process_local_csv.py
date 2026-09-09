"""Process SRWI notebook CSV exports locally.

This script avoids new Earth Engine or WEkEO calls. It reads CSV files already
exported by the notebooks and rebuilds the most useful analysis tables with
pandas.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


DEFAULT_PHENO_YEAR_START_MONTH = 7
DEFAULT_THRESHOLD_FRACTION = 0.20
DEFAULT_SMOOTH_WINDOW = 7


def read_csv_if_exists(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_csv(path)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"wrote {path} ({len(df)} rows)")


def parse_dates(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce", utc=True).dt.tz_convert(None)
    return df


def numeric_columns(df: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def assign_pheno_year(dates: pd.Series, start_month: int) -> np.ndarray:
    parsed = pd.to_datetime(dates, errors="coerce")
    return np.where(parsed.dt.month >= start_month, parsed.dt.year, parsed.dt.year - 1)


def pheno_year_series(dates: pd.Series, start_month: int) -> pd.Series:
    return pd.Series(assign_pheno_year(dates, start_month), index=dates.index).astype("Int64")


def smooth_series(values: pd.Series, window: int) -> pd.Series:
    if window <= 1 or len(values) < 3:
        return values
    window = min(window, len(values))
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return values
    return values.rolling(window=window, center=True, min_periods=1).mean()


def phenology_for_season(
    group: pd.DataFrame,
    threshold_fraction: float,
    smooth_window: int,
) -> tuple[dict | None, pd.DataFrame | None]:
    g = group[["date", "NDVI"]].dropna().sort_values("date")
    if len(g) < 5:
        return None, None

    daily_index = pd.date_range(g.date.min(), g.date.max(), freq="D")
    ndvi = (
        g.set_index("date")["NDVI"]
        .reindex(daily_index)
        .interpolate("time")
        .ffill()
        .bfill()
    )
    smoothed = smooth_series(ndvi, smooth_window)

    vmin = float(smoothed.min())
    vmax = float(smoothed.max())
    amplitude = vmax - vmin
    if amplitude <= 0:
        return None, None

    threshold = vmin + threshold_fraction * amplitude
    peak = smoothed.idxmax()
    peak_position = smoothed.index.get_loc(peak)

    before_peak = smoothed.iloc[: peak_position + 1]
    after_peak = smoothed.iloc[peak_position:]
    sos_candidates = before_peak[before_peak >= threshold]
    eos_candidates = after_peak[after_peak >= threshold]
    sos = sos_candidates.index.min() if not sos_candidates.empty else pd.NaT
    eos = eos_candidates.index.max() if not eos_candidates.empty else pd.NaT

    metrics = {
        "SOS": sos,
        "Peak": peak,
        "EOS": eos,
        "SOS_DOY": sos.dayofyear if pd.notna(sos) else np.nan,
        "Peak_DOY": peak.dayofyear,
        "EOS_DOY": eos.dayofyear if pd.notna(eos) else np.nan,
        "NDVI_min": vmin,
        "NDVI_peak": vmax,
        "Amplitude": amplitude,
        "Season_length_days": (eos - sos).days if pd.notna(sos) and pd.notna(eos) else np.nan,
        "NDVI_integral": float(np.trapezoid(smoothed.values, dx=1)),
    }
    curve = pd.DataFrame({"date": daily_index, "NDVI_smooth": smoothed.values})
    return metrics, curve


def build_vi_tables(input_dir: Path, output_dir: Path, args: argparse.Namespace) -> pd.DataFrame | None:
    vi_path = input_dir / args.sentinel_csv
    vi = read_csv_if_exists(vi_path)
    if vi is None:
        print(f"missing {vi_path}; skipping Sentinel-2/phenology tables")
        return None

    vi = parse_dates(vi, ["date"])
    vi = numeric_columns(vi, ["NDVI", "EVI2", "NDMI", "longitude", "latitude"])
    vi = vi.dropna(subset=["site_id", "date", "NDVI"]).copy()

    if "vegetation_role" not in vi.columns:
        vi["vegetation_role"] = "target"
    if "vegetation_name" not in vi.columns:
        vi["vegetation_name"] = "unknown"
    if "source_layer" not in vi.columns:
        vi["source_layer"] = "unknown"

    vi["pheno_year"] = pheno_year_series(vi["date"], args.pheno_year_start_month)
    vi["month"] = vi["date"].dt.month
    vi["doy"] = vi["date"].dt.dayofyear

    group_cols = [
        "site_id",
        "vegetation_role",
        "vegetation_name",
        "source_layer",
        "longitude",
        "latitude",
        "date",
    ]
    existing_group_cols = [c for c in group_cols if c in vi.columns]
    agg_map = {"n_images": ("NDVI", "count"), "NDVI": ("NDVI", "median")}
    if "EVI2" in vi.columns:
        agg_map["EVI2"] = ("EVI2", "median")
    if "NDMI" in vi.columns:
        agg_map["NDMI"] = ("NDMI", "median")

    vi_daily = (
        vi.groupby(existing_group_cols, as_index=False)
        .agg(**agg_map)
        .sort_values(["site_id", "date"])
        .reset_index(drop=True)
    )
    vi_daily["pheno_year"] = pheno_year_series(vi_daily["date"], args.pheno_year_start_month)
    write_csv(vi_daily, output_dir / "local_sentinel2_timeseries_by_site.csv")

    summary = (
        vi_daily.groupby(["vegetation_role", "vegetation_name", "source_layer", "date"], as_index=False)
        .agg(
            site_count=("site_id", "nunique"),
            mean_NDVI=("NDVI", "mean"),
            median_NDVI=("NDVI", "median"),
            mean_EVI2=("EVI2", "mean") if "EVI2" in vi_daily.columns else ("NDVI", "mean"),
            mean_NDMI=("NDMI", "mean") if "NDMI" in vi_daily.columns else ("NDVI", "mean"),
        )
    )
    write_csv(summary, output_dir / "local_sentinel2_timeseries_summary_by_vegetation_role.csv")

    target = vi_daily[vi_daily["vegetation_role"].eq("target")].copy()
    if target.empty:
        target = vi_daily.copy()

    metrics_rows = []
    curve_rows = []
    for (site_id, pheno_year), group in target.groupby(["site_id", "pheno_year"], dropna=True):
        metrics, curve = phenology_for_season(group, args.threshold_fraction, args.smooth_window)
        if metrics is None or curve is None:
            continue
        first = group.iloc[0]
        for column in ["site_id", "vegetation_role", "vegetation_name", "source_layer"]:
            metrics[column] = first.get(column)
            curve[column] = first.get(column)
        metrics["phenology_target"] = "flowering_proxy"
        metrics["pheno_year"] = int(pheno_year)
        curve["phenology_target"] = "flowering_proxy"
        curve["pheno_year"] = int(pheno_year)
        metrics_rows.append(metrics)
        curve_rows.append(curve)

    phenology = pd.DataFrame(metrics_rows)
    if not phenology.empty:
        phenology = phenology.sort_values(["site_id", "pheno_year"]).reset_index(drop=True)
    curves = pd.concat(curve_rows, ignore_index=True) if curve_rows else pd.DataFrame()
    write_csv(phenology, output_dir / "local_mango_flowering_proxy_metrics_by_site.csv")
    write_csv(curves, output_dir / "local_mango_flowering_proxy_smooth_curves.csv")
    return phenology


def build_climate_tables(input_dir: Path, output_dir: Path, args: argparse.Namespace) -> pd.DataFrame | None:
    climate = read_csv_if_exists(input_dir / args.climate_csv)
    if climate is None:
        print(f"missing {input_dir / args.climate_csv}; skipping climate tables")
        return None

    climate = parse_dates(climate, ["date"])
    climate = numeric_columns(
        climate,
        [
            "Tmean_C",
            "Tmax_C",
            "Tmin_C",
            "P_mm",
            "Tmean_K",
            "Tmax_K",
            "Tmin_K",
            "P_m",
            "P_roll_mm",
        ],
    )
    if "Tmean_C" not in climate.columns and "Tmean_K" in climate.columns:
        climate["Tmean_C"] = climate["Tmean_K"] - 273.15
    if "Tmax_C" not in climate.columns and "Tmax_K" in climate.columns:
        climate["Tmax_C"] = climate["Tmax_K"] - 273.15
    if "Tmin_C" not in climate.columns and "Tmin_K" in climate.columns:
        climate["Tmin_C"] = climate["Tmin_K"] - 273.15
    if "P_mm" not in climate.columns and "P_m" in climate.columns:
        climate["P_mm"] = (climate["P_m"] * 1000).clip(lower=0)

    if "vegetation_role" not in climate.columns:
        climate["vegetation_role"] = "target"
    if "vegetation_name" not in climate.columns:
        climate["vegetation_name"] = "unknown"
    if "source_layer" not in climate.columns:
        climate["source_layer"] = "unknown"

    climate["pheno_year"] = pheno_year_series(climate["date"], args.pheno_year_start_month)
    if "heavy_rain" not in climate.columns and "P_mm" in climate.columns:
        climate["heavy_rain"] = climate["P_mm"] >= args.heavy_rain_mm
    if "extreme_heat" not in climate.columns and "Tmax_C" in climate.columns:
        climate["extreme_heat"] = climate["Tmax_C"] >= args.heat_c
    if "dry_extreme" not in climate.columns and "P_roll_mm" in climate.columns:
        climate["dry_extreme"] = climate["P_roll_mm"] <= args.dry_roll_mm

    season = (
        climate.groupby(["site_id", "vegetation_role", "vegetation_name", "source_layer", "pheno_year"], as_index=False)
        .agg(
            mean_T_C=("Tmean_C", "mean"),
            max_T_C=("Tmax_C", "max"),
            total_P_mm=("P_mm", "sum"),
            heat_days=("extreme_heat", "sum") if "extreme_heat" in climate.columns else ("P_mm", "count"),
            heavy_rain_days=("heavy_rain", "sum") if "heavy_rain" in climate.columns else ("P_mm", "count"),
            dry_extreme_days=("dry_extreme", "sum") if "dry_extreme" in climate.columns else ("P_mm", "count"),
        )
        .reset_index(drop=True)
    )
    write_csv(season, output_dir / "local_climate_summary_by_site_year.csv")
    return season


def merge_phenology_climate(phenology: pd.DataFrame | None, climate: pd.DataFrame | None, output_dir: Path) -> None:
    if phenology is None or climate is None or phenology.empty or climate.empty:
        print("phenology or climate table unavailable; skipping merged hypothesis table")
        return

    keys = ["site_id", "vegetation_role", "vegetation_name", "source_layer", "pheno_year"]
    merged = phenology.merge(climate, on=keys, how="left")
    write_csv(merged, output_dir / "local_mango_flowering_climate_by_site_year.csv")

    corr_cols = [
        "NDVI_peak",
        "Amplitude",
        "NDVI_integral",
        "total_P_mm",
        "heavy_rain_days",
        "dry_extreme_days",
        "heat_days",
    ]
    available = [c for c in corr_cols if c in merged.columns]
    corr = merged[available].corr(numeric_only=True) if len(merged) >= 3 else pd.DataFrame(columns=available)
    corr.to_csv(output_dir / "local_mango_flowering_rain_correlation.csv")
    print(f"wrote {output_dir / 'local_mango_flowering_rain_correlation.csv'}")


def profile_csvs(input_dir: Path, output_dir: Path) -> None:
    rows = []
    for csv_path in sorted(input_dir.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path, nrows=100000)
        except Exception as exc:
            rows.append({"file": csv_path.name, "error": str(exc)})
            continue
        row = {
            "file": csv_path.name,
            "rows_read": len(df),
            "columns": len(df.columns),
            "column_names": ",".join(df.columns),
        }
        for candidate in ["date", "time", "event_date", "Peak", "SOS", "EOS"]:
            if candidate in df.columns:
                parsed = pd.to_datetime(df[candidate], errors="coerce")
                row[f"{candidate}_min"] = parsed.min()
                row[f"{candidate}_max"] = parsed.max()
        rows.append(row)
    write_csv(pd.DataFrame(rows), output_dir / "local_csv_profile.csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process SRWI notebook CSV exports locally.")
    parser.add_argument("--input-dir", default=".", type=Path, help="Directory containing notebook CSV exports.")
    parser.add_argument("--output-dir", default=Path("local_csv_outputs"), type=Path, help="Directory for processed CSVs.")
    parser.add_argument("--sentinel-csv", default="sentinel2_timeseries_by_site.csv")
    parser.add_argument("--climate-csv", default="climate_extremes_by_site.csv")
    parser.add_argument("--pheno-year-start-month", default=DEFAULT_PHENO_YEAR_START_MONTH, type=int)
    parser.add_argument("--threshold-fraction", default=DEFAULT_THRESHOLD_FRACTION, type=float)
    parser.add_argument("--smooth-window", default=DEFAULT_SMOOTH_WINDOW, type=int)
    parser.add_argument("--heavy-rain-mm", default=20.0, type=float)
    parser.add_argument("--heat-c", default=32.0, type=float)
    parser.add_argument("--dry-roll-mm", default=5.0, type=float)
    parser.add_argument("--profile-only", action="store_true", help="Only profile CSV files; do not rebuild analysis tables.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.input_dir = args.input_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    profile_csvs(args.input_dir, args.output_dir)
    if args.profile_only:
        return

    phenology = build_vi_tables(args.input_dir, args.output_dir, args)
    climate = build_climate_tables(args.input_dir, args.output_dir, args)
    merge_phenology_climate(phenology, climate, args.output_dir)


if __name__ == "__main__":
    main()

# EUMETSAT Access Script

`eumetsat_access.py` supports two practical Meteosat access routes:

- EUMETView WMS for rendered Meteosat-9/IODC frames and GIF animations.
- EUMDAC Data Store search for original product discovery/download workflows.

## Install

```bash
pip install requests pillow eumdac
```

`eumdac` is only needed for the Data Store command.

## List Meteosat IODC Layers

```bash
python scripts/eumetsat_access.py list-layers --pattern "msg_iodc|ir108|hrv|natural"
```

Useful layers include:

- `msg_iodc:ir108` for thermal infrared 10.8 micrometer imagery.
- `msg_iodc:clm` for cloud mask.
- `msg_iodc:cth` for cloud top height.
- `msg_iodc:h63` for precipitation rate.

## Download One WMS Frame

EUMETView WMS is mainly near-real-time/rolling. Use recent timestamps for WMS frames.

```bash
python scripts/eumetsat_access.py download-wms ^
  --layer msg_iodc:ir108 ^
  --time 2026-09-01T09:00:00Z ^
  --bbox 47.4 -17.6 52.1 -13.2 ^
  --width 720 ^
  --height 720 ^
  --output outputs/maroantsetra_ir108.png
```

## Build A GIF

```bash
python scripts/eumetsat_access.py animate-wms ^
  --layer msg_iodc:ir108 ^
  --name maroantsetra_ir108 ^
  --start 2026-09-01T00:00:00Z ^
  --end 2026-09-01T23:45:00Z ^
  --step-minutes 15 ^
  --bbox 47.4 -17.6 52.1 -13.2 ^
  --width 720 ^
  --height 720 ^
  --fps 6
```

If an event is too large, the script splits it automatically so that each GIF stays under the Earth Engine-style pixel budget used as a practical guardrail.

## Search Original Products With EUMDAC

Create API credentials in the EUMETSAT user portal, then either export them:

```bash
set EUMETSAT_CONSUMER_KEY=your_key
set EUMETSAT_CONSUMER_SECRET=your_secret
```

or pass them as CLI arguments.

```bash
python scripts/eumetsat_access.py search-datastore ^
  --collection EO:EUM:DAT:MSG:HRSEVIRI-IODC ^
  --start 2024-03-26T00:00:00Z ^
  --end 2024-03-29T23:59:59Z ^
  --limit 10
```

Use the Data Store path for archived original products, and EUMETView WMS for quick rendered images and near-real-time animations.

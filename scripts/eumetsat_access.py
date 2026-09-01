#!/usr/bin/env python3
"""Access EUMETSAT Meteosat imagery for Maroantsetra flood-event analysis.

Two access modes are provided:
- EUMETView WMS: list layers, download rendered frames, and build GIFs.
- EUMDAC Data Store: search/download original products when credentials are set.
"""

from __future__ import annotations

import argparse
import io
import math
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode

import requests
from PIL import Image, ImageDraw, ImageFont


DEFAULT_WMS_URL = "https://view.eumetsat.int/geoserver/wms"
DEFAULT_LAYER = "msg_iodc:ir108"
DEFAULT_CRS = "EPSG:4326"
DEFAULT_BBOX = (47.4, -17.6, 52.1, -13.2)
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 720


def safe_print(value: object = "") -> None:
    text = str(value)
    encoding = sys.stdout.encoding or "utf-8"
    sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))


@dataclass(frozen=True)
class LayerInfo:
    name: str
    title: str


def parse_datetime(value: str) -> datetime:
    value = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iter_times(start: datetime, end: datetime, step_minutes: int) -> Iterable[datetime]:
    step = timedelta(minutes=step_minutes)
    current = start
    while current <= end:
        yield current
        current += step


def wms_params(layer: str, bbox: tuple[float, float, float, float], width: int, height: int, time_: datetime) -> dict[str, str]:
    # WMS 1.1.1 keeps EPSG:4326 BBOX order as lon/lat, which is less surprising for scripts.
    return {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": layer,
        "STYLES": "",
        "SRS": DEFAULT_CRS,
        "BBOX": ",".join(str(v) for v in bbox),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": "image/png",
        "TRANSPARENT": "false",
        "TIME": time_.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def get_capabilities(wms_url: str) -> ET.Element:
    response = requests.get(
        wms_url,
        params={"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetCapabilities"},
        timeout=60,
    )
    response.raise_for_status()
    return ET.fromstring(response.content)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def first_child_text(element: ET.Element, child_name: str) -> str | None:
    for child in element:
        if local_name(child.tag) == child_name and child.text:
            return child.text.strip()
    return None


def list_wms_layers(wms_url: str, pattern: str | None = None) -> list[LayerInfo]:
    root = get_capabilities(wms_url)
    layers: list[LayerInfo] = []
    regex = re.compile(pattern, re.IGNORECASE) if pattern else None

    for layer in root.iter():
        if local_name(layer.tag) != "Layer":
            continue
        name = first_child_text(layer, "Name")
        if not name:
            continue
        title = first_child_text(layer, "Title") or ""
        haystack = f"{name} {title}"
        if regex and not regex.search(haystack):
            continue
        layers.append(LayerInfo(name=name, title=title))

    return layers


def download_wms_frame(
    wms_url: str,
    layer: str,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    time_: datetime,
    out_path: Path,
) -> Path:
    response = requests.get(
        wms_url,
        params=wms_params(layer, bbox, width, height, time_),
        timeout=90,
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "xml" in content_type.lower():
        raise RuntimeError(response.text[:1000])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(response.content)
    return out_path


def annotate_frame(path: Path, label: str) -> None:
    image = Image.open(path).convert("RGBA")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    margin = 10
    padding = 6
    bbox = draw.textbbox((0, 0), label, font=font)
    box = (
        margin,
        margin,
        margin + (bbox[2] - bbox[0]) + 2 * padding,
        margin + (bbox[3] - bbox[1]) + 2 * padding,
    )
    draw.rectangle(box, fill=(0, 0, 0, 170))
    draw.text((margin + padding, margin + padding), label, fill=(255, 255, 255, 255), font=font)
    image.convert("RGB").save(path)


def build_gif(frame_paths: list[Path], out_gif: Path, fps: int) -> Path:
    if not frame_paths:
        raise ValueError("No frames downloaded; cannot build GIF")

    images = [Image.open(path).convert("P", palette=Image.Palette.ADAPTIVE) for path in frame_paths]
    duration_ms = int(1000 / fps)
    out_gif.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out_gif,
        save_all=True,
        append_images=images[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    return out_gif


def safe_event_chunks(start: datetime, end: datetime, step_minutes: int, width: int, height: int, max_pixels: int) -> list[tuple[datetime, datetime]]:
    frames = math.ceil(((end - start).total_seconds() / 60) / step_minutes) + 1
    if frames * width * height <= max_pixels:
        return [(start, end)]

    max_frames = max(1, max_pixels // (width * height))
    chunk_minutes = max_frames * step_minutes
    chunks = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(minutes=chunk_minutes - step_minutes))
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(minutes=step_minutes)
    return chunks


def command_list_layers(args: argparse.Namespace) -> int:
    layers = list_wms_layers(args.wms_url, args.pattern)
    for layer in layers:
        safe_print(f"{layer.name}\t{layer.title}")
    safe_print(f"{len(layers)} layers")
    return 0


def command_download_wms(args: argparse.Namespace) -> int:
    bbox = tuple(args.bbox)
    time_ = parse_datetime(args.time)
    out_path = Path(args.output)
    download_wms_frame(args.wms_url, args.layer, bbox, args.width, args.height, time_, out_path)
    annotate_frame(out_path, f"{args.layer} {time_:%Y-%m-%d %H:%M UTC}")
    print(out_path)
    return 0


def command_animate_wms(args: argparse.Namespace) -> int:
    bbox = tuple(args.bbox)
    start = parse_datetime(args.start)
    end = parse_datetime(args.end)
    out_dir = Path(args.output_dir)
    event_name = re.sub(r"[^A-Za-z0-9]+", "_", args.name).strip("_")
    all_gifs = []

    chunks = safe_event_chunks(start, end, args.step_minutes, args.width, args.height, args.max_pixels)
    for chunk_index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        frame_paths = []
        chunk_dir = out_dir / event_name / f"chunk_{chunk_index:02d}"
        for time_ in iter_times(chunk_start, chunk_end, args.step_minutes):
            frame_path = chunk_dir / f"{time_:%Y%m%dT%H%M%SZ}.png"
            download_wms_frame(args.wms_url, args.layer, bbox, args.width, args.height, time_, frame_path)
            annotate_frame(frame_path, f"{args.layer} {time_:%Y-%m-%d %H:%M UTC}")
            frame_paths.append(frame_path)
            print("downloaded", frame_path)
            if args.pause:
                time.sleep(args.pause)

        suffix = f"_part{chunk_index:02d}" if len(chunks) > 1 else ""
        gif_path = out_dir / f"{event_name}{suffix}.gif"
        build_gif(frame_paths, gif_path, args.fps)
        all_gifs.append(gif_path)
        print("gif", gif_path)

    print("GIF outputs:")
    for gif in all_gifs:
        print(gif)
    return 0


def command_search_datastore(args: argparse.Namespace) -> int:
    try:
        import eumdac
    except ImportError:
        print("Install EUMDAC first: pip install eumdac", file=sys.stderr)
        return 2

    key = args.consumer_key or os.environ.get("EUMETSAT_CONSUMER_KEY")
    secret = args.consumer_secret or os.environ.get("EUMETSAT_CONSUMER_SECRET")
    if not key or not secret:
        print("Set EUMETSAT_CONSUMER_KEY and EUMETSAT_CONSUMER_SECRET, or pass both CLI options.", file=sys.stderr)
        return 2

    token = eumdac.AccessToken((key, secret))
    datastore = eumdac.DataStore(token)
    collection = datastore.get_collection(args.collection)

    products = collection.search(dtstart=parse_datetime(args.start), dtend=parse_datetime(args.end))
    for idx, product in enumerate(products):
        if idx >= args.limit:
            break
        print(product)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(required=True)

    list_parser = subparsers.add_parser("list-layers", help="List EUMETView WMS layers")
    list_parser.add_argument("--wms-url", default=DEFAULT_WMS_URL)
    list_parser.add_argument("--pattern", default="msg_iodc|iodc|ir10|ir108|hrv|natural")
    list_parser.set_defaults(func=command_list_layers)

    frame_parser = subparsers.add_parser("download-wms", help="Download one rendered WMS frame")
    frame_parser.add_argument("--wms-url", default=DEFAULT_WMS_URL)
    frame_parser.add_argument("--layer", default=DEFAULT_LAYER)
    frame_parser.add_argument("--time", required=True)
    frame_parser.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX, metavar=("W", "S", "E", "N"))
    frame_parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    frame_parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    frame_parser.add_argument("--output", default="eumetsat_frame.png")
    frame_parser.set_defaults(func=command_download_wms)

    anim_parser = subparsers.add_parser("animate-wms", help="Download WMS frames and build a GIF")
    anim_parser.add_argument("--wms-url", default=DEFAULT_WMS_URL)
    anim_parser.add_argument("--layer", default=DEFAULT_LAYER)
    anim_parser.add_argument("--name", default="maroantsetra_meteosat")
    anim_parser.add_argument("--start", required=True)
    anim_parser.add_argument("--end", required=True)
    anim_parser.add_argument("--step-minutes", type=int, default=15)
    anim_parser.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX, metavar=("W", "S", "E", "N"))
    anim_parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    anim_parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    anim_parser.add_argument("--fps", type=int, default=6)
    anim_parser.add_argument("--max-pixels", type=int, default=26_214_400)
    anim_parser.add_argument("--pause", type=float, default=0.2)
    anim_parser.add_argument("--output-dir", default="eumetsat_outputs")
    anim_parser.set_defaults(func=command_animate_wms)

    store_parser = subparsers.add_parser("search-datastore", help="Search original products through EUMDAC")
    store_parser.add_argument("--collection", default="EO:EUM:DAT:MSG:HRSEVIRI-IODC")
    store_parser.add_argument("--start", required=True)
    store_parser.add_argument("--end", required=True)
    store_parser.add_argument("--limit", type=int, default=10)
    store_parser.add_argument("--consumer-key")
    store_parser.add_argument("--consumer-secret")
    store_parser.set_defaults(func=command_search_datastore)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

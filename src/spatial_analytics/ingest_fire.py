"""Pull NPWS Fire History off the ArcGIS service into GeoParquet.

38,092 polygons of every mapped wildfire and prescribed burn in NSW.

Two things about this service shape the whole module.

**Paging is by OBJECTID, not by offset.** `resultOffset` makes the server
re-sort and re-scan for every page, and a read that takes minutes has time to
disagree with itself. Walking a monotonic key means each request is a range scan
and the set cannot shift under us mid-read. It also makes the job resumable: the
parts already on disk tell us where to start again.

**The advertised page size is a lie.** The layer reports `maxRecordCount: 1000`,
but the real ceiling is bytes. Around OBJECTID 30,000 the polygons get big --
500 features is a 10.7 MB response -- and a 1,000-row request there returns a
500 every time, reproducibly. So the page size adapts: halve on failure, drift
back up once the server is happy again.

    uv run python -m spatial_analytics.ingest_fire
"""
from __future__ import annotations

import sys
import time

import geopandas as gpd
import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from .config import CRS_FIRE_SOURCE, FIRE_PAGE_SIZE, FIRE_SERVICE_URL, RAW, ensure_dirs

TIMEOUT = httpx.Timeout(300.0, connect=30.0)
PARTS_DIR = RAW / "fire_parts"
OUT_PARQUET = RAW / "npws_fire_history.parquet"

MIN_PAGE = 50          # below this, stop blaming the page size
FLUSH_EVERY = 2_000    # features held in memory before a part hits disk
MAX_ATTEMPTS = 5

FIELDS = (
    "OBJECTID,FireType,FireName,FireNo,FireYear,Label,"
    "StartDate,EndDate,Intensity,AreaHa,NPWSBranch,NPWSArea"
)


def _get(client: httpx.Client, params: dict) -> dict:
    r = client.get(f"{FIRE_SERVICE_URL}/query", params=params)
    r.raise_for_status()
    body = r.json()
    # ArcGIS answers a failed query with HTTP 200 and an error object, so a
    # status check alone proves nothing.
    if "error" in body:
        raise RuntimeError(f"service error: {body['error']}")
    return body


def total_count(client: httpx.Client) -> int:
    return _get(client, {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]


def fetch_page(client: httpx.Client, after_oid: int, size: int) -> dict:
    return _get(
        client,
        {
            "where": f"OBJECTID>{after_oid}",
            "outFields": FIELDS,
            "orderByFields": "OBJECTID ASC",
            "resultRecordCount": size,
            "returnGeometry": "true",
            "outSR": CRS_FIRE_SOURCE,  # keep native GDA94; reproject in DuckDB
            "f": "geojson",
        },
    )


def resume_point() -> tuple[int, int]:
    """Highest OBJECTID already written, and how many features that covers."""
    if not PARTS_DIR.exists():
        return -1, 0
    parts = sorted(PARTS_DIR.glob("part_*.parquet"))
    if not parts:
        return -1, 0
    last_oid, n = -1, 0
    for p in parts:
        t = pq.read_table(p, columns=["OBJECTID"])
        n += t.num_rows
        if t.num_rows:
            last_oid = max(last_oid, max(t.column("OBJECTID").to_pylist()))
    return last_oid, n


def flush(features: list[dict], seq: int) -> None:
    gdf = gpd.GeoDataFrame.from_features(features, crs=f"EPSG:{CRS_FIRE_SOURCE}")
    gdf.to_parquet(PARTS_DIR / f"part_{seq:04d}.parquet")


def fetch_all() -> int:
    ensure_dirs()
    PARTS_DIR.mkdir(parents=True, exist_ok=True)

    last_oid, done = resume_point()
    seq = len(list(PARTS_DIR.glob("part_*.parquet")))
    if done:
        print(f"  resuming after OBJECTID {last_oid} ({done:,} already on disk)")

    buf: list[dict] = []
    size = FIRE_PAGE_SIZE

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        expected = total_count(client)
        print(f"  service reports {expected:,} polygons")

        while True:
            page, attempt = None, 0
            while page is None:
                attempt += 1
                try:
                    page = fetch_page(client, last_oid, size)
                except (httpx.HTTPStatusError, httpx.TransportError, RuntimeError) as e:
                    if attempt >= MAX_ATTEMPTS:
                        raise
                    # A 500 here is almost always the response being too large,
                    # not the query being wrong. Shrink first, then back off.
                    if size > MIN_PAGE:
                        size = max(MIN_PAGE, size // 2)
                        print(f"\n  {type(e).__name__} — page size -> {size}")
                    else:
                        print(f"\n  {type(e).__name__} — retry {attempt}/{MAX_ATTEMPTS}")
                    time.sleep(2 * attempt)

            got = page.get("features") or []
            if not got:
                break

            buf.extend(got)
            done += len(got)
            last_oid = got[-1]["properties"]["OBJECTID"]
            print(f"  {done:>6,} / {expected:,}  (page {size})", end="\r", flush=True)

            if len(buf) >= FLUSH_EVERY:
                flush(buf, seq)
                buf, seq = [], seq + 1

            # Recover from a shrink: the big polygons are clustered, not global.
            if size < FIRE_PAGE_SIZE and len(got) == size:
                size = min(FIRE_PAGE_SIZE, size * 2)

    if buf:
        flush(buf, seq)

    print(f"\n  fetched {done:,} polygons")
    if done != expected:
        print(f"  WARNING: expected {expected:,}, got {done:,}")
    return done


def merge_parts() -> None:
    """Concatenate the part files into one GeoParquet, metadata intact.

    Each part was typed independently from whatever JSON happened to be in that
    page, so the schemas drift: a page where every `Intensity` is null lands as
    double, a page of real codes lands as int64, and concatenating them refuses.
    Unifying the schemas first and casting every part to it is the fix -- the
    alternative, trusting the first part's types, silently truncates.
    """
    parts = sorted(PARTS_DIR.glob("part_*.parquet"))
    if not parts:
        raise SystemExit("no part files to merge")

    tables = [pq.read_table(p) for p in parts]
    schema = pa.unify_schemas(
        [t.schema for t in tables], promote_options="permissive"
    )
    merged = pa.concat_tables([t.cast(schema) for t in tables])
    # Keep the geo metadata, or the file stops being GeoParquet.
    merged = merged.replace_schema_metadata(tables[0].schema.metadata)
    pq.write_table(merged, OUT_PARQUET, compression="zstd")

    mb = OUT_PARQUET.stat().st_size / 1024 / 1024
    print(f"  wrote {OUT_PARQUET.name}  {merged.num_rows:,} rows, {mb:.1f} MB")


def main() -> int:
    fetch_all()
    merge_parts()
    return 0


if __name__ == "__main__":
    sys.exit(main())

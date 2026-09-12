"""Load the NSW slice of G-NAF into DuckDB as one point per address.

G-NAF does not arrive as a file you can open. The August 2026 release is a
1.7 GB zip of 212 pipe-separated fragments, national, and the address you want
is spread across three of them: ADDRESS_DETAIL (what it is), DEFAULT_GEOCODE
(where it is) and ADDRESS_MESH_BLOCK_2021 (which ABS mesh block it sits in).
This joins the NSW three into one table with a metric point geometry.

Two filters matter and both are easy to forget:

* `DATE_RETIRED is null` on every table. G-NAF keeps history; a retired address
  is still a row.
* `LEVEL_GEOCODED_CODE = 7`. Only that level means the point is *this address*.
  Anything lower is a street or locality centroid -- hundreds of addresses on
  one dot, all of which land on the same side of every fire line -- and
  counting them would be counting the town, not the houses.

Extract first (only the NSW files are needed):

    unzip -j data/raw/gnaf/g-naf_aug26_gda2020.zip \\
      "G-NAF/G-NAF AUGUST 2026/Standard/NSW_ADDRESS_DETAIL_psv.psv" \\
      "G-NAF/G-NAF AUGUST 2026/Standard/NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv" \\
      "G-NAF/G-NAF AUGUST 2026/Standard/NSW_ADDRESS_MESH_BLOCK_2021_psv.psv" \\
      "G-NAF/G-NAF AUGUST 2026/Standard/NSW_MB_2021_psv.psv" \\
      -d data/raw/gnaf
    uv run python -m spatial_analytics.ingest_gnaf
"""
from __future__ import annotations

import sys

from .config import CRS_GNAF_SOURCE, CRS_METRIC, RAW
from .db import connect

GNAF = RAW / "gnaf"
FILES = {
    "detail": GNAF / "NSW_ADDRESS_DETAIL_psv.psv",
    "geocode": GNAF / "NSW_ADDRESS_DEFAULT_GEOCODE_psv.psv",
    "mb_link": GNAF / "NSW_ADDRESS_MESH_BLOCK_2021_psv.psv",
    "mb": GNAF / "NSW_MB_2021_psv.psv",
}


def psv(path) -> str:
    # Every column as text first; nothing in G-NAF is safe to auto-type
    # (postcodes with leading zeros, PIDs that look numeric and are not).
    return (
        f"read_csv('{path.as_posix()}', delim='|', header=true, "
        f"all_varchar=true, quote='')"
    )


BUILD = f"""
create or replace table gnaf_address as
with detail as (
    select ADDRESS_DETAIL_PID          as address_pid,
           LEVEL_GEOCODED_CODE::int   as geocoded_level,
           CONFIDENCE::int            as confidence,
           nullif(FLAT_NUMBER, '')    as flat_number,
           nullif(NUMBER_FIRST, '')   as number_first,
           STREET_LOCALITY_PID        as street_locality_pid,
           LOCALITY_PID               as locality_pid,
           POSTCODE                   as postcode,
           PRIMARY_SECONDARY          as primary_secondary
    from {psv(FILES['detail'])}
    where DATE_RETIRED is null
),
geocode as (
    select ADDRESS_DETAIL_PID  as address_pid,
           GEOCODE_TYPE_CODE   as geocode_type,
           LONGITUDE::double   as lon,
           LATITUDE::double    as lat
    from {psv(FILES['geocode'])}
    where DATE_RETIRED is null
),
mb as (
    select l.ADDRESS_DETAIL_PID as address_pid,
           m.MB_2021_CODE       as mb_2021_code
    from {psv(FILES['mb_link'])} l
    join {psv(FILES['mb'])} m using (MB_2021_PID)
    where l.DATE_RETIRED is null and m.DATE_RETIRED is null
)
select d.*,
       g.geocode_type, g.lon, g.lat,
       mb.mb_2021_code,
       st_transform(st_point(g.lon, g.lat),
                    'EPSG:{CRS_GNAF_SOURCE}', 'EPSG:{CRS_METRIC}',
                    always_xy := true) as geom
from detail d
join geocode g using (address_pid)
left join mb using (address_pid)
"""


def main() -> int:
    missing = [k for k, p in FILES.items() if not p.exists()]
    if missing:
        raise SystemExit(f"missing extracted G-NAF files: {missing} — see module docstring")

    con = connect()
    print("  loading NSW G-NAF (detail + geocode + mesh block)...")
    con.execute(BUILD)

    total = con.execute("select count(*) from gnaf_address").fetchone()[0]
    print(f"  {total:,} live NSW addresses with a geocode")

    print("\n  -- geocoded level (7 = this address; lower = a centroid of something bigger)")
    print(
        con.execute(
            """
            select geocoded_level, count(*) as n,
                   round(100.0 * count(*) / sum(count(*)) over (), 2) as pct
            from gnaf_address group by 1 order by 1
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- geocode type, top 8")
    print(
        con.execute(
            "select geocode_type, count(*) as n from gnaf_address "
            "group by 1 order by n desc limit 8"
        ).fetchdf().to_string(index=False)
    )

    no_mb = con.execute(
        "select count(*) from gnaf_address where mb_2021_code is null"
    ).fetchone()[0]
    print(f"\n  addresses with no 2021 mesh block: {no_mb:,}")

    empty = con.execute("select count(*) from gnaf_address where st_isempty(geom)").fetchone()[0]
    if empty:
        raise SystemExit(f"{empty} empty points after reprojection — check axis order")

    con.execute("checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Which addresses sit in the bushfire interface, and how close.

The plan said this join would be the engineering problem: a few million points
against thousands of large polygons, an expensive predicate run millions of
times. It was not. DuckDB's planner picks a dedicated SPATIAL_JOIN operator,
builds the RTree itself, and the naive query finishes in about six seconds. So
there is no dissolve step -- overlapping fires are handled by counting each
address once, and the buffers are computed per fire, not on a merged extent.

Interface width is a result, not a constant, so the join runs once per band in
`BUFFER_BANDS_M` and every address keeps the tightest band it falls in.

    uv run python -m spatial_analytics.build_exposure
"""
from __future__ import annotations

import sys
import time

from .config import BUFFER_BANDS_M
from .db import connect


def main() -> int:
    con = connect()
    bands = ", ".join(str(b) for b in BUFFER_BANDS_M)

    # One buffered copy of every wildfire per band. Band 0 is the footprint.
    t = time.time()
    con.execute(
        f"""
        create or replace table fire_buffer as
        select b.band_m, f.fire_id, f.season_start_year,
               case when b.band_m = 0 then f.geom else st_buffer(f.geom, b.band_m) end as geom
        from fire f, (select unnest([{bands}]) as band_m) b
        where f.fire_type_code = 1
        """
    )
    print(f"  fire_buffer built  [{time.time() - t:.1f}s]")

    # Every (address, fire, band) hit. Address-level geocodes only: a street or
    # locality centroid is not a house, and every address sharing it would land
    # on the same side of every line.
    t = time.time()
    con.execute(
        """
        create or replace table exposure_pairs as
        select a.address_pid, f.fire_id, f.band_m, f.season_start_year
        from gnaf_address a
        join fire_buffer f on st_intersects(a.geom, f.geom)
        where a.geocoded_level = 7
        """
    )
    n = con.execute("select count(*) from exposure_pairs").fetchone()[0]
    print(f"  exposure_pairs: {n:,} address-fire-band hits  [{time.time() - t:.1f}s]")

    # One row per address: how close, how many, how recent.
    con.execute(
        """
        create or replace table address_exposure as
        select a.address_pid, a.mb_2021_code, a.locality_pid, a.postcode, a.geom,
               min(p.band_m)                                            as min_band_m,
               count(distinct p.fire_id) filter (where p.band_m = 0)    as fires_on_site,
               count(distinct p.fire_id)                                as fires_within_max_band,
               max(p.season_start_year) filter (where p.band_m = 0)     as last_burnt_season,
               max(p.season_start_year)                                 as last_fire_nearby_season
        from gnaf_address a
        join exposure_pairs p using (address_pid)
        group by all
        """
    )

    total = con.execute(
        "select count(*) from gnaf_address where geocoded_level = 7"
    ).fetchone()[0]
    print(f"\n  -- exposure by interface width ({total:,} address-level points in NSW)")
    print(
        con.execute(
            f"""
            select band_m as within_m,
                   count(distinct address_pid)                                    as addresses,
                   round(100.0 * count(distinct address_pid) / {total}, 2)        as pct_of_nsw,
                   count(distinct address_pid) filter (where season_start_year >= 2000) as since_2000,
                   count(distinct address_pid) filter (where season_start_year >= 2019) as since_black_summer
            from exposure_pairs group by 1 order by 1
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- addresses burnt over more than once (footprint only)")
    print(
        con.execute(
            """
            select fires_on_site, count(*) as addresses
            from address_exposure where fires_on_site > 0
            group by 1 order by 1 limit 8
            """
        ).fetchdf().to_string(index=False)
    )

    con.execute("checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Turn the raw fire history into one clean, metric, analysis-ready table.

Four things happen here, and each one exists because of something the raw data
does wrong:

1. **Repair.** 332 polygons are invalid. `ST_MakeValid` fixes all of them, but it
   can change a geometry's type -- a self-intersecting ring splits into a
   multipolygon -- so the type is not assumed downstream, and the area before and
   after is compared to prove the repair did not quietly eat land.
2. **Decode `FireYear`.** It is a 6-digit financial year (`190203` = the 1902-03
   season), not a year, with exactly one malformed 4-digit row. Comparing it to a
   calendar year without decoding silently matches the whole table.
3. **Fix the dates.** ArcGIS hands back epoch milliseconds, which land as DOUBLE.
   They are also null on 40% and 53% of rows, which is why the decoded season
   year is the real time dimension.
4. **Reproject.** Everything measured happens in EPSG:7856 (GDA2020 / MGA zone
   56), metres. The source is GDA94 geographic, so this is both a datum shift and
   a projection, and it has to happen before any distance or area is believed.
   EPSG:4283 is defined lat/lon in the registry; the data is lon/lat. Without
   `always_xy` PROJ takes 152 as a latitude, every point is out of range, and
   `ST_Transform` returns EMPTY for the whole table -- no error, no warning.

    uv run python -m spatial_analytics.prepare_fire
"""
from __future__ import annotations

import sys

from .config import CRS_FIRE_SOURCE, CRS_METRIC
from .db import connect, register_raw

BUILD = f"""
create or replace table fire as
select
    OBJECTID                                as fire_id,
    FireType                                as fire_type_code,
    case FireType when 1 then 'Wildfire'
                  when 2 then 'Prescribed Burn'
                  else 'Unknown' end        as fire_type,
    nullif(trim(FireName), '')              as fire_name,
    nullif(trim(FireNo), '')                as fire_no,
    FireYear                                as fire_year_raw,
    -- 190203 -> 1902. The one 4-digit row is already a year, so pass it through.
    case when FireYear >= 10000 then (FireYear // 100)::int
         else FireYear::int end             as season_start_year,
    nullif(Intensity, 9999)                 as intensity,
    AreaHa                                  as area_ha_published,
    nullif(trim(NPWSBranch), '')            as npws_branch,
    nullif(trim(NPWSArea), '')              as npws_area,
    -- epoch milliseconds, and null more often than not
    case when StartDate is not null
         then epoch_ms(StartDate::bigint) end as start_date,
    case when EndDate is not null
         then epoch_ms(EndDate::bigint) end   as end_date,
    not st_isvalid(geometry)                as was_repaired,
    -- repaired twice: once so the transform has valid input, once after,
    -- because projecting a near-degenerate ring can make it invalid again
    st_makevalid(st_transform(st_makevalid(geometry),
                 'EPSG:{CRS_FIRE_SOURCE}', 'EPSG:{CRS_METRIC}',
                 always_xy := true))            as geom
from fire_raw
"""


def main() -> int:
    con = connect()
    register_raw(con)

    print("  building fire (repair -> decode -> reproject)...")
    con.execute(BUILD)

    n, repaired = con.execute(
        "select count(*), count(*) filter (where was_repaired) from fire"
    ).fetchone()
    print(f"  {n:,} polygons, {repaired:,} repaired")

    bad, empty = con.execute(
        "select count(*) filter (where not st_isvalid(geom)), "
        "count(*) filter (where st_isempty(geom)) from fire"
    ).fetchone()
    print(f"  invalid after repair: {bad}   empty after reproject: {empty}")
    if empty:
        raise SystemExit("reprojection produced empty geometries — check axis order")

    # Did the repair lose land? Compare computed area against the published
    # AreaHa, on the repaired rows only.
    print("\n  -- repair area check (hectares, computed vs published)")
    print(
        con.execute(
            """
            select was_repaired,
                   count(*) as n,
                   round(sum(st_area(geom)) / 10000.0, 1) as computed_ha,
                   round(sum(area_ha_published), 1)       as published_ha,
                   round(100.0 * (sum(st_area(geom)) / 10000.0
                         - sum(area_ha_published)) / sum(area_ha_published), 3) as pct_diff
            from fire group by 1 order by was_repaired
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- season coverage")
    print(
        con.execute(
            """
            select min(season_start_year) as first_season,
                   max(season_start_year) as last_season,
                   count(distinct season_start_year) as seasons
            from fire
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- wildfire area by decade (million ha burnt)")
    print(
        con.execute(
            """
            select (season_start_year // 10) * 10 as decade,
                   count(*) as fires,
                   round(sum(st_area(geom)) / 1e10, 2) as million_ha
            from fire where fire_type_code = 1
            group by 1 order by 1
            """
        ).fetchdf().to_string(index=False)
    )

    con.execute("checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())

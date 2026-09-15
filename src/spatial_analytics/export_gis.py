"""Write the results as a GeoPackage, for QGIS or ArcGIS Pro.

Everything the analysis produced, in the one file format both desktop GIS
tools open natively, all in EPSG:7856 (GDA2020 / MGA zone 56):

  fires          every mapped wildfire, repaired, with the decoded season
  burnt_5yr      the dissolved burnt extent in five-year bins (what the map plays)
  homes_100m     today's homes and farm addresses within 100 m of burnt land
  sa1_exposure   ABS SA1 polygons with the share of homes exposed, by band

GeoPandas writes it; DuckDB does the joins. SA1 polygons come from the ABS
digital boundary file (GDA2020), filtered to NSW.

    uv run python -m spatial_analytics.export_gis
"""
from __future__ import annotations

import sys
import time

import geopandas as gpd

from .config import CRS_METRIC, PROCESSED, RAW
from .db import connect

OUT = PROCESSED / "bushfire.gpkg"
SA1_SHP = RAW / "abs" / "sa1" / "SA1_2021_AUST_GDA2020.shp"


def frame(con, sql: str) -> gpd.GeoDataFrame:
    df = con.execute(sql).fetchdf()
    return gpd.GeoDataFrame(df.drop(columns=["wkb"]), geometry=gpd.GeoSeries.from_wkb(df["wkb"].map(bytes)), crs=f"EPSG:{CRS_METRIC}")


def main() -> int:
    con = connect(read_only=True)
    if OUT.exists():
        OUT.unlink()
    t = time.time()

    fires = frame(con, """
        select fire_id, fire_name, season_start_year, fire_type, intensity, area_ha_published,
               npws_branch, was_repaired, st_aswkb(geom) as wkb
        from fire where fire_type_code = 1 and season_start_year is not null""")
    fires.to_file(OUT, layer="fires", driver="GPKG")
    print(f"  fires         {len(fires):>8,}  [{time.time() - t:.0f}s]")

    burnt = frame(con, """
        select (season_start_year // 5) * 5 as period_from, (season_start_year // 5) * 5 + 4 as period_to,
               st_aswkb(st_union_agg(st_makevalid(st_buffer(geom, 20)))) as wkb
        from fire where fire_type_code = 1 and season_start_year is not null group by 1, 2 order by 1""")
    burnt["area_ha"] = burnt.geometry.area / 10_000
    burnt.to_file(OUT, layer="burnt_5yr", driver="GPKG")
    print(f"  burnt_5yr     {len(burnt):>8,}  [{time.time() - t:.0f}s]")

    homes = frame(con, """
        with first as (select address_pid, min(season_start_year) as first_exposed
                       from exposure_pairs where band_m <= 100 and season_start_year is not null group by 1)
        select h.address_pid, h.sa2_name, h.sa4_name, h.mb_category,
               h.min_band_m as within_m, h.fires_on_site, h.last_burnt_season, f.first_exposed,
               st_aswkb(h.geom) as wkb
        from address_home h left join first f using (address_pid)
        where h.min_band_m <= 100 and (h.is_residential or h.is_primary_production)""")
    homes.to_file(OUT, layer="homes_100m", driver="GPKG")
    print(f"  homes_100m    {len(homes):>8,}  [{time.time() - t:.0f}s]")

    sa1 = gpd.read_file(SA1_SHP, where="STE_NAME21 = 'New South Wales'")[["SA1_CODE21", "SA2_NAME21", "SA4_NAME21", "geometry"]]
    sa1 = sa1.rename(columns={"SA1_CODE21": "sa1_code", "SA2_NAME21": "sa2_name", "SA4_NAME21": "sa4_name"}).to_crs(CRS_METRIC)
    exp = con.execute("""
        select sa1_code, homes, persons, median_age, median_hh_income_weekly,
               max(case when band_m = 0 then homes_exposed end)    as homes_in_footprint,
               max(case when band_m = 100 then homes_exposed end)  as homes_100m,
               max(case when band_m = 500 then homes_exposed end)  as homes_500m,
               max(case when band_m = 1000 then homes_exposed end) as homes_1km,
               max(case when band_m = 100 then share_exposed end)  as share_100m,
               max(case when band_m = 100 then persons_exposed end) as persons_100m,
               max(case when band_m = 100 then age_65_74_exposed + age_75_84_exposed + age_85_plus_exposed end) as persons_65plus_100m
        from sa1_exposure group by 1, 2, 3, 4, 5""").fetchdf()
    sa1 = sa1.merge(exp, on="sa1_code", how="left")
    sa1.to_file(OUT, layer="sa1_exposure", driver="GPKG")
    print(f"  sa1_exposure  {len(sa1):>8,}  [{time.time() - t:.0f}s]")

    print(f"  wrote {OUT.name}  {OUT.stat().st_size / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

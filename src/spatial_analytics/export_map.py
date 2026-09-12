"""Write the small files the deck.gl map reads.

The analysis never needed a dissolve; the map does. 22,810 overlapping wildfire
polygons is 210 MB of GeoJSON, and the same ground drawn twelve times. Unioned
into three eras, simplified to 80 m and rounded to 4 decimals it is a few MB
and looks the same at any zoom the map offers.

Points are homes and farms within 100 m of a mapped wildfire, as a flat array
rather than GeoJSON -- 185,000 features with property objects is most of the
size; 185,000 rows of seven numbers is not.

    uv run python -m spatial_analytics.export_map
"""
from __future__ import annotations

import json
import sys

from .config import ROOT
from .db import connect

WEB_DATA = ROOT / "web" / "data"

ERAS = [
    ("before 2000", "season_start_year < 2000"),
    ("2000–2018", "season_start_year between 2000 and 2018"),
    ("2019 onward", "season_start_year >= 2019"),
]


def to_wgs84(expr: str) -> str:
    return f"st_transform({expr}, 'EPSG:7856', 'EPSG:4326', always_xy := true)"


def export_fires(con) -> None:
    features = []
    for label, where in ERAS:
        gj = con.execute(
            f"""
            select st_asgeojson({to_wgs84("st_simplify(st_union_agg(st_simplify(st_buffer(geom, 20), 60)), 80)")})
            from fire where fire_type_code = 1 and {where}
            """
        ).fetchone()[0]
        geom = json.loads(gj)
        _round_coords(geom["coordinates"])
        features.append({"type": "Feature", "properties": {"era": label}, "geometry": geom})
    out = WEB_DATA / "fires.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")))
    print(f"  fires.geojson  {out.stat().st_size / 1e6:.1f} MB, {len(features)} eras")


def _round_coords(c, nd=4):
    if isinstance(c[0], (int, float)):
        c[0], c[1] = round(c[0], nd), round(c[1], nd)
    else:
        for x in c:
            _round_coords(x, nd)


def export_homes(con) -> None:
    sa2 = [r[0] for r in con.execute(
        "select distinct sa2_name from address_home where min_band_m <= 100 "
        "and (is_residential or is_primary_production) order by 1"
    ).fetchall()]
    idx = {name: i for i, name in enumerate(sa2)}

    rows = con.execute(
        f"""
        select round(st_x(p), 5), round(st_y(p), 5), min_band_m, sa2_name,
               coalesce(last_burnt_season, 0), coalesce(fires_on_site, 0), is_primary_production
        from (select {to_wgs84("geom")} as p, * from address_home)
        where min_band_m <= 100 and (is_residential or is_primary_production)
        """
    ).fetchall()
    pts = [[x, y, b, idx[s], ls, fo, int(f)] for x, y, b, s, ls, fo, f in rows]
    out = WEB_DATA / "homes.json"
    out.write_text(json.dumps({"sa2": sa2, "cols": ["lon", "lat", "band_m", "sa2", "last_burnt", "fires_on_site", "farm"], "pts": pts}, separators=(",", ":")))
    print(f"  homes.json     {out.stat().st_size / 1e6:.1f} MB, {len(pts):,} points")


def export_summary(con) -> None:
    bands = con.execute(
        """
        select band_m, sum(homes_exposed)::int, sum(farm_addresses_exposed)::int,
               sum(persons_exposed)::int,
               sum(age_65_74_exposed + age_75_84_exposed + age_85_plus_exposed)::int,
               sum(age_0_4_exposed + age_5_14_exposed)::int
        from sa1_exposure group by 1 order by 1
        """
    ).fetchall()
    top = con.execute(
        """
        select m.sa2_name, m.sa4_name, sum(s.homes_exposed)::int, sum(s.persons_exposed)::int,
               round(100.0 * sum(s.homes_exposed) / sum(s.homes), 1)
        from sa1_exposure s
        join (select distinct sa1_code, sa2_name, sa4_name from mesh_block) m using (sa1_code)
        where s.band_m = 100 group by 1, 2 order by 4 desc limit 15
        """
    ).fetchall()
    gccsa = con.execute(
        """
        select m.gccsa_name, sum(s.homes_exposed)::int, sum(s.persons_exposed)::int
        from sa1_exposure s join (select distinct sa1_code, gccsa_name from mesh_block) m using (sa1_code)
        where s.band_m = 100 group by 1 order by 3 desc
        """
    ).fetchall()
    out = WEB_DATA / "summary.json"
    out.write_text(json.dumps({
        "bands": [dict(zip(("within_m", "homes", "farms", "persons", "aged_65_plus", "under_15"), r)) for r in bands],
        "top_sa2": [dict(zip(("sa2", "sa4", "homes", "persons", "pct_of_homes"), r)) for r in top],
        "gccsa": [dict(zip(("name", "homes", "persons"), r)) for r in gccsa],
    }, indent=1))
    print(f"  summary.json")


def main() -> int:
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    con = connect(read_only=True)
    export_fires(con)
    export_homes(con)
    export_summary(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())

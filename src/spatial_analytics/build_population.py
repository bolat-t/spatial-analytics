"""From exposed addresses to exposed people.

The plan was an area-weighted overlay: intersect the interface with SA1
polygons and assume population is spread evenly across each one. G-NAF made
that unnecessary. Every address already carries its mesh block, every mesh block
rolls up to an SA1, so the exposed share of an SA1 is simply

    exposed residential addresses in the SA1 / all residential addresses in it

which weights by where the houses actually are rather than by land area. A
bush-backed SA1 with all its houses on the road frontage gets the right answer;
an area overlay would have called half of it exposed.

The one assumption left is that people are spread evenly across the *addresses*
of an SA1 -- that a flat and a house on the same block hold the same number of
people. Stated here because every number below rests on it.

"Home" means an address in a mesh block the ABS classes as Residential. Primary
Production blocks (farms) are reported separately: farmhouses are homes and they
are the most exposed kind, but the block category cannot tell a homestead from a
paddock, so they get their own column rather than a silent inclusion.

    uv run python -m spatial_analytics.build_population
"""
from __future__ import annotations

import sys

from .config import BUFFER_BANDS_M
from .db import connect

AGE_BANDS = (
    "age_0_4", "age_5_14", "age_15_19", "age_20_24", "age_25_34", "age_35_44",
    "age_45_54", "age_55_64", "age_65_74", "age_75_84", "age_85_plus",
)


def main() -> int:
    con = connect()
    bands = ", ".join(str(b) for b in BUFFER_BANDS_M)

    # Every address-level point with its block category and SA1, exposed or not.
    con.execute(
        """
        create or replace table address_home as
        select a.address_pid, a.geom, m.mb_category, m.sa1_code, m.sa2_name, m.sa4_name,
               m.gccsa_name,
               m.mb_category = 'Residential'        as is_residential,
               m.mb_category = 'Primary Production' as is_primary_production,
               e.min_band_m, e.fires_on_site, e.last_burnt_season, e.last_fire_nearby_season
        from gnaf_address a
        join mesh_block m using (mb_2021_code)
        left join address_exposure e using (address_pid)
        where a.geocoded_level = 7
        """
    )

    # Per SA1, per band: the exposed share, and people scaled by it.
    age_cols = ",\n               ".join(
        f"round(c.{col} * x.share_exposed) as {col}_exposed" for col in AGE_BANDS
    )
    con.execute(
        f"""
        create or replace table sa1_exposure as
        with base as (
            select sa1_code,
                   count(*) filter (where is_residential)        as homes,
                   count(*) filter (where is_primary_production) as farm_addresses
            from address_home group by 1
        ),
        exposed as (
            select h.sa1_code, b.band_m,
                   count(*) filter (where h.is_residential and h.min_band_m <= b.band_m)        as homes_exposed,
                   count(*) filter (where h.is_primary_production and h.min_band_m <= b.band_m) as farm_addresses_exposed
            from address_home h, (select unnest([{bands}]) as band_m) b
            group by 1, 2
        ),
        x as (
            select e.sa1_code, e.band_m, base.homes, e.homes_exposed,
                   base.farm_addresses, e.farm_addresses_exposed,
                   case when base.homes > 0 then e.homes_exposed::double / base.homes else 0 end as share_exposed
            from exposed e join base using (sa1_code)
        )
        select x.*,
               c.persons,
               round(c.persons * x.share_exposed)   as persons_exposed,
               {age_cols},
               c.median_age, c.median_hh_income_weekly
        from x join census_sa1 c using (sa1_code)
        """
    )

    # ---- headline ----------------------------------------------------------
    print("\n  == NSW homes and people in the bushfire interface ==")
    print("  (home = address in a Residential mesh block; people scaled by exposed share of SA1)\n")
    print(
        con.execute(
            """
            select band_m as within_m,
                   sum(homes_exposed)          as homes,
                   sum(farm_addresses_exposed) as farm_addresses,
                   sum(persons_exposed)        as persons,
                   round(sum(age_65_74_exposed + age_75_84_exposed + age_85_plus_exposed)) as persons_65_plus,
                   round(sum(age_0_4_exposed + age_5_14_exposed))                          as persons_under_15,
                   count(*) filter (where homes_exposed > 0)   as sa1s_touched
            from sa1_exposure group by 1 order by 1
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- age profile: within 100 m of a wildfire vs all of NSW (% of persons)")
    sums = ", ".join(f"sum({c}_exposed) as {c}" for c in AGE_BANDS)
    nsw = ", ".join(f"sum({c}) as {c}" for c in AGE_BANDS)
    print(
        con.execute(
            f"""
            with e as (select {sums}, sum(persons_exposed) as total from sa1_exposure where band_m = 100),
                 n as (select {nsw}, sum(persons) as total from census_sa1)
            select band,
                   round(100.0 * e_val / e.total, 1) as pct_exposed,
                   round(100.0 * n_val / n.total, 1) as pct_nsw
            from e, n,
                 (select unnest([{", ".join(repr(c) for c in AGE_BANDS)}]) as band,
                         unnest([{", ".join("e." + c for c in AGE_BANDS)}]) as e_val,
                         unnest([{", ".join("n." + c for c in AGE_BANDS)}]) as n_val
                  from e, n) u
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- top 12 SA2s by people within 100 m of a mapped wildfire")
    print(
        con.execute(
            """
            select m.sa2_name, m.sa4_name,
                   sum(s.homes_exposed) as homes, sum(s.persons_exposed) as persons,
                   round(100.0 * sum(s.homes_exposed) / sum(s.homes), 1) as pct_of_homes
            from sa1_exposure s
            join (select distinct sa1_code, sa2_name, sa4_name from mesh_block) m using (sa1_code)
            where s.band_m = 100
            group by 1, 2 order by persons desc limit 12
            """
        ).fetchdf().to_string(index=False)
    )

    print("\n  -- greater Sydney vs rest of NSW, within 100 m")
    print(
        con.execute(
            """
            select m.gccsa_name, sum(s.homes_exposed) as homes, sum(s.persons_exposed) as persons
            from sa1_exposure s
            join (select distinct sa1_code, gccsa_name from mesh_block) m using (sa1_code)
            where s.band_m = 100 group by 1 order by persons desc
            """
        ).fetchdf().to_string(index=False)
    )

    con.execute("checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())

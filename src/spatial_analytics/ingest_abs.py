"""Load the ABS side: mesh block allocation and the SA1 census tables.

Two files, both small, both from abs.gov.au:

* `MB_2021_AUST.xlsx` -- every mesh block in the country with its category
  (Residential, Parkland, Commercial ...) and the SA1/SA2/SA4 it rolls up to.
  The CSV version 404s; the xlsx is what actually downloads.
* `2021_GCP_SA1_for_NSW` DataPack -- G01 (persons by age band) and G02
  (medians) per SA1. Downloaded as one zip; only those two CSVs are extracted.

G-NAF already tags every address with a 2021 mesh block, so joining an address
to its SA1 -- and knowing whether it sits in a residential block -- is a key
join, not a spatial one. That was not the plan. It is better than the plan.

    uv run python -m spatial_analytics.ingest_abs
"""
from __future__ import annotations

import sys

from .config import RAW
from .db import connect

ABS = RAW / "abs"
MB_XLSX = ABS / "MB_2021_AUST.xlsx"
G01 = ABS / "2021Census_G01_NSW_SA1.csv"
G02 = ABS / "2021Census_G02_NSW_SA1.csv"


def main() -> int:
    for p in (MB_XLSX, G01, G02):
        if not p.exists():
            raise SystemExit(f"missing {p.relative_to(RAW.parent)} — see module docstring")

    con = connect()
    con.execute("INSTALL excel; LOAD excel;")

    con.execute(
        f"""
        create or replace table mesh_block as
        select MB_CODE_2021      as mb_2021_code,
               MB_CATEGORY_2021  as mb_category,
               SA1_CODE_2021     as sa1_code,
               SA2_CODE_2021     as sa2_code,
               SA2_NAME_2021     as sa2_name,
               SA3_NAME_2021     as sa3_name,
               SA4_CODE_2021     as sa4_code,
               SA4_NAME_2021     as sa4_name,
               GCCSA_NAME_2021   as gccsa_name,
               AREA_ALBERS_SQKM  as area_sqkm
        from read_xlsx('{MB_XLSX.as_posix()}')
        where STATE_NAME_2021 = 'New South Wales'
        """
    )
    n = con.execute("select count(*) from mesh_block").fetchone()[0]
    print(f"  mesh_block: {n:,} NSW mesh blocks")

    # G01 is wide (over 100 columns); keep the population and the age bands.
    con.execute(
        f"""
        create or replace table census_sa1 as
        select g1.SA1_CODE_2021   as sa1_code,
               g1.Tot_P_P         as persons,
               g1.Age_0_4_yr_P    as age_0_4,
               g1.Age_5_14_yr_P   as age_5_14,
               g1.Age_15_19_yr_P  as age_15_19,
               g1.Age_20_24_yr_P  as age_20_24,
               g1.Age_25_34_yr_P  as age_25_34,
               g1.Age_35_44_yr_P  as age_35_44,
               g1.Age_45_54_yr_P  as age_45_54,
               g1.Age_55_64_yr_P  as age_55_64,
               g1.Age_65_74_yr_P  as age_65_74,
               g1.Age_75_84_yr_P  as age_75_84,
               g1.Age_85ov_P      as age_85_plus,
               g2.Median_age_persons        as median_age,
               g2.Median_tot_hhd_inc_weekly as median_hh_income_weekly,
               g2.Median_rent_weekly        as median_rent_weekly
        from read_csv('{G01.as_posix()}', header=true, types={{'SA1_CODE_2021': 'VARCHAR'}}) g1
        join read_csv('{G02.as_posix()}', header=true, types={{'SA1_CODE_2021': 'VARCHAR'}}) g2
          using (SA1_CODE_2021)
        """
    )
    n, pop = con.execute("select count(*), sum(persons) from census_sa1").fetchone()
    print(f"  census_sa1: {n:,} SA1s, {pop:,} persons")

    # Do the address mesh blocks actually resolve?
    print("\n  -- address-level G-NAF points by mesh block category")
    print(
        con.execute(
            """
            select coalesce(m.mb_category, '(no match)') as mb_category,
                   count(*) as addresses,
                   round(100.0 * count(*) / sum(count(*)) over (), 2) as pct
            from gnaf_address a
            left join mesh_block m using (mb_2021_code)
            where a.geocoded_level = 7
            group by 1 order by addresses desc
            """
        ).fetchdf().to_string(index=False)
    )

    con.execute("checkpoint")
    return 0


if __name__ == "__main__":
    sys.exit(main())

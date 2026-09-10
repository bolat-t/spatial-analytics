"""Data-quality report on the raw fire history.

Run before trusting anything downstream. Every number here is a finding that
ends up in the write-up, so the script prints rather than asserts -- the point
is to see the shape of the mess, not to fail a build.

    uv run python -m spatial_analytics.profile_fire
"""
from __future__ import annotations

import sys

from .config import FIRE_TYPE
from .db import connect, register_raw

QUERIES: list[tuple[str, str]] = [
    (
        "Geometry validity",
        """
        select st_isvalid(geometry) as valid, count(*) as n,
               round(100.0 * count(*) / sum(count(*)) over (), 2) as pct
        from fire_raw group by 1 order by valid
        """,
    ),
    (
        "Geometry type",
        "select st_geometrytype(geometry) as type, count(*) as n "
        "from fire_raw group by 1 order by n desc",
    ),
    (
        "Fire type",
        """
        select FireType as code, count(*) as n,
               round(sum(AreaHa) / 1e6, 2) as million_ha
        from fire_raw group by 1 order by 1
        """,
    ),
    (
        "FireYear encoding",
        """
        select case when FireYear < 10000 then '4-digit (malformed)'
                    else '6-digit financial year' end as format,
               count(*) as n, min(FireYear) as min, max(FireYear) as max
        from fire_raw group by 1 order by n
        """,
    ),
    (
        "Null dates",
        """
        select count(*) filter (where StartDate is null) as null_start,
               count(*) filter (where EndDate is null) as null_end,
               count(*) as total
        from fire_raw
        """,
    ),
]


def main() -> int:
    con = connect()
    register_raw(con)

    total = con.execute("select count(*) from fire_raw").fetchone()[0]
    print(f"\nNPWS Fire History — {total:,} polygons\n")

    for title, sql in QUERIES:
        print(f"-- {title}")
        print(con.execute(sql).fetchdf().to_string(index=False))
        print()

    print("-- Fire type codes (from the layer renderer, not a field domain)")
    for code, name in FIRE_TYPE.items():
        print(f"   {code} = {name}")

    invalid = con.execute(
        "select count(*) from fire_raw where not st_isvalid(geometry)"
    ).fetchone()[0]
    if invalid:
        print(
            f"\n   {invalid:,} invalid geometries. Nothing may be joined against "
            "these until they are repaired — a spatial predicate over an invalid "
            "ring returns an error or, worse, a plausible wrong answer."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Spatial Analytics

*NSW bushfire exposure — repo for portfolio project 5, the spatial lane.*

One question, asked properly:

> **How many NSW homes sit inside the bushfire interface, and who lives in them?**

Everyone has seen the map of where the fires were. The useful map is where the
fires were **and** where the houses are **and** who is inside those houses —
because a thousand exposed dwellings full of people over seventy is a different
problem from a thousand exposed dwellings in a new estate, and only the third
layer tells you which one you are looking at.

## Status

Building. Fire history ingest works; addresses and population are next.

| Stage | State |
|---|---|
| NPWS fire history → GeoParquet | ✅ **38,092 polygons landed**, 88.5 MB |
| Geometry / field profiling | ✅ `profile_fire` |
| Repair the 332 invalid geometries | ⬜ |
| G-NAF addresses → DuckDB | ⬜ |
| ABS SA1 + census → DuckDB | ⬜ |
| Dissolve, buffer, point-in-polygon | ⬜ |
| SA1 areal overlay → exposure by age band | ⬜ |
| deck.gl map | ⬜ |

## Stack

| Layer | Choice | Why |
|---|---|---|
| **Ingestion** | Python + `httpx` (managed by `uv`) | public ArcGIS + data.gov.au, no API key |
| **Engine** | **DuckDB** + `spatial` | reads GeoParquet and shapefiles, runs the predicates in-process — no PostGIS server to stand up or pay for |
| **Escape hatch** | GeoPandas / Shapely | for the parts that are genuinely easier in Python |
| **Map** | deck.gl | draws millions of points in the browser without a tile server |

## Sources

| Dataset | Where | Shape |
|---|---|---|
| NPWS Fire History (wildfires + prescribed burns) | [SEED](https://datasets.seed.nsw.gov.au/dataset/fire-history-wildfires-and-prescribed-burns-1e8b6) — ArcGIS MapServer | 38,092 polygons, **GDA94** (EPSG:4283) |
| G-NAF, Aug 2026, GDA2020 | [data.gov.au](https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf) | 1.7 GB zip of pipe-separated fragments, **GDA2020** (EPSG:7844) |
| SA1 boundaries + census | ABS ASGS 2021 | polygons + age/income tables |

## Datums, and why they get their own section

The two halves of this project arrive on different datums. Fire history is
GDA94; the G-NAF release used here is GDA2020. They differ by roughly **1.8 m** —
invisible at state scale, and not invisible when the question is whether one
specific house falls inside one specific line. Nothing is compared until it is on
the same datum, and every measurement happens in **EPSG:7856** (GDA2020 / MGA
zone 56), because buffering by `100` in a geographic system buffers by a hundred
*degrees* and nothing warns you.

## Data quality notes

Measured, not assumed — reproduce with `uv run python -m spatial_analytics.profile_fire`.

- **332 of 38,092 geometries are invalid** (0.87%). Predicted before ingest, and
  there they are. Nothing joins against these until they are repaired: a spatial
  predicate over a self-intersecting ring returns an error or, worse, a
  plausible wrong answer.
- **`FireYear` is a financial year, not a year.** 6-digit concatenations:
  `190203` is the 1902–03 season, `202627` is 2026–27. Filtering `FireYear > 2019`
  silently matches almost every row in the table.
- **One row breaks that rule.** Exactly 1 of 38,092 (`Tamban Forest fire`) carries
  a 4-digit `2004` with a null `StartDate`. Any parser has to survive it.
- **`StartDate` is null on 15,341 rows and `EndDate` on 20,018** — 40% and 53%.
  So the malformed-`FireYear` field is not a convenience, it is the only usable
  time dimension for most of the table.
- **`FireType` is a coded integer with no domain on the field** — the codes live
  in the layer's renderer. `1` = Wildfire (22,810 rows, 30.8 M ha),
  `2` = Prescribed Burn (15,282 rows, 3.7 M ha).
- **The service's advertised page size is a lie.** The layer reports
  `maxRecordCount: 1000`, but the real ceiling is bytes. Around OBJECTID 30,000
  the polygons get big — 500 features is a 10.7 MB response — and a 1,000-row
  request there returns HTTP 500 every time, reproducibly. The ingest halves its
  page size on failure and drifts back up.
- **Parquet parts written page-by-page drift in schema.** A page where every
  `Intensity` is null types as `double`; a page of real codes types as `int64`;
  concatenating them refuses. The merge unifies schemas and casts, rather than
  trusting the first part.
- **`StartDate` / `EndDate` arrive as ArcGIS epoch milliseconds**, so they land
  as `DOUBLE` and need converting before they are dates.

## Running it

```bash
uv sync
uv run python -m spatial_analytics.ingest_fire   # resumable
uv run python -m spatial_analytics.profile_fire  # data-quality report
```

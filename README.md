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

Pipeline runs end to end; the map is next.

| Stage | State |
|---|---|
| NPWS fire history → GeoParquet | ✅ 38,092 polygons, 88.5 MB |
| Geometry / field profiling | ✅ `profile_fire` |
| Repair, decode, reproject → `fire` | ✅ `prepare_fire` — 332 repaired, area within 0.2% of published |
| G-NAF NSW → `gnaf_address` | ✅ `ingest_gnaf` — 5.22 M live addresses, 6 s |
| Point-in-buffer join → `address_exposure` | ✅ `build_exposure` — 6.6 M hits, ~50 s |
| ABS mesh blocks + census → `census_sa1` | ✅ `ingest_abs` |
| Dwelling-weighted overlay → `sa1_exposure` | ✅ `build_population` |
| deck.gl map | ⬜ |

## First results

**92,846 NSW homes sit inside the footprint of a mapped wildfire. Within 100 m
of one there are 165,304 homes and roughly 332,000 people, 65,000 of them over 65.**

| Within | Homes | People | Aged 65+ | Under 15 | SA1s touched |
|---|---|---|---|---|---|
| footprint | 92,846 | 192,000 | 35,200 | 37,200 | 1,137 |
| 100 m | 165,304 | 331,600 | 65,200 | 63,000 | 1,853 |
| 500 m | 480,649 | 939,200 | 192,400 | 176,800 | 3,219 |
| 1 km | 871,497 | 1,676,000 | 336,600 | 314,900 | 4,851 |

Three things the numbers say that the fire map alone does not:

- **More exposed people live in Greater Sydney (188,000) than in the rest of
  NSW (144,000).** The interface is a suburban problem before it is a rural one.
- **The interface is older.** Within 100 m of a mapped fire, 55–74 year olds are
  over-represented against the NSW profile (24.8% vs 21.7%) and 20–34 year olds
  are under-represented (15.6% vs 20.3%). The young-adult deficit of the
  peri-urban fringe, measured.
- **Springwood–Winmalee has 7,369 homes within 100 m of a mapped wildfire — 76%
  of every home in the SA2.** Lawson–Hazelbrook–Linden is at 91%. Googong, a new
  estate outside Queanbeyan, is at 100%, because it was built on ground that had
  already burnt — which is the reminder that *ever burnt* and *currently bush*
  are different things, and the `since_2000` / `since_2019` columns exist for it.

Reproduce: run the stages in order under *Running it*, then
`uv run python -m spatial_analytics.build_population`.

### How people are counted

The plan was an area-weighted overlay. G-NAF made that unnecessary: every
address carries its ABS mesh block, every mesh block rolls up to an SA1, so an
SA1's exposed share is *exposed residential addresses ÷ all residential
addresses in it*. That weights by where the houses actually are, not by land
area — a bush-backed SA1 with all its houses on the road frontage gets the
right answer. The one assumption left is that people are spread evenly across
the addresses of an SA1. Census counts are 2021; addresses are August 2026.

"Home" means an address in a mesh block the ABS classes as **Residential**.
Farms (*Primary Production* blocks, 254,000 NSW addresses) are the most exposed
kind of home, but the category cannot tell a homestead from a paddock, so they
are reported in their own column rather than silently included or dropped.

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
- **`ST_Transform` from EPSG:4283 returns EMPTY without `always_xy`.** The
  registry defines GDA94 as lat/lon; the data is lon/lat. PROJ takes 152 as a
  latitude, every point is out of range, and the whole table comes back as
  empty geometries — no error, no warning. Both source CRSs need the flag.
- **Reprojection can re-break a repaired polygon.** 3 of the 332 went invalid
  again after the transform. Repaired twice, checked after.
- **`::int` rounds in DuckDB.** `(2026 / 10)::int` is 203, not 202. Decades
  came out as 2030 until the integer-division operator `//` replaced it.
- **G-NAF keeps history.** A retired address is still a row; `DATE_RETIRED is
  null` (null, not empty string — DuckDB reads the empty PSV field as null)
  on every table, or the count includes every address that ever existed.
- **4.4% of NSW addresses are not geocoded to the address.** `LEVEL_GEOCODED_CODE`
  below 7 means a street or locality centroid — 230,000 addresses stacked on a
  few thousand dots, each of which lands on the same side of every fire line.
  Excluded, and the exclusion is a number rather than a silent bias.
- **The join was not the problem.** The plan predicted the point-in-polygon
  join would be where the project became engineering. DuckDB 1.5 picks a
  `SPATIAL_JOIN` operator on its own, builds the RTree itself, and 5 M points
  against 22,810 polygons takes 5.7 s. No dissolve needed; overlapping fires
  are handled by counting each address once.

## Running it

```bash
uv sync
uv run python -m spatial_analytics.ingest_fire       # NPWS -> GeoParquet (resumable)
uv run python -m spatial_analytics.profile_fire      # data-quality report
uv run python -m spatial_analytics.prepare_fire      # repair, decode, reproject
uv run python -m spatial_analytics.ingest_gnaf       # after extracting the NSW PSVs (see module docstring)
uv run python -m spatial_analytics.build_exposure    # addresses x fires x bands
uv run python -m spatial_analytics.ingest_abs        # mesh blocks + census
uv run python -m spatial_analytics.build_population  # people, by age, by SA1
```

"""Paths, coordinate systems and source URLs.

Everything the pipeline needs to find its inputs lives here, so no other module
carries a hard-coded URL or a magic EPSG number.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
DB_PATH = DATA / "bushfire.duckdb"

# --- coordinate systems -------------------------------------------------
#
# The two halves of this project arrive on different datums, which is the whole
# reason ST_Transform shows up before any measurement does.
#
#   NPWS fire history  -> GDA94 geographic      (EPSG:4283, the service's own SR)
#   G-NAF (Aug 2026)   -> GDA2020 geographic    (EPSG:7844, the release we pull)
#   measurement        -> GDA2020 / MGA zone 56 (EPSG:7856, metres, covers Sydney)
#
# GDA94 and GDA2020 differ by roughly 1.8 m. That is invisible on a state-wide
# map and very much not invisible when the question is whether one house falls
# inside one line, so nothing gets compared until it is on the same datum.
CRS_FIRE_SOURCE = 4283
CRS_GNAF_SOURCE = 7844
CRS_METRIC = 7856

# Interface width in metres: how far from previously-burnt ground still counts
# as exposed. Sensitivity to this number is a result, not a constant, so the
# pipeline runs the whole join across all of these.
BUFFER_BANDS_M = (0, 100, 500, 1000)

# --- sources ------------------------------------------------------------
FIRE_SERVICE_URL = (
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services"
    "/Fire/NPWS_Fire_History/MapServer/0"
)
FIRE_PAGE_SIZE = 1000  # the service's own maxRecordCount; asking for more is ignored

# FireType is a coded integer; the codes come from the layer's renderer.
FIRE_TYPE = {1: "Wildfire", 2: "Prescribed Burn"}

# G-NAF, August 2026 release, GDA2020. 1.7 GB zipped, pipe-separated fragments.
GNAF_PACKAGE = "geocoded-national-address-file-g-naf"
GNAF_DATASET_URL = f"https://data.gov.au/data/dataset/{GNAF_PACKAGE}"


def ensure_dirs() -> None:
    for d in (RAW, PROCESSED):
        d.mkdir(parents=True, exist_ok=True)

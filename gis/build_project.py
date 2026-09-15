"""Build the QGIS project from the GeoPackage, headless, with QGIS's own Python.

    /Applications/QGIS-final-4_2_2.app/Contents/MacOS/python3.12 gis/build_project.py

Produces gis/bushfire.qgz (layers, styles, a print layout) and
gis/bushfire-sydney.png (the layout exported at 150 dpi). The project uses a
relative path to bushfire.gpkg, so the two files travel together.
"""
import os, sys, shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (  # noqa: E402
    Qgis, QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsGraduatedSymbolRenderer, QgsClassificationQuantile, QgsClassificationEqualInterval, QgsStyle,
    QgsSingleSymbolRenderer, QgsFillSymbol, QgsMarkerSymbol, QgsRendererCategory, QgsCategorizedSymbolRenderer,
    QgsLayout, QgsLayoutItemMap, QgsLayoutItemLegend, QgsLayoutItemLabel, QgsLayoutItemScaleBar,
    QgsLayoutExporter, QgsLayoutSize, QgsLayoutPoint, QgsUnitTypes, QgsRectangle, QgsLayerTreeLayer,
    QgsTextFormat, QgsLegendStyle, QgsRendererRange, QgsSymbol, QgsGradientColorRamp,
)
from qgis.PyQt.QtGui import QColor, QFont  # noqa: E402
from qgis.PyQt.QtCore import QSizeF  # noqa: E402

HERE = Path(__file__).resolve().parent
GPKG = HERE / "bushfire.gpkg"
SRC = HERE.parent / "data" / "processed" / "bushfire.gpkg"
if not GPKG.exists() and SRC.exists():
    os.symlink(SRC, GPKG)

qgs = QgsApplication([], False)
qgs.initQgis()
project = QgsProject.instance()
project.setCrs(QgsCoordinateReferenceSystem("EPSG:7856"))
project.setTitle("NSW bushfires, over time — homes within reach of burnt land")


def layer(name, title):
    lyr = QgsVectorLayer(f"{GPKG}|layername={name}", title, "ogr")
    assert lyr.isValid(), name
    project.addMapLayer(lyr)
    return lyr


# No basemap: the XYZ provider is not available headless, and the SA1 layer
# covers all of NSW anyway. Add OpenStreetMap from Browser > XYZ Tiles if wanted.

# ---- SA1 choropleth: share of homes within 100 m ----------------------------
sa1 = layer("sa1_exposure", "Share of homes within 100 m of burnt land, by SA1")
ramp = QgsGradientColorRamp(QColor("#f4f1ea"), QColor("#2f3e5c"))   # parchment -> slate: neutral, so fire stays the warm colour
sym = QgsFillSymbol.createSimple({"outline_color": "255,255,255,40", "outline_width": "0.08"})
gr = QgsGraduatedSymbolRenderer("share_100m", [])
gr.setSourceSymbol(sym)
gr.setClassificationMethod(QgsClassificationEqualInterval())
gr.updateClasses(sa1, 5)
gr.updateColorRamp(ramp)
for i, rng in enumerate(gr.ranges()):
    gr.updateRangeLabel(i, f"{rng.lowerValue():.0%} – {rng.upperValue():.0%}")
sa1.setRenderer(gr)
sa1.setOpacity(0.85)

# ---- burnt extent by five-year period --------------------------------------
burnt = layer("burnt_5yr", "Burnt land, by era")
eras = [(1900, 1949, "#d9c9a8", "before 1950"), (1950, 1999, "#f2a45c", "1950–1999"),
        (2000, 2018, "#e8602c", "2000–2018"), (2019, 2029, "#b3121b", "2019 onward")]
ranges = []
for lo, hi, col, lab in eras:
    fs_ = QgsFillSymbol.createSimple({"color": col, "outline_style": "no"})
    ranges.append(QgsRendererRange(lo, hi, fs_, lab))
burnt.setRenderer(QgsGraduatedSymbolRenderer("period_from", ranges))
burnt.setOpacity(0.55)

# ---- homes ---------------------------------------------------------------------
homes = layer("homes_100m", "Today's homes within 100 m of burnt land")
ms = QgsMarkerSymbol.createSimple({"name": "circle", "color": "#1c1917", "outline_style": "no", "size": "0.5"})
homes.setRenderer(QgsSingleSymbolRenderer(ms))

# ---- individual fires (off by default; 22,809 polygons) ----------------------
fires = layer("fires", "Individual wildfires, 1902–2026 (repaired)")
fs = QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "#7f1d1d", "outline_width": "0.15"})
fires.setRenderer(QgsSingleSymbolRenderer(fs))

# layer order (top first) and visibility
root = project.layerTreeRoot()
for lyr in (homes, burnt, fires, sa1):
    node = root.findLayer(lyr.id())
    clone = node.clone(); root.insertChildNode(-1, clone); root.removeChildNode(node)
root.findLayer(fires.id()).setItemVisibilityChecked(False)

# ---- print layout ---------------------------------------------------------------
from qgis.core import QgsPrintLayout, QgsLayoutItemPage  # noqa: E402
layout = QgsPrintLayout(project)
layout.initializeDefaults()
layout.setName("Greater Sydney")
layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Orientation.Landscape)

m = QgsLayoutItemMap(layout)
m.attemptMove(QgsLayoutPoint(10, 22, QgsUnitTypes.LayoutMillimeters))
m.attemptResize(QgsLayoutSize(300, 260, QgsUnitTypes.LayoutMillimeters))
m.setCrs(project.crs())
m.setLayers([homes, burnt, sa1])
# Greater Sydney, in MGA56 metres
m.setExtent(QgsRectangle(255000, 6215000, 375000, 6320000))
m.setBackgroundEnabled(True)
layout.addLayoutItem(m)

title = QgsLayoutItemLabel(layout)
title.setText("NSW bushfires, over time — where burnt land meets today's homes")
f = QgsTextFormat(); f.setFont(QFont("Helvetica")); f.setSize(20)
title.setTextFormat(f)
title.attemptMove(QgsLayoutPoint(10, 8, QgsUnitTypes.LayoutMillimeters))
title.attemptResize(QgsLayoutSize(300, 12, QgsUnitTypes.LayoutMillimeters))
layout.addLayoutItem(title)

legend = QgsLayoutItemLegend(layout)
legend.setTitle("Legend")
legend.setLinkedMap(m)
legend.setSyncMode(Qgis.LayoutLegendSyncMode.Manual) if hasattr(Qgis, "LayoutLegendSyncMode") else legend.setAutoUpdateModel(False)
lt = legend.model().rootGroup()
lt.clear()
for lyr in (homes, burnt, sa1):
    lt.addLayer(lyr)
legend.attemptMove(QgsLayoutPoint(318, 22, QgsUnitTypes.LayoutMillimeters))
legend.attemptResize(QgsLayoutSize(95, 200, QgsUnitTypes.LayoutMillimeters))
layout.addLayoutItem(legend)

sb = QgsLayoutItemScaleBar(layout)
sb.setLinkedMap(m); sb.setStyle("Line Ticks Up"); sb.setUnits(Qgis.DistanceUnit.Kilometers)
sb.setUnitsPerSegment(10); sb.setNumberOfSegments(3); sb.setNumberOfSegmentsLeft(0); sb.setUnitLabel("km"); sb.update()
sb.attemptMove(QgsLayoutPoint(12, 272, QgsUnitTypes.LayoutMillimeters))
layout.addLayoutItem(sb)

credit = QgsLayoutItemLabel(layout)
credit.setText("NPWS Fire History (SEED) · Geoscape G-NAF Aug 2026 · ABS Census 2021 · basemap © CARTO, © OpenStreetMap contributors · EPSG:7856 · github.com/bolat-t/spatial-analytics")
cf = QgsTextFormat(); cf.setFont(QFont("Helvetica")); cf.setSize(8)
credit.setTextFormat(cf)
credit.attemptMove(QgsLayoutPoint(10, 285, QgsUnitTypes.LayoutMillimeters))
credit.attemptResize(QgsLayoutSize(400, 6, QgsUnitTypes.LayoutMillimeters))
layout.addLayoutItem(credit)
project.layoutManager().addLayout(layout)

# ---- save + export ---------------------------------------------------------------
project.setFilePathStorage(Qgis.FilePathType.Relative) if hasattr(Qgis, "FilePathType") else None
out = HERE / "bushfire.qgz"
assert project.write(str(out)), "project write failed"
print("  wrote", out.name)

png = HERE / "bushfire-sydney.png"
exp = QgsLayoutExporter(layout)
settings = QgsLayoutExporter.ImageExportSettings(); settings.dpi = 150
res = exp.exportToImage(str(png), settings)
print("  layout export:", res, png.name, f"{png.stat().st_size / 1e6:.1f} MB" if png.exists() else "")
qgs.exitQgis()

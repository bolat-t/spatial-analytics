# Environment for running PyQGIS headless with the QGIS.app bundle on macOS.
#   source gis/qgis-env.sh && "$QGIS_PY" gis/build_project.py
Q="/Applications/QGIS-final-4_2_2.app/Contents"
export PYTHONPATH="$Q/Resources/python3.12:$Q/Resources/python3.12/lib-dynload:$Q/Resources/python3.12/site-packages:$Q/Resources/qgis/python"
export QT_QPA_PLATFORM=offscreen QGIS_PREFIX_PATH="$Q/MacOS"
export PROJ_DATA="$Q/Resources/qgis/proj" GDAL_DATA="$Q/Resources/qgis/gdal"
export QGIS_PY="$Q/MacOS/python3.12"
unset PYTHONHOME

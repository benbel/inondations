import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window, intersect, transform
from rasterio.windows import from_bounds as fenetre_de
from shapely.geometry import box

from tuiles import bornes_tuile, numero_tuile, terrarium

territoire = sys.argv[1]

emprise = (
    gpd.read_file(f"site/donnees/{territoire}/communes.geojson")
    .to_crs(2154)
    .buffer(2000)
    .to_crs(3857)
    .union_all()
    )

with rasterio.open(f"data/mnt_{territoire}.tif") as source:
    hauteurs = source.read(1)

    transformation = source.transform

    bornes = transform_bounds(source.crs, "EPSG:4326", *source.bounds)

etendue = Window(0, 0, hauteurs.shape[1], hauteurs.shape[0])

nombre = 0

for zoom in range(9, 15):
    premiere_colonne, premiere_ligne = numero_tuile(bornes[0], bornes[3], zoom)

    derniere_colonne, derniere_ligne = numero_tuile(bornes[2], bornes[1], zoom)

    for colonne in range(premiere_colonne, derniere_colonne + 1):
        for ligne in range(premiere_ligne, derniere_ligne + 1):
            bornes_mercator = bornes_tuile(colonne, ligne, zoom)

            if not emprise.intersects(box(*bornes_mercator)):
                continue

            fenetre_tuile = (
                fenetre_de(
                    *transform_bounds("EPSG:3857", "EPSG:2154", *bornes_mercator),
                    transformation,
                    )
                .round_offsets(op="floor")
                .round_lengths(op="ceil")
                )

            if not intersect(fenetre_tuile, etendue):
                continue

            fenetre = fenetre_tuile.intersection(etendue)

            tuile = np.full((256, 256), np.nan, dtype="float32")

            reproject(
                source=hauteurs[fenetre.toslices()],
                destination=tuile,
                src_transform=transform(fenetre, transformation),
                src_crs="EPSG:2154",
                src_nodata=-9999,
                dst_transform=from_bounds(*bornes_mercator, 256, 256),
                dst_crs="EPSG:3857",
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
                )

            chemin = Path(f"site/donnees/{territoire}/relief/{zoom}/{colonne}/{ligne}.png")

            chemin.parent.mkdir(parents=True, exist_ok=True)

            altitudes = np.round(np.nan_to_num(tuile, nan=0), 1)

            Image.fromarray(terrarium(altitudes)).save(chemin, optimize=True)

            nombre = nombre + 1

print(f"écrit {nombre} tuiles de relief dans site/donnees/{territoire}/relief")

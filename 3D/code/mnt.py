import io
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin
from shapely.geometry import box


def mnt(couche, gauche, bas):
    for essai in range(5):
        try:
            reponse = requests.get(
                "https://data.geopf.fr/wms-r",
                params={
                    "SERVICE": "WMS",
                    "VERSION": "1.3.0",
                    "REQUEST": "GetMap",
                    "LAYERS": couche,
                    "STYLES": "",
                    "CRS": "EPSG:2154",
                    "BBOX": f"{gauche},{bas},{gauche + 2000},{bas + 2000}",
                    "WIDTH": 400,
                    "HEIGHT": 400,
                    "FORMAT": "image/geotiff",
                    },
                timeout=300,
                )

            if reponse.ok:
                break
        except requests.RequestException:
            if essai == 4:
                raise

        time.sleep(10 * (essai + 1))

    reponse.raise_for_status()

    with rasterio.open(io.BytesIO(reponse.content)) as source:
        hauteurs = source.read(1)

    return hauteurs


def dalle(gauche, bas):
    if not emprise.intersects(box(gauche, bas, gauche + 2000, bas + 2000)):
        return np.full((400, 400), -9999, dtype="float32")

    lidar = mnt("IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", gauche, bas)

    if not (lidar == -9999).any():
        return lidar

    rge_alti = mnt("ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES", gauche, bas)

    return np.where(lidar == -9999, rge_alti, lidar)


territoire = sys.argv[1]

communes = gpd.read_file(f"site/donnees/{territoire}/communes.geojson").to_crs(2154)

emprise = communes.buffer(2000).union_all()

perimetre = communes.total_bounds

gauche = math.floor(perimetre[0] / 2000) * 2000 - 2000

bas = math.floor(perimetre[1] / 2000) * 2000 - 2000

droite = math.ceil(perimetre[2] / 2000) * 2000 + 2000

haut = math.ceil(perimetre[3] / 2000) * 2000 + 2000

colonnes = list(range(gauche, droite, 2000))

lignes = list(range(haut - 2000, bas - 1, -2000))

with ThreadPoolExecutor(max_workers=16) as executeur:
    dalles = list(
        executeur.map(dalle, colonnes * len(lignes), np.repeat(lignes, len(colonnes)).tolist())
        )

bandes = []

for rang in range(len(lignes)):
    bandes.append(np.hstack(dalles[rang * len(colonnes):(rang + 1) * len(colonnes)]))

hauteurs = np.vstack(bandes)

Path("data").mkdir(exist_ok=True)

with rasterio.open(
    f"data/mnt_{territoire}.tif",
    "w",
    driver="GTiff",
    width=hauteurs.shape[1],
    height=hauteurs.shape[0],
    count=1,
    dtype="float32",
    crs="EPSG:2154",
    transform=from_origin(gauche, haut, 5, 5),
    nodata=-9999,
    compress="deflate",
    tiled=True,
    ) as sortie:
    sortie.write(hauteurs.astype("float32"), 1)

print(f"écrit data/mnt_{territoire}.tif, {hauteurs.shape[1]} × {hauteurs.shape[0]} pixels de 5 m")

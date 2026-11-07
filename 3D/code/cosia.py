import io
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests
from PIL import Image
from shapely.geometry import box

from tuiles import bornes_tuile, numero_tuile


def recolorer(tache):
    zoom, colonne, ligne, chemin = tache

    for essai in range(5):
        try:
            reponse = requests.get(
                "https://data.geopf.fr/wmts",
                params={
                    "SERVICE": "WMTS",
                    "REQUEST": "GetTile",
                    "VERSION": "1.0.0",
                    "LAYER": "IGNF_COSIA_2021-2023",
                    "STYLE": "normal",
                    "TILEMATRIXSET": "PM_6_18",
                    "TILEMATRIX": zoom,
                    "TILEROW": ligne,
                    "TILECOL": colonne,
                    "FORMAT": "image/png",
                    },
                timeout=300,
                )

            if reponse.ok or reponse.status_code == 404:
                break
        except requests.RequestException:
            if essai == 4:
                raise

        time.sleep(10 * (essai + 1))

    if reponse.status_code == 404:
        return

    reponse.raise_for_status()

    image = np.array(Image.open(io.BytesIO(reponse.content)).convert("RGBA"))

    ecarts = (
        (image[:, :, None, :3].astype("int32") - origines[None, None, :, :]) ** 2
        ).sum(axis=3)

    indices = np.where(image[:, :, 3] < 128, len(teintes), destinations[ecarts.argmin(axis=2)])

    tuile = Image.fromarray(indices.astype("uint8"), mode="P")

    tuile.putpalette(palette)

    chemin.parent.mkdir(parents=True, exist_ok=True)

    tuile.save(chemin, optimize=True, transparency=len(teintes))


territoire = sys.argv[1]

classes = {
    "bâtiment": ["#ce7079", "#efe9dd"],
    "zone perméable": ["#987752", "#dde6c4"],
    "zone imperméable": ["#a6aab7", "#efe9dd"],
    "piscine": ["#62d0ff", "#efe9dd"],
    "sol nu": ["#bbb096", "#dde6c4"],
    "surface eau": ["#3375a1", "#b8c8d2"],
    "neige": ["#e9effe", "#dde6c4"],
    "conifère": ["#126421", "#8fb877"],
    "feuillu": ["#4c9129", "#8fb877"],
    "broussaille": ["#b5c335", "#c3dba6"],
    "vigne": ["#b08290", "#dde6c4"],
    "pelouse": ["#8cd76a", "#c3dba6"],
    "culture": ["#decf55", "#dde6c4"],
    "terre labourée": ["#d0a349", "#dde6c4"],
    "serre": ["#b9e2d4", "#efe9dd"],
    "coupe": ["#df8b52", "#c3dba6"],
    "autre": ["#222222", "#efe9dd"],
    }

teintes = sorted({destination for _, destination in classes.values()})

origines = []

destinations = []

palette = []

for origine, destination in classes.values():
    origines.append([int(origine[rang:rang + 2], 16) for rang in (1, 3, 5)])

    destinations.append(teintes.index(destination))

for teinte in teintes:
    palette.extend([int(teinte[rang:rang + 2], 16) for rang in (1, 3, 5)])

palette.extend([0, 0, 0])

origines = np.array(origines)

destinations = np.array(destinations, dtype="uint8")

communes = gpd.read_file(f"site/donnees/{territoire}/communes.geojson")

emprise = communes.to_crs(2154).buffer(2000).to_crs(3857).union_all()

bornes = communes.total_bounds

taches = []

for zoom in range(9, 17):
    premiere_colonne, premiere_ligne = numero_tuile(bornes[0], bornes[3], zoom)

    derniere_colonne, derniere_ligne = numero_tuile(bornes[2], bornes[1], zoom)

    for colonne in range(premiere_colonne, derniere_colonne + 1):
        for ligne in range(premiere_ligne, derniere_ligne + 1):
            chemin = Path(f"site/donnees/{territoire}/cosia/{zoom}/{colonne}/{ligne}.png")

            if not chemin.exists() and emprise.intersects(box(*bornes_tuile(colonne, ligne, zoom))):
                taches.append((zoom, colonne, ligne, chemin))

with ThreadPoolExecutor(max_workers=16) as executeur:
    list(executeur.map(recolorer, taches))

print(f"écrit {len(taches)} tuiles d'occupation du sol dans site/donnees/{territoire}/cosia")

"""Zones inondables JRC (périodes de retour 10, 100 et 500 ans) sur la France métropolitaine,
vectorisées pour les tuiles : aucun service de tuiles ne les diffuse.

python code/jrc.py
"""

import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.windows import Window, from_bounds

from commun import jrc

FRANCE = (-5.3, 41.3, 9.7, 51.2)

BLOC = 2048

Path("data").mkdir(exist_ok=True)

nombre = 0

with open("data/jrc.geojsonl", "w") as sortie:
    for periode in (10, 100, 500):
        with rasterio.open(jrc(periode)) as source:
            fenetre = from_bounds(*FRANCE, source.transform).round_offsets().round_lengths()

            for ligne in range(0, int(fenetre.height), BLOC):
                for colonne in range(0, int(fenetre.width), BLOC):
                    bloc = Window(
                        fenetre.col_off + colonne,
                        fenetre.row_off + ligne,
                        min(BLOC, fenetre.width - colonne),
                        min(BLOC, fenetre.height - ligne),
                        )

                    profondeurs = source.read(1, window=bloc)

                    inondable = profondeurs > 0

                    if not inondable.any():
                        continue

                    for geometrie, _ in shapes(
                        inondable.astype("uint8"),
                        mask=inondable,
                        transform=source.window_transform(bloc),
                        ):
                        geometrie["coordinates"] = [
                            np.round(anneau, 5).tolist() for anneau in geometrie["coordinates"]
                            ]

                        sortie.write(
                            json.dumps(
                                {
                                    "type": "Feature",
                                    "properties": {"p": periode},
                                    "geometry": geometrie,
                                    },
                                separators=(",", ":"),
                                )
                            + "\n"
                            )

                        nombre = nombre + 1

        print(f"période {periode} : {nombre} polygones cumulés")

print("écrit data/jrc.geojsonl")

"""Rassemble les sorties départementales : tuiles des bâtiments exposés, tuiles JRC et
synthèse par commune pour le site.

python code/assembler.py
"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from commun import scenarios

MESURES = ["batiments", "logements", "habitants", "valeur"]

sortie = Path("site/donnees")

sortie.mkdir(parents=True, exist_ok=True)

dossiers = sorted(Path("data/departements").glob("*/communes.csv"))

print(f"{len(dossiers)} départements")

subprocess.run(
    [
        "tippecanoe",
        "--force",
        "--quiet",
        "--output",
        str(sortie / "batiments.pmtiles"),
        "--layer",
        "batiments",
        "--minimum-zoom",
        "13",
        "--maximum-zoom",
        "14",
        # Au zoom 13, une grille de 1 024 pas (environ 3 m) suffit et réduit d'un tiers le poids.
        "--low-detail",
        "10",
        "--no-tile-size-limit",
        "--no-feature-limit",
        *[str(dossier.parent / "batiments.geojsonl") for dossier in dossiers],
        ],
    check=True,
    )

if Path("data/jrc.geojsonl").exists():
    subprocess.run(
        [
            "tippecanoe",
            "--force",
            "--quiet",
            "--output",
            str(sortie / "jrc.pmtiles"),
            "--layer",
            "jrc",
            "--minimum-zoom",
            "5",
            "--maximum-zoom",
            "12",
            "--drop-smallest-as-needed",
            "--simplification",
            "2",
            "data/jrc.geojsonl",
            ],
        check=True,
        )

communes = pd.concat(
    [pd.read_csv(dossier, dtype={"code_insee": str}) for dossier in dossiers], ignore_index=True
    )

groupes = ["ensemble", *scenarios]

colonnes = [f"{groupe}:{mesure}" for groupe in groupes for mesure in MESURES]

valeurs = communes[colonnes].round(0).astype("int64")

valeurs[[colonne for colonne in colonnes if colonne.endswith(":valeur")]] = (
    communes[[colonne for colonne in colonnes if colonne.endswith(":valeur")]].round(1)
    )

lignes = []

for rang, commune in communes.iterrows():
    lignes.append(
        [
            commune["code_insee"],
            commune["nom"],
            commune["longitude"],
            commune["latitude"],
            *valeurs.iloc[rang].tolist(),
            ]
        )

(sortie / "communes.json").write_text(
    json.dumps(
        {
            "scenarios": [
                {"cle": cle, "libelle": definition["libelle"], "source": definition["source"]}
                for cle, definition in scenarios.items()
                ],
            "groupes": groupes,
            "mesures": MESURES,
            "communes": lignes,
            },
        ensure_ascii=False,
        separators=(",", ":"),
        )
    )

carreaux = (
    pd.concat(
        [pd.read_csv(dossier.parent / "carreaux.csv") for dossier in dossiers], ignore_index=True
        )
    .groupby(["x", "y"], as_index=False)
    .sum()
    )

longitudes, latitudes = map(np.asarray, Transformer.from_crs(2154, 4326, always_xy=True).transform(
    carreaux["x"] * 1000 + 500, carreaux["y"] * 1000 + 500
    ))

# Par scénario, seuls les carreaux touchés : [longitude, latitude, bâtiments, habitants, M€].
grille = {}

for cle in scenarios:
    touches = carreaux[f"{cle}:batiments"] > 0

    grille[cle] = [
        [round(longitude, 3), round(latitude, 3), int(round(batiments)), int(round(habitants)),
         round(valeur, 1)]
        for longitude, latitude, batiments, habitants, valeur in zip(
            longitudes[touches],
            latitudes[touches],
            carreaux.loc[touches, f"{cle}:batiments"],
            carreaux.loc[touches, f"{cle}:habitants"],
            carreaux.loc[touches, f"{cle}:valeur"],
            strict=True,
            )
        ]

(sortie / "carreaux.json").write_text(json.dumps(grille, separators=(",", ":")))

print(f"{len(carreaux)} carreaux de 1 km")

total = communes[colonnes].sum()

print(
    pd.DataFrame(
        {mesure: [total[f"{groupe}:{mesure}"] for groupe in groupes] for mesure in MESURES},
        index=groupes,
        ).round(0).to_string()
    )

for fichier in sorted(sortie.iterdir()):
    print(f"{fichier.name} : {fichier.stat().st_size / 1e6:.1f} Mo")

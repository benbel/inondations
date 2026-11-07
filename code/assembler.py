"""Rassemble les sorties départementales : tuiles des bâtiments exposés, tuiles JRC et
synthèse par commune pour le site.

python code/assembler.py
"""

import json
import subprocess
from pathlib import Path

import pandas as pd

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
        "14",
        "--maximum-zoom",
        "14",
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

total = communes[colonnes].sum()

print(
    pd.DataFrame(
        {mesure: [total[f"{groupe}:{mesure}"] for groupe in groupes] for mesure in MESURES},
        index=groupes,
        ).round(0).to_string()
    )

for fichier in sorted(sortie.iterdir()):
    print(f"{fichier.name} : {fichier.stat().st_size / 1e6:.1f} Mo")

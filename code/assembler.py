import json
import subprocess
from pathlib import Path

import pandas as pd
from pyproj import Transformer

from commun import MESURES, scenarios

SORTIE = Path("site/donnees")

COLONNES = [f"{cle}:{mesure}" for cle in scenarios for mesure in MESURES]


def tuiler(entrees, sortie, couche, options):
    subprocess.run(
        [
            "tippecanoe",
            "--force",
            "--quiet",
            "--output",
            str(sortie),
            "--layer",
            couche,
            *options,
            *[str(entree) for entree in entrees],
            ],
        check=True,
        )


def lire(dossiers, nom):
    return pd.concat(
        [pd.read_csv(dossier / nom, dtype={"code_insee": str}) for dossier in dossiers],
        ignore_index=True,
        )


def arrondir(table):
    entiers = [colonne for colonne in COLONNES if not colonne.endswith(":valeur")]

    return (
        table[COLONNES]
        .round(1)
        .astype(dict.fromkeys(entiers, int))
        .astype(object)
        .to_numpy()
        .tolist()
        )


def ecrire_communes(communes):
    lignes = [
        [code, nom, longitude, latitude, *valeurs]
        for code, nom, longitude, latitude, valeurs in zip(
            communes["code_insee"],
            communes["nom"],
            communes["longitude"],
            communes["latitude"],
            arrondir(communes),
            strict=True,
            )
        ]

    (SORTIE / "communes.json").write_text(
        json.dumps(
            {
                "scenarios": [
                    {"cle": cle, "libelle": definition["libelle"], "source": definition["source"]}
                    for cle, definition in scenarios.items()
                    ],
                "colonnes": COLONNES,
                "communes": lignes,
                },
            ensure_ascii=False,
            separators=(",", ":"),
            )
        )


def carreaux_du_scenario(centres, cle):
    return (
        centres
        .loc[centres[f"{cle}:batiments"] > 0]
        .assign(
            batiments=lambda table: table[f"{cle}:batiments"].round(0).astype(int),
            habitants=lambda table: table[f"{cle}:habitants"].round(0).astype(int),
            valeur=lambda table: table[f"{cle}:valeur"].round(1),
            )
        [["longitude", "latitude", *MESURES]]
        .astype(object)
        .to_numpy()
        .tolist()
        )


def ecrire_carreaux(carreaux):
    longitudes, latitudes = Transformer.from_crs(2154, 4326, always_xy=True).transform(
        carreaux["x"] * 1000 + 500, carreaux["y"] * 1000 + 500
        )

    centres = carreaux.assign(longitude=longitudes.round(3), latitude=latitudes.round(3))

    grille = {cle: carreaux_du_scenario(centres, cle) for cle in scenarios}

    (SORTIE / "carreaux.json").write_text(json.dumps(grille, separators=(",", ":")))


SORTIE.mkdir(parents=True, exist_ok=True)

dossiers = sorted(chemin.parent for chemin in Path("data/departements").glob("*/communes.csv"))

tuiler(
    [dossier / "batiments.geojsonl" for dossier in dossiers],
    SORTIE / "batiments.pmtiles",
    "batiments",
    [
        "--minimum-zoom=13",
        "--maximum-zoom=14",
        "--low-detail=10",
        "--no-tile-size-limit",
        "--no-feature-limit",
        ],
    )

tuiler(
    [Path("data/jrc.geojsonl")],
    SORTIE / "jrc.pmtiles",
    "jrc",
    ["--minimum-zoom=5", "--maximum-zoom=12", "--drop-smallest-as-needed", "--simplification=2"],
    )

ecrire_communes(lire(dossiers, "communes.csv"))

ecrire_carreaux(lire(dossiers, "carreaux.csv").groupby(["x", "y"], as_index=False).sum())

for fichier in sorted(SORTIE.iterdir()):
    print(f"{fichier.name} : {fichier.stat().st_size / 1e6:.1f} Mo")

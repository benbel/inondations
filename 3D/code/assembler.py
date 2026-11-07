import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd


def tuiler(couches, sortie, zoom_minimum):
    commande = [
        "tippecanoe",
        "--force",
        "--output",
        sortie,
        "--minimum-zoom",
        str(zoom_minimum),
        "--maximum-zoom",
        "15",
        "--no-tile-size-limit",
        "--no-feature-limit",
        ]

    for nom, chemin in couches.items():
        commande.extend(["--named-layer", f"{nom}:{chemin}"])

    subprocess.run(commande, check=True)


territoire = sys.argv[1]

description = json.loads(Path(f"site/donnees/{territoire}/territoire.json").read_text())

scenarios = [scenario["cle"] for scenario in description["scenarios"]]

batiments = (
    gpd.read_file(f"data/{territoire}/batiments.gpkg")
    .merge(
        pd.read_csv(f"data/{territoire}/population_batiments.csv")
        .loc[lambda table: table["annee"] == 2021]
        [["cleabs", "habitants"]],
        on="cleabs",
        how="left",
        )
    .merge(
        pd.read_csv(f"data/{territoire}/valeurs_batiments.csv")[["cleabs", "valeur"]],
        on="cleabs",
        how="left",
        )
    )

lignes = []

groupes = {"ensemble": batiments}

for scenario in scenarios:
    groupes[scenario] = batiments.loc[batiments[scenario].notna()]

for groupe, selection in groupes.items():
    lignes.append(["synthese", "bâtiments", groupe, len(selection)])

    lignes.append(["synthese", "logements", groupe, selection["nombre_de_logements"].sum()])

    lignes.append(["synthese", "habitants en 2021", groupe, selection["habitants"].sum()])

    lignes.append(
        ["synthese", "valeur des logements (M€)", groupe, selection["valeur"].sum() / 1e6]
        )

tableaux = (
    pd.DataFrame(lignes, columns=["tableau", "ligne", "groupe", "valeur"])
    .assign(valeur=lambda table: table["valeur"].astype(float).round(1))
    )

print(
    tableaux
    .pivot_table(index="ligne", columns="groupe", values="valeur", sort=False)
    .to_string()
    )

rare = "extreme" if "extreme" in scenarios else scenarios[-1]

centres = groupes[rare].centroid

cellules = (
    (centres.x // 1000).astype(int).astype(str)
    + " "
    + (centres.y // 1000).astype(int).astype(str)
    )

densite_maximale = centres[cellules == cellules.value_counts().idxmax()]

centre = (
    gpd.GeoSeries([densite_maximale.union_all().centroid], crs=2154)
    .to_crs(4326)
    .iloc[0]
    )

indicateurs = {
    "tableaux": tableaux.to_dict(orient="records"),
    "centre": [round(centre.x, 6), round(centre.y, 6)],
    }

(
    batiments
    [["usage_1", "hauteur", *scenarios, "geometry"]]
    .assign(geometry=lambda table: table.geometry.force_2d())
    .to_crs(4326)
    .to_file(f"data/{territoire}/batiments.geojson", driver="GeoJSON", COORDINATE_PRECISION=6)
    )

tuiler(
    {"batiments": f"data/{territoire}/batiments.geojson"},
    f"site/donnees/{territoire}/batiments.pmtiles",
    12,
    )

tuiler(
    {"emprises": f"data/{territoire}/emprises.geojson"},
    f"site/donnees/{territoire}/zones.pmtiles",
    9,
    )

Path(f"site/donnees/{territoire}/indicateurs.json").write_text(
    json.dumps(indicateurs, ensure_ascii=False)
    )

print(f"écrit site/donnees/{territoire} : batiments.pmtiles, zones.pmtiles, indicateurs.json")

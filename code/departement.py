"""Bâtiments d'un département, leur exposition aux trois sources d'aléa, leurs habitants et
leur valeur, agrégés par commune.

python code/departement.py 37
"""

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from shapely.geometry import box

from commun import jrc, masque_eaip, scenarios, touche, wfs_georisques, wfs_ign

PROPRIETES = (
    "cleabs,usage_1,etat_de_l_objet,nombre_de_logements,nombre_d_etages,hauteur,geometrie"
    )

# Les ventes DVF de Paris, Lyon et Marseille sont codées par arrondissement.
ARRONDISSEMENTS = {
    **{f"751{numero:02d}": "75056" for numero in range(1, 21)},
    **{f"6938{numero}": "69123" for numero in range(1, 10)},
    **{f"132{numero:02d}": "13055" for numero in range(1, 17)},
    }


def batiments_de_la_cellule(cellule):
    pages = []

    depart = 0

    while True:
        page = wfs_ign(
            "BDTOPO_V3:batiment",
            {
                "BBOX": ",".join(str(round(borne)) for borne in cellule) + ",EPSG:2154",
                "PROPERTYNAME": PROPRIETES,
                "SORTBY": "cleabs",
                "COUNT": 5000,
                "STARTINDEX": depart,
                },
            )

        pages.append(page)

        if len(page) < 5000:
            break

        depart = depart + 5000

    return pd.concat(pages, ignore_index=True)


def lire_batiments(perimetre, cote=5000):
    bornes = perimetre.bounds

    cellules = []

    for gauche in np.arange(np.floor(bornes[0] / cote) * cote, bornes[2], cote):
        for bas in np.arange(np.floor(bornes[1] / cote) * cote, bornes[3], cote):
            cellule = (gauche, bas, gauche + cote, bas + cote)

            if perimetre.intersects(box(*cellule)):
                cellules.append(cellule)

    with ThreadPoolExecutor(max_workers=8) as executeur:
        morceaux = list(executeur.map(batiments_de_la_cellule, cellules))

    return (
        gpd.GeoDataFrame(pd.concat(morceaux, ignore_index=True), geometry="geometry", crs=2154)
        .drop_duplicates("cleabs")
        .query("etat_de_l_objet == 'En service'")
        .astype(
            {
                "nombre_de_logements": "float",
                "nombre_d_etages": "float",
                "hauteur": "float",
                }
            )
        .reset_index(drop=True)
        )


def expose_tri(batiments, definition, bornes):
    zones = pd.concat(
        [wfs_georisques(couche, bornes) for couche in definition["couches"]], ignore_index=True
        )

    if len(zones) == 0:
        return np.zeros(len(batiments), dtype=bool), 0

    zones = zones.geometry.make_valid()

    # Les zones (très grands polygones) sont passées en entrée de la requête : shapely les
    # prépare une fois chacune, ce qui est bien plus rapide que l'inverse.
    _, touches = batiments.sindex.query(zones, predicate="intersects")

    expose = np.zeros(len(batiments), dtype=bool)

    expose[touches] = True

    return expose, len(zones)


def expose_jrc(batiments, definition, bornes):
    bornes_wgs84 = transform_bounds("EPSG:2154", "EPSG:4326", *bornes)

    with rasterio.open(jrc(definition["periode"])) as source:
        fenetre = from_bounds(*bornes_wgs84, source.transform).round_offsets().round_lengths()

        profondeurs = source.read(1, window=fenetre, boundless=True, fill_value=0)

        transformation = source.window_transform(fenetre)

    rectangles = batiments.to_crs(4326).bounds.to_numpy()

    return touche(
        profondeurs > 0,
        transformation.c,
        transformation.f,
        transformation.a,
        -transformation.e,
        rectangles,
        )


def expose_eaip(batiments, bornes):
    masque, (gauche, haut, resolution) = masque_eaip(bornes)

    return touche(masque, gauche, haut, resolution, resolution, batiments.bounds.to_numpy())


def habitants(batiments, bornes):
    bornes_laea = transform_bounds("EPSG:2154", "EPSG:3035", *bornes)

    carreaux = (
        pd.read_parquet("data/filosofi_2021.parquet")
        .loc[lambda table: table["est"].between(bornes_laea[0] - 200, bornes_laea[2])]
        .loc[lambda table: table["nord"].between(bornes_laea[1] - 200, bornes_laea[3])]
        .reset_index(drop=True)
        )

    carres = gpd.GeoDataFrame(
        carreaux,
        geometry=[
            box(est, nord, est + 200, nord + 200)
            for est, nord in zip(carreaux["est"], carreaux["nord"], strict=True)
            ],
        crs=3035,
        ).to_crs(2154)

    centres = (
        batiments
        .loc[batiments["poids"] > 0, ["cleabs", "poids", "geometry"]]
        .assign(geometry=lambda table: table.centroid)
        )

    repartition = (
        gpd.sjoin(centres, carres, predicate="within")
        .drop_duplicates("cleabs")
        .assign(
            part=lambda table: (
                table["poids"] / table.groupby("index_right")["poids"].transform("sum")
                ),
            habitants=lambda table: table["ind"] * table["part"],
            )
        )

    print(
        f"{carreaux['ind'].sum():.0f} habitants dans les carreaux lus, "
        f"{repartition['habitants'].sum():.0f} répartis sur le bâti"
        )

    return batiments["cleabs"].map(repartition.set_index("cleabs")["habitants"]).fillna(0)


def ventes(departement, annee):
    try:
        return pd.read_csv(
            f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/departements/"
            f"{departement}.csv.gz",
            dtype={"code_commune": str, "id_mutation": str},
            usecols=[
                "id_mutation",
                "nature_mutation",
                "valeur_fonciere",
                "code_commune",
                "type_local",
                "surface_reelle_bati",
                ],
            )
    except Exception as erreur:
        print(f"DVF {departement} {annee} indisponible : {erreur!r}")

        return None


def valeurs(batiments, departement):
    morceaux = [ventes(departement, annee) for annee in range(2021, 2026)]

    morceaux = [morceau for morceau in morceaux if morceau is not None]

    if not morceaux:
        return pd.Series(np.nan, index=batiments.index)

    prix = (
        pd.concat(morceaux, ignore_index=True)
        .loc[lambda table: table["nature_mutation"] == "Vente"]
        .assign(
            locaux=lambda table: table.groupby("id_mutation")["type_local"].transform("count"),
            code_commune=lambda table: table["code_commune"].replace(ARRONDISSEMENTS),
            )
        .loc[lambda table: table["locaux"] == 1]
        .loc[lambda table: table["type_local"].isin(["Maison", "Appartement"])]
        .loc[lambda table: table["surface_reelle_bati"] > 0]
        .assign(prix_m2=lambda table: table["valeur_fonciere"] / table["surface_reelle_bati"])
        )

    prix_communes = (
        prix
        .groupby(["code_commune", "type_local"])
        .agg(ventes=("prix_m2", "size"), prix_m2=("prix_m2", "median"))
        .query("ventes >= 30")
        ["prix_m2"]
        )

    prix_departement = prix.groupby("type_local")["prix_m2"].median()

    type_local = np.where(batiments["nombre_de_logements"] == 1, "Maison", "Appartement")

    cles = pd.MultiIndex.from_arrays([batiments["code_insee"], type_local])

    prix_m2 = (
        pd.Series(prix_communes.reindex(cles).to_numpy(), index=batiments.index)
        .fillna(pd.Series(type_local, index=batiments.index).map(prix_departement))
        )

    surface = batiments.area * batiments["niveaux"] * 0.8

    return (surface * prix_m2).where(batiments["nombre_de_logements"] > 0, 0)


departement = sys.argv[1]

sortie = Path(f"data/departements/{departement}")

sortie.mkdir(parents=True, exist_ok=True)

communes = wfs_ign(
    "ADMINEXPRESS-COG.LATEST:commune",
    {"CQL_FILTER": f"code_insee_du_departement='{departement}'", "COUNT": 5000},
    )[["code_insee", "nom_officiel", "geometry"]]

print(f"{departement} : {len(communes)} communes")

perimetre = communes.union_all().buffer(500)

bornes = perimetre.bounds

batiments = lire_batiments(perimetre)

print(f"{len(batiments)} bâtiments lus")

batiments = batiments.assign(
    niveaux=lambda table: (
        table["nombre_d_etages"].fillna((table["hauteur"] / 3).round()).fillna(1).clip(lower=1)
        ),
    poids=lambda table: (
        table["nombre_de_logements"]
        .where(table["nombre_de_logements"] > 0)
        .fillna(table.area * table["niveaux"] / 80)
        .where((table["nombre_de_logements"] > 0) | (table["usage_1"] == "Résidentiel"), 0)
        ),
    )

batiments["habitants"] = habitants(batiments, bornes)

centres = gpd.sjoin(
    batiments[["geometry"]].assign(geometry=lambda table: table.centroid),
    communes[["code_insee", "geometry"]],
    predicate="within",
    )

batiments = (
    batiments
    .assign(code_insee=centres["code_insee"].groupby(level=0).first())
    .dropna(subset=["code_insee"])
    .reset_index(drop=True)
    )

print(f"{len(batiments)} bâtiments dans le département")

batiments["valeur"] = valeurs(batiments, departement)

batiments["z"] = 0

for rang, (cle, definition) in enumerate(scenarios.items()):
    if definition["source"] == "tri":
        expose, nombre = expose_tri(batiments, definition, bornes)
        print(f"{cle} : {nombre} polygones")
    elif definition["source"] == "jrc":
        expose = expose_jrc(batiments, definition, bornes)
    else:
        expose = expose_eaip(batiments, bornes)

    batiments[cle] = expose

    batiments["z"] = batiments["z"] + (expose.astype(int) << rang)

    print(f"{cle} : {expose.sum()} bâtiments exposés")

mesures = {
    "batiments": batiments.assign(batiments=1)["batiments"],
    "logements": batiments["nombre_de_logements"].fillna(0),
    "habitants": batiments["habitants"],
    "valeur": batiments["valeur"].fillna(0) / 1e6,
    }

colonnes = {}

for groupe in ["ensemble", *scenarios]:
    filtre = 1 if groupe == "ensemble" else batiments[groupe]

    for mesure, valeurs_mesure in mesures.items():
        colonnes[f"{groupe}:{mesure}"] = valeurs_mesure * filtre

centroides = communes.set_index("code_insee").representative_point().to_crs(4326)

synthese = (
    pd.DataFrame(colonnes)
    .groupby(batiments["code_insee"])
    .sum()
    .reindex(communes["code_insee"], fill_value=0)
    .assign(
        nom=communes.set_index("code_insee")["nom_officiel"],
        longitude=centroides.x.round(5),
        latitude=centroides.y.round(5),
        )
    )

synthese.round(2).to_csv(sortie / "communes.csv")

# Même synthèse sur une grille de 1 km (Lambert-93), pour compter ce qui est à l'écran ;
# seuls les carreaux ayant au moins un bâtiment exposé sont gardés.
centres_batiments = batiments.centroid

carreaux = (
    pd.DataFrame(
        {
            colonne: valeurs_colonne
            for colonne, valeurs_colonne in colonnes.items()
            if not colonne.startswith("ensemble:") and not colonne.endswith(":logements")
            }
        )
    .assign(
        x=(centres_batiments.x // 1000).astype(int),
        y=(centres_batiments.y // 1000).astype(int),
        )
    .loc[batiments["z"] > 0]
    .groupby(["x", "y"])
    .sum()
    )

carreaux.round(2).to_csv(sortie / "carreaux.csv")

(
    batiments
    .loc[batiments["z"] > 0, ["z", "geometry"]]
    .assign(geometry=lambda table: table.geometry.force_2d())
    .to_crs(4326)
    .to_file(sortie / "batiments.geojsonl", driver="GeoJSONSeq", COORDINATE_PRECISION=6)
    )

print(synthese.drop(columns=["nom", "longitude", "latitude"]).sum().round(0).to_string())

print(f"écrit {sortie}")

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.warp import transform_bounds
from shapely.geometry import box

from commun import MESURES, dalles, scenarios, toutes_les_pages, wfs_ign, zones

PROPRIETES = "cleabs,usage_1,etat_de_l_objet,nombre_de_logements,nombre_d_etages,hauteur,geometrie"

ARRONDISSEMENTS = {
    **{f"751{numero:02d}": "75056" for numero in range(1, 21)},
    **{f"6938{numero}": "69123" for numero in range(1, 10)},
    **{f"132{numero:02d}": "13055" for numero in range(1, 17)},
    }


def lire_communes(departement):
    return wfs_ign(
        "ADMINEXPRESS-COG.LATEST:commune",
        {"CQL_FILTER": f"code_insee_du_departement='{departement}'", "COUNT": 5000},
        )[["code_insee", "nom_officiel", "geometry"]]


def page_batiments(cellule, depart):
    return wfs_ign(
        "BDTOPO_V3:batiment",
        {
            "BBOX": ",".join(str(round(borne)) for borne in cellule) + ",EPSG:2154",
            "PROPERTYNAME": PROPRIETES,
            "SORTBY": "cleabs",
            "COUNT": 5000,
            "STARTINDEX": depart,
            },
        )


def batiments_de_la_cellule(cellule):
    return toutes_les_pages(lambda depart: page_batiments(cellule, depart), 5000)


def lire_batiments(perimetre, cote=5000):
    cellules = [
        (gauche, bas, gauche + cote, bas + cote)
        for gauche, bas in dalles(perimetre.bounds, cote)
        if perimetre.intersects(box(gauche, bas, gauche + cote, bas + cote))
        ]

    with ThreadPoolExecutor(max_workers=8) as executeur:
        morceaux = list(executeur.map(batiments_de_la_cellule, cellules))

    return (
        gpd.GeoDataFrame(pd.concat(morceaux, ignore_index=True), geometry="geometry", crs=2154)
        .drop_duplicates("cleabs")
        .query("etat_de_l_objet == 'En service'")
        .astype({"nombre_de_logements": "float", "nombre_d_etages": "float", "hauteur": "float"})
        .reset_index(drop=True)
        )


def ajouter_poids(batiments):
    return batiments.assign(
        batiments=1,
        niveaux=lambda table: (
            table["nombre_d_etages"]
            .fillna((table["hauteur"] / 3).round())
            .fillna(1)
            .clip(lower=1)
            ),
        poids=lambda table: (
            table["nombre_de_logements"]
            .where(table["nombre_de_logements"] > 0)
            .fillna(table.area * table["niveaux"] / 80)
            .where((table["nombre_de_logements"] > 0) | (table["usage_1"] == "Résidentiel"), 0)
            ),
        )


def lire_carreaux(bornes):
    bornes_laea = transform_bounds("EPSG:2154", "EPSG:3035", *bornes)

    carreaux = (
        pd.read_parquet("data/filosofi_2021.parquet")
        .loc[lambda table: table["est"].between(bornes_laea[0] - 200, bornes_laea[2])]
        .loc[lambda table: table["nord"].between(bornes_laea[1] - 200, bornes_laea[3])]
        .reset_index(drop=True)
        )

    return (
        gpd.GeoDataFrame(
            carreaux,
            geometry=[
                box(est, nord, est + 200, nord + 200)
                for est, nord in zip(carreaux["est"], carreaux["nord"], strict=True)
                ],
            crs=3035,
            )
        .to_crs(2154)
        )


def ajouter_habitants(batiments, bornes):
    repartition = (
        gpd.sjoin(
            batiments
            .loc[batiments["poids"] > 0, ["cleabs", "poids", "geometry"]]
            .assign(geometry=lambda table: table.centroid),
            lire_carreaux(bornes),
            predicate="within",
            )
        .drop_duplicates("cleabs")
        .assign(
            part=lambda table: (
                table["poids"] / table.groupby("index_right")["poids"].transform("sum")
                ),
            habitants=lambda table: table["ind"] * table["part"],
            )
        .set_index("cleabs")
        ["habitants"]
        )

    return batiments.assign(habitants=batiments["cleabs"].map(repartition).fillna(0))


def ajouter_communes(batiments, communes):
    centres = gpd.sjoin(
        batiments[["geometry"]].assign(geometry=lambda table: table.centroid),
        communes[["code_insee", "geometry"]],
        predicate="within",
        )

    return (
        batiments
        .assign(code_insee=centres["code_insee"].groupby(level=0).first())
        .dropna(subset=["code_insee"])
        .reset_index(drop=True)
        )


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
    except HTTPError:
        return pd.DataFrame()


def prix_au_m2(departement):
    return (
        pd.concat([ventes(departement, annee) for annee in range(2021, 2026)], ignore_index=True)
        .reindex(
            columns=[
                "id_mutation",
                "nature_mutation",
                "valeur_fonciere",
                "code_commune",
                "type_local",
                "surface_reelle_bati",
                ]
            )
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


def ajouter_valeurs(batiments, departement):
    prix = prix_au_m2(departement)

    prix_communes = (
        prix
        .groupby(["code_commune", "type_local"])
        .agg(ventes=("prix_m2", "size"), prix_m2=("prix_m2", "median"))
        .query("ventes >= 30")
        ["prix_m2"]
        )

    prix_departement = prix.groupby("type_local")["prix_m2"].median()

    type_local = pd.Series(
        np.where(batiments["nombre_de_logements"] == 1, "Maison", "Appartement"),
        index=batiments.index,
        )

    prix_m2 = (
        pd.Series(
            prix_communes
            .reindex(pd.MultiIndex.from_arrays([batiments["code_insee"], type_local]))
            .to_numpy(),
            index=batiments.index,
            )
        .fillna(type_local.map(prix_departement))
        )

    return batiments.assign(
        valeur=(
            (batiments.area * batiments["niveaux"] * 0.8 * prix_m2 / 1e6)
            .where(batiments["nombre_de_logements"] > 0, 0)
            .fillna(0)
            )
        )


def ajouter_expositions(batiments, bornes):
    exposes = batiments.copy()

    for cle, definition in scenarios.items():
        _, touches = batiments.sindex.query(
            zones(definition, bornes).make_valid(), predicate="intersects"
            )

        exposes[cle] = batiments.index.isin(touches)

        print(f"{cle} : {exposes[cle].sum()} bâtiments exposés")

    return exposes


def synthese(batiments, groupes):
    colonnes = batiments[groupes].copy()

    for cle in scenarios:
        for mesure in MESURES:
            colonnes[f"{cle}:{mesure}"] = batiments[mesure].where(batiments[cle], 0)

    return colonnes.groupby(groupes).sum()


def synthese_des_communes(batiments, communes):
    centres = communes.set_index("code_insee").representative_point().to_crs(4326)

    return (
        synthese(batiments, ["code_insee"])
        .reindex(communes["code_insee"], fill_value=0)
        .assign(
            nom=communes.set_index("code_insee")["nom_officiel"],
            longitude=centres.x.round(5),
            latitude=centres.y.round(5),
            )
        )


def synthese_des_carreaux(batiments):
    return synthese(
        batiments
        .loc[batiments[list(scenarios)].any(axis=1)]
        .assign(
            x=lambda table: (table.centroid.x // 1000).astype(int),
            y=lambda table: (table.centroid.y // 1000).astype(int),
            ),
        ["x", "y"],
        )


def pour_les_tuiles(batiments):
    tuiles = batiments.loc[batiments[list(scenarios)].any(axis=1), ["geometry"]]

    for cle in scenarios:
        tuiles[cle] = batiments[cle].map({True: 1, False: None})

    return tuiles.assign(geometry=lambda table: table.geometry.force_2d()).to_crs(4326)


departement = sys.argv[1]

sortie = Path(f"data/departements/{departement}")

sortie.mkdir(parents=True, exist_ok=True)

communes = lire_communes(departement)

perimetre = communes.union_all().buffer(500)

batiments = (
    lire_batiments(perimetre)
    .pipe(ajouter_poids)
    .pipe(ajouter_habitants, perimetre.bounds)
    .pipe(ajouter_communes, communes)
    .pipe(ajouter_valeurs, departement)
    .pipe(ajouter_expositions, perimetre.bounds)
    )

synthese_des_communes(batiments, communes).round(2).to_csv(sortie / "communes.csv")

synthese_des_carreaux(batiments).round(2).to_csv(sortie / "carreaux.csv")

pour_les_tuiles(batiments).to_file(
    sortie / "batiments.geojsonl", driver="GeoJSONSeq", COORDINATE_PRECISION=6
    )

print(f"{departement} : {len(communes)} communes, {len(batiments)} bâtiments, écrit {sortie}")

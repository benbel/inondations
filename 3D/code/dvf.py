import sys

import geopandas as gpd
import pandas as pd


def ventes(departement, annee):
    return pd.read_csv(
        f"https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/departements/{departement}.csv.gz",
        dtype={"code_commune": str, "id_mutation": str},
        usecols=[
            "id_mutation",
            "date_mutation",
            "nature_mutation",
            "valeur_fonciere",
            "code_commune",
            "type_local",
            "surface_reelle_bati",
            ],
        )


territoire = sys.argv[1]

communes = gpd.read_file(f"site/donnees/{territoire}/communes.geojson").to_crs(2154)

departements = sorted({code[:2] for code in communes["code_insee"]})

morceaux = []

for departement in departements:
    for annee in range(2021, 2026):
        morceaux.append(ventes(departement, annee))

mutations = pd.concat(morceaux, ignore_index=True)

prix = (
    mutations
    .loc[lambda mutations: mutations["code_commune"].isin(communes["code_insee"])]
    .loc[lambda mutations: mutations["nature_mutation"] == "Vente"]
    .assign(locaux=lambda table: table.groupby("id_mutation")["type_local"].transform("count"))
    .loc[lambda table: table["locaux"] == 1]
    .loc[lambda table: table["type_local"].isin(["Maison", "Appartement"])]
    .loc[lambda table: table["surface_reelle_bati"] > 0]
    .assign(prix_m2=lambda table: table["valeur_fonciere"] / table["surface_reelle_bati"])
    )

prix_communes = (
    prix
    .groupby(["code_commune", "type_local"], as_index=False)
    .agg(ventes=("prix_m2", "size"), prix_m2_median=("prix_m2", "median"))
    )

prix_territoire = (
    prix
    .groupby("type_local", as_index=False)
    .agg(ventes_territoire=("prix_m2", "size"), prix_m2_territoire=("prix_m2", "median"))
    )

print(prix_communes.round(0).to_string(index=False))

print(prix_territoire.round(0).to_string(index=False))

batiments = (
    gpd.read_file(f"data/{territoire}/batiments.gpkg")
    .loc[lambda batiments: batiments["nombre_de_logements"] > 0]
    .assign(
        type_local=lambda batiments: batiments["nombre_de_logements"].map(
            lambda logements: "Maison" if logements == 1 else "Appartement"
            ),
        niveaux=lambda batiments: (
            batiments["nombre_d_etages"]
            .fillna((batiments["hauteur"] / 3).round())
            .fillna(1)
            .clip(lower=1)
            ),
        surface_habitable=lambda batiments: batiments.area * batiments["niveaux"] * 0.8,
        )
    )

valeurs = (
    gpd.sjoin(
        batiments.assign(geometry=lambda batiments: batiments.centroid),
        communes[["code_insee", "geometry"]],
        predicate="within",
        )
    .rename(columns={"code_insee": "code_commune"})
    .merge(prix_communes, on=["code_commune", "type_local"], how="left")
    .merge(prix_territoire, on="type_local", how="left")
    .assign(
        prix_du_territoire=lambda table: ~(table["ventes"] >= 30),
        prix_m2=lambda table: (
            table["prix_m2_median"].where(~table["prix_du_territoire"], table["prix_m2_territoire"])
            ),
        valeur=lambda table: table["surface_habitable"] * table["prix_m2"],
        )
    [
        [
            "cleabs",
            "code_commune",
            "type_local",
            "surface_habitable",
            "prix_m2",
            "prix_du_territoire",
            "valeur",
            ]
        ]
    )

print(
    valeurs
    .agg(
        {
            "cleabs": "count",
            "surface_habitable": "sum",
            "valeur": "sum",
            "prix_du_territoire": "sum",
            }
        )
    )

valeurs.round(0).to_csv(f"data/{territoire}/valeurs_batiments.csv", index=False)

prix_communes.round(0).to_csv(f"data/{territoire}/prix_communes.csv", index=False)

print(f"écrit data/{territoire}/valeurs_batiments.csv et prix_communes.csv")

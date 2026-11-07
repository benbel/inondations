import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from rasterio.features import shapes
from rasterio.windows import from_bounds


def wfs(couche, parametres):
    for essai in range(5):
        try:
            reponse = requests.get(
                "https://data.geopf.fr/wfs/ows",
                params={
                    "SERVICE": "WFS",
                    "VERSION": "2.0.0",
                    "REQUEST": "GetFeature",
                    "TYPENAMES": couche,
                    "SRSNAME": "EPSG:2154",
                    "OUTPUTFORMAT": "application/json",
                    **parametres,
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

    return gpd.GeoDataFrame.from_features(reponse.json()["features"], crs=2154)


def nombre_de_batiments(bornes):
    reponse = requests.get(
        "https://data.geopf.fr/wfs/ows",
        params={
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": "BDTOPO_V3:batiment",
            "RESULTTYPE": "hits",
            "BBOX": f"{bornes[0]},{bornes[1]},{bornes[2]},{bornes[3]},EPSG:2154",
            },
        timeout=300,
        )

    reponse.raise_for_status()

    return int(re.search(r'numberMatched="([0-9]+)"', reponse.text).group(1))


def page_batiments(bornes, depart):
    return wfs(
        "BDTOPO_V3:batiment",
        {
            "BBOX": f"{bornes[0]},{bornes[1]},{bornes[2]},{bornes[3]},EPSG:2154",
            "SORTBY": "cleabs",
            "COUNT": 5000,
            "STARTINDEX": depart,
            },
        )


def zones_tri(definition, bornes):
    departement = definition["departement"]

    morceaux = []

    for scenario, couches in definition["couches"].items():
        for couche in couches:
            morceau = (
                gpd.read_file(
                    f"/vsizip/vsicurl/https://files.georisques.fr/di_2020/tri_2020_sig_di_{departement}.zip"
                    f"/tri_2020_sig_di_{departement}/{definition['dossier']}"
                    f"/n_tri_{definition['prefixe']}_{couche}_{departement.zfill(3)}.shp",
                    bbox=tuple(bornes),
                    )
                .to_crs(2154)
                .assign(scenario=scenario)
                )

            if "ht_min" not in morceau.columns:
                morceau = morceau.assign(ht_min=0.0, ht_max=0.0)

            morceaux.append(morceau)

    return pd.concat(morceaux, ignore_index=True)[["scenario", "ht_min", "ht_max", "geometry"]]


def zones_jrc(definition, bornes):
    bornes_wgs84 = (
        gpd.GeoSeries(gpd.points_from_xy(bornes[[0, 2]], bornes[[1, 3]]), crs=2154)
        .to_crs(4326)
        .total_bounds
        )

    morceaux = []

    for scenario, periode in definition["periodes"].items():
        with rasterio.open(
            "/vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/public/JRC-OpenData/CEMS-EFAS/flood_hazard"
            f"/Europe_RP{periode}_filled_depth.tif"
            ) as source:
            fenetre = from_bounds(*bornes_wgs84, source.transform).round_offsets().round_lengths()

            profondeurs = source.read(1, window=fenetre)

            transformation = source.window_transform(fenetre)

        classes = np.digitize(profondeurs, [0.5, 1, 2]).astype("int16")

        entites = []

        for geometrie, classe in shapes(classes, mask=profondeurs > 0, transform=transformation):
            entites.append(
                {"type": "Feature", "properties": {"classe": classe}, "geometry": geometrie}
                )

        morceaux.append(
            gpd.GeoDataFrame.from_features(entites, crs=4326)
            .to_crs(2154)
            .assign(
                scenario=scenario,
                ht_min=lambda zones: zones["classe"].map({0: 0.0, 1: 0.5, 2: 1.0, 3: 2.0}),
                ht_max=lambda zones: zones["classe"].map({0: 0.5, 1: 1.0, 2: 2.0, 3: 999.0}),
                )
            )

    return pd.concat(morceaux, ignore_index=True)[["scenario", "ht_min", "ht_max", "geometry"]]


def polygones(surfaces):
    return (
        surfaces
        .assign(geometry=lambda surfaces: surfaces.geometry.simplify(1))
        .explode(index_parts=False)
        .loc[lambda parties: parties.geom_type.isin(["Polygon", "MultiPolygon"])]
        .explode(index_parts=False)
        .to_crs(4326)
        )


def ecrire(table, chemin):
    table.to_file(chemin, driver="GeoJSON", COORDINATE_PRECISION=6)


territoires = {
    "saint_omer": {
        "nom": "Saint-Omer",
        "source": "tri",
        "departement": "62",
        "dossier": "FRA_TRI_SAINT_OMER",
        "prefixe": "saint_omer",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": [
            "62040", "62139", "62225", "62403", "62423", "62452", "62525",
            "62757", "62765", "62772", "62792", "62819", "62902",
            ],
        },
    "boucle_marne": {
        "nom": "la boucle de la Marne",
        "source": "tri",
        "departement": "94",
        "dossier": "FRH_TRI_METROPOLEFRANCILIENNE",
        "prefixe": "metropolefrancilienne",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": ["94011", "94017", "94028", "94042", "94046", "94068"],
        },
    "ales": {
        "nom": "Alès",
        "source": "tri",
        "departement": "30",
        "dossier": "FRD_TRI_ALES",
        "prefixe": "ales",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": [
            "30007", "30010", "30027", "30037", "30042", "30045", "30051", "30068",
            "30077", "30120", "30129", "30132", "30147", "30152", "30162",
            "30165", "30167", "30171", "30173", "30223", "30227", "30237", "30239",
            "30243", "30259", "30268", "30269", "30270", "30271", "30274", "30284",
            "30294", "30303", "30305", "30307", "30329",
            ],
        },
    "saint_malo": {
        "nom": "Saint-Malo et de la baie du Mont-Saint-Michel",
        "source": "tri",
        "departement": "35",
        "dossier": "FRG_TRI_ST_MALO_BAIE_DU_MONT_ST_MICHEL",
        "prefixe": "st_malo_baie_du_mont_st_michel",
        "couches": {
            "frequent": ["iso_ht_03_01for_s"],
            "moyen": ["iso_ht_03_02moy_s"],
            "moyen_climat": ["iso_ht_03_03mcc_s"],
            "extreme": ["iso_ht_03_04fai_s"],
            },
        "communes": [
            "35010", "35049", "35070", "35078", "35095", "35116", "35122", "35132",
            "35153", "35179", "35186", "35224", "35246", "35247", "35255", "35259",
            "35270", "35279", "35288", "35291", "35299", "35306", "35361", "50042",
            "50353", "50410",
            ],
        },
    "aude": {
        "nom": "l'Aude, de Carcassonne à Trèbes",
        "source": "tri",
        "departement": "11",
        "dossier": "FRD_TRI_CARCASSONNE",
        "prefixe": "carcassonne",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": ["11037", "11069", "11088", "11397"],
        },
    "calais": {
        "nom": "Calais",
        "source": "tri",
        "departement": "62",
        "dossier": "FRA_TRI_CALAIS",
        "prefixe": "calais",
        "couches": {
            "frequent": ["iso_ht_03_01for_s"],
            "moyen": ["iso_ht_03_02moy_s"],
            "moyen_climat": ["iso_ht_03_03mcc_s"],
            },
        "communes": ["62193", "62239", "62244", "62360", "62548", "62774"],
        },
    "delta_rhone": {
        "nom": "Arles et du delta du Rhône",
        "source": "tri",
        "departement": "30",
        "dossier": "FRD_TRI_DELTA",
        "prefixe": "delta",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "moyen_climat": ["iso_ht_01_03mcc_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": [
            "13004", "13078", "13096", "13108",
            "30032", "30034", "30117", "30258",
            ],
        },
    "paris": {
        "nom": "Paris",
        "source": "tri",
        "departement": "75",
        "dossier": "FRH_TRI_METROPOLEFRANCILIENNE",
        "prefixe": "metropolefrancilienne",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": ["75056"],
        },
    "lyon": {
        "nom": "Lyon et Villeurbanne",
        "source": "tri",
        "departement": "69",
        "dossier": "FRD_TRI_LYON",
        "prefixe": "lyon",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": ["69123", "69266"],
        },
    "marseille": {
        "nom": "Marseille et Aubagne",
        "source": "tri",
        "departement": "13",
        "dossier": "FRD_TRI_MARSEILLE",
        "prefixe": "marseille",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": ["13055", "13005"],
        },
    "bastia": {
        "nom": "Bastia",
        "source": "tri",
        "departement": "2B",
        "dossier": "FRE_TRI_BASTIA",
        "prefixe": "bastia",
        "couches": {
            "frequent": ["iso_ht_01_01for_s", "iso_ht_02_01for_s", "iso_ht_03_01for_s"],
            "moyen": ["iso_ht_01_02moy_s", "iso_ht_02_02moy_s", "iso_ht_03_02moy_s"],
            "moyen_climat": ["iso_ht_03_03mcc_s"],
            "extreme": ["iso_ht_01_04fai_s", "iso_ht_02_04fai_s", "iso_ht_03_04fai_s"],
            },
        "communes": ["2B033", "2B353"],
        },
    "toulouse": {
        "nom": "Toulouse",
        "source": "tri",
        "departement": "31",
        "dossier": "FRF_TRI_TOULOUSE",
        "prefixe": "toulouse",
        "couches": {
            "frequent": ["inondable_01_01for_s"],
            "moyen": ["inondable_01_02moy_s"],
            "extreme": ["inondable_01_04fai_s"],
            },
        "communes": [
            "31056", "31069", "31182", "31205", "31259", "31293", "31420",
            "31433", "31458", "31490", "31541", "31555", "31575",
            ],
        },
    "bordeaux": {
        "nom": "Bordeaux",
        "source": "tri",
        "departement": "33",
        "dossier": "FRF_TRI_BORD",
        "prefixe": "bord",
        "couches": {
            "frequent": ["iso_ht_01_01for_s", "iso_ht_03_01for_s"],
            "moyen": ["iso_ht_01_02moy_s", "iso_ht_03_02moy_s"],
            "moyen_climat": ["iso_ht_03_03mcc_s"],
            "extreme": ["iso_ht_01_04fai_s", "iso_ht_03_04fai_s"],
            },
        "communes": ["33032", "33039", "33063", "33065", "33119", "33167", "33249"],
        },
    "grenoble": {
        "nom": "Grenoble",
        "source": "tri",
        "departement": "38",
        "dossier": "FRD_TRI_GRENOBLE_VOIRON",
        "prefixe": "grenoble_voiron",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": [
            "38151", "38169", "38185", "38229", "38382", "38421", "38423",
            "38474", "38485", "38516",
            ],
        },
    "nimes": {
        "nom": "Nîmes",
        "source": "tri",
        "departement": "30",
        "dossier": "FRD_TRI_NIMES",
        "prefixe": "nimes",
        "couches": {
            "frequent": ["iso_ht_01_01for_s"],
            "moyen": ["iso_ht_01_02moy_s"],
            "extreme": ["iso_ht_01_04fai_s"],
            },
        "communes": ["30189"],
        },
    "roya_vesubie": {
        "nom": "la Roya et de la Vésubie",
        "source": "jrc",
        "periodes": {"frequent": 10, "moyen": 100, "extreme": 500},
        "communes": [
            "06013", "06020", "06023", "06062", "06074", "06103",
            "06127", "06132", "06156", "06162", "06163",
            ],
        },
    }

libelles = {
    "tri": {
        "frequent": "le scénario fréquent",
        "moyen": "le scénario moyen",
        "moyen_climat": "le scénario moyen avec changement climatique",
        "extreme": "le scénario extrême",
        },
    "jrc": {
        "frequent": "la crue décennale",
        "moyen": "la crue centennale",
        "extreme": "la crue de cinq cents ans",
        },
    }

territoire = sys.argv[1]

definition = territoires[territoire]

communes = wfs(
    "ADMINEXPRESS-COG.LATEST:commune",
    {"CQL_FILTER": "code_insee IN ('" + "', '".join(definition["communes"]) + "')"},
    )[["code_insee", "nom_officiel", "population", "geometry"]]

print(communes[["code_insee", "nom_officiel", "population"]])

print(f"{len(communes)} communes sur {len(definition['communes'])}")

if definition["source"] == "tri":
    morceaux = zones_tri(definition, communes.total_bounds)
else:
    morceaux = zones_jrc(definition, communes.total_bounds)

perimetre = communes.union_all()

morceaux = (
    morceaux
    .assign(geometry=lambda morceaux: morceaux.geometry.make_valid())
    .clip(perimetre)
    )

scenarios = []

for scenario in libelles[definition["source"]]:
    if scenario in set(morceaux["scenario"]):
        scenarios.append(scenario)

zones = morceaux.dissolve(by=["scenario", "ht_min", "ht_max"], as_index=False)

emprises = zones.dissolve(by="scenario", as_index=False)[["scenario", "geometry"]]

nombre = nombre_de_batiments(communes.total_bounds)

print(f"{nombre} bâtiments à lire")

departs = list(range(0, nombre, 5000))

with ThreadPoolExecutor(max_workers=8) as executeur:
    pages = list(executeur.map(page_batiments, [communes.total_bounds] * len(departs), departs))

print(f"{sum(len(page) for page in pages)} bâtiments lus")

batiments = (
    pd.concat(pages, ignore_index=True)
    .query("etat_de_l_objet == 'En service'")
    .loc[lambda batiments: batiments.intersects(perimetre)]
    [
        [
            "cleabs",
            "usage_1",
            "nombre_de_logements",
            "nombre_d_etages",
            "hauteur",
            "altitude_minimale_sol",
            "geometry",
            ]
        ]
    .astype(
        {
            "nombre_de_logements": "float",
            "nombre_d_etages": "float",
            "hauteur": "float",
            "altitude_minimale_sol": "float",
            }
        )
    .reset_index(drop=True)
    )

hauteur_d_eau = (
    gpd.sjoin(batiments[["cleabs", "geometry"]], morceaux, predicate="intersects")
    .groupby(["cleabs", "scenario"], as_index=False)
    .agg(ht_min=("ht_min", "max"))
    .pivot(index="cleabs", columns="scenario", values="ht_min")
    .reindex(columns=scenarios)
    .reset_index()
    )

batiments = batiments.merge(hauteur_d_eau, on="cleabs", how="left")

print(
    pd.DataFrame(
        {
            "bâtiments": [batiments[scenario].notna().sum() for scenario in scenarios],
            "logements": [
                batiments.loc[batiments[scenario].notna(), "nombre_de_logements"].sum()
                for scenario in scenarios
                ],
            },
        index=scenarios,
        )
    )

Path(f"site/donnees/{territoire}").mkdir(parents=True, exist_ok=True)

Path(f"data/{territoire}").mkdir(parents=True, exist_ok=True)

batiments.to_file(f"data/{territoire}/batiments.gpkg")

emprises.to_file(f"data/{territoire}/emprises.gpkg")

ecrire(polygones(emprises), f"data/{territoire}/emprises.geojson")

ecrire(communes.to_crs(4326), f"site/donnees/{territoire}/communes.geojson")

Path(f"site/donnees/{territoire}/territoire.json").write_text(
    json.dumps(
        {
            "nom": definition["nom"],
            "source": definition["source"],
            "scenarios": [
                {"cle": scenario, "libelle": libelles[definition["source"]][scenario]}
                for scenario in scenarios
                ],
            },
        ensure_ascii=False,
        indent=1,
        )
    )

print(f"écrit site/donnees/{territoire} et data/{territoire}, {len(batiments)} bâtiments")

import io
import sys
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import py7zr
import requests
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import box


def telecharger(url, chemin, essais=6):
    chemin = Path(chemin)
    if chemin.exists() and zipfile.is_zipfile(chemin):
        return chemin
    partiel = chemin.with_name(chemin.name + ".part")

    for essai in range(1, essais + 1):
        deja = partiel.stat().st_size if partiel.exists() else 0
        entetes = {"Range": f"bytes={deja}-"} if deja else {}
        try:
            with requests.get(url, headers=entetes, stream=True, timeout=(30, 300)) as r:
                if r.status_code != 416:
                    r.raise_for_status()
                    reprise = deja > 0 and r.status_code == 206
                    with open(partiel, "ab" if reprise else "wb") as f:
                        for bloc in r.iter_content(chunk_size=1 << 20):
                            f.write(bloc)
            if zipfile.is_zipfile(partiel):
                partiel.replace(chemin)
                return chemin
            partiel.unlink(missing_ok=True)
            raise IOError("archive incomplète ou corrompue")
        except (requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                IOError) as e:
            if essai == essais:
                raise
            attente = 10 * 2 ** (essai - 1)
            print(f"Téléchargement interrompu ({e!r}), essai {essai}/{essais}, reprise dans {attente}s")
            time.sleep(attente)


def lire_filosofi(annee, url, membre):
    archive = telecharger(url, f"data/filosofi/{annee}.zip")

    with zipfile.ZipFile(archive) as contenant:
        if membre in contenant.namelist():
            return pd.read_csv(contenant.open(membre), dtype=str)

        septzip = contenant.read(contenant.namelist()[0])

    with py7zr.SevenZipFile(io.BytesIO(septzip)) as contenant_7z:
        contenant_7z.extract(path="data/filosofi", targets=[membre])

    return pd.read_csv(f"data/filosofi/{membre}", dtype=str)


def carreaux_du_territoire(annee, url, membre, bornes):
    carreaux = (
        lire_filosofi(annee, url, membre)
        .rename(columns=str.lower)
        .rename(columns={"idinspire": "idcar_200m", "i_est_cr": "i_est_200"})
        .assign(
            nord=lambda table: table["idcar_200m"].str.extract(r"N(\d+)E")[0].astype(int),
            est=lambda table: table["idcar_200m"].str.extract(r"E(\d+)$")[0].astype(int),
            )
        .loc[lambda table: table["est"].between(bornes[0] - 200, bornes[2])]
        .loc[lambda table: table["nord"].between(bornes[1] - 200, bornes[3])]
        [
            [
                "idcar_200m",
                "est",
                "nord",
                "i_est_200",
                "ind",
                "men",
                "men_pauv",
                "ind_snv",
                "ind_65_79",
                "ind_80p",
                ]
            ]
        .astype(
            {
                "i_est_200": "int",
                "ind": "float",
                "men": "float",
                "men_pauv": "float",
                "ind_snv": "float",
                "ind_65_79": "float",
                "ind_80p": "float",
                }
            )
        .assign(
            annee=annee,
            ind_65p=lambda table: table["ind_65_79"] + table["ind_80p"],
            )
        )

    carres = []

    for est, nord in zip(carreaux["est"], carreaux["nord"], strict=True):
        carres.append(box(est, nord, est + 200, nord + 200))

    return gpd.GeoDataFrame(carreaux, geometry=carres, crs=3035).to_crs(2154)


def part_en_zone(carreaux, emprise):
    gauche = carreaux["est"].min()

    haut = carreaux["nord"].max() + 200

    colonnes = int((carreaux["est"].max() + 200 - gauche) / 200)

    lignes = int((haut - carreaux["nord"].min()) / 200)

    masque = rasterize(
        emprise.to_crs(3035).geometry,
        out_shape=(lignes * 20, colonnes * 20),
        transform=from_origin(gauche, haut, 10, 10),
        fill=0,
        default_value=1,
        dtype="uint8",
        )

    parts = masque.reshape(lignes, 20, colonnes, 20).mean(axis=(1, 3))

    return parts[
        ((haut - 200 - carreaux["nord"]) / 200).astype(int),
        ((carreaux["est"] - gauche) / 200).astype(int),
        ]


def sommes_au_prorata(table, part):
    return {
        "habitants": (table["ind"] * part).sum(),
        "menages": (table["men"] * part).sum(),
        "menages_pauvres": (table["men_pauv"] * part).sum(),
        "habitants_65_ans_et_plus": (table["ind_65p"] * part).sum(),
        "niveaux_de_vie": (table["ind_snv"] * part).sum(),
        "habitants_carreaux_imputes": (table["ind"] * part).where(table["i_est_200"] == 1, 0).sum(),
        }


territoire = sys.argv[1]

batiments = (
    gpd.read_file(f"data/{territoire}/batiments.gpkg")
    .assign(
        niveaux=lambda table: (
            table["nombre_d_etages"]
            .fillna((table["hauteur"] / 3).round())
            .fillna(1)
            .clip(lower=1)
            ),
        residentiel=lambda table: (
            (table["nombre_de_logements"] > 0) | (table["usage_1"] == "Résidentiel")
            ),
        poids=lambda table: (
            table["nombre_de_logements"]
            .where(table["nombre_de_logements"] > 0)
            .fillna(table.area * table["niveaux"] / 80)
            .where(table["residentiel"], 0)
            ),
        )
    )

emprises = gpd.read_file(f"data/{territoire}/emprises.gpkg")

communes = gpd.read_file(f"site/donnees/{territoire}/communes.geojson").to_crs(2154)

Path("data/filosofi").mkdir(parents=True, exist_ok=True)

millesimes = {
    2015: (
        "https://www.insee.fr/fr/statistiques/fichier/4176290/Filosofi2015_carreaux_200m_csv.zip",
        "Filosofi2015_carreaux_200m_metropole.csv",
        ),
    2017: (
        "https://www.insee.fr/fr/statistiques/fichier/6215138/Filosofi2017_carreaux_200m_csv.zip",
        "Filosofi2017_carreaux_200m_met.csv",
        ),
    2019: (
        "https://www.insee.fr/fr/statistiques/fichier/7655475/Filosofi2019_carreaux_200m_csv.zip",
        "carreaux_200m_met.csv",
        ),
    2021: (
        "https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip",
        "carreaux_200m_met.csv",
        ),
    }

repartitions = []

expositions = []

for annee, (url, membre) in millesimes.items():
    carreaux = (
        carreaux_du_territoire(annee, url, membre, communes.to_crs(3035).total_bounds)
        .loc[lambda table: table.intersects(communes.union_all())]
        )

    centres = (
        batiments
        .loc[batiments["poids"] > 0, ["cleabs", "poids", "geometry"]]
        .assign(geometry=lambda table: table.centroid)
        )

    repartition = (
        gpd.sjoin(centres, carreaux, predicate="within")
        .assign(
            part=lambda table: (
                table["poids"] / table.groupby("idcar_200m")["poids"].transform("sum")
                ),
            habitants=lambda table: table["ind"] * table["part"],
            menages=lambda table: table["men"] * table["part"],
            menages_pauvres=lambda table: table["men_pauv"] * table["part"],
            habitants_65_ans_et_plus=lambda table: table["ind_65p"] * table["part"],
            niveaux_de_vie=lambda table: table["ind_snv"] * table["part"],
            impute=lambda table: table["i_est_200"],
            )
        [
            [
                "cleabs",
                "idcar_200m",
                "annee",
                "habitants",
                "menages",
                "menages_pauvres",
                "habitants_65_ans_et_plus",
                "niveaux_de_vie",
                "impute",
                ]
            ]
        )

    print(
        pd.Series(
            {
                "annee": annee,
                "carreaux": len(carreaux),
                "habitants des carreaux": carreaux["ind"].sum(),
                "habitants répartis sur le bâti": repartition["habitants"].sum(),
                "écart": carreaux["ind"].sum() - repartition["habitants"].sum(),
                "carreaux sans bâtiment résidentiel": (
                    (~carreaux["idcar_200m"].isin(repartition["idcar_200m"])).sum()
                    ),
                }
            )
        )

    repartitions.append(repartition)

    for scenario in emprises["scenario"]:
        touches = repartition.merge(batiments.loc[batiments[scenario].notna(), ["cleabs"]])

        part = part_en_zone(carreaux, emprises.loc[emprises["scenario"] == scenario])

        expositions.append(
            {
                "annee": annee,
                "scenario": scenario,
                "methode": "bâti",
                "habitants": touches["habitants"].sum(),
                "menages": touches["menages"].sum(),
                "menages_pauvres": touches["menages_pauvres"].sum(),
                "habitants_65_ans_et_plus": touches["habitants_65_ans_et_plus"].sum(),
                "niveaux_de_vie": touches["niveaux_de_vie"].sum(),
                "habitants_carreaux_imputes": (
                    touches["habitants"].where(touches["impute"] == 1, 0).sum()
                    ),
                }
            )

        expositions.append(
            {
                "annee": annee,
                "scenario": scenario,
                "methode": "carreau",
                **sommes_au_prorata(carreaux, part),
                }
            )

    expositions.append(
        {
            "annee": annee,
            "scenario": "ensemble",
            "methode": "carreau",
            **sommes_au_prorata(carreaux, 1),
            }
        )

pd.concat(repartitions).round(3).to_csv(f"data/{territoire}/population_batiments.csv", index=False)

exposition = pd.DataFrame(expositions)

print(exposition.round(0).to_string(index=False))

exposition.to_csv(f"data/{territoire}/population_exposee.csv", index=False)

print(f"écrit data/{territoire}/population_batiments.csv et population_exposee.csv")

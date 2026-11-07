import io
import time

import geopandas as gpd
import numpy as np
import requests
from PIL import Image

departements = [
    *[f"{numero:02d}" for numero in range(1, 20)],
    "2A",
    "2B",
    *[f"{numero:02d}" for numero in range(21, 96)],
    ]

# Chaque scénario occupe un bit de l'attribut « z » des bâtiments tuilés.
scenarios = {
    "tri_frequent": {
        "source": "tri",
        "libelle": "fréquente (TRI)",
        "couches": ["ALEA_SYNT_01_01FOR_FXX", "ALEA_SYNT_02_01FOR_FXX", "ALEA_SYNT_03_01FOR_FXX"],
        },
    "tri_moyen": {
        "source": "tri",
        "libelle": "moyenne (TRI)",
        "couches": ["ALEA_SYNT_01_02MOY_FXX", "ALEA_SYNT_02_02MOY_FXX", "ALEA_SYNT_03_02MOY_FXX"],
        },
    "tri_moyen_climat": {
        "source": "tri",
        "libelle": "moyenne + climat (TRI)",
        "couches": ["ALEA_SYNT_01_03MCC_FXX", "ALEA_SYNT_03_03MCC_FXX"],
        },
    "tri_extreme": {
        "source": "tri",
        "libelle": "extrême (TRI)",
        "couches": ["ALEA_SYNT_01_04FAI_FXX", "ALEA_SYNT_02_04FAI_FXX", "ALEA_SYNT_03_04FAI_FXX"],
        },
    "jrc_10": {"source": "jrc", "libelle": "décennale (JRC)", "periode": 10},
    "jrc_100": {"source": "jrc", "libelle": "centennale (JRC)", "periode": 100},
    "jrc_500": {"source": "jrc", "libelle": "cinq-centennale (JRC)", "periode": 500},
    "eaip": {"source": "eaip", "libelle": "maximale (EAIP)"},
    }

GEORISQUES = "https://www.georisques.gouv.fr/services"


def jrc(periode):
    return (
        "/vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/public/JRC-OpenData/CEMS-EFAS/flood_hazard"
        f"/Europe_RP{periode}_filled_depth.tif"
        )


def obtenir(url, parametres, essais=6, accepter=()):
    for essai in range(essais):
        try:
            reponse = requests.get(url, params=parametres, timeout=300)

            if reponse.ok or reponse.status_code in accepter:
                return reponse
        except requests.RequestException:
            if essai == essais - 1:
                raise

        time.sleep(10 * (essai + 1))

    reponse.raise_for_status()

    return reponse


def wfs_ign(couche, parametres):
    reponse = obtenir(
        "https://data.geopf.fr/wfs/ows",
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": couche,
            "SRSNAME": "EPSG:2154",
            "OUTPUTFORMAT": "application/json",
            **parametres,
            },
        )

    entites = reponse.json()["features"]

    if not entites:
        return gpd.GeoDataFrame(geometry=[], crs=2154)

    return gpd.GeoDataFrame.from_features(entites, crs=2154)


def wfs_georisques(couche, bornes, taille=500):
    """Lit une couche WFS de Géorisques par pages (le serveur coupe les réponses longues)."""
    pages = []

    depart = 0

    while True:
        reponse = obtenir(
            GEORISQUES,
            {
                "SERVICE": "WFS",
                "VERSION": "2.0.0",
                "REQUEST": "GetFeature",
                "TYPENAMES": f"ms:{couche}",
                "SRSNAME": "EPSG:2154",
                "BBOX": ",".join(str(round(borne)) for borne in bornes)
                + ",urn:ogc:def:crs:EPSG::2154",
                "COUNT": taille,
                "STARTINDEX": depart,
                },
            )

        page = gpd.read_file(io.BytesIO(reponse.content))

        if len(page) == 0:
            break

        pages.append(page.set_crs(2154, allow_override=True)[["geometry"]])

        if len(page) < taille:
            break

        depart = depart + taille

    if not pages:
        return gpd.GeoDataFrame(geometry=[], crs=2154)

    return gpd.GeoDataFrame(
        gpd.pd.concat(pages, ignore_index=True), geometry="geometry", crs=2154
        )


def masque_eaip(bornes, resolution=26, cote=4000):
    """Raster booléen de l'EAIP, lu sur le WMS de Géorisques (seule diffusion nationale).

    Le WMS n'affiche la couche qu'entre le 1:90 000 et le 1:500 000, soit des pixels
    d'au moins 25,2 m.
    """
    gauche = np.floor(bornes[0] / resolution) * resolution

    haut = np.ceil(bornes[3] / resolution) * resolution

    colonnes = int(np.ceil((bornes[2] - gauche) / resolution))

    lignes = int(np.ceil((haut - bornes[1]) / resolution))

    masque = np.zeros((lignes, colonnes), dtype=bool)

    for ligne in range(0, lignes, cote):
        for colonne in range(0, colonnes, cote):
            x = gauche + colonne * resolution

            y = haut - ligne * resolution

            reponse = obtenir(
                GEORISQUES,
                {
                    "SERVICE": "WMS",
                    "VERSION": "1.3.0",
                    "REQUEST": "GetMap",
                    "LAYERS": "MASQ_EAIP",
                    "STYLES": "",
                    "CRS": "EPSG:2154",
                    "BBOX": f"{x},{y - cote * resolution},{x + cote * resolution},{y}",
                    "WIDTH": cote,
                    "HEIGHT": cote,
                    "FORMAT": "image/png",
                    "TRANSPARENT": "true",
                    },
                )

            if not reponse.headers.get("content-type", "").startswith("image/png"):
                raise RuntimeError(f"réponse WMS inattendue : {reponse.text[:300]}")

            alpha = np.array(Image.open(io.BytesIO(reponse.content)).convert("RGBA"))[:, :, 3]

            morceau = alpha >= 40

            hauteur = min(cote, lignes - ligne)

            largeur = min(cote, colonnes - colonne)

            masque[ligne:ligne + hauteur, colonne:colonne + largeur] = (
                morceau[:hauteur, :largeur]
                )

    return masque, (gauche, haut, resolution)


def touche(masque, gauche, haut, pas_x, pas_y, bornes):
    """Vrai pour chaque rectangle `bornes` (n × 4) qui recouvre au moins un pixel du masque."""
    cumul = np.zeros((masque.shape[0] + 1, masque.shape[1] + 1), dtype="int32")

    cumul[1:, 1:] = masque.astype("int32").cumsum(axis=0).cumsum(axis=1)

    colonne_0 = np.floor((bornes[:, 0] - gauche) / pas_x).astype(int)

    colonne_1 = np.floor((bornes[:, 2] - gauche) / pas_x).astype(int)

    ligne_0 = np.floor((haut - bornes[:, 3]) / pas_y).astype(int)

    ligne_1 = np.floor((haut - bornes[:, 1]) / pas_y).astype(int)

    dehors = (
        (colonne_1 < 0)
        | (ligne_1 < 0)
        | (colonne_0 >= masque.shape[1])
        | (ligne_0 >= masque.shape[0])
        )

    colonne_0 = np.clip(colonne_0, 0, masque.shape[1] - 1)

    colonne_1 = np.clip(colonne_1, 0, masque.shape[1] - 1) + 1

    ligne_0 = np.clip(ligne_0, 0, masque.shape[0] - 1)

    ligne_1 = np.clip(ligne_1, 0, masque.shape[0] - 1) + 1

    somme = (
        cumul[ligne_1, colonne_1]
        - cumul[ligne_0, colonne_1]
        - cumul[ligne_1, colonne_0]
        + cumul[ligne_0, colonne_0]
        )

    return (somme > 0) & ~dehors

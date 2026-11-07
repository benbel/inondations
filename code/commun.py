import io
import time
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from PIL import Image
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from shapely.geometry import box, shape

GEORISQUES = "https://www.georisques.gouv.fr/services"

MESURES = ["batiments", "habitants", "valeur"]

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


def jrc(periode):
    return (
        "/vsicurl/https://jeodpp.jrc.ec.europa.eu/ftp/public/JRC-OpenData/CEMS-EFAS/flood_hazard"
        f"/Europe_RP{periode}_filled_depth.tif"
        )


def obtenir(url, parametres):
    for essai in range(6):
        try:
            reponse = requests.get(url, params=parametres, timeout=300)

            if reponse.ok:
                return reponse
        except requests.RequestException:
            if essai == 5:
                raise

        time.sleep(10 * (essai + 1))

    reponse.raise_for_status()


def toutes_les_pages(lire_page, taille):
    pages = []

    depart = 0

    while True:
        page = lire_page(depart)

        pages.append(page)

        if len(page) < taille:
            return pd.concat(pages, ignore_index=True)

        depart = depart + taille


def dalles(bornes, cote):
    return [
        (gauche, bas)
        for gauche in np.arange(np.floor(bornes[0] / cote) * cote, bornes[2], cote)
        for bas in np.arange(np.floor(bornes[1] / cote) * cote, bornes[3], cote)
        ]


def cellules(perimetre, cote):
    return [
        (gauche, bas, gauche + cote, bas + cote)
        for gauche, bas in dalles(perimetre.bounds, cote)
        if perimetre.intersects(box(gauche, bas, gauche + cote, bas + cote))
        ]


def wfs_ign(couche, parametres):
    entites = (
        obtenir(
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
        .json()["features"]
        )

    if not entites:
        return gpd.GeoDataFrame(geometry=[], crs=2154)

    return gpd.GeoDataFrame.from_features(entites, crs=2154)


def page_georisques(couche, bornes, depart):
    reponse = obtenir(
        GEORISQUES,
        {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": f"ms:{couche}",
            "SRSNAME": "EPSG:2154",
            "BBOX": ",".join(str(round(borne)) for borne in bornes) + ",urn:ogc:def:crs:EPSG::2154",
            "COUNT": 200,
            "STARTINDEX": depart,
            },
        )

    return (
        gpd.read_file(io.BytesIO(reponse.content))
        .set_crs(2154, allow_override=True)
        [["geometry"]]
        )


def polygones(masque, transformation, crs):
    return gpd.GeoSeries(
        [
            shape(geometrie)
            for geometrie, _ in shapes(
                masque.astype("uint8"), mask=masque, transform=transformation
                )
            ],
        crs=crs,
        )


def cellule_georisques(couche, cellule):
    return toutes_les_pages(lambda depart: page_georisques(couche, cellule, depart), 200)


def couche_georisques(couche, perimetre):
    with ThreadPoolExecutor(max_workers=4) as executeur:
        morceaux = list(
            executeur.map(
                lambda cellule: cellule_georisques(couche, cellule), cellules(perimetre, 10000)
                )
            )

    return pd.concat(morceaux, ignore_index=True)


def zones_tri(couches, perimetre):
    zones = (
        pd.concat([couche_georisques(couche, perimetre) for couche in couches], ignore_index=True)
        .geometry
        )

    return zones[~zones.to_wkb().duplicated()]


def zones_jrc(periode, bornes):
    bornes_wgs84 = transform_bounds("EPSG:2154", "EPSG:4326", *bornes)

    with rasterio.open(jrc(periode)) as source:
        fenetre = from_bounds(*bornes_wgs84, source.transform).round_offsets().round_lengths()

        profondeurs = source.read(1, window=fenetre, boundless=True, fill_value=0)

        transformation = source.window_transform(fenetre)

    return polygones(profondeurs > 0, transformation, 4326).to_crs(2154)


def dalle_eaip(gauche, bas, resolution, cote):
    etendue = resolution * cote

    reponse = obtenir(
        GEORISQUES,
        {
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": "MASQ_EAIP",
            "STYLES": "",
            "CRS": "EPSG:2154",
            "BBOX": f"{gauche},{bas},{gauche + etendue},{bas + etendue}",
            "WIDTH": cote,
            "HEIGHT": cote,
            "FORMAT": "image/png",
            "TRANSPARENT": "true",
            },
        )

    alpha = np.array(Image.open(io.BytesIO(reponse.content)).convert("RGBA"))[:, :, 3]

    return polygones(
        alpha >= 40, from_origin(gauche, bas + etendue, resolution, resolution), 2154
        )


def zones_eaip(bornes, resolution=26, cote=4000):
    return pd.concat(
        [
            dalle_eaip(gauche, bas, resolution, cote)
            for gauche, bas in dalles(bornes, resolution * cote)
            ],
        ignore_index=True,
        )


def zones(definition, perimetre):
    if definition["source"] == "tri":
        return zones_tri(definition["couches"], perimetre)

    if definition["source"] == "jrc":
        return zones_jrc(definition["periode"], perimetre.bounds)

    return zones_eaip(perimetre.bounds)

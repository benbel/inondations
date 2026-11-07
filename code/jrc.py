from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio
from rasterio.windows import Window, from_bounds

from commun import jrc, polygones

FRANCE = (-5.3, 41.3, 9.7, 51.2)

BLOC = 2048


def blocs(fenetre):
    return [
        Window(
            fenetre.col_off + colonne,
            fenetre.row_off + ligne,
            min(BLOC, fenetre.width - colonne),
            min(BLOC, fenetre.height - ligne),
            )
        for ligne in range(0, int(fenetre.height), BLOC)
        for colonne in range(0, int(fenetre.width), BLOC)
        ]


def zones(periode):
    with rasterio.open(jrc(periode)) as source:
        fenetre = from_bounds(*FRANCE, source.transform).round_offsets().round_lengths()

        morceaux = [
            polygones(source.read(1, window=bloc) > 0, source.window_transform(bloc), 4326)
            for bloc in blocs(fenetre)
            ]

    return gpd.GeoDataFrame(geometry=pd.concat(morceaux, ignore_index=True), crs=4326).assign(
        p=periode
        )


Path("data").mkdir(exist_ok=True)

toutes = pd.concat([zones(periode) for periode in (10, 100, 500)], ignore_index=True)

toutes.to_file("data/jrc.geojsonl", driver="GeoJSONSeq", COORDINATE_PRECISION=5)

print(f"{len(toutes)} polygones JRC")

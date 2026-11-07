"""Télécharge les carreaux Filosofi 2021 (200 m, métropole) et n'en garde que la population."""

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import py7zr
import requests

URL = "https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip"

MEMBRE = "carreaux_200m_met.csv"


def telecharger(url, chemin, essais=6):
    chemin = Path(chemin)

    if chemin.exists() and zipfile.is_zipfile(chemin):
        return chemin

    partiel = chemin.with_name(chemin.name + ".part")

    for essai in range(1, essais + 1):
        deja = partiel.stat().st_size if partiel.exists() else 0

        entetes = {"Range": f"bytes={deja}-"} if deja else {}

        try:
            with requests.get(url, headers=entetes, stream=True, timeout=(30, 300)) as reponse:
                if reponse.status_code != 416:
                    reponse.raise_for_status()

                    reprise = deja > 0 and reponse.status_code == 206

                    with open(partiel, "ab" if reprise else "wb") as fichier:
                        for bloc in reponse.iter_content(chunk_size=1 << 20):
                            fichier.write(bloc)

            if zipfile.is_zipfile(partiel):
                partiel.replace(chemin)

                return chemin

            partiel.unlink(missing_ok=True)

            raise OSError("archive incomplète ou corrompue")
        except (requests.RequestException, OSError) as erreur:
            if essai == essais:
                raise

            print(f"téléchargement interrompu ({erreur!r}), nouvel essai")

            time.sleep(10 * 2 ** (essai - 1))


Path("data").mkdir(exist_ok=True)

archive = telecharger(URL, "data/filosofi_2021.zip")

with zipfile.ZipFile(archive) as contenant:
    if MEMBRE in contenant.namelist():
        table = pd.read_csv(contenant.open(MEMBRE), dtype=str)
    else:
        with py7zr.SevenZipFile(io.BytesIO(contenant.read(contenant.namelist()[0]))) as septzip:
            septzip.extract(path="data", targets=[MEMBRE])

        table = pd.read_csv(f"data/{MEMBRE}", dtype=str)

carreaux = (
    table
    .rename(columns=str.lower)
    .rename(columns={"idinspire": "idcar_200m"})
    .assign(
        nord=lambda table: table["idcar_200m"].str.extract(r"N(\d+)E")[0].astype("int32"),
        est=lambda table: table["idcar_200m"].str.extract(r"E(\d+)$")[0].astype("int32"),
        ind=lambda table: table["ind"].astype("float32"),
        )
    [["est", "nord", "ind"]]
    )

carreaux.to_parquet("data/filosofi_2021.parquet", index=False)

print(
    f"écrit data/filosofi_2021.parquet, {len(carreaux)} carreaux, "
    f"{carreaux['ind'].sum():.0f} habitants"
    )

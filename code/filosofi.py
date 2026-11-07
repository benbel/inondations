import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

URL = "https://www.insee.fr/fr/statistiques/fichier/8735162/Filosofi2021_carreaux_200m_csv.zip"


def telecharger(url, chemin):
    for essai in range(6):
        try:
            with requests.get(url, stream=True, timeout=(30, 300)) as reponse:
                reponse.raise_for_status()

                with open(chemin, "wb") as fichier:
                    for bloc in reponse.iter_content(chunk_size=2**20):
                        fichier.write(bloc)

            if zipfile.is_zipfile(chemin):
                return chemin
        except requests.RequestException:
            if essai == 5:
                raise

        time.sleep(30 * (essai + 1))

    raise OSError(f"archive incomplète : {url}")


def lire(archive):
    with zipfile.ZipFile(archive) as contenant:
        return pd.read_csv(contenant.open("carreaux_200m_met.csv"), dtype=str)


Path("data").mkdir(exist_ok=True)

carreaux = (
    lire(telecharger(URL, "data/filosofi_2021.zip"))
    .rename(columns=str.lower)
    .assign(
        nord=lambda table: table["idcar_200m"].str.extract(r"N(\d+)E")[0].astype("int32"),
        est=lambda table: table["idcar_200m"].str.extract(r"E(\d+)$")[0].astype("int32"),
        ind=lambda table: table["ind"].astype("float32"),
        )
    [["est", "nord", "ind"]]
    )

carreaux.to_parquet("data/filosofi_2021.parquet", index=False)

print(f"{len(carreaux)} carreaux, {carreaux['ind'].sum():.0f} habitants")

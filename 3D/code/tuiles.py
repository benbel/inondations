import math

import numpy as np


def numero_tuile(longitude, latitude, zoom):
    colonne = math.floor((longitude + 180) / 360 * 2 ** zoom)

    ligne = math.floor(
        (1 - math.asinh(math.tan(math.radians(latitude))) / math.pi) / 2 * 2 ** zoom
        )

    return colonne, ligne


def bornes_tuile(colonne, ligne, zoom):
    cote = 2 * 20037508.342789244 / 2 ** zoom

    gauche = -20037508.342789244 + colonne * cote

    haut = 20037508.342789244 - ligne * cote

    return gauche, haut - cote, gauche + cote, haut


def terrarium(hauteurs):
    valeurs = hauteurs + 32768

    rouge = np.floor(valeurs / 256)

    vert = np.floor(valeurs) % 256

    bleu = np.floor((valeurs - np.floor(valeurs)) * 256)

    return np.dstack([rouge, vert, bleu]).astype("uint8")

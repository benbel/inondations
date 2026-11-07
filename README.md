# Inondations

Carte des bâtiments en zone inondable en France métropolitaine, avec leurs logements,
leurs habitants (Filosofi 2021) et une valeur estimée (DVF 2021-2025), pour trois sources
d'aléa :

| Source | Scénarios | Couverture | Affichage |
|---|---|---|---|
| TRI (Géorisques) | fréquent, moyen, moyen avec changement climatique, extrême | territoires à risque important seulement | WMS Géorisques |
| JRC | périodes de retour 10, 100 et 500 ans | grands cours d'eau, 100 m | tuiles produites ici |
| EAIP (Géorisques) | enveloppe maximale | tout le territoire | WMS Géorisques |

Le fond (Plan IGN, estompage) et les zones TRI et EAIP viennent de services de tuiles ;
le site ne contient que les bâtiments exposés (zoom 14), les zones JRC et une synthèse par
commune.

## Chaîne de traitement

```bash
python code/filosofi.py          # carreaux Filosofi 2021 → data/filosofi_2021.parquet
python code/jrc.py               # zones JRC vectorisées → data/jrc.geojsonl
python code/departement.py 37    # un département → data/departements/37
python code/assembler.py         # tuiles et synthèse → site/donnees
```

Le workflow `page` lance les 96 départements en parallèle puis assemble et publie le site.
Il échoue si le site dépasse 1 Go, la limite de GitHub Pages.

## Méthode et limites

- Bâtiments : BD TOPO (WFS IGN), en service.
- Un bâtiment est exposé s'il touche la zone. Pour le JRC (pixels de 100 m) et l'EAIP
  (lue sur le WMS à 26 m, seule diffusion nationale), on teste son rectangle englobant
  contre le raster, ce qui surestime un peu l'exposition.
- Habitants : population des carreaux de 200 m répartie sur les bâtiments résidentiels au
  prorata des logements (ou de la surface de plancher).
- Valeur : surface habitable estimée × prix médian au m² de la commune (au moins 30 ventes),
  sinon du département. DVF ne couvre pas la Moselle, le Bas-Rhin et le Haut-Rhin : la
  valeur y est nulle.

La première version, en 3D sur seize territoires, est archivée dans `3D/`.

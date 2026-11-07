# Version 3D (archivée)

Première version du site : seize territoires, bâtiments extrudés, relief LiDAR HD
et occupation du sol CoSIA recolorée, tuiles produites par le projet.

Elle n'est plus publiée. Pour la reconstruire, depuis ce dossier :

```bash
cd 3D
python code/carte.py saint_omer
python code/population.py saint_omer
python code/dvf.py saint_omer
python code/assembler.py saint_omer
python code/mnt.py saint_omer
python code/relief.py saint_omer
python code/cosia.py saint_omer
cd site && python -m RangeHTTPServer
```

`page.yml` est l'ancien workflow GitHub Actions, désactivé (il n'est pas dans
`.github/workflows`).

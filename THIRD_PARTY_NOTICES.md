# Hinweise zu Drittinhalten

Inhalte Dritter, die direkt im Repository enthalten sind (nicht über Paketmanager
bezogene Abhängigkeiten – deren Lizenzen stehen in den jeweiligen Paketen).

## RAL-Classic-Farbtabelle

- Datei: `services/blv/src/lumira_blv/logic/ral_table.py` (aus CSV erzeugt)
- Quelle: <https://gist.github.com/lunohodov/1995178> (`ral_classic.csv`)
- Lizenz: MIT (vom Autor für den Gist angegeben)

```
MIT License

Copyright (c) lunohodov (https://gist.github.com/lunohodov)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

„RAL" ist eine Marke der RAL gGmbH. Die Hex-Werte sind sRGB-Näherungen; farbverbindlich
sind ausschließlich die offiziellen RAL-Farbkarten.

## Bodentexturen (nicht im Repository, per `make textures` geladen)

- Dateien: `assets/textures/<id>/` – Liste und Prüfsummen in `scripts/fetch-textures.sh` und
  `scripts/textures.sha256`
- Quelle: Poly Haven (<https://polyhaven.com>): `oak_wood_planks`, `laminate_floor_02`,
  `laminate_floor_03`, `herringbone_parquet`, `plank_flooring_04`
- Lizenz: CC0 1.0 (gemeinfrei) – Nutzung, Veränderung und Weitergabe auch kommerziell ohne
  Namensnennung erlaubt. Die Nennung hier dient nur der Nachvollziehbarkeit.

Fliesen-, Putz- und Teppichstrukturen erzeugt das Blender-Skript selbst (keine Drittinhalte).

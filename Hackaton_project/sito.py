"""
Configurazione del sito (capannone di PAD2.step), condivisa da centrale.py e giro_punti.py.
Coordinate in metri della pianta: X verso destra, Y verso l'alto; direzioni in gradi, 0 = +X, 90 = +Y.
Si leggono sulla finestra «Mappa» (griglia di 1 m) o con:  python mappa.py PAD2.step  (salva pianta.png).

Zone: zones.json del team (repo BMO-Grokbot, 5 novembre), stesso sistema di coordinate di PAD2.step; unica modifica:
la zona centrale «PAD» si chiama CENTER (il pad vero è nella zona HUB). Ogni zona ha un punto principale («hover»):
è lì che va il drone quando la telecamera della zona vede un'anomalia.
Il team fa partire il suo simulatore da HUB.origin (-1,63; 3,61): qui la partenza resta il pad confermato qui sotto.

Misure del 5 novembre, ricavate dalle zone e dalla pianta:
- telefono a 1 m da terra in (-0,25; 3,47), confermato da Andrea;
- telefono e muso del drone guardano dritto verso ovest (180 gradi), lungo la fascia tra y 3 e y 4;
- pad nella stessa direzione, 0,5 m davanti al telefono;
- ingresso = apertura B della parete nord, larga 1,3 m (x -9,68..-8,48), nel campo visivo del telefono
  (l'apertura A, larga 3,6 m a x 3,22..6,72, resta alle spalle del telefono).
Tutto confermato da Andrea il 5 novembre.
Se la posizione reale è diversa, correggere qui e lasciare le *verificata* a False finché non è misurata.
"""
import json
import os

FILE_STEP = "PAD2.step"
FILE_ZONE = "zones.json"
MARGINE_OSTACOLI_M = 0.5            # distanza minima dalle pareti e dai pilastri della pianta
CHIUDI_APERTURE_PERIMETRO = True    # porte e finestre del perimetro diventano pareti: il drone non esce
SPESSORE_PERIMETRO_M = 0.5

# Ostacoli che non sono in PAD2.step ma sono nel simulatore del team (tello_sim/world.py, _add_pad_mezz_props),
# ricavati dalle sue zone: per prudenza il drone li evita. Da verificare sul posto. (x_min, x_max, y_min, y_max) in m
OSTACOLI_EXTRA = {
    "parete tra CENTER e UNDER_MEZZ": (-1.68, -1.53, -2.91, 1.24),
    "pilastro 1 x 1 m nell'angolo sud-est di ZONE_7": (-3.85, -2.85, 1.24, 2.24),
}

# Vetrate non presenti in PAD2.step: il drone non ci passa, la telecamera ci vede attraverso. (x, x, y, y) in m
# Quella a ovest l'ha segnalata Andrea il 5 novembre («c'è di mezzo un muro di vetro» tra ZONE_7 e la zona della
# porta B); la posizione è stimata [ipotesi]: sotto il bordo del soppalco a 2,70 m (x -6,39), dal pilastro P1 alla
# parete nord, dove finisce anche la ZONE_7 del team. Da verificare sul posto.
VETRATE = {
    "vetrata ovest (stanza della porta B, sotto il soppalco a 2,70 m)": (-6.45, -6.33, 1.20, 5.90),
}

# Ingresso della stanza: apertura B della parete nord
INGRESSO = {"x_min": -9.68, "x_max": -8.48, "y": 5.8}

# Mission pad del drone
BASE_X_M, BASE_Y_M = -0.75, 3.47    # 0,5 m davanti al telefono, nella direzione in cui guarda
BASE_DIREZIONE_GRADI = 180          # muso dritto verso ovest, come il telefono
BASE_VERIFICATA = True


def zone(cartella=os.path.dirname(os.path.abspath(__file__))):
    percorso = os.path.join(cartella, FILE_ZONE)
    if not os.path.isfile(percorso):
        return []
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)["zones"]


def prepara_mappa(mappa):
    """Chiude le aperture del perimetro, aggiunge gli ostacoli extra e mette zone e ingresso nella descrizione."""
    if CHIUDI_APERTURE_PERIMETRO:
        mappa.chiudi_perimetro(SPESSORE_PERIMETRO_M)
    for x_min, x_max, y_min, y_max in OSTACOLI_EXTRA.values():
        mappa.aggiungi_rettangolo(x_min, x_max, y_min, y_max)
    for x_min, x_max, y_min, y_max in VETRATE.values():
        mappa.aggiungi_rettangolo(x_min, x_max, y_min, y_max, trasparente=True)
    righe = [f"Ingresso della stanza: apertura nella parete nord tra x {INGRESSO['x_min']:.2f} e "
             f"{INGRESSO['x_max']:.2f} m (y circa {INGRESSO['y']:.1f} m)."]
    for z in zone():
        righe.append(f"Zona {z['id']}: X {z['xmin']:.2f}..{z['xmax']:.2f}, Y {z['ymin']:.2f}..{z['ymax']:.2f}, "
                     f"quota {z['zmin']:.2f}..{z['zmax']:.2f} m")
    mappa.note = "\n".join(righe)
    return mappa

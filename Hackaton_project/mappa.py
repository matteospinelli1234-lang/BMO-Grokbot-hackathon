"""
Pianta del capannone dal file STEP: ostacoli alla quota di volo, percorso senza ostacoli (A*) e disegno.

Il file STEP (PAD2.step, esportato da Fusion) contiene solidi con sole facce piane, in millimetri, con Z verticale.
Ogni solido viene tagliato con piani orizzontali alle quote di volo: una cella della griglia è occupata
se sta dentro un solido ad almeno una delle quote. Mobili, scaffali e persone NON sono nel file.

Coordinate della mappa: metri, assi X e Y del file STEP (origine al centro del capannone).
Direzioni della mappa: gradi, 0 = verso +X, positivi in senso antiorario (verso +Y), vista dall'alto.

Prova da sola:  python mappa.py PAD2.step   (salva pianta.png)
"""
import heapq
import math
import re
import sys

import cv2
import numpy as np

RISOLUZIONE_M = 0.10                          # lato di una cella della griglia
QUOTE_TAGLIO_M = (0.15, 0.5, 0.9, 1.3, 1.7)   # da terra fino a sopra la quota di volo (soppalchi da 2,15 m)
SPOSTAMENTO_QUOTA_M = 0.0007                  # evita che il piano di taglio passi esattamente per un vertice
QUOTA_SOPRA_VOLO_M = 2.0                      # solidi che iniziano oltre questa quota non sono ostacoli
DISTANZA_COMODA_M = 1.0                       # A* preferisce stare almeno a questa distanza dagli ostacoli
PESO_VICINANZA = 2.0


# ---------------- Lettura dello STEP ----------------
def _leggi_entita(percorso):
    testo = open(percorso, encoding="utf-8", errors="ignore").read()
    if "SI_UNIT(.MILLI.,.METRE.)" in testo:
        scala = 0.001
    elif "SI_UNIT($,.METRE.)" in testo:
        scala = 1.0
    else:
        raise ValueError("Unità di lunghezza del file STEP non riconosciuta")
    dati = testo.split("DATA;", 1)[1]
    entita = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([A-Z0-9_]*)\s*\((.*?)\);\s*(?=#\d+\s*=|ENDSEC)", dati, re.S):
        entita[int(m.group(1))] = (m.group(2), m.group(3).replace("\n", ""))
    return entita, scala


def _riferimenti(argomenti):
    return [int(x) for x in re.findall(r"#(\d+)", argomenti)]


def _prima_lista(argomenti):
    """Riferimenti contenuti nella prima lista tra parentesi, es. CLOSED_SHELL('',(#1,#2)) -> [1, 2]."""
    return _riferimenti(re.search(r"\(([^()]*)\)", argomenti).group(1))


def carica_solidi(percorso):
    """Restituisce [{'nome', 'facce': [array (n spigoli, 2, 3) in metri], 'min', 'max'}]."""
    ent, scala = _leggi_entita(percorso)

    def punto(id_vertice):
        id_punto = _riferimenti(ent[id_vertice][1])[0]
        numeri = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", ent[id_punto][1].split("(", 1)[1])
        return [float(n) * scala for n in numeri[:3]]

    solidi = []
    for tipo, argomenti in ent.values():
        if tipo != "MANIFOLD_SOLID_BREP":
            continue
        nome = re.search(r"'([^']*)'", argomenti).group(1)
        facce = []
        for id_faccia in _prima_lista(ent[_riferimenti(argomenti)[0]][1]):
            spigoli = []
            for id_bordo in _prima_lista(ent[id_faccia][1]):
                id_anello = _riferimenti(ent[id_bordo][1])[0]
                for id_orientato in _prima_lista(ent[id_anello][1]):
                    id_spigolo = _riferimenti(ent[id_orientato][1])[0]
                    v1, v2 = _riferimenti(ent[id_spigolo][1])[:2]
                    spigoli.append((punto(v1), punto(v2)))
            facce.append(np.array(spigoli))
        tutti = np.concatenate([f.reshape(-1, 3) for f in facce])
        solidi.append({"nome": nome, "facce": facce, "min": tutti.min(axis=0), "max": tutti.max(axis=0)})
    solidi.sort(key=lambda s: int(re.sub(r"\D", "", s["nome"]) or 0))
    return solidi


def _segmenti_sezione(solido, h):
    """Segmenti 2D del contorno della sezione del solido con il piano z = h."""
    segmenti = []
    for spigoli in solido["facce"]:
        if np.ptp(spigoli[:, :, 2]) < 1e-9:                  # faccia orizzontale: non taglia
            continue
        a, b = spigoli[:, 0], spigoli[:, 1]
        za, zb = a[:, 2] - h, b[:, 2] - h
        taglia = za * zb < 0
        if not taglia.any():
            continue
        t = za[taglia] / (za[taglia] - zb[taglia])
        p = a[taglia, :2] + t[:, None] * (b[taglia, :2] - a[taglia, :2])
        if len(p) % 2:                                       # caso degenere: si salta la faccia
            continue
        direzione = p[np.argmax(np.linalg.norm(p - p[0], axis=1))] - p[0]
        p = p[np.argsort((p - p[0]) @ direzione)]
        segmenti.extend(zip(p[0::2], p[1::2]))               # tratti interni alla faccia (pari/dispari)
    return segmenti


def _riempi(segmenti, griglia, x0, y0, ris):
    """Riempie le celle dentro il contorno chiuso (regola pari/dispari, scansione per righe)."""
    if not segmenti:
        return
    s = np.array(segmenti)
    ymin, ymax = np.minimum(s[:, 0, 1], s[:, 1, 1]), np.maximum(s[:, 0, 1], s[:, 1, 1])
    righe, colonne = griglia.shape
    for r in range(righe):
        yc = y0 + (r + 0.5) * ris
        scelti = s[(ymin <= yc) & (yc < ymax)]
        if not len(scelti):
            continue
        t = (yc - scelti[:, 0, 1]) / (scelti[:, 1, 1] - scelti[:, 0, 1])
        xs = np.sort(scelti[:, 0, 0] + t * (scelti[:, 1, 0] - scelti[:, 0, 0]))
        for xa, xb in zip(xs[0::2], xs[1::2]):
            ca = max(0, math.ceil((xa - x0) / ris - 0.5))
            cb = min(colonne - 1, math.floor((xb - x0) / ris - 0.5))
            if cb >= ca:
                griglia[r, ca:cb + 1] = True


def _classifica(solidi):
    """Elementi con nome, per la descrizione testuale della pianta e per il disegno."""
    edificio = max(solidi, key=lambda s: np.prod(s["max"][:2] - s["min"][:2]))
    elementi, pilastri = [], []
    for s in solidi:
        dx, dy = s["max"][:2] - s["min"][:2]
        zmin, zmax = s["min"][2], s["max"][2]
        if s is edificio:
            tipo = "edificio"
        elif zmin >= QUOTA_SOPRA_VOLO_M:
            tipo = "copertura" if zmin >= 4.0 else "soppalco"
        elif zmax >= QUOTA_SOPRA_VOLO_M and dx * dy < 4:
            tipo = "pilastro"
        else:
            tipo = "blocco"
        e = {"tipo": tipo, "solido": s["nome"], "min": s["min"].round(2).tolist(), "max": s["max"].round(2).tolist()}
        (pilastri if tipo == "pilastro" else elementi).append(e)
    for i, p in enumerate(sorted(pilastri, key=lambda e: e["min"][0]), 1):
        p["nome"] = f"P{i}"
        elementi.append(p)
    for i, b in enumerate([e for e in elementi if e["tipo"] == "blocco"], 1):
        b["nome"] = f"B{i}"
    for i, b in enumerate([e for e in elementi if e["tipo"] == "soppalco"], 1):
        b["nome"] = f"S{i}"
    return [e for e in elementi if e["tipo"] != "copertura"]


# ---------------- Mappa ----------------
class Mappa:
    def __init__(self, occupata, x0, y0, ris, elementi, margine_m):
        self.occupata = occupata                           # righe = Y (dal basso), colonne = X
        self.x0, self.y0, self.ris = x0, y0, ris
        self.elementi = elementi
        self.margine_m = margine_m
        self.note = ""                                     # testo extra per Grok (zone, ingresso)
        self.trasparente = np.zeros_like(occupata)         # vetrate: ostacoli per il volo, non per la vista
        self.aggiorna()

    @classmethod
    def da_step(cls, percorso, margine_m, quote=QUOTE_TAGLIO_M, ris=RISOLUZIONE_M):
        solidi = carica_solidi(percorso)
        edificio = max(solidi, key=lambda s: np.prod(s["max"][:2] - s["min"][:2]))
        x0, y0 = edificio["min"][:2]
        colonne = math.ceil((edificio["max"][0] - x0) / ris)
        righe = math.ceil((edificio["max"][1] - y0) / ris)
        occupata = np.zeros((righe, colonne), bool)
        for s in solidi:
            for h in quote:
                h += SPOSTAMENTO_QUOTA_M
                if s["min"][2] < h < s["max"][2]:
                    sezione = np.zeros_like(occupata)
                    _riempi(_segmenti_sezione(s, h), sezione, x0, y0, ris)
                    occupata |= sezione
        occupata[[0, -1], :] = True                          # bordo chiuso: il drone non esce dalla griglia
        occupata[:, [0, -1]] = True
        return cls(occupata, float(x0), float(y0), ris, _classifica(solidi), margine_m)

    def aggiorna(self):
        """Ricalcola distanze dagli ostacoli e zona sicura (dopo l'aggiunta di un ostacolo)."""
        libere = (~self.occupata).astype(np.uint8)
        self.distanza = cv2.distanceTransform(libere, cv2.DIST_L2, 5) * self.ris
        self.sicura = self.distanza >= self.margine_m

    # --- conversioni ---
    def cella(self, x, y):
        return int((y - self.y0) // self.ris), int((x - self.x0) // self.ris)

    def centro(self, r, c):
        return self.x0 + (c + 0.5) * self.ris, self.y0 + (r + 0.5) * self.ris

    def dentro(self, r, c):
        return 0 <= r < self.occupata.shape[0] and 0 <= c < self.occupata.shape[1]

    def e_sicuro(self, x, y):
        r, c = self.cella(x, y)
        return self.dentro(r, c) and bool(self.sicura[r, c])

    def distanza_ostacoli(self, x, y):
        r, c = self.cella(x, y)
        return float(self.distanza[r, c]) if self.dentro(r, c) else 0.0

    def linea_sicura(self, p, q):
        n = max(2, int(math.dist(p, q) / (self.ris / 2)) + 1)
        return all(self.e_sicuro(p[0] + (q[0] - p[0]) * i / (n - 1), p[1] + (q[1] - p[1]) * i / (n - 1))
                   for i in range(n))

    def linea_con_margine(self, p, q, margine_m):
        """Come linea_sicura ma con un margine diverso da quello della mappa (es. ultimo tratto verso la persona)."""
        n = max(2, int(math.dist(p, q) / (self.ris / 2)) + 1)
        for i in range(n):
            r, c = self.cella(p[0] + (q[0] - p[0]) * i / (n - 1), p[1] + (q[1] - p[1]) * i / (n - 1))
            if not self.dentro(r, c) or self.distanza[r, c] < margine_m:
                return False
        return True

    def linea_libera(self, p, q):
        """Come linea_sicura ma senza margine: serve per sapere se da p si vede q (attraverso le vetrate sì)."""
        n = max(2, int(math.dist(p, q) / (self.ris / 2)) + 1)
        for i in range(n):
            r, c = self.cella(p[0] + (q[0] - p[0]) * i / (n - 1), p[1] + (q[1] - p[1]) * i / (n - 1))
            if not self.dentro(r, c) or (self.occupata[r, c] and not self.trasparente[r, c]):
                return False
        return True

    def raggiungibili(self, x, y):
        """Celle sicure collegate a (x, y)."""
        r, c = self.cella(x, y)
        maschera = np.zeros((self.sicura.shape[0] + 2, self.sicura.shape[1] + 2), np.uint8)
        immagine = self.sicura.astype(np.uint8)
        if self.dentro(r, c) and not immagine[r, c]:         # partenza nel margine: si parte dal punto sicuro vicino
            uscita = self._uscita(x, y)
            if uscita:
                r, c = self.cella(*uscita)
        if not self.dentro(r, c) or not immagine[r, c]:
            return np.zeros_like(self.sicura)
        cv2.floodFill(immagine, maschera, (c, r), 2, flags=8)
        return immagine == 2

    def punto_sicuro_vicino(self, x, y, da=None):
        """Punto sicuro più vicino a (x, y); se 'da' è dato, solo tra quelli raggiungibili da lì."""
        candidati = self.raggiungibili(*da) if da else self.sicura
        if not candidati.any():
            return None
        righe, colonne = np.nonzero(candidati)
        cx, cy = self.centro(righe, colonne)
        i = int(np.argmin((cx - x) ** 2 + (cy - y) ** 2))
        return float(cx[i]), float(cy[i])

    def _uscita(self, x, y):
        """Punto sicuro più vicino raggiungibile in linea retta senza attraversare ostacoli."""
        righe, colonne = np.nonzero(self.sicura)
        cx, cy = self.centro(righe, colonne)
        for i in np.argsort((cx - x) ** 2 + (cy - y) ** 2)[:200]:
            if self.linea_libera((x, y), (cx[i], cy[i])):
                return float(cx[i]), float(cy[i])
        return None

    def chiudi_perimetro(self, spessore_m=0.5):
        """Porte e finestre del perimetro diventano pareti: il drone non può uscire dall'edificio."""
        k = max(1, round(spessore_m / self.ris))
        self.occupata[:k, :] = True
        self.occupata[-k:, :] = True
        self.occupata[:, :k] = True
        self.occupata[:, -k:] = True
        self.aggiorna()

    def aggiungi_ostacolo(self, x, y, raggio_m=0.3):
        """Segna un ostacolo non presente nella pianta (visto dal sensore ToF)."""
        r, c = self.cella(x, y)
        cv2.circle(self.occupata.view(np.uint8), (c, r), max(1, round(raggio_m / self.ris)), 1, -1)
        self.aggiorna()

    def aggiungi_rettangolo(self, x_min, x_max, y_min, y_max, trasparente=False):
        """Segna un ostacolo rettangolare non presente nella pianta (es. pareti e pilastri del simulatore del team).
        trasparente: vetrata, il drone non ci passa ma la telecamera ci vede attraverso."""
        r0, c0 = self.cella(x_min, y_min)
        r1, c1 = self.cella(x_max, y_max)
        zona = (slice(max(0, r0), max(0, r1 + 1)), slice(max(0, c0), max(0, c1 + 1)))
        self.occupata[zona] = True
        if trasparente:
            self.trasparente[zona] = True
        self.aggiorna()

    # --- percorso ---
    def percorso(self, da, a):
        """Waypoint [(x, y), ...] da 'da' ad 'a' nella zona sicura, oppure None se non esiste."""
        partenza, arrivo = self.cella(*da), self.cella(*a)
        if self.dentro(*partenza) and not self.sicura[partenza]:
            uscita = self._uscita(*da)                       # es. dopo un ostacolo visto dal ToF
            resto = uscita and self.percorso(uscita, a)
            return [tuple(da)] + resto if resto else None
        for r, c in (partenza, arrivo):
            if not self.dentro(r, c) or not self.sicura[r, c]:
                return None
        vicini = [(-1, 0, 1), (1, 0, 1), (0, -1, 1), (0, 1, 1),
                  (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2))]
        costi, da_dove = {partenza: 0.0}, {}
        coda = [(0.0, partenza)]
        while coda:
            _, attuale = heapq.heappop(coda)
            if attuale == arrivo:
                break
            for dr, dc, passo in vicini:
                r, c = attuale[0] + dr, attuale[1] + dc
                if not self.dentro(r, c) or not self.sicura[r, c]:
                    continue
                vicinanza = max(0.0, (DISTANZA_COMODA_M - self.distanza[r, c]) / DISTANZA_COMODA_M)
                nuovo = costi[attuale] + passo * (1 + PESO_VICINANZA * vicinanza)
                if nuovo < costi.get((r, c), math.inf):
                    costi[(r, c)] = nuovo
                    da_dove[(r, c)] = attuale
                    stima = math.hypot(arrivo[0] - r, arrivo[1] - c)
                    heapq.heappush(coda, (nuovo + stima, (r, c)))
        if arrivo not in costi:
            return None
        celle = [arrivo]
        while celle[-1] != partenza:
            celle.append(da_dove[celle[-1]])
        punti = [tuple(da)] + [self.centro(r, c) for r, c in reversed(celle[1:-1])] + [tuple(a)]
        return self._semplifica(punti)

    def _semplifica(self, punti):
        """Toglie i punti intermedi finché il tratto diritto resta lontano dagli ostacoli quanto il percorso A* che
        sostituisce (fino a DISTANZA_COMODA_M). Prima bastava la zona sicura (0,5 m) e le scorciatoie tagliavano gli
        angoli dei pilastri: il 5 novembre il tratto UNDER_MEZZ -> CENTER passava a 0,52 m da un pilastro."""
        distanze = [self.distanza_ostacoli(*p) for p in punti]
        risultato, i = [punti[0]], 0
        while i < len(punti) - 1:
            j = len(punti) - 1
            while j > i + 1:
                richiesta = min(DISTANZA_COMODA_M, min(distanze[i:j + 1])) - self.ris / 2
                if self.linea_con_margine(punti[i], punti[j], max(self.margine_m, richiesta)):
                    break
                j -= 1
            risultato.append(punti[j])
            i = j
        return risultato

    # --- descrizione e disegno ---
    def descrizione(self):
        """Testo della pianta per Grok (numeri con il punto decimale, come nel JSON richiesto)."""
        righe = [f"Capannone: X da {self.x0:.2f} a {self.x0 + self.occupata.shape[1] * self.ris:.2f} m, "
                 f"Y da {self.y0:.2f} a {self.y0 + self.occupata.shape[0] * self.ris:.2f} m. "
                 "Asse X verso destra, asse Y verso l'alto nella pianta. Altezza utile 4.35 m."]
        for e in self.elementi:
            (x1, y1, z1), (x2, y2, z2) = e["min"], e["max"]
            if e["tipo"] == "edificio":
                continue
            righe.append(f"- {e['nome']} ({e['tipo']}): X {x1:.2f}..{x2:.2f}, Y {y1:.2f}..{y2:.2f}, "
                         f"quota {z1:.2f}..{z2:.2f} m")
        if self.note:
            righe.append(self.note)
        return "\n".join(righe)

    def disegna(self, scala=4, percorso=None, drone=None, anomalia=None, raggio_anomalia=None,
                punti=None, titolo=None):
        """Immagine BGR della pianta con griglia a 1 m, elementi, percorso, drone e anomalia."""
        bordo = 40
        righe, colonne = self.occupata.shape
        img = np.full((righe * scala + 2 * bordo, colonne * scala + 2 * bordo, 3), 255, np.uint8)

        def px(x, y):
            return (int(bordo + (x - self.x0) / self.ris * scala),
                    int(bordo + (self.y0 + righe * self.ris - y) / self.ris * scala))

        colori = np.full((righe, colonne, 3), 255, np.uint8)
        colori[~self.sicura] = (225, 225, 225)
        colori[self.occupata] = (60, 60, 60)
        colori[self.occupata & self.trasparente] = (230, 160, 60)   # vetrate in azzurro
        img[bordo:bordo + righe * scala, bordo:bordo + colonne * scala] = cv2.resize(
            colori[::-1], (colonne * scala, righe * scala), interpolation=cv2.INTER_NEAREST)
        x1, y1 = self.x0, self.y0
        x2, y2 = self.x0 + colonne * self.ris, self.y0 + righe * self.ris
        for x in range(math.ceil(x1), math.floor(x2) + 1):
            cv2.line(img, px(x, y1), px(x, y2), (200, 200, 255) if x else (120, 120, 255), 1)
            if x % 2 == 0:
                cv2.putText(img, str(x), (px(x, y1)[0] - 6, px(x, y1)[1] + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 200), 1)
        for y in range(math.ceil(y1), math.floor(y2) + 1):
            cv2.line(img, px(x1, y), px(x2, y), (200, 200, 255) if y else (120, 120, 255), 1)
            cv2.putText(img, str(y), (4, px(x1, y)[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 200), 1)
        for e in self.elementi:
            if e["tipo"] in ("pilastro", "blocco", "soppalco"):
                a, b = px(e["min"][0], e["max"][1]), px(e["max"][0], e["min"][1])
                if e["tipo"] == "soppalco":
                    cv2.rectangle(img, a, b, (180, 130, 0), 1)
                    etichetta = f"{e['nome']} soppalco {e['min'][2]:.2f} m".replace(".", ",")
                else:
                    etichetta = e["nome"]
                cv2.putText(img, etichetta, (a[0] + 3, a[1] + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 130, 0), 1)
        if percorso:
            for p, q in zip(percorso, percorso[1:]):
                cv2.line(img, px(*p), px(*q), (255, 120, 0), 2)
            for p in percorso:
                cv2.circle(img, px(*p), 3, (255, 120, 0), -1)
        for nome, (x, y) in (punti or {}).items():
            cv2.circle(img, px(x, y), 5, (0, 160, 0), -1)
            cv2.putText(img, nome, (px(x, y)[0] + 6, px(x, y)[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 120, 0), 1)
        if anomalia:
            if raggio_anomalia:
                cv2.circle(img, px(*anomalia), int(raggio_anomalia / self.ris * scala), (0, 0, 255), 1)
            cv2.drawMarker(img, px(*anomalia), (0, 0, 255), cv2.MARKER_TILTED_CROSS, 14, 2)
        if drone:
            x, y, direzione = drone
            punta = px(x + 0.45 * math.cos(math.radians(direzione)), y + 0.45 * math.sin(math.radians(direzione)))
            cv2.circle(img, px(x, y), 6, (0, 0, 200), -1)
            cv2.line(img, px(x, y), punta, (0, 0, 200), 2)
        if titolo:
            cv2.putText(img, titolo, (bordo, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        return img


def scegli_punto_vista(mappa, bersaglio, distanza, da, angolo_preferito=None):
    """Punto sicuro a 'distanza' dal bersaglio, raggiungibile da 'da', da cui il bersaglio si vede.
    Prova prima la direzione preferita (di solito quella da cui arriva il drone), poi ruota attorno."""
    if angolo_preferito is None:
        angolo_preferito = math.degrees(math.atan2(da[1] - bersaglio[1], da[0] - bersaglio[0]))
    raggiungibili = mappa.raggiungibili(*da)
    for extra in (0.0, 0.3, 0.6, 1.0):
        for scarto in (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180):
            a = math.radians(angolo_preferito + scarto)
            p = (bersaglio[0] + (distanza + extra) * math.cos(a), bersaglio[1] + (distanza + extra) * math.sin(a))
            r, c = mappa.cella(*p)
            if mappa.dentro(r, c) and raggiungibili[r, c] and mappa.linea_libera(p, bersaglio):
                return p, math.degrees(math.atan2(bersaglio[1] - p[1], bersaglio[0] - p[0]))
    return None, None


if __name__ == "__main__":
    mappa = Mappa.da_step(sys.argv[1] if len(sys.argv) > 1 else "PAD2.step", margine_m=0.5)
    print(mappa.descrizione())
    print(f"Celle: {mappa.occupata.shape}, occupate {mappa.occupata.mean():.0%}, sicure {mappa.sicura.mean():.0%}")
    cv2.imwrite("pianta.png", mappa.disegna(titolo="PAD2.step - ostacoli tra 0,15 e 1,7 m"))
    print("Salvata pianta.png")

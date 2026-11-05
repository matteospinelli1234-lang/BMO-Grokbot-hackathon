"""
Giro di prova a 1,70 m di quota su tutta la pianta: il drone va dal pad a una serie di punti, uno dopo l'altro, fa una
foto a ogni tappa e torna al pad. Serve a provare sul drone vero percorsi e ritorno, senza telecamere e Grok.
    python giro_punti.py                            simulazione: tutta la pianta (vedi sotto), ordine più corto
    python giro_punti.py HUB ZONE_7 UNDER_MEZZ      zone scelte, in quest'ordine (si va al loro punto principale)
    python giro_punti.py -2.5 3.0 -4.0 4.0          punti in metri, a coppie x y (si possono mescolare con le zone)
    python giro_punti.py tutti --tasti              simulazione con i tasti veri (per provarli)
    python giro_punti.py tutti --arm                volo vero: controlli a terra, conferma, poi decollo
«tutti» (o nessun argomento) = i punti principali di tutte le zone di zones.json più punti di copertura, finché ogni
parte raggiungibile della pianta è a meno di RAGGIO_COPERTURA_M da una tappa. Ogni tappa sta ad almeno
DISTANZA_DAI_MURI_M da pareti, pilastri, soppalchi e vetrate (spostata nel punto più vicino che lo rispetta).
Opzioni: --senza-foto (video spento, il drone scalda meno); --senza-tof (sensore frontale ignorato: solo se è guasto,
protegge solo la pianta). Prima del volo salva giro_punti.png con il percorso.

Il percorso lo segue da solo. Tasti (Windows, letti anche se la finestra non è in primo piano), con --arm e --tasti:
    SPAZIO      kill switch: rc 0 e land subito, anche a metà comando
    BACKSPACE   emergenza: motori spenti, il drone cade
    Ctrl+C      come SPAZIO
A 1,70 m il drone è all'altezza della testa: nessuno nella sala lungo il percorso. Il programma stampa da dove il drone
arriva al pad alla fine: chi pilota sta dall'altra parte, ad almeno 2 m.
"""
import ctypes
import itertools
import math
import os
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

import ispezione as isp
import mappa as modulo_mappa
import sito
from mappa import Mappa

GIRO_PREDEFINITO = ["TUTTI"]
IMMAGINE = "giro_punti.png"
QUOTA_GIRO_CM = 170
RAGGIO_COPERTURA_M = 3.0
DISTANZA_DAI_MURI_M = 1.2       # tappe lontane da pareti, pilastri, soppalchi e vetrate (il volo ne resta a 0,5 m)
# la pianta si taglia ogni 10 cm fino a 0,5 m sopra la quota di volo (2,2 m): i soppalchi a 2,15 m diventano ostacoli
# (niente voli sotto di loro), quello a 2,70 m no (sopra la porta B restano 1 m sopra il drone)
_CIMA_M = QUOTA_GIRO_CM / 100 + 0.5
QUOTE_TAGLIO_M = tuple(sorted(set(modulo_mappa.QUOTE_TAGLIO_M)
                              | {round(1.8 + 0.1 * k, 2) for k in range(round((_CIMA_M - 1.8) / 0.1) + 1)}))
TASTO_SPAZIO, TASTO_BACKSPACE = 0x20, 0x08

POSIZIONE_DI_PARTENZA = (
    f"PARTENZA: drone sul pad in ({sito.BASE_X_M:.2f}; {sito.BASE_Y_M:.2f}) m".replace(".", ",")
    + f" con il MUSO VERSO OVEST ({sito.BASE_DIREZIONE_GRADI} gradi): guarda lungo la fascia nord verso ZONE_7 e la\n"
    "          porta B, la parete ovest è 10,7 m davanti. Corpo PARALLELO alla parete nord, che resta alla destra del\n"
    "          drone: muso e coda tutti e due a 2,20 m da quella parete. Telefono 0,5 m dietro la coda, rivolto nella\n"
    "          stessa direzione. Ogni grado di storto diventa circa 17 cm di errore ogni 10 m di volo.")


def premuto(codice):
    """Stato vero del tasto (giù / su) da Windows, anche se la finestra non è in primo piano."""
    return bool(ctypes.windll.user32.GetAsyncKeyState(codice) & 0x8000)


def prepara_pianta():
    """Pianta del giro: tagliata fino a 0,5 m sopra la quota di volo, con ostacoli extra e area dell'operatore."""
    return sito.prepara_mappa(Mappa.da_step(sito.FILE_STEP, sito.MARGINE_OSTACOLI_M, quote=QUOTE_TAGLIO_M))


def comode(mappa, base):
    """Celle raggiungibili dal pad ad almeno DISTANZA_DAI_MURI_M da ogni ostacolo: lì si mettono le tappe."""
    return mappa.raggiungibili(*base[:2]) & (mappa.distanza >= DISTANZA_DAI_MURI_M)


def punti_di_copertura(mappa, base, tappe):
    """Punti in più finché ogni cella raggiungibile (griglia di 0,5 m) è a meno di RAGGIO_COPERTURA_M da una tappa
    o dal pad: ogni volta, tra le celle comode, quella che copre più celle ancora scoperte."""
    passo = max(1, round(0.5 / mappa.ris))
    righe, colonne = np.nonzero(mappa.raggiungibili(*base[:2])[::passo, ::passo])
    celle = np.column_stack(mappa.centro(righe * passo, colonne * passo))
    righe, colonne = np.nonzero(comode(mappa, base)[::passo, ::passo])
    candidati = np.column_stack(mappa.centro(righe * passo, colonne * passo))
    vicine = np.linalg.norm(candidati[:, None, :] - celle[None, :, :], axis=2) <= RAGGIO_COPERTURA_M
    distanza = np.full(len(celle), np.inf)
    for p in [base[:2]] + [p for _, p in tappe]:
        distanza = np.minimum(distanza, np.hypot(celle[:, 0] - p[0], celle[:, 1] - p[1]))
    extra = []
    scoperte = distanza > RAGGIO_COPERTURA_M
    while scoperte.any():
        i = int(np.argmax(vicine[:, scoperte].sum(axis=1)))
        extra.append((f"EXTRA_{len(extra) + 1}", (float(candidati[i, 0]), float(candidati[i, 1]))))
        scoperte &= ~vicine[i]
    return extra


def leggi_tappe(argomenti, mappa, base):
    """Nomi di zona, 'tutti' o coppie x y -> [(nome, (x, y))], ogni punto controllato sulla pianta."""
    zone = {z["id"].upper(): z for z in sito.zone()}
    tutti = any(a.upper() == "TUTTI" for a in argomenti)
    argomenti = [nome for a in argomenti for nome in (zone if a.upper() == "TUTTI" else [a])]
    tappe, i = [], 0
    while i < len(argomenti):
        a = argomenti[i]
        if a.upper() in zone:
            z = zone[a.upper()]
            tappe.append((z["id"], tuple(z["hover"][:2])))
            i += 1
            continue
        try:
            x, y = float(a.replace(",", ".")), float(argomenti[i + 1].replace(",", "."))
        except (ValueError, IndexError):
            sys.exit(f"Non capisco {a!r}: scrivi il nome di una zona ({', '.join(zone)}) o una coppia x y in metri")
        tappe.append((f"({x:.1f}; {y:.1f})".replace(".", ","), (x, y)))
        i += 2
    buone = comode(mappa, base)
    righe, colonne = np.nonzero(buone)
    cx, cy = mappa.centro(righe, colonne)
    controllate = []
    for nome, (x, y) in tappe:
        r, c = mappa.cella(x, y)
        if not (mappa.dentro(r, c) and buone[r, c]):
            i = int(np.argmin((cx - x) ** 2 + (cy - y) ** 2))
            vicino = (float(cx[i]), float(cy[i]))
            if math.dist(vicino, (x, y)) > 2.5:
                sys.exit(f"Tappa {nome}: fuori dalla pianta, sotto un soppalco, oltre una vetrata o lontana dalla zona "
                         "in cui si vola")
            print(f"Tappa {nome}: a meno di {DISTANZA_DAI_MURI_M} m da pareti, pilastri, soppalchi o vetrate, uso "
                  f"({vicino[0]:.2f}; {vicino[1]:.2f})".replace(".", ","))
            x, y = vicino
        controllate.append((nome, (x, y)))
    if tutti:
        extra = punti_di_copertura(mappa, base, controllate)
        print(f"Punti di copertura per tutta la pianta: {len(extra)}")
        controllate += extra
    return controllate


def lunghezza(mappa, a, b):
    tratto = mappa.percorso(a, b)
    return sum(math.dist(p, q) for p, q in zip(tratto, tratto[1:])) if tratto else math.inf


def ordina_tappe(mappa, base, tappe):
    """Ordine con il giro più corto pad -> tutte le tappe -> pad, sulle distanze A* della pianta: tutte le
    permutazioni fino a 7 tappe, oltre la tappa più vicina ogni volta e poi scambi 2-opt finché accorciano."""
    punti = [base[:2]] + [p for _, p in tappe]
    d = {}
    for i, j in itertools.combinations(range(len(punti)), 2):
        d[i, j] = d[j, i] = lunghezza(mappa, punti[i], punti[j])

    def giro(ordine):
        return d[0, ordine[0]] + sum(d[a, b] for a, b in zip(ordine, ordine[1:])) + d[ordine[-1], 0]

    if len(tappe) <= 7:
        return [tappe[i - 1] for i in min(itertools.permutations(range(1, len(punti))), key=giro)]
    ordine, resto = [], set(range(1, len(punti)))
    while resto:
        ultimo = ordine[-1] if ordine else 0
        ordine.append(min(resto, key=lambda j: d[ultimo, j]))
        resto.remove(ordine[-1])
    migliorato = True
    while migliorato:
        migliorato = False
        for i, j in itertools.combinations(range(len(ordine)), 2):
            nuovo = ordine[:i] + ordine[i:j + 1][::-1] + ordine[j + 1:]
            if giro(nuovo) < giro(ordine) - 1e-6:
                ordine, migliorato = nuovo, True
    return [tappe[i - 1] for i in ordine]


def anteprima(mappa, base, tappe):
    """Percorso completo pad -> tappe -> pad sulla pianta, salvato in giro_punti.png. Restituisce i metri."""
    soste = [base[:2]] + [p for _, p in tappe] + [base[:2]]
    percorso, metri = [soste[0]], 0.0
    for a, b in zip(soste, soste[1:]):
        tratto = mappa.percorso(a, b)
        if not tratto:
            sys.exit(f"Nessun percorso sicuro da ({a[0]:.1f}; {a[1]:.1f}) a ({b[0]:.1f}; {b[1]:.1f})")
        metri += sum(math.dist(p, q) for p, q in zip(tratto, tratto[1:]))
        percorso += tratto[1:]
    operatore, margine = posto_operatore(mappa, base, percorso)
    punti = {"pad": base[:2], "OPERATORE": operatore,
             **{f"{i} {nome}": p for i, (nome, p) in enumerate(tappe, 1)}}
    titolo = f"Giro a {QUOTA_GIRO_CM / 100:.2f} m: {len(tappe)} tappe, {metri:.1f} m".replace(".", ",")
    immagine = mappa.disegna(scala=5, percorso=percorso, punti=punti, titolo=titolo, drone=base)   # pallino rosso = muso
    cv2.imwrite(IMMAGINE, immagine)
    print(f"{titolo}: pad -> {' -> '.join(nome for nome, _ in tappe)} -> pad\nSalvata {IMMAGINE}\n"
          f"{POSIZIONE_DI_PARTENZA}\n"
          f"OPERATORE: stai in ({_m(operatore[0])}; {_m(operatore[1])}) m, a {_m(math.dist(operatore, base[:2]))} m "
          f"dal pad e a {_m(margine)} m dal percorso più vicino (punto OPERATORE in {IMMAGINE}).")
    return metri


def _m(valore):
    return f"{valore:.1f}".replace(".", ",")


def posto_operatore(mappa, base, percorso):
    """Dove sta chi pilota: tra 2 e 4 m dal pad, non dentro un ostacolo, il più lontano possibile da ogni tratto del
    percorso (a 1,7 m il drone vola all'altezza della testa). Restituisce (punto, distanza dal percorso più vicino)."""
    righe, colonne = np.nonzero(~mappa.occupata & (mappa.distanza >= 0.3))
    xs, ys = mappa.centro(righe, colonne)
    vicino_al_pad = np.hypot(xs - base[0], ys - base[1])
    tieni = (vicino_al_pad >= 2.0) & (vicino_al_pad <= 4.0)
    xs, ys = xs[tieni], ys[tieni]
    lontano = np.full(len(xs), np.inf)
    for (ax, ay), (bx, by) in zip(percorso, percorso[1:]):
        dx, dy = bx - ax, by - ay
        t = np.clip(((xs - ax) * dx + (ys - ay) * dy) / max(dx * dx + dy * dy, 1e-9), 0, 1)
        lontano = np.minimum(lontano, np.hypot(xs - (ax + t * dx), ys - (ay + t * dy)))
    i = int(np.argmax(lontano))
    return (float(xs[i]), float(ys[i])), float(lontano[i])


def sorveglia_tasti(volo, drone, fine):
    """SPAZIO: atterra dove si trova. BACKSPACE: emergenza."""
    while not fine.is_set():
        if premuto(TASTO_SPAZIO) and not volo.atterra.is_set():
            print("\n[SPAZIO] Kill switch: atterro dove sono")
            volo.atterra_subito()
        if premuto(TASTO_BACKSPACE):
            print("\n[BACKSPACE] EMERGENZA: motori spenti")
            volo.atterra.set()
            try:
                drone.emergency()
            except Exception as e:
                print("emergency non mandato:", e)
            volo.in_volo = False
            return
        time.sleep(0.02)


def svuota_tastiera():
    """Toglie dalla console i tasti premuti in volo: altrimenti finiscono sul prompt quando il programma esce."""
    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getwch()
    except Exception:
        pass


def con_i_tasti(volo, drone, tappe, scatta, quattro_lati=False):
    """Giro automatico con SPAZIO, BACKSPACE e Ctrl+C. Restituisce le tappe raggiunte."""
    if os.name != "nt":
        sys.exit("I tasti si leggono solo su Windows")
    print("\nDecollo: il percorso lo segue da solo. SPAZIO = atterra dove si trova, BACKSPACE = emergenza, "
          "Ctrl+C = atterra.")
    fine, esito = threading.Event(), {}
    threading.Thread(target=sorveglia_tasti, args=(volo, drone, fine), daemon=True).start()
    filo = threading.Thread(target=lambda: esito.update(raggiunte=volo.esegui_giro(tappe, scatta, quattro_lati)))
    filo.start()
    while filo.is_alive():
        try:
            filo.join(0.2)
        except KeyboardInterrupt:
            print("\nCtrl+C: atterro dove sono")
            volo.atterra_subito()
    fine.set()
    svuota_tastiera()
    return esito.get("raggiunte", [])


def simula(tappe, mappa, base, scatta, tasti, usa_tof=True):
    from simulatore import DroneSimulato
    isp.ATTESA_STABILIZZAZIONE_S = isp.PAUSA_YAW_S = 0.0
    cartella = os.path.join(tempfile.gettempdir(), "giro_punti")
    os.makedirs(cartella, exist_ok=True)
    drone = DroneSimulato(mappa, base, rumore=0.05)
    volo = isp.Ispezione(drone, None, cartella, os.path.join(cartella, "registro.jsonl"), mappa=mappa, base=base,
                         zone=sito.zone())
    volo.quota_volo_cm, volo.usa_tof = QUOTA_GIRO_CM, usa_tof
    vere, quote, originale = [], [], volo.vai_a_mappa

    def vai(destinazione, *argomenti, **opzioni):            # posizione vera dopo ogni tratto (solo in simulazione)
        originale(destinazione, *argomenti, **opzioni)
        vere.append((drone.x, drone.y))
        quote.append(round(drone.quota))

    volo.vai_a_mappa = vai
    raggiunte = con_i_tasti(volo, drone, tappe, scatta) if tasti else volo.esegui_giro(tappe, scatta)
    print("\nSIMULAZIONE con il 5% di errore su ogni passo e rotazione:")
    for (nome, stimata), vera in zip(raggiunte, vere):
        print(f"  {nome}: stimata ({stimata[0]:.2f}; {stimata[1]:.2f}), vera ({vera[0]:.2f}; {vera[1]:.2f}), "
              f"scarto {math.dist(stimata, vera):.2f} m".replace(".", ","))
    print(f"  tappe {len(raggiunte)} di {len(tappe)}; urti: {drone.urti or 'nessuno'}; quote {sorted(set(quote))} cm; "
          f"atterrato a {math.dist((drone.x, drone.y), base[:2]):.2f} m dal pad".replace(".", ","))


BATTERIA_MINIMA_AVVIO = 50


def prepara(volo):
    """Controlli a terra: SDK, modulo di espansione (ToF), batteria, temperatura, mission pad.
    Il video NON parte qui: a terra con il video acceso il Tello si surriscalda."""
    volo.drone.connect()
    print("SDK:", volo.leggi("sdk?", tentativi=1))
    print("Numero di serie:", volo.leggi("sn?", tentativi=1))
    hardware = volo.leggi("hardware?", lambda r: r in ("RMTT", "TELLO"), tentativi=6)
    print("Hardware:", hardware)
    if hardware == "TELLO" and volo.usa_tof:                # Tello EDU: nessun modulo di espansione, quindi nessun ToF
        raise RuntimeError("modulo di espansione non visto: serve il Tello Talent (RMTT). Con il Tello EDU aggiungi --senza-tof")
    if hardware != "RMTT" and volo.usa_tof:
        raise RuntimeError(f"hardware {hardware}: il ToF richiede il Tello Talent (RMTT)")
    if volo.usa_tof:
        print("ToF frontale:", volo.tof_cm(), "cm")         # sempre 0 mm -> errore: sensore coperto o guasto
    else:
        print(f"ToF frontale DISATTIVATO ({hardware}): protegge solo la pianta (SPAZIO = atterra)")
    livello = volo.batteria()
    stato = volo.stato()
    print(f"Batteria {livello}% - temperatura {stato.get('templ')}-{stato.get('temph')} C")
    if livello < BATTERIA_MINIMA_AVVIO:
        raise RuntimeError(f"batteria sotto il {BATTERIA_MINIMA_AVVIO}%")
    volo.esegui("mon")
    volo.esegui("mdirection 0")                         # pad visto dal basso: la telecamera davanti resta libera
    return livello


def vola(tappe, mappa, base, scatta, usa_tof=True, quattro_lati=False, cartella="foto_giro",
         registro="registro_giro.jsonl"):
    from djitellopy import Tello
    if os.name != "nt":
        sys.exit("Il volo vero usa i tasti di Windows (spazio, backspace)")
    import logging
    Tello.LOGGER.setLevel(logging.WARNING)   # niente riga di djitellopy per ogni comando
    drone = Tello(retry_count=1)                           # ogni comando inviato una volta sola
    Tello.FRAME_GRAB_TIMEOUT = 10
    volo = isp.Ispezione(drone, None, cartella, registro, mappa=mappa, base=base, zone=sito.zone())
    volo.quota_volo_cm, volo.usa_tof = QUOTA_GIRO_CM, usa_tof
    try:
        prepara(volo)                                      # SDK, modulo ToF, batteria >= 50%, mission pad
    except Exception as e:
        try:
            drone.end()
        except Exception:
            pass
        aiuto = ("\nIl sensore frontale non misura: per volare lo stesso, protetti solo dalla pianta, "
                 "aggiungi --senza-tof" if "ToF" in str(e) else
                 " Il PC è collegato al Wi-Fi del drone?")
        sys.exit(f"Drone non pronto: {e}.{aiuto}")
    senza_tof = "" if usa_tof else " SENSORE FRONTALE DISATTIVATO: niente frenate davanti agli ostacoli non in pianta."
    risposta = input(f"\nParaeliche montate, drone sul pad con il muso verso ovest, NESSUNO nella sala lungo il percorso "
                     f"(a 1,7 m il drone è all'altezza della testa), tu fuori dal percorso?{senza_tof} "
                     "Scrivi si per continuare: ")
    if risposta.strip().lower() not in ("si", "sì"):
        drone.end()
        sys.exit("Decollo annullato")
    raggiunte = con_i_tasti(volo, drone, tappe, scatta, quattro_lati)
    try:
        drone.end()
    except Exception:
        pass
    print(f"\nTappe raggiunte: {len(raggiunte)} di {len(tappe)}. Registro: {registro}, foto in {cartella}/. "
          "Controlla a occhio quanto il drone si è fermato lontano da ogni punto.")
    return raggiunte


def main(argomenti):
    opzioni = {"--arm", "--senza-foto", "--tasti", "--senza-tof"}
    arm, scatta, tasti = "--arm" in argomenti, "--senza-foto" not in argomenti, "--tasti" in argomenti
    usa_tof = "--senza-tof" not in argomenti
    argomenti = [a for a in argomenti if a not in opzioni] or GIRO_PREDEFINITO
    mappa = prepara_pianta()
    base = (sito.BASE_X_M, sito.BASE_Y_M, sito.BASE_DIREZIONE_GRADI)
    tappe = leggi_tappe(argomenti, mappa, base)
    if any(a.upper() == "TUTTI" for a in argomenti):
        tappe = ordina_tappe(mappa, base, tappe)
    anteprima(mappa, base, tappe)
    if arm:
        vola(tappe, mappa, base, scatta, usa_tof)
    else:
        simula(tappe, mappa, base, scatta, tasti, usa_tof)


if __name__ == "__main__":
    main(sys.argv[1:])

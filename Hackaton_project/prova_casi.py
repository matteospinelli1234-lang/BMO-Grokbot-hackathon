"""
Casi del volo e delle telecamere in simulazione, sulla pianta vera con le zone di sito.py: kill switch, batteria,
risposte perse e guasti del drone, ToF, giro a 1,70 m, 4 foto, qualità delle foto, rilevamento delle telecamere e
verdetto di Grok. Nessun drone, nessuna rete.      python prova_casi.py
(La prova completa della centrale con la dashboard è  python prova_centrale.py)
"""
import math
import os
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

import centrale
import giro_punti
import ispezione as isp
import sito
from simulatore import DroneSimulato

# tempo del simulatore 50 volte più veloce: la batteria si rilegge prima di ogni comando (in volo vero ogni 20 s)
isp.ATTESA_STABILIZZAZIONE_S, isp.PAUSA_YAW_S, isp.INTERVALLO_BATTERIA_S = 0.0, 0.0, 0.0
CARTELLA = os.path.join(tempfile.gettempdir(), "prova_casi")
BASE = (sito.BASE_X_M, sito.BASE_Y_M, sito.BASE_DIREZIONE_GRADI)
ZONE_7, HUB = ("ZONE_7", (-4.25, 3.54)), ("HUB", (-2.46, 3.63))
esiti = []


def giro_nuovo(usa_tof=True):
    """Drone simulato sulla pianta del giro (tagliata fino a 2,2 m), a 1,70 m, sul percorso da solo."""
    os.makedirs(CARTELLA, exist_ok=True)
    mappa = giro_punti.prepara_pianta()
    drone = DroneSimulato(mappa, BASE, batteria=100)
    volo = isp.Ispezione(drone, None, CARTELLA, os.path.join(CARTELLA, "registro.jsonl"), mappa=mappa, base=BASE,
                         zone=sito.zone())
    volo.quota_volo_cm, volo.usa_tof = giro_punti.QUOTA_GIRO_CM, usa_tof
    return drone, volo, mappa


def dal_pad(drone):
    return math.dist((drone.x, drone.y), BASE[:2])


def lontano_dal_pad(drone, metri=1.5):
    return lambda: drone.in_volo and dal_pad(drone) > metri


def quando(condizione, azione, timeout=60):
    def filo():
        fine = time.time() + timeout
        while not condizione() and time.time() < fine:
            time.sleep(0.005)
        azione()
    threading.Thread(target=filo, daemon=True).start()


def a_terra_senza_urti(drone):
    assert not drone.urti, f"urti {drone.urti}"
    assert not drone.in_volo, "ancora in volo"


def conta_foto(volo):
    scattate, originale = [], volo.foto

    def foto(nome, **opzioni):
        scattate.append(nome)
        return originale(nome, **opzioni)

    volo.foto = foto
    return scattate


def risposta_persa(c):
    return f"Aborting command '{c}'. Did not receive a response after 7 seconds"


def caso(nome):
    def esegui(prova):
        print(f"\n=== {nome}")
        try:
            dettaglio = prova()
            print(f"  OK {dettaglio or ''}")
            esiti.append((nome, True))
        except AssertionError as e:
            print(f"  FALLITO: {e}")
            esiti.append((nome, False))
        except Exception as e:
            print(f"  ERRORE: {type(e).__name__}: {e}")
            esiti.append((nome, False))
    return esegui


# ---------------- ritorno al pad (5 novembre: arrivava lungo e un po' a sinistra) ----------------
@caso("senza ToF un tratto è un solo 'forward' (distanza misurata dal Tello), prima del tratto muso entro 1 grado")
def _():
    drone, volo, _ = giro_nuovo(usa_tof=False)
    raggiunte = volo.esegui_giro([ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    avanti = [c for c in drone.comandi if c.startswith("forward")]
    assert len(raggiunte) == 1 and len(avanti) == 2, avanti        # andata e ritorno: un comando ciascuno
    assert not any(c.startswith("rc ") and c != "rc 0 0 0 0" for c in drone.comandi), "volo continuo con rc"
    assert dal_pad(drone) < 0.3, f"atterrato a {dal_pad(drone):.2f} m dal pad"
    return f"{avanti}, atterrato a {dal_pad(drone):.2f} m dal pad"


@caso("ricerca del pad sopra un oggetto (ToF in basso 30 cm, h 80): quote prese da h, mai oltre 1,2 m")
def _():
    drone, volo, _ = giro_nuovo(usa_tof=False)
    originale = drone.get_current_state

    def stato():
        s = dict(originale())
        if volo.fase.startswith(("cerco il pad", "pad non visto")):
            s["tof"], s["mid"] = max(10, s["h"] - 50), -1          # oggetto alto 50 cm sotto il drone, pad non visto
        return s

    drone.get_current_state = stato
    volo.esegui_giro([HUB], scatta=False)
    a_terra_senza_urti(drone)
    salite = [c for c in drone.comandi[drone.comandi.index("down 90"):] if c.startswith("up")]
    assert salite == ["up 20"], f"dopo la discesa a 80 cm: {salite} (doveva salire solo a 1 m)"


# ---------------- kill switch ----------------
@caso("SPAZIO (kill switch) a metà tratto: atterra dove si trova")
def _():
    drone, volo, _ = giro_nuovo()
    quando(lontano_dal_pad(drone, 2.0), volo.atterra_subito)
    raggiunte = volo.esegui_giro([ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    assert not raggiunte and dal_pad(drone) > 1.5, f"è andato avanti o è tornato ({dal_pad(drone):.2f} m dal pad)"
    assert not any(c.startswith("go ") for c in drone.comandi), "è andato a cercare il pad"
    return f"atterrato a {dal_pad(drone):.2f} m dal pad"


@caso("SPAZIO durante la salita a 1,70 m: rc 0 e land subito senza aspettare la risposta, nessun passo dopo")
def _():
    drone, volo, _ = giro_nuovo()
    originale = drone.send_command_with_return

    def comando(c, timeout=7):
        if c.startswith("up"):
            volo.atterra_subito()                           # come sorveglia_tasti allo SPAZIO, a metà salita
            return "error Not joystick"
        return originale(c, timeout)

    drone.send_command_with_return = comando
    volo.esegui_giro([HUB], scatta=False)
    a_terra_senza_urti(drone)
    i = drone.comandi.index("rc 0 0 0 0")
    assert drone.comandi[i + 1] == "land", f"dopo rc 0 è arrivato {drone.comandi[i + 1]!r} invece di land"
    assert not any(c.startswith("forward") for c in drone.comandi), "è partito lo stesso"


@caso("SPAZIO durante il decollo: land subito, nessuna salita né passo dopo il decollo")
def _():
    drone, volo, _ = giro_nuovo()
    originale = drone.takeoff

    def decollo():
        volo.atterra_subito()
        originale()

    drone.takeoff = decollo
    volo.esegui_giro([HUB], scatta=False)
    a_terra_senza_urti(drone)
    i = drone.comandi.index("takeoff")
    assert "land" in drone.comandi[i:] and not any(c.startswith(("up", "forward")) for c in drone.comandi[i:])


# ---------------- batteria ----------------
@caso("batteria sotto il 30% all'andata: niente foto, torna al pad")
def _():
    drone, volo, _ = giro_nuovo()
    scattate = conta_foto(volo)
    quando(lontano_dal_pad(drone), lambda: setattr(drone, "batteria", 27))
    volo.esegui_giro([ZONE_7], scatta=True, quattro_lati=True)
    a_terra_senza_urti(drone)
    assert not scattate, f"ha scattato {scattate}"
    assert dal_pad(drone) < 0.3, f"non è tornato al pad ({dal_pad(drone):.2f} m)"


@caso("batteria sotto il 20%: atterra dove si trova")
def _():
    drone, volo, _ = giro_nuovo()
    quando(lontano_dal_pad(drone), lambda: setattr(drone, "batteria", 15))
    volo.esegui_giro([ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    assert dal_pad(drone) > 1.0, "è tornato al pad con la batteria al minimo"
    return f"atterrato a {dal_pad(drone):.2f} m dal pad"


# ---------------- guasti del drone e Wi-Fi ----------------
@caso("decollo rifiutato (error, high temp): land mandato, nessun movimento")
def _():
    drone, volo, _ = giro_nuovo()
    originale = drone.send_command_with_return
    drone.send_command_with_return = lambda c, timeout=7: "error, high temp" if c == "takeoff" else originale(c, timeout)
    volo.esegui_giro([ZONE_7], scatta=False)
    assert not any(c.startswith("forward") for c in drone.comandi), "si è mosso"
    assert "land" in drone.comandi and not drone.in_volo


@caso("errore di un comando in volo (error Motor stop): atterra dove si trova")
def _():
    drone, volo, _ = giro_nuovo()
    originale, conta = drone.send_command_with_return, {"n": 0}

    def guasto(c, timeout=7):
        if c.startswith("forward"):
            conta["n"] += 1
            return "error Motor stop"
        return originale(c, timeout)

    drone.send_command_with_return = guasto
    raggiunte = volo.esegui_giro([ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    assert conta["n"] == 1, f"{conta['n']} passi dopo l'errore"
    assert not raggiunte and "land" in drone.comandi, "dopo l'errore non è atterrato"


@caso("risposta persa a una rotazione (Wi-Fi): rimisura lo yaw e continua fino al pad")
def _():
    drone, volo, _ = giro_nuovo()
    originale, perse = drone.send_command_with_return, []

    def comando(c, timeout=7):
        if c.split()[0] in ("cw", "ccw") and not perse:
            originale(c, timeout)                           # il drone gira, la risposta non arriva
            perse.append(c)
            return risposta_persa(c)
        return originale(c, timeout)

    drone.send_command_with_return = comando
    raggiunte = volo.esegui_giro([HUB, ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    assert perse and len(raggiunte) == 2 and dal_pad(drone) < 0.3, "non ha finito il giro sul pad"
    return f"persa la risposta a {perse[0]!r}, giro finito"


@caso("risposta persa a un 'forward': atterra dove si trova (non sa dov'è)")
def _():
    drone, volo, _ = giro_nuovo()
    originale = drone.send_command_with_return
    drone.send_command_with_return = lambda c, timeout=7: risposta_persa(c) if c.startswith("forward") else originale(c, timeout)
    raggiunte = volo.esegui_giro([HUB], scatta=False)
    a_terra_senza_urti(drone)
    assert not raggiunte and "land" in drone.comandi and dal_pad(drone) < 0.3


@caso("video del drone assente: giro completo senza foto, torna al pad")
def _():
    drone, volo, _ = giro_nuovo()
    drone.get_frame_read = lambda: type("SenzaVideo", (), {"frame": None})()
    raggiunte = volo.esegui_giro([ZONE_7], scatta=True, quattro_lati=True)
    a_terra_senza_urti(drone)
    assert len(raggiunte) == 1 and dal_pad(drone) < 0.3, f"non è tornato al pad ({dal_pad(drone):.2f} m)"


# ---------------- ToF frontale (il 5 novembre leggeva sempre 0) ----------------
@caso("ToF che legge sempre 0 a terra: il controllo prima del volo lo ferma")
def _():
    drone, volo, _ = giro_nuovo()
    drone._tof_mm = lambda: 0
    try:
        giro_punti.prepara(volo)
    except RuntimeError as e:
        assert "ToF" in str(e), f"messaggio poco chiaro: {e}"
        assert "takeoff" not in drone.comandi
        return f"fermato: {e}"
    raise AssertionError("ha accettato un ToF che legge 0")


@caso("ToF che va a 0 in volo: niente ostacolo finto, atterra dove si trova")
def _():
    drone, volo, _ = giro_nuovo()
    quando(lontano_dal_pad(drone, 1.0), lambda: setattr(drone, "_tof_mm", lambda: 0))
    raggiunte = volo.esegui_giro([ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    assert not raggiunte and dal_pad(drone) > 0.8, "ha continuato con il sensore guasto"
    return f"atterrato a {dal_pad(drone):.2f} m dal pad"


@caso("--senza-tof con il sensore a 0: giro completo protetto solo dalla pianta, torna al pad")
def _():
    drone, volo, _ = giro_nuovo(usa_tof=False)
    drone._tof_mm = lambda: 0
    giro_punti.prepara(volo)
    raggiunte = volo.esegui_giro([HUB, ZONE_7], scatta=False)
    a_terra_senza_urti(drone)
    assert len(raggiunte) == 2 and dal_pad(drone) < 0.3, f"{len(raggiunte)} tappe, {dal_pad(drone):.2f} m dal pad"
    assert "EXT tof?" not in drone.comandi, "ha letto il ToF disattivato"


# ---------------- pianta e giro a 1,70 m ----------------
@caso("giro tutti a 1,70 m: niente soppalchi a 2,15 m né stanza oltre la vetrata, tappe a 1,2 m dai muri, pianta coperta")
def _():
    _, _, mappa = giro_nuovo()
    bassi = [(-11.42, -2.40, -3.85, 0.73), (-7.43, -0.53, -5.95, -3.49)]   # Corpo14, Corpo5/13: solette a 2,15 m
    oltre_vetrata = [(-11.42, -6.45, 1.20, 5.79)]                        # stanza della porta B, dietro la vetrata

    def dentro(x, y, rettangoli):
        return any(x1 <= x <= x2 and y1 <= y <= y2 for x1, x2, y1, y2 in rettangoli)

    tappe = giro_punti.leggi_tappe(["TUTTI"], mappa, BASE)
    for nome, (x, y) in tappe:
        assert not dentro(x, y, bassi + oltre_vetrata), f"tappa {nome} ({x:.2f}; {y:.2f}) sotto un soppalco o oltre il vetro"
        assert mappa.distanza_ostacoli(x, y) >= giro_punti.DISTANZA_DAI_MURI_M - 0.05, \
            f"tappa {nome} a {mappa.distanza_ostacoli(x, y):.2f} m da un muro"
    soste = [BASE[:2]] + [p for _, p in tappe] + [BASE[:2]]
    tutto = []
    for a, b in zip(soste, soste[1:]):
        tratto = mappa.percorso(a, b)
        assert tratto, f"nessun percorso da {a} a {b}"
        tutto += tratto
        for p, q in zip(tratto, tratto[1:]):
            for t in range(11):
                px, py = p[0] + (q[0] - p[0]) * t / 10, p[1] + (q[1] - p[1]) * t / 10
                assert not dentro(px, py, bassi + oltre_vetrata), f"percorso vietato in ({px:.1f}; {py:.1f})"
    raggiungibili = mappa.raggiungibili(*BASE[:2])
    righe_, colonne = np.nonzero(raggiungibili[::5, ::5])
    xs, ys = mappa.centro(righe_ * 5, colonne * 5)
    punti = np.array([BASE[:2]] + [p for _, p in tappe])
    lontana = max(min(math.hypot(x - px, y - py) for px, py in punti) for x, y in zip(xs, ys))
    assert lontana <= giro_punti.RAGGIO_COPERTURA_M + 0.01, f"una parte della pianta è a {lontana:.1f} m dalle tappe"
    operatore, margine = giro_punti.posto_operatore(mappa, BASE, tutto)
    assert 2.0 <= math.dist(operatore, BASE[:2]) <= 4.0 and margine >= 1.5, f"operatore a {margine:.1f} m dal percorso"
    return f"{len(tappe)} tappe, pianta entro {lontana:.1f} m, operatore a {margine:.1f} m dal percorso"


@caso("giro tutti a 1,70 m simulato: tutte le tappe, quota 170 cm, nessun urto, di nuovo sul pad")
def _():
    drone, volo, mappa = giro_nuovo()
    tappe = giro_punti.ordina_tappe(mappa, BASE, giro_punti.leggi_tappe(["TUTTI"], mappa, BASE))
    quote, originale = [], volo.vai_a_mappa

    def vai(destinazione, *a, **k):
        originale(destinazione, *a, **k)
        quote.append(drone.quota)

    volo.vai_a_mappa = vai
    raggiunte = volo.esegui_giro(tappe, scatta=False)
    a_terra_senza_urti(drone)
    assert len(raggiunte) == len(tappe), f"{len(raggiunte)} tappe su {len(tappe)}"
    assert all(abs(q - giro_punti.QUOTA_GIRO_CM) <= 10 for q in quote), f"quote {quote}"
    assert dal_pad(drone) < 0.3, f"atterrato a {dal_pad(drone):.2f} m dal pad"
    return f"{len(raggiunte)} tappe a {giro_punti.QUOTA_GIRO_CM} cm, batteria al {drone.batteria:.0f}%"


@caso("4 foto davvero ruotate di 90 gradi l'una dall'altra, dal punto della tappa")
def _():
    drone, volo, _ = giro_nuovo()
    direzioni, originale = [], volo.foto

    def foto(nome, **opzioni):
        direzioni.append((round(drone.dir) % 360, round(drone.x, 2), round(drone.y, 2)))
        return originale(nome, **opzioni)

    volo.foto = foto
    raggiunte = volo.esegui_giro([("CENTER", (0.0, 0.0))], scatta=True, quattro_lati=True)
    a_terra_senza_urti(drone)
    gradi = sorted(round(d / 90) * 90 % 360 for d, _, _ in direzioni)
    assert gradi == [0, 90, 180, 270], direzioni
    assert all(abs(d - g) <= isp.TOLLERANZA_GRADI + 3 or abs(d - g) >= 360 - isp.TOLLERANZA_GRADI - 3
               for (d, _, _), g in zip(direzioni, [round(d / 90) * 90 % 360 for d, _, _ in direzioni])), direzioni
    assert len({(x, y) for _, x, y in direzioni}) == 1, "si è spostato tra una foto e l'altra"
    assert len(raggiunte) == 1 and dal_pad(drone) < 0.3
    return f"direzioni {[d for d, _, _ in direzioni]}"


# ---------------- foto del drone ----------------
@caso("foto nitide: lettore video morto riavviato, salvato il fotogramma più nitido")
def _():
    class Lettore:
        def __init__(self, vivo):
            self.worker = threading.Thread(target=lambda: time.sleep(5 if vivo else 0), daemon=True)
            self.worker.start()
            self.n = 0

        @property
        def frame(self):
            self.n += 1
            img = np.full((720, 960, 3), 100, np.uint8)
            if self.n % 5 == 0:
                cv2.putText(img, "NITIDA", (100, 300), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
                return img
            return cv2.GaussianBlur(img, (31, 31), 0)

        def stop(self):
            pass

    drone, volo, _ = giro_nuovo()
    drone.get_frame_read = lambda: Lettore(True)
    volo.lettore = Lettore(False)
    time.sleep(0.1)
    percorso, _ = volo.foto("prova_nitidezza")
    originale = cv2.imread(os.path.join(os.path.dirname(percorso), "originali", os.path.basename(percorso)))
    nitidezza = cv2.Laplacian(cv2.cvtColor(originale, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    assert nitidezza > 50, f"salvata una foto mossa ({nitidezza:.0f})"
    assert "streamon" in drone.comandi, "lettore morto non riavviato"
    return f"nitidezza {nitidezza:.0f}"


@caso("foto: tra i fotogrammi viene scelto quello senza blocchi, l'originale resta in originali/")
def _():
    class Lettore:
        def __init__(self):
            self.worker = threading.Thread(target=lambda: time.sleep(5), daemon=True)
            self.worker.start()
            self.n = 0
            self.pulito = np.zeros((720, 960, 3), np.uint8)
            cv2.putText(self.pulito, "TELLO", (150, 400), cv2.FONT_HERSHEY_SIMPLEX, 6, (200, 200, 200), 12)
            self.pulito = cv2.GaussianBlur(self.pulito, (5, 5), 0)

        @property
        def frame(self):
            self.n += 1
            if self.n % 4:                                  # 3 su 4 con i blocchi di pacchetti persi
                rotto = self.pulito.copy()
                for y in range(0, 720, 16):
                    for x in range(0, 960, 16):
                        if (x // 16 + y // 16) % 3 == 0:
                            rotto[y:y + 16, x:x + 16] = rotto[y:y + 16, x:x + 16].mean()
                return rotto
            return self.pulito.copy()

        def stop(self):
            pass

    _, volo, _ = giro_nuovo()
    volo.lettore = Lettore()
    percorso, _ = volo.foto("prova_blocchi")
    misure = isp.qualita_foto(cv2.imread(os.path.join(os.path.dirname(percorso), "originali",
                                                      os.path.basename(percorso))))
    assert misure["blocchi"] < 2.0, f"salvato un fotogramma a blocchi ({misure['blocchi']:.2f}, i rotti valgono 4)"
    return f"blocchi {misure['blocchi']:.2f}"


# ---------------- telecamere fisse e Grok (centrale.py) ----------------
class Stanza:
    """Telecamera finta: stanza vuota; con 'movimento' compare una cosa grande (o piccola, con piccola=True)."""

    def __init__(self, piccola=False):
        self.movimento, self.piccola, self.aperta = False, piccola, True

    def isOpened(self):
        return self.aperta

    def read(self):
        time.sleep(0.01)
        img = np.full((480, 640, 3), 30, np.uint8)
        cv2.rectangle(img, (0, 0), (639, 40), (70, 55, 40), -1)
        if self.movimento and self.piccola:
            cv2.rectangle(img, (300, 200), (330, 230), (230, 230, 230), -1)   # 0,3% dell'immagine
        elif self.movimento:
            cv2.rectangle(img, (40, 50), (600, 450), (230, 230, 230), -1)
        return True, img

    def release(self):
        self.aperta = False


def allarme_dopo(stanza, attesa_s):
    """Sorveglia la stanza con la centrale; il movimento parte dopo 0,6 s. Secondi dall'inizio del movimento
    all'allarme (camera_input), o None se in attesa_s non arriva."""
    drone, volo, mappa = giro_nuovo()
    camera = {"id": "cam9", "nome": "Camera 9", "url": "finta", "zona": "HUB", "ruota": 0, "finta": stanza}
    c = centrale.Centrale([camera], volo, drone, mappa, finte=True, cartella=tempfile.mkdtemp(prefix="casi_"))
    riscaldamento, centrale.RISCALDAMENTO_S = centrale.RISCALDAMENTO_S, 0.4
    inizio = {}
    try:
        threading.Thread(target=c.sorveglia, args=(c.telecamere["cam9"],), daemon=True).start()
        quando(lambda: True, lambda: (time.sleep(0.6), inizio.setdefault("t", time.time()), setattr(stanza, "movimento", True)))
        fine = time.time() + attesa_s
        while time.time() < fine:
            if any(e["event"] == "camera_input" for e in c.eventi):
                return time.time() - inizio["t"]
            time.sleep(0.01)
        return None
    finally:
        c.fine.set()
        centrale.RISCALDAMENTO_S = riscaldamento


@caso("telecamera: un movimento piccolo non dà allarme; uno grande sì, ma solo mezzo secondo dopo")
def _():
    assert allarme_dopo(Stanza(piccola=True), 2.5) is None, "allarme per un movimento piccolo"
    ritardo = allarme_dopo(Stanza(), 5)
    assert ritardo is not None and ritardo >= centrale.ATTESA_INGRESSO_S, f"allarme dopo {ritardo}"
    return f"grande: allarme dopo {ritardo:.2f} s"


class GrokFinto:
    """Client finto con la stessa interfaccia di openai: risponde il testo dato o solleva l'eccezione data."""

    def __init__(self, risposta):
        self.risposta, self.messaggi = risposta, None
        self.chat = self.completions = self

    def create(self, **argomenti):
        self.messaggi = argomenti["messages"]
        if isinstance(self.risposta, Exception):
            raise self.risposta
        messaggio = type("M", (), {"content": self.risposta})()
        return type("R", (), {"choices": [type("C", (), {"message": messaggio})()]})()


@caso("verdetto di Grok dopo le 4 foto: ANOMALY DETECTED, FALSO ALLARME, risposte non valide = VERIFICA MANUALE")
def _():
    os.makedirs(CARTELLA, exist_ok=True)
    foto = []
    for lato in ("est", "nord", "ovest", "sud"):
        f = os.path.join(CARTELLA, f"prova_{lato}.jpg")
        cv2.imwrite(f, np.full((720, 960, 3), 90, np.uint8))
        foto.append((lato, f))
    scena = np.full((480, 640, 3), 30, np.uint8)
    client = GrokFinto('{"esito": "anomaly detected", "descrizione": "persona vicino al pilastro", "foto": ["nord"], '
                       '"confidenza": 0.9}')
    v = centrale.verifica_con_grok(client, scena, scena, foto, 9)
    assert v["esito"] == "ANOMALY DETECTED" and v["foto"] == ["nord"], v
    immagini = sum(1 for parte in client.messaggi[0]["content"] if parte["type"] == "image_url")
    assert immagini == 6, f"{immagini} immagini invece di 6 (scena normale, allarme, 4 del drone)"
    falso = GrokFinto('{"esito": "FALSO ALLARME", "confidenza": 0.7}')
    assert centrale.verifica_con_grok(falso, scena, scena, foto, 9)["esito"] == "FALSO ALLARME"
    for risposta in ('{"esito": "forse"}', "rotto", ConnectionError("rete")):
        assert centrale.verifica_con_grok(GrokFinto(risposta), scena, scena, foto, 9)["esito"] == "VERIFICA MANUALE", risposta


if __name__ == "__main__":
    superati = sum(ok for _, ok in esiti)
    print(f"\n{superati} casi su {len(esiti)} superati")
    for nome, ok in esiti:
        if not ok:
            print("  FALLITO:", nome)
    sys.exit(0 if superati == len(esiti) else 1)

"""
Centrale di sorveglianza (progetto finale, traccia D): telecamere FISSE, ognuna assegnata a una zona del capannone
all'avvio; quando una telecamera vede qualcosa di grande la dashboard chiede all'operatore se mandare il drone; con
il Sì il drone vola dal pad al punto principale della zona a 1,70 m, scatta 4 foto (una ogni 90 gradi), torna al pad e
Grok confronta telecamera e foto: ANOMALY DETECTED o FALSO ALLARME. Tutto è sulla dashboard.

    python centrale.py                       drone simulato, telecamere vere (IP Webcam)
    python centrale.py --camere-finte        drone e telecamere simulati: in dashboard «Simula evento» (prova completa)
    python centrale.py --arm --senza-tof     volo vero
    python centrale.py ... --telefono        dashboard aperta anche dal telefono (stampa l'indirizzo da usare)
Dashboard: http://localhost:5000 (console operatore). Grafana (facoltativo): cd dashboard, docker compose up -d,
poi http://localhost:3000/d/drone-konsol; legge dashboard/logs/events.json come la dashboard del team.

All'avvio chiede per ogni telecamera la sua zona (Invio = quella proposta, ricordata in dashboard/camere.json).
Le telecamere sono in TELECAMERE qui sotto (indirizzi di IP Webcam). In volo: STOP e EMERGENZA nella dashboard,
oppure SPAZIO (atterra dove si trova) e BACKSPACE (emergenza) sul PC; Ctrl+C chiude tutto facendo atterrare il drone.
Compatibile con la console del team (bmo-grokbot-hackathon/log): stessi indirizzi /status, /pending, /decision,
/command, /frames e stessi nomi di evento in logs/events.json (camera_input, awaiting_decision, final_decision,
drone_dispatched, mission_completed, ...), più drone_photo e verdict.
"""
import os

os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")    # niente righe «[mjpeg] overread» dagli stream dei telefoni

import argparse
import base64
import glob
import math
import heapq
import itertools
import json
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import cv2
import numpy as np

import giro_punti
import ispezione as isp
import sito

# Telecamere fisse (indirizzo che IP Webcam mostra sul telefono, con /video). zona = proposta all'avvio.
STREAM_URL = "http://192.168.5.133:8080/video"
TELECAMERE = [
    {"id": "cam1", "nome": "Camera 1", "url": STREAM_URL, "zona": "NORTH_OPEN", "ruota": 0},
    {"id": "cam2", "nome": "Camera 2", "url": "http://192.168.5.148:8080/video", "zona": "ZONE_7", "ruota": 0},
]
CARTELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard")
FRAMES = os.path.join(CARTELLA, "frames")
FILE_EVENTI = os.path.join(CARTELLA, "logs", "events.json")   # letto da Alloy -> Loki -> Grafana
FILE_CAMERE = os.path.join(CARTELLA, "camere.json")
PRIORITA = {"high": 3, "medium": 2, "low": 1}
PRIORITA_MANUALE = 4
PAUSA_CAMERA_S = 10             # dopo un allarme la stessa telecamera tace per questo tempo
BATTERIA_MINIMA_MISSIONE = 40
TELEMETRIA_S = 2                # in volo: un evento 'telemetry' ogni 2 s (Grafana: batteria, quota, temperatura)
BASE = (sito.BASE_X_M, sito.BASE_Y_M, sito.BASE_DIREZIONE_GRADI)
FILE_CHIAVE = os.path.join(os.path.expanduser("~"), "chiave_xai.txt")
AREA_MINIMA = 0.03              # allarme solo se la macchia in movimento più grande copre almeno il 3% dell'immagine
ATTESA_INGRESSO_S = 0.5         # poi si aspetta che la cosa entri nell'inquadratura; se è sparita, niente allarme
RISCALDAMENTO_S = 3             # all'avvio della telecamera: il fondo si assesta
# livello di ogni evento (Loki lo legge come detected_level: colori nei log di Grafana invece di «UNK»)
LIVELLI = {"camera_error": "warning", "mission_aborted": "warning", "task_refused": "warning", "stop_pressed": "warning",
           "ignored_own_drone": "debug", "telemetry": "debug", "mission_error": "error", "emergency_pressed": "error"}


def adesso():
    return datetime.now().isoformat(timespec="seconds")


def zone_ids():
    return [z["id"] for z in sito.zone()]


# ---------------- telecamere finte (--camere-finte e prove) ----------------
class CameraFinta:
    """Stanza vuota; con 'evento' compare una sagoma grande (persona) per qualche secondo."""

    def __init__(self, nome):
        self.nome, self.aperta, self.fino = nome, True, 0.0

    def isOpened(self):
        return self.aperta

    def read(self):
        time.sleep(0.04)
        immagine = np.full((480, 640, 3), (60, 70, 80), np.uint8)
        cv2.rectangle(immagine, (0, 330), (639, 479), (90, 100, 110), -1)
        cv2.putText(immagine, self.nome, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (220, 220, 220), 2)
        if time.time() < self.fino:
            cv2.ellipse(immagine, (320, 150), (40, 50), 0, 0, 360, (190, 170, 150), -1)
            cv2.rectangle(immagine, (260, 200), (380, 430), (40, 40, 160), -1)
        return True, immagine

    def evento(self, secondi=6):
        self.fino = time.time() + secondi

    def release(self):
        self.aperta = False


# ---------------- telecamere: ultimo fotogramma, rilevamento di cose grandi ----------------
class UltimoFotogramma:
    """Legge lo stream in un thread e tiene solo l'ultimo fotogramma (senza, lo stream accumula secondi di ritardo)."""

    def __init__(self, cap):
        self.cap, self.frame, self.conta, self.visti = cap, None, 0, 0
        self.nuovo = threading.Condition()
        self.attivo = True
        self.thread = threading.Thread(target=self._ciclo, daemon=True)
        self.thread.start()

    def _ciclo(self):
        while self.attivo:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self.nuovo:
                self.frame, self.conta = frame, self.conta + 1
                self.nuovo.notify_all()

    def read(self, timeout=2.0):
        with self.nuovo:
            if not self.nuovo.wait_for(lambda: self.conta > self.visti, timeout):
                return False, None
            self.visti = self.conta
            return True, self.frame

    def stop(self):
        self.attivo = False
        self.thread.join(timeout=2)


def ruota(immagine, gradi):
    codici = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return cv2.rotate(immagine, codici[gradi]) if gradi in codici else immagine


def macchia_piu_grande(maschera):
    """Frazione dell'immagine coperta dalla macchia in movimento più grande (piccoli movimenti sparsi non contano)."""
    contorni, _ = cv2.findContours(maschera, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max((cv2.contourArea(c) for c in contorni), default=0.0) / maschera.size


def differenza_dal_fondo(fondo, frame):
    """Frazione dell'immagine coperta dalla macchia più grande diversa dalla scena normale (vale anche per una cosa
    ferma, che il rilevatore di movimento dopo un po' considera sfondo)."""
    a = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(fondo, (640, 480)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
    b = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(frame, (640, 480)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, diversa = cv2.threshold(cv2.absdiff(a, b), 30, 255, cv2.THRESH_BINARY)
    return macchia_piu_grande(cv2.dilate(cv2.erode(diversa, None, iterations=1), None, iterations=2))


def pianta_con_zone(mappa, evidenzia=None, percorso=None, meta=None, drone=None, scala=5, titolo=None, etichette=None):
    """Pianta con le zone (rettangoli e nomi), il pad, il drone (posa (x, y, direzione); predefinito sul pad) e, se
    dati, la zona scelta, la meta e il percorso. etichette: {zona: testo in più sotto il nome}."""
    bordo = 40
    immagine = mappa.disegna(scala=scala, percorso=percorso, drone=drone or BASE,
                             punti={"pad": BASE[:2], **({f"meta {evidenzia}": meta} if meta else {})},
                             titolo=titolo or "Pianta del capannone (metri). Nero = pareti e pilastri, azzurro = vetrate")
    righe = mappa.occupata.shape[0]

    def px(x, y):
        return int(bordo + (x - mappa.x0) / mappa.ris * scala), int(bordo + (mappa.y0 + righe * mappa.ris - y) / mappa.ris * scala)

    for z in sito.zone():
        colore = (0, 0, 255) if z["id"] == evidenzia else tuple(int(c * 0.7) for c in reversed(z.get("color", (150,) * 3)))
        spessore = 3 if z["id"] == evidenzia else 1
        angolo = px(z["xmin"], z["ymax"])
        cv2.rectangle(immagine, angolo, px(z["xmax"], z["ymin"]), colore, spessore)
        cv2.putText(immagine, z["id"], (angolo[0] + 4, angolo[1] + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, colore, 2)
        if etichette and etichette.get(z["id"]):
            cv2.putText(immagine, etichette[z["id"]], (angolo[0] + 4, angolo[1] + 38), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        colore, 1)
    return immagine


# ---------------- Grok ----------------
def leggi_chiave():
    """Chiave xAI da XAI_API_KEY o da FILE_CHIAVE (fuori da OneDrive). Mai scritta nel codice o nei log."""
    chiave = os.environ.get("XAI_API_KEY", "").strip()
    if not chiave and os.path.isfile(FILE_CHIAVE):
        with open(FILE_CHIAVE, encoding="utf-8") as f:
            chiave = f.readline().strip()
    return chiave


def b64(immagine, lato=1024):
    alto, largo = immagine.shape[:2]
    scala = min(1.0, lato / max(alto, largo))
    if scala < 1:
        immagine = cv2.resize(immagine, (round(largo * scala), round(alto * scala)))
    return base64.b64encode(cv2.imencode(".jpg", immagine, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()).decode()


def verifica_con_grok(client, fondo, allarme, foto_drone, numero):
    """Grok confronta la telecamera di sorveglianza (scena normale e allarme) con le foto del drone.
    Restituisce {esito: 'ANOMALY DETECTED' | 'FALSO ALLARME' | 'VERIFICA MANUALE', descrizione, foto, confidenza}."""
    testo = (
        f"Sei il sistema di sorveglianza di un capannone. La telecamera numero {numero} ha visto un movimento: ti mando "
        "la sua immagine della scena normale (prima del movimento) e quella dell'allarme. Poi un drone è andato sul "
        "posto e ha scattato 4 foto girandosi di 90 gradi alla volta (est, nord, ovest, sud). Confronta: che cosa è "
        "cambiato nella scena della telecamera (persona, oggetto spostato o nuovo, porta aperta...)? Le foto del drone "
        "mostrano la stessa cosa? Ignora il drone e il suo pad. Rispondi SOLO con un oggetto JSON con i campi: esito "
        "(\"ANOMALY DETECTED\" se le foto del drone confermano l'anomalia vista dalla telecamera, \"FALSO ALLARME\" "
        "se la telecamera non mostra niente di anomalo o le foto del drone non la confermano), descrizione (italiano, "
        "2-3 frasi: cosa è successo e cosa si vede nelle foto del drone), foto (lista dei lati in cui si vede "
        "l'anomalia: est, nord, ovest, sud), confidenza (0-1).")
    contenuto = [{"type": "text", "text": testo}]
    immagini = ([("Telecamera, scena normale:", fondo)] if fondo is not None else []) + [("Telecamera, allarme:", allarme)]
    immagini += [(f"Foto del drone verso {lato}:", cv2.imread(f)) for lato, f in foto_drone]
    for didascalia, immagine in immagini:
        if immagine is not None:
            contenuto += [{"type": "text", "text": didascalia},
                          {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64(immagine)}"}}]
    try:
        risposta = client.chat.completions.create(model=isp.MODELLO_GROK, response_format={"type": "json_object"},
                                                  messages=[{"role": "user", "content": contenuto}])
        r = json.loads(risposta.choices[0].message.content)
    except Exception as e:
        return {"esito": "VERIFICA MANUALE", "descrizione": f"Grok non ha risposto: {e}", "foto": [], "confidenza": 0.0}
    esito = str(r.get("esito", "")).strip().upper()
    if esito not in ("ANOMALY DETECTED", "FALSO ALLARME"):
        esito = "VERIFICA MANUALE"
    try:
        confidenza = min(1.0, max(0.0, float(r.get("confidenza", 0))))
    except (TypeError, ValueError):
        confidenza = 0.0
    return {"esito": esito, "descrizione": str(r.get("descrizione", "")), "foto": r.get("foto", []),
            "confidenza": confidenza}


def descrivi_evento(client, fondo, allarme):
    """Grok confronta la scena normale con l'allarme (come camera.py del team). Mai bloccante per l'operatore."""
    testo = ("Sei un agente di sicurezza. Ti mando due immagini della stessa telecamera fissa: 1) scena normale, "
             "2) adesso. Che cosa è cambiato (persone, oggetti nuovi o spariti, porte aperte, sedie spostate)? "
             "Rispondi SOLO con un oggetto JSON: deviation_detected (true/false), changes_detected (lista di brevi "
             "frasi in italiano), reasoning (italiano, una frase), severity (\"low\", \"medium\" o \"high\").")
    contenuto = [{"type": "text", "text": testo}]
    for didascalia, immagine in (("1) scena normale:", fondo), ("2) adesso:", allarme)):
        contenuto += [{"type": "text", "text": didascalia},
                      {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64(immagine, 800)}"}}]
    inizio = time.time()
    try:
        risposta = client.chat.completions.create(model=isp.MODELLO_GROK, response_format={"type": "json_object"},
                                                  messages=[{"role": "user", "content": contenuto}])
        r = json.loads(risposta.choices[0].message.content)
    except Exception as e:
        return {"deviation_detected": None, "changes_detected": [], "reasoning": f"Grok non ha risposto: {e}",
                "severity": "medium", "grok_s": round(time.time() - inizio, 1)}
    severita = str(r.get("severity", "medium")).lower()
    cambi = r.get("changes_detected", [])
    return {"deviation_detected": r.get("deviation_detected") in (True, "true"),
            "changes_detected": [str(c) for c in cambi] if isinstance(cambi, list) else [str(cambi)],
            "reasoning": str(r.get("reasoning", "")),
            "severity": severita if severita in PRIORITA else "medium", "grok_s": round(time.time() - inizio, 1)}


# ---------------- la centrale ----------------
class Centrale:
    def __init__(self, telecamere, volo, drone, mappa, client=None, finte=False, cartella=CARTELLA):
        self.telecamere = {c["id"]: dict(c, stato="avvio", ultimo=None, pausa_fino=0.0) for c in telecamere}
        self.volo, self.drone, self.mappa, self.client, self.finte = volo, drone, mappa, client, finte
        self.cartella = cartella
        self.frames = os.path.join(cartella, "frames")
        self.file_eventi = os.path.join(cartella, "logs", "events.json")
        os.makedirs(self.frames, exist_ok=True)
        os.makedirs(os.path.dirname(self.file_eventi), exist_ok=True)
        self.lock = threading.RLock()
        self.pending, self.coda, self.contatore = {}, [], itertools.count()
        self.missione, self.ultime_missioni, self.eventi = None, [], []
        self.fine, self.nuovo_compito = threading.Event(), threading.Event()
        self.bloccato = False                               # dopo un'emergenza: niente più voli
        self._mappa_cache = (0.0, None)
        self.telemetria = {}
        self.avvisi, self.contatore_avvisi = [], itertools.count(1)   # notifiche per la dashboard (anomalie di Grok)
        volo.cartella_foto = self.frames

    # --- registro eventi (stesso formato di webhook.py: una riga JSON con time ed event) ---
    def evento(self, nome, **dati):
        riga = {"time": adesso(), "event": nome, "level": LIVELLI.get(nome, "info"), **dati}
        with self.lock:
            with open(self.file_eventi, "a", encoding="utf-8") as f:
                f.write(json.dumps(riga, ensure_ascii=False) + "\n")
            self.eventi.append(riga)
            del self.eventi[:-200]
        if nome != "telemetry":
            print(f"[{riga['time'][11:]}] {nome} " + " ".join(f"{k}={v}" for k, v in dati.items()
                                                               if k not in ("photo", "photo_normale")))
        return riga

    # --- notifiche per la dashboard: Grok ha visto un'anomalia (la dashboard le mostra e le tiene finché non le chiudi) ---
    def avvisa(self, tipo, testo, **dati):
        with self.lock:
            avviso = {"id": next(self.contatore_avvisi), "tipo": tipo, "testo": testo, "time": adesso(), **dati}
            self.avvisi.append(avviso)
            del self.avvisi[:-30]
        return avviso

    # --- telemetria: dallo stato che il Tello manda da solo (nessun comando in più) ---
    def leggi_telemetria(self):
        try:
            s = self.volo.stato()
        except Exception:
            s = {}
        x, y, direzione = self.volo.posa_mappa() or BASE
        numero = lambda chiave: s.get(chiave) if isinstance(s.get(chiave), (int, float)) else None
        vgx, vgy = numero("vgx") or 0, numero("vgy") or 0
        self.telemetria = {
            "fase": self.volo.fase, "in_volo": bool(self.volo.in_volo), "batteria": numero("bat"),
            "quota_cm": numero("h"), "tof_basso_cm": numero("tof"), "temperatura": numero("temph"),
            "x_m": round(x, 2), "y_m": round(y, 2), "direzione": round(direzione) % 360,
            "velocita_cm_s": round(math.hypot(vgx, vgy) * 10, 1)}   # vgx, vgy in dm/s
        return self.telemetria

    def registra_telemetria(self):
        """Thread: aggiorna la telemetria per la console ogni secondo e la scrive nel registro ogni TELEMETRIA_S in volo."""
        ultimo = 0.0
        while not self.fine.wait(1.0):
            dati = self.leggi_telemetria()
            if self.volo.in_volo and time.time() - ultimo >= TELEMETRIA_S:
                m = self.missione
                self.evento("telemetry", task_id=m and m["task_id"], target_zone=m and m["target_zone"], **dati)
                ultimo = time.time()

    # --- telecamere ---
    def _apri(self, cam):
        if self.finte:
            cam["finta"] = cam.get("finta") or CameraFinta(cam["nome"])
            cam["finta"].aperta = True
            return cam["finta"]
        return cv2.VideoCapture(cam["url"])

    def sorveglia(self, cam):
        """Un thread per telecamera: allarme per una macchia grande (AREA_MINIMA), scattato ATTESA_INGRESSO_S dopo e
        solo se la scena è ancora diversa da quella normale."""
        while not self.fine.is_set():
            cap = self._apri(cam)
            if not cap.isOpened():
                if cam["stato"] != "non raggiungibile":
                    self.evento("camera_error", camera_id=cam["id"], url=cam["url"])
                cam["stato"] = "non raggiungibile"
                self.fine.wait(5)
                continue
            lettore = UltimoFotogramma(cap)
            cam["stato"] = "online"
            self.evento("camera_online", camera_id=cam["id"], target_zone=cam["zona"])
            sottrattore = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
            avvio, ultimo_ok, fondo, visto = time.time(), time.time(), None, None
            while not self.fine.is_set():
                ok, frame = lettore.read(timeout=1.0)
                if not ok:
                    if time.time() - ultimo_ok > 10:
                        self.evento("camera_error", camera_id=cam["id"], motivo="nessuna immagine da 10 s")
                        cam["stato"] = "senza immagini"
                        break
                    continue
                ultimo_ok = time.time()
                frame = ruota(frame, cam.get("ruota", 0))
                cam["ultimo"] = frame
                maschera = sottrattore.apply(cv2.GaussianBlur(cv2.resize(frame, (640, 480)), (5, 5), 0))
                _, maschera = cv2.threshold(maschera, 254, 255, cv2.THRESH_BINARY)
                maschera = cv2.dilate(cv2.erode(maschera, None, iterations=1), None, iterations=2)
                if time.time() - avvio < RISCALDAMENTO_S or time.time() < cam["pausa_fino"]:
                    fondo, visto = frame, None
                    continue
                area = macchia_piu_grande(maschera)
                if visto is None:
                    if area >= AREA_MINIMA:
                        visto = time.time()
                    else:
                        fondo = frame
                elif time.time() - visto >= ATTESA_INGRESSO_S:
                    if fondo is None or differenza_dal_fondo(fondo, frame) >= AREA_MINIMA / 2:
                        self.nuovo_allarme(cam, frame, fondo, area)
                        cam["pausa_fino"] = time.time() + PAUSA_CAMERA_S
                    visto = None
            lettore.stop()
            cap.release()

    def nuovo_allarme(self, cam, frame, fondo, area):
        ora = time.strftime("%Y%m%d_%H%M%S")
        allarme = f"{ora}_{cam['id']}_allarme.jpg"
        cv2.imwrite(os.path.join(self.frames, allarme), frame)
        normale = None
        if fondo is not None:
            normale = f"{ora}_{cam['id']}_normale.jpg"
            cv2.imwrite(os.path.join(self.frames, normale), fondo)
        self.evento("camera_input", camera_id=cam["id"], camera=cam["nome"], target_zone=cam["zona"],
                    severity="medium", deviation_detected=True, photo=allarme, photo_normale=normale,
                    area=round(area, 3), area_percento=round(area * 100, 1))
        with self.lock:
            m = self.missione
            if m and m["target_zone"] == cam["zona"]:          # vede il nostro drone: come webhook.py
                self.evento("ignored_own_drone", camera_id=cam["id"], target_zone=cam["zona"])
                return None
            tid = uuid.uuid4().hex[:8]
            self.pending[tid] = {
                "task_id": tid, "camera_id": cam["id"], "camera": cam["nome"], "target_zone": cam["zona"],
                "severity": "medium", "priority": PRIORITA["medium"], "order": next(self.contatore),
                "changes_detected": [f"qualcosa di grande ({area:.0%} dell'immagine)"],
                "reasoning": "Grok sta guardando..." if self.client else "Grok non configurato",
                "photo": allarme, "photo_normale": normale, "time": adesso(), "creato": time.time(),
                "area_percento": round(area * 100, 1), "grok_deviation": None}
            self.evento("awaiting_decision", task_id=tid, camera_id=cam["id"], camera=cam["nome"],
                        target_zone=cam["zona"], severity="medium", priority=PRIORITA["medium"], photo=allarme,
                        area_percento=round(area * 100, 1))
        if self.client is not None and fondo is not None:
            threading.Thread(target=self._grok_evento, args=(tid, fondo, frame), daemon=True).start()
        return tid

    def _grok_evento(self, tid, fondo, frame):
        r = descrivi_evento(self.client, fondo, frame)
        with self.lock:
            compito = self.pending.get(tid) or {}
            if tid in self.pending:
                compito.update(changes_detected=r["changes_detected"] or compito["changes_detected"],
                               reasoning=r["reasoning"], severity=r["severity"], priority=PRIORITA[r["severity"]],
                               grok_deviation=r["deviation_detected"], grok_s=r["grok_s"])
        anomalia = {True: "si", False: "no"}.get(r["deviation_detected"], "errore")
        self.evento("grok_analysis", task_id=tid, camera_id=compito.get("camera_id"), camera=compito.get("camera"),
                    target_zone=compito.get("target_zone"), deviation_detected=r["deviation_detected"],
                    anomalia=anomalia, severity=r["severity"], changes_detected="; ".join(r["changes_detected"]),
                    reasoning=r["reasoning"], grok_s=r["grok_s"])
        if r["deviation_detected"] is True:
            self.avvisa("grok_anomalia", f"Grok ha visto un'anomalia: {compito.get('camera', '?')} → "
                        f"{compito.get('target_zone', '?')}. {r['reasoning']}",
                        task_id=tid, target_zone=compito.get("target_zone"), camera=compito.get("camera"))

    # --- decisioni e coda (come webhook.py) ---
    def decisione(self, tid, scelta):
        with self.lock:
            compito = self.pending.pop(tid, None)
            if compito is None:
                return {"error": "not found"}, 404
            self.evento("final_decision", task_id=tid, target_zone=compito["target_zone"],
                        camera_id=compito["camera_id"], camera=compito.get("camera"), priority=compito["priority"],
                        severity=compito.get("severity"), decision=scelta,
                        grok_anomalia={True: "si", False: "no"}.get(compito.get("grok_deviation"), "nd"),
                        tempo_decisione_s=round(time.time() - compito.get("creato", time.time()), 1))
            if scelta != "yes":
                return {"status": "rejected"}, 200
            return {"status": self._metti_in_coda(compito)}, 200

    def comando(self, zona):
        if zona not in zone_ids():
            return {"error": f"zona sconosciuta: {zona}"}, 400
        compito = {"task_id": uuid.uuid4().hex[:8], "camera_id": "operator", "camera": "operatore",
                   "target_zone": zona, "priority": PRIORITA_MANUALE, "order": next(self.contatore), "time": adesso()}
        self.evento("manual_command", task_id=compito["task_id"], target_zone=zona)
        with self.lock:
            return {"status": self._metti_in_coda(compito)}, 200

    def _metti_in_coda(self, compito):
        if self.bloccato:
            self.evento("task_refused", task_id=compito["task_id"], motivo="emergenza usata: riavvia la centrale")
            return "refused_after_emergency"
        heapq.heappush(self.coda, (-compito["priority"], compito["order"], compito))
        stato = "dispatched" if self.missione is None and len(self.coda) == 1 else "queued"
        if stato == "queued":
            self.evento("task_queued", task_id=compito["task_id"], target_zone=compito["target_zone"],
                        priority=compito["priority"], queue_size=len(self.coda))
        self.nuovo_compito.set()
        return stato

    # --- il drone ---
    def lavora(self):
        """Thread del drone: una missione alla volta, dalla coda per priorità."""
        while not self.fine.is_set():
            self.nuovo_compito.wait(0.5)
            with self.lock:
                if not self.coda or self.missione is not None:
                    self.nuovo_compito.clear()
                    continue
                _, _, compito = heapq.heappop(self.coda)
                self.missione = compito
            try:
                self.esegui(compito)
            except Exception as e:
                self.evento("mission_error", task_id=compito["task_id"], errore=str(e))
            finally:
                with self.lock:
                    self.missione = None
                    if not self.coda:
                        self.evento("drone_idle")

    def esegui(self, compito):
        zona, tid = compito["target_zone"], compito["task_id"]
        if self.bloccato:
            self.evento("task_refused", task_id=tid, motivo="emergenza usata")
            return
        try:
            batteria = self.volo.batteria()
        except Exception as e:
            self.evento("mission_aborted", task_id=tid, target_zone=zona, motivo=f"batteria non letta: {e}")
            return
        if batteria < BATTERIA_MINIMA_MISSIONE:
            self.evento("mission_aborted", task_id=tid, target_zone=zona, motivo=f"batteria {batteria}%")
            return
        punto = giro_punti.leggi_tappe([zona], self.mappa, BASE)[0][1]
        compito["punto"] = [round(v, 2) for v in punto]
        andata = self.mappa.percorso(BASE[:2], punto) or []
        distanza = sum(math.dist(p, q) for p, q in zip(andata, andata[1:]))
        self.evento("drone_dispatched", task_id=tid, target_zone=zona, camera_id=compito["camera_id"],
                    camera=compito.get("camera"), priority=compito["priority"], battery=batteria,
                    punto_m=compito["punto"], percorso_andata_m=round(distanza, 1),
                    quota_cm=giro_punti.QUOTA_GIRO_CM)
        inizio = time.time()
        raggiunte = self.volo.esegui_giro([(zona, punto)], scatta=True, quattro_lati=True)
        durata = time.time() - inizio
        foto = [f for f in glob.glob(os.path.join(self.frames, f"*_tappa1_{isp.nome_file(zona)}_*.jpg"))
                if os.path.getmtime(f) >= inizio - 1]
        foto = [(os.path.splitext(f)[0].rsplit("_", 1)[1], f) for f in sorted(foto)]
        qualita = []
        for lato, f in foto:
            originale = cv2.imread(os.path.join(os.path.dirname(f), "originali", os.path.basename(f)))
            q = isp.qualita_foto(originale if originale is not None else cv2.imread(f))
            qualita.append(q)
            self.evento("drone_photo", task_id=tid, target_zone=zona, lato=lato, photo=os.path.basename(f),
                        nitidezza=round(q["nitidezza"]), blocchi=round(q["blocchi"], 2), luce=round(q["luce"]))
        try:
            batteria_fine = self.volo.batteria()
        except Exception:
            batteria_fine = None
        missione = {"task_id": tid, "target_zone": zona, "camera": compito.get("camera"), "time": adesso(),
                    "foto": [{"lato": lato, "photo": os.path.basename(f), "luce": round(q["luce"]),
                              "nitidezza": round(q["nitidezza"])} for (lato, f), q in zip(foto, qualita)],
                    "photo_camera": compito.get("photo"), "raggiunta": bool(raggiunte), "verdetto": None,
                    "durata_s": round(durata), "batteria_inizio": batteria, "batteria_fine": batteria_fine,
                    "pad_visto": self.volo.pad_visto}
        self.evento("mission_completed", task_id=tid, target_zone=zona, camera=compito.get("camera"),
                    reached=bool(raggiunte), photos=len(foto), landed=self.volo.in_volo is False,
                    pad_visto=self.volo.pad_visto, durata_s=round(durata), batteria_inizio=batteria,
                    batteria_fine=batteria_fine,
                    luce_media=round(sum(q["luce"] for q in qualita) / len(qualita)) if qualita else None)
        if self.client is not None and foto and compito.get("photo"):
            allarme = cv2.imread(os.path.join(self.frames, compito["photo"]))
            normale = (cv2.imread(os.path.join(self.frames, compito["photo_normale"]))
                       if compito.get("photo_normale") else None)
            partenza = time.time()
            verdetto = verifica_con_grok(self.client, normale, allarme, foto, compito.get("camera", "?"))
            verdetto["grok_s"] = round(time.time() - partenza, 1)
            missione["verdetto"] = verdetto
            self.evento("verdict", task_id=tid, target_zone=zona, camera=compito.get("camera"),
                        esito=verdetto["esito"], descrizione=verdetto["descrizione"],
                        confidenza=verdetto["confidenza"], foto_con_anomalia=", ".join(map(str, verdetto.get("foto") or [])),
                        grok_s=verdetto["grok_s"])
            if verdetto["esito"] == "ANOMALY DETECTED":
                self.avvisa("anomalia_confermata", f"ANOMALIA CONFERMATA in {zona} ({compito.get('camera', '?')}), "
                            f"confidenza {round(verdetto['confidenza'] * 100)}%: {verdetto['descrizione']}",
                            task_id=tid, target_zone=zona, camera=compito.get("camera"))
        with self.lock:
            self.ultime_missioni.insert(0, missione)
            del self.ultime_missioni[10:]

    def stop(self):
        self.evento("stop_pressed", in_volo=bool(self.volo.in_volo))
        with self.lock:
            self.coda.clear()
        self.volo.atterra_subito()

    def emergenza(self):
        self.evento("emergency_pressed", in_volo=bool(self.volo.in_volo))
        self.bloccato = True
        with self.lock:
            self.coda.clear()
        self.volo.atterra.set()
        try:
            self.drone.emergency()
        except Exception as e:
            print("emergency non mandato:", e)
        self.volo.in_volo = False

    # --- stato per la dashboard ---
    def stato(self):
        with self.lock:
            coda = [c for _, _, c in sorted(self.coda)]
            m = self.missione
            return {
                "drone_busy": m is not None, "mission": m and {k: m.get(k) for k in ("task_id", "target_zone", "camera")},
                "queue": [{"zone": c["target_zone"], "priority": c["priority"]} for c in coda],
                "pending": len(self.pending), "fase": self.volo.fase, "in_volo": bool(self.volo.in_volo),
                "batteria": self.telemetria.get("batteria") or self.volo.livello_batteria,
                "telemetria": self.telemetria, "bloccato": self.bloccato, "finte": self.finte,
                "posa": [round(v, 2) for v in (self.volo.posa_mappa() or BASE)],
                "telecamere": [{"id": c["id"], "nome": c["nome"], "zona": c["zona"], "stato": c["stato"]}
                               for c in self.telecamere.values()],
                "zone": zone_ids(), "ultime_missioni": self.ultime_missioni[:5],
                "avvisi": list(reversed(self.avvisi[-10:]))}

    def in_attesa(self):
        with self.lock:
            return sorted(self.pending.values(), key=lambda t: (-t["priority"], t["order"]))

    def mappa_png(self):
        if time.time() - self._mappa_cache[0] < 1.0 and self._mappa_cache[1] is not None:
            return self._mappa_cache[1]
        m = self.missione
        etichette = {}
        for c in self.telecamere.values():
            etichette[c["zona"]] = (etichette.get(c["zona"], "") + " " + c["nome"]).strip()
        immagine = pianta_con_zone(self.mappa, evidenzia=m and m["target_zone"],
                                      percorso=self.volo.percorso_mappa if self.volo.in_volo else None,
                                      drone=self.volo.posa_mappa() or BASE, scala=4, etichette=etichette,
                                      titolo=f"Drone: {self.volo.fase}")
        dati = cv2.imencode(".png", immagine)[1].tobytes()
        self._mappa_cache = (time.time(), dati)
        return dati

    def camera_jpg(self, cid):
        cam = self.telecamere.get(cid)
        if not cam or cam["ultimo"] is None:
            return None
        return cv2.imencode(".jpg", cv2.resize(cam["ultimo"], (480, 360)), [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()

    def simula(self, cid):
        cam = self.telecamere.get(cid)
        if not cam or not cam.get("finta"):
            return {"error": "solo con --camere-finte"}, 400
        cam["finta"].evento()
        return {"status": "evento simulato", "camera_id": cid}, 200

    def avvia(self):
        for cam in self.telecamere.values():
            threading.Thread(target=self.sorveglia, args=(cam,), daemon=True).start()
        threading.Thread(target=self.lavora, daemon=True).start()
        threading.Thread(target=self.registra_telemetria, daemon=True).start()
        self.evento("centrale_started", cameras={c["id"]: c["zona"] for c in self.telecamere.values()},
                    simulated_drone=not hasattr(self.drone, "background_frame_read"))

    def chiudi(self):
        self.fine.set()
        if self.volo.in_volo:
            self.volo.atterra_subito()
            for _ in range(100):
                if not self.volo.in_volo:
                    break
                time.sleep(0.1)


# ---------------- server web della dashboard ----------------
def crea_server(centrale, host="127.0.0.1", porta=5000):
    pagina = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard", "index.html")

    class Gestore(BaseHTTPRequestHandler):
        def log_message(self, *argomenti):
            pass

        def _invia(self, corpo, tipo="application/json", codice=200):
            if isinstance(corpo, (dict, list)):
                corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
                tipo = "application/json; charset=utf-8"
            self.send_response(codice)
            self.send_header("Content-Type", tipo)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                self.wfile.write(corpo)
            except (ConnectionError, OSError):                   # il browser ha chiuso (es. immagine non più visibile)
                pass

        def _json(self):
            lunghezza = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(lunghezza) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            percorso = unquote(urlparse(self.path).path)
            if percorso in ("/", "/index.html"):
                with open(pagina, "rb") as f:
                    return self._invia(f.read(), "text/html; charset=utf-8")
            if percorso == "/status":
                return self._invia(centrale.stato())
            if percorso == "/pending":
                return self._invia(centrale.in_attesa())
            if percorso == "/eventi":
                with centrale.lock:
                    return self._invia(centrale.eventi[-40:][::-1])
            if percorso == "/mappa.png":
                return self._invia(centrale.mappa_png(), "image/png")
            if percorso.startswith("/camera/"):
                dati = centrale.camera_jpg(percorso.rsplit("/", 1)[1].removesuffix(".jpg"))
                return self._invia(dati, "image/jpeg") if dati else self._invia({"error": "nessuna immagine"}, codice=404)
            if percorso.startswith("/frames/"):
                nome = os.path.basename(percorso)                  # niente ../: solo file della cartella frames
                file = os.path.join(centrale.frames, nome)
                if nome and os.path.isfile(file):
                    with open(file, "rb") as f:
                        return self._invia(f.read(), "image/jpeg")
            return self._invia({"error": "not found"}, codice=404)

        def do_POST(self):
            percorso, dati = urlparse(self.path).path, self._json()
            if percorso == "/decision":
                corpo, codice = centrale.decisione(dati.get("task_id"), dati.get("decision"))
            elif percorso == "/command":
                corpo, codice = centrale.comando(str(dati.get("target_zone", "")))
            elif percorso == "/stop":
                centrale.stop()
                corpo, codice = {"status": "atterraggio"}, 200
            elif percorso == "/emergenza":
                centrale.emergenza()
                corpo, codice = {"status": "motori spenti"}, 200
            elif percorso == "/simula":
                corpo, codice = centrale.simula(str(dati.get("camera_id", "")))
            else:
                corpo, codice = {"error": "not found"}, 404
            return self._invia(corpo, codice=codice)

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], (ConnectionError, OSError)):
                super().handle_error(request, client_address)

    return Server((host, porta), Gestore)


# ---------------- avvio ----------------
def configura_telecamere(chiedi=input, file=FILE_CAMERE):
    """Per ogni telecamera fissa chiede la zona che inquadra (Invio = proposta: quella dell'ultima volta)."""
    salvate = {}
    if os.path.isfile(file):
        with open(file, encoding="utf-8") as f:
            salvate = json.load(f)
    nomi = zone_ids()
    print("Zone: " + ", ".join(nomi))
    camere = []
    for c in TELECAMERE:
        c = dict(c, **{k: v for k, v in salvate.get(c["id"], {}).items() if k in ("zona", "url", "ruota")})
        while True:
            risposta = chiedi(f"{c['nome']} ({c['url']}) inquadra la zona [{c['zona']}]: ").strip().upper()
            if not risposta or risposta in nomi:
                c["zona"] = risposta or c["zona"]
                break
            print(f"Zona {risposta} sconosciuta")
        camere.append(c)
    os.makedirs(os.path.dirname(file), exist_ok=True)
    with open(file, "w", encoding="utf-8") as f:
        json.dump({c["id"]: {k: c[k] for k in ("zona", "url", "ruota")} for c in camere}, f, indent=2)
    return camere


def prepara_drone(arm, usa_tof):
    mappa = giro_punti.prepara_pianta()
    if arm:
        import logging
        from djitellopy import Tello
        Tello.LOGGER.setLevel(logging.WARNING)
        Tello.FRAME_GRAB_TIMEOUT = 10
        drone = Tello(retry_count=1)
    else:
        from simulatore import DroneSimulato
        drone = DroneSimulato(mappa, BASE, rumore=0.05, batteria=100)
    volo = isp.Ispezione(drone, None, FRAMES, os.path.join(CARTELLA, "registro_volo.jsonl"), mappa=mappa, base=BASE,
                         zone=sito.zone())
    volo.quota_volo_cm, volo.usa_tof = giro_punti.QUOTA_GIRO_CM, usa_tof
    if arm:
        try:
            giro_punti.prepara(volo)
        except Exception as e:
            sys.exit(f"Drone non pronto: {e}. Il PC è collegato al Wi-Fi del drone?")
    else:
        isp.ATTESA_STABILIZZAZIONE_S = isp.PAUSA_YAW_S = 0.0
    return drone, volo, mappa


def indirizzi_di_rete():
    """Indirizzi IPv4 del PC sulle reti locali (quelli da aprire dal telefono), senza 127.x e 169.254.x.
    Primo quello della rete principale (verso Internet), poi gli altri, tra cui gli adattatori virtuali (WSL, Hyper-V)."""
    import socket
    try:
        tutti = {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        tutti = set()
    principale = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))                       # nessun pacchetto inviato: sceglie solo l'interfaccia
            principale = s.getsockname()[0]
    except OSError:
        pass
    altri = sorted(ip for ip in tutti - {principale} if not ip.startswith(("127.", "169.254.")))
    return ([principale] if principale else []) + altri


def main(argomenti):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arm", action="store_true", help="drone vero")
    parser.add_argument("--senza-tof", action="store_true", help="sensore frontale guasto: ignoralo")
    parser.add_argument("--camere-finte", action="store_true", help="telecamere simulate (eventi dalla dashboard)")
    parser.add_argument("--senza-grok", action="store_true", help="niente descrizioni e verdetti di Grok")
    parser.add_argument("--senza-domande", action="store_true", help="zone delle telecamere dall'ultima volta")
    parser.add_argument("--host", default="127.0.0.1",
                        help="127.0.0.1 = dashboard solo da questo PC; 0.0.0.0 = da tutta la rete (chiunque può "
                             "mandare il drone)")
    parser.add_argument("--telefono", action="store_true",
                        help="come --host 0.0.0.0: dashboard da telefono o tablet sulla stessa rete Wi-Fi")
    parser.add_argument("--porta", type=int, default=5000)
    a = parser.parse_args(argomenti)
    if a.telefono:
        a.host = "0.0.0.0"
    camere = configura_telecamere((lambda _: "") if a.senza_domande else input)
    client = None
    if not a.senza_grok:
        chiave = leggi_chiave()
        if chiave:
            from openai import OpenAI
            client = OpenAI(api_key=chiave, base_url="https://api.x.ai/v1", timeout=90, max_retries=0)
        else:
            print(f"Chiave xAI non trovata (XAI_API_KEY o {FILE_CHIAVE}): niente Grok")
    drone, volo, mappa = prepara_drone(a.arm, not a.senza_tof)
    if a.arm:
        print(giro_punti.POSIZIONE_DI_PARTENZA)
        if input("\nParaeliche montate, drone sul pad, NESSUNO nella sala lungo i percorsi (a 1,7 m il drone è "
                 "all'altezza della testa)? Scrivi si per avviare la centrale: ").strip().lower() not in ("si", "sì"):
            drone.end()
            sys.exit("Annullato")
    centrale = Centrale(camere, volo, drone, mappa, client, finte=a.camere_finte)
    centrale.avvia()
    fine_tasti = threading.Event()
    if a.arm and os.name == "nt":
        threading.Thread(target=giro_punti.sorveglia_tasti, args=(volo, drone, fine_tasti), daemon=True).start()
    server = crea_server(centrale, a.host, a.porta)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\nDashboard: http://localhost:{a.porta}   (Grafana: cd dashboard; docker compose up -d -> "
          "http://localhost:3000/d/drone-konsol)")
    if a.host == "0.0.0.0":
        indirizzi = indirizzi_di_rete()
        print("Dal telefono, sulla stessa rete Wi-Fi del PC: " +
              (f"http://{indirizzi[0]}:{a.porta}" if indirizzi else "nessun indirizzo di rete trovato") +
              (f"   (se non va, altri indirizzi del PC: {', '.join(indirizzi[1:])})" if len(indirizzi) > 1 else "") +
              "\nAttenzione: chiunque su quella rete può aprire la dashboard e mandare il drone.")
    print("Ctrl+C per chiudere (il drone atterra).")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nChiusura...")
    finally:
        fine_tasti.set()
        centrale.chiudi()
        server.shutdown()
        if a.arm:
            try:
                drone.end()
            except Exception:
                pass
        giro_punti.svuota_tastiera()


if __name__ == "__main__":
    main(sys.argv[1:])

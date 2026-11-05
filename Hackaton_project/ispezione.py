"""
Missione semplice con il Tello Talent (RMTT), dal 5 novembre:
1. il drone parte dal pad (punto fisso di sito.py);
2. va al punto principale («hover» di zones.json) della zona in cui il telefono ha visto l'anomalia, con un percorso
   A* sulla pianta PAD2.step che evita pareti, pilastri e la persona stessa (segnata sulla pianta solo per questo volo);
3. gira il muso, e quindi la telecamera, verso l'anomalia; se la mira locale la vede corregge una volta; foto;
4. torna al pad sulla pianta e atterra. Grok descrive la foto mentre il drone rientra.
Niente avvicinamento alla persona, niente deviazioni laterali, niente istruzioni dopo la foto.
Il progetto finale (centrale.py) usa esegui_giro: dal pad al punto della zona, quattro foto, ritorno al pad.

Senza stima sulla pianta, o con la persona a meno di DISTANZA_MIN_PERSONA_M dal pad, il drone non si sposta:
si gira verso la direzione vista dal telefono, fotografa e riatterra.

Sicurezza: il sensore ToF frontale (1,2 m), se attivo, è letto prima di ogni passo; un ostacolo non in pianta viene segnato e il
percorso ricalcolato. Tasto l / STOP = atterra dove si trova; batteria sotto BATTERIA_RITORNO = torna al pad,
sotto BATTERIA_MIN = atterra dove si trova; un solo invio per comando.

Posizione stimata in cm nel sistema del pad: x in avanti (direzione del muso al decollo), y a sinistra.
Direzioni in gradi, 0 = direzione di decollo, positive in senso antiorario.
"""
import base64
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from mappa import scegli_punto_vista

# ---- Missione semplice ----
DISTANZA_MIN_PERSONA_M = 2.0    # il drone si ferma ad almeno questa distanza dalla stima della persona
RAGGIO_PERSONA_M = 1.0          # la stima diventa un ostacolo: il percorso le passa ad almeno 1,5 m (+ margine 0,5)
PASSO_MIN_CM = 20               # minimo ammesso dal drone
HFOV_GRADI = 70.0               # campo orizzontale stimato (82,6 gradi in diagonale): da verificare
CORREZIONE_MIN_GRADI = 5        # mire più piccole non si correggono

# ---- Ostacoli ----
MAX_DEVIAZIONI = 3              # ostacoli non in pianta: al massimo 3 ricalcoli del percorso
DISTANZA_SICUREZZA_CM = 40      # spazio libero richiesto oltre la lunghezza del passo
ATTESA_OSTACOLO_S = 8           # al ritorno: attesa che l'ostacolo si sposti prima di segnarlo e girargli attorno
ATTESA_OSTACOLO_ANDATA_S = 3    # all'andata

# ---- Volo e sicurezza ----
VELOCITA = 40                   # cm/s dei comandi forward: movimenti tranquilli
# Tratti con 'forward': la distanza la misura il Tello con la sua telecamera in basso. Il volo continuo con rc (fino al
# 5 novembre) la stimava sommando le velocità dello stato, in dm/s interi: lo stesso 'forward 80' risultava 50, 61 o
# 79 cm, e al ritorno il drone arrivava lungo. Alle 09:41 ha anche continuato ad andare a 30-40 cm/s per 10 s dopo la
# fine del tratto (con ogni probabilità l'rc 0 di frenata perso nel Wi-Fi): con 'forward' quel rischio non c'è.
PASSO_MAX_CM = 500              # senza ToF: un tratto = un 'forward' (massimo del Tello)
PASSO_CON_TOF_CM = 100          # con il ToF: passi da 1 m, spazio libero controllato prima di ognuno (portata 1,2 m)
VERSO_YAW = 1                   # verificato il 4 novembre: cw 30 -> yaw +30
TOLLERANZA_GRADI = 5             # rotazioni per le foto: una sola nella maggior parte dei casi
TOLLERANZA_ROTTA_GRADI = 1       # prima di un tratto: il 5 novembre dopo i 'cw' restavano 2-3 gradi verso sinistra,
                                 # cioè 12-18 cm di lato su 3,5 m (il drone tornava un po' a sinistra del pad)
PAUSA_YAW_S = 0.15
TIMEOUT_S = 20
BATTERIA_MIN = 20               # sotto: atterra dove si trova
BATTERIA_RITORNO = 30           # sotto: niente foto, torna al pad
INTERVALLO_BATTERIA_S = 20      # lettura della batteria anche durante i movimenti
INTERVALLO_KEEPALIVE_S = 4      # il Tello atterra da solo dopo 15 s senza comandi (attese di Grok)
ATTESA_STABILIZZAZIONE_S = 1.0  # prima di una foto: il drone si ferma dopo rotazioni e frenate (0,5 s: foto mosse)
FINESTRA_FOTO_S = 1.5           # si raccolgono i fotogrammi di questo intervallo e si salva il migliore
# video per foto migliori [ipotesi, comandi dell'SDK 3.0]: 720p, 15 fotogrammi al secondo e bitrate massimo (5 Mbps):
# più dati per fotogramma, meno blocchi di compressione. Se il Wi-Fi è debole il bitrate alto può perdere più pacchetti.
IMPOSTAZIONI_VIDEO = ("setresolution high", "setfps middle", "setbitrate 5")
QUOTA_VOLO_CM = 150             # quota di volo: sopra tavoli e sedie, sotto i soppalchi (2,15 m)
QUOTA_PAD_CM = 80               # il pad si vede solo tra 0,3 e 1,2 m: si scende prima di cercarlo
QUOTA_RICERCA_PAD_CM = 100      # più in alto il pad si vede in un'area più larga; h del Tello sbaglia di 10-20 cm,
                                # quindi non 120
ATTESA_PAD_S = 2.0
MODELLO_GROK = "grok-4.5"           # misurato il 4 novembre: circa 8 s per domanda in volo (grok-4.7 molto più lento)
TIMEOUT_GROK_S = 20             # in volo: oltre, si prosegue come senza Grok (il drone non resta sospeso)
# se la risposta a questi comandi si perde (Wi-Fi, 5 novembre) si rimisura quota o direzione dallo stato e si continua
COMANDI_RIMISURABILI = ("up", "down", "cw", "ccw")
VELOCITA_ROTAZIONE_GRADI_S = 45  # [ipotesi] prudente: serve solo a stimare quanto aspettare la risposta
MARGINE_RISPOSTA_S = 5          # attesa della risposta = durata prevista del comando + questo (prima: 20 s fissi)


class Interruzione(Exception):
    """Atterrare subito dove si è (tasto l, batteria minima)."""


class Bloccato(Exception):
    """Nessun passaggio libero: atterrare dove si è invece di forzare."""


class Ritorno(Exception):
    """Batteria sotto BATTERIA_RITORNO: si salta il resto della missione e si torna al pad."""


class RispostaPersa(RuntimeError):
    """Il Tello non ha risposto in tempo: il comando può essere stato eseguito o no."""


def nome_file(testo):
    """Nome di tappa usabile in un nome di file: es. '(-2,5; 3,0)' -> '-2_5_3_0'."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", testo).strip("_")


def qualita_foto(immagine):
    """Misure di un fotogramma. nitidezza: varianza del laplaciano a metà risoluzione (a metà i bordi dei blocchi
    contano meno). blocchi: salti di luminosità sui bordi della griglia 8 x 8 del video H.264 rispetto alla media
    (1 = nessun blocco; pacchetti persi -> 1,3 e oltre). luce: luminosità media (0-255). punteggio: nitidezza
    penalizzata dai blocchi, per scegliere il fotogramma da salvare."""
    grigio = cv2.cvtColor(immagine, cv2.COLOR_BGR2GRAY).astype(np.float32)
    dx, dy = np.abs(np.diff(grigio, axis=1)), np.abs(np.diff(grigio, axis=0))
    bordi = dx.mean() + dy.mean()
    blocchi = float((dx[:, 7::8].mean() + dy[7::8, :].mean()) / bordi) if bordi > 0.5 else 1.0   # piatta: neutro
    nitidezza = float(cv2.Laplacian(cv2.resize(grigio, None, fx=0.5, fy=0.5), cv2.CV_32F).var())
    return {"nitidezza": nitidezza, "blocchi": blocchi, "luce": float(grigio.mean()),
            "punteggio": nitidezza / max(blocchi, 1.0) ** 4}


def migliora_foto(immagine):
    """Correzione leggera per una sala poco illuminata (5 novembre: luce 23-56 su 255, dominante viola): poco rumore in
    meno, bilanciamento del bianco a mondo grigio con guadagni limitati (0,85-1,2), schiarita gamma 0,8. Correzioni più
    forti (contrasto locale, gamma 0,45) sono state provate sulle foto vere e le peggiorano: tirano fuori le macchie di
    rumore e i blocchi della compressione. Il dettaglio che manca lo dà solo più luce in sala."""
    pulita = cv2.fastNlMeansDenoisingColored(immagine, None, 4, 4, 7, 21).astype(np.float32)
    medie = pulita.reshape(-1, 3).mean(axis=0)
    pulita *= np.clip(medie.mean() / np.maximum(medie, 1.0), 0.85, 1.2)
    return (255 * (np.clip(pulita, 0, 255) / 255) ** 0.8).astype(np.uint8)


def durata_prevista(testo):
    """Secondi che il Tello dovrebbe impiegare per il comando (alla velocità VELOCITA)."""
    parti = testo.split()
    if parti[0] == "go":
        return 8.0
    try:
        valore = abs(float(parti[1])) if len(parti) > 1 else 0.0
    except ValueError:
        valore = 0.0
    if parti[0] in ("forward", "back", "left", "right", "up", "down"):
        return valore / VELOCITA
    if parti[0] in ("cw", "ccw"):
        return valore / VELOCITA_ROTAZIONE_GRADI_S
    return 0.0


def norma(angolo):
    return (angolo + 180) % 360 - 180


# ---------------- Zone: dove va il drone ----------------
def zona_della_persona(zone, persona):
    """Zona di zones.json che contiene la persona (a pari merito vince la priorità più alta); se è fuori da tutte,
    quella con il punto principale più vicino. Contano solo le zone in cui si vola alla quota di volo."""
    quota = QUOTA_VOLO_CM / 100
    zone = [z for z in zone or () if z.get("hover") and z.get("zmin", 0) <= quota <= z.get("zmax", 99)]
    dentro = [z for z in zone if z["xmin"] <= persona[0] <= z["xmax"] and z["ymin"] <= persona[1] <= z["ymax"]]
    if dentro:
        return max(dentro, key=lambda z: z.get("priority", 0))
    return min(zone, key=lambda z: math.dist(z["hover"][:2], persona), default=None)


def punto_principale(mappa, zone, persona, base):
    """Dove va il drone per guardare la persona: (punto (x, y) in m oppure None = resta sul pad, zona, motivo).
    Di norma il punto principale della zona. Se è a meno di DISTANZA_MIN_PERSONA_M dalla persona, fuori dalla zona
    sicura o da lì la persona non si vede, il punto più vicino a quella distanza dalla persona, dalla stessa parte."""
    if math.dist(persona, base[:2]) < DISTANZA_MIN_PERSONA_M:
        return None, None, f"persona a meno di {DISTANZA_MIN_PERSONA_M:.0f} m dal pad: la guardo da qui"
    zona = zona_della_persona(zone, persona)
    nome, verso, problema = (zona["id"] if zona else "nessuna"), None, "nessuna zona"
    if zona:
        h = tuple(zona["hover"][:2])
        r, c = mappa.cella(*h)
        if math.dist(h, persona) < DISTANZA_MIN_PERSONA_M:
            problema = f"punto principale a {math.dist(h, persona):.1f} m dalla persona"
        elif not (mappa.dentro(r, c) and mappa.raggiungibili(*base[:2])[r, c]):
            problema = "punto principale non raggiungibile"
        elif not mappa.linea_libera(h, persona):
            problema = "dal punto principale la persona è coperta"
        else:
            return h, nome, "punto principale della zona"
        if math.dist(h, persona) > 0.1:
            verso = math.degrees(math.atan2(h[1] - persona[1], h[0] - persona[0]))
    punto, _ = scegli_punto_vista(mappa, persona, DISTANZA_MIN_PERSONA_M, base[:2], verso)
    if punto is None:
        return None, nome, f"{problema} e nessun punto sicuro da cui vederla: la guardo dal pad"
    return punto, nome, f"{problema}: mi fermo a {math.dist(punto, persona):.1f} m da lei, dalla stessa parte"


# ---------------- Grok che guarda il fotogramma del drone ----------------
class VisioneGrok:
    def __init__(self, client, modello=MODELLO_GROK):
        self.client, self.modello = client, modello

    def _chiedi(self, frame, testo):
        jpeg = cv2.imencode(".jpg", cv2.resize(frame, (640, 480)), [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        risposta = self.client.responses.create(model=self.modello, store=False, input=[{"role": "user", "content": [
            {"type": "input_image", "detail": "high", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
            {"type": "input_text", "text": testo},
        ]}])
        return risposta.output_text

    def _json(self, frame, testo):
        m = re.search(r"\{.*\}", self._chiedi(frame, testo + "\nRispondi SOLO con un oggetto JSON."), re.S)
        if not m:
            raise ValueError("risposta senza JSON")
        return json.loads(m.group(0))

    def trova_bersaglio(self, frame, descrizione):
        r = self._json(frame, f"Foto dalla telecamera frontale di un drone. Cerca: {descrizione}. "
                              "Campi: visibile (true/false), posizione_orizzontale (0 = bordo sinistro, "
                              "1 = bordo destro, centro del bersaglio).")
        u = float(r.get("posizione_orizzontale", 0.5))
        if not 0 <= u <= 1:
            raise ValueError(f"posizione_orizzontale {u}")
        return {"visibile": r.get("visibile") in (True, "true"), "posizione_orizzontale": u}

    def descrivi(self, frame, descrizione):
        return self._chiedi(frame, f"Descrivi in italiano, in massimo tre frasi, cosa vedi. Il rilevatore ha "
                                   f"segnalato: {descrizione}. Di' se la segnalazione è confermata.")


# ---------------- Ispezione ----------------
class Ispezione:
    def __init__(self, drone, visione=None, cartella_foto="foto_avvicinamento",
                 file_registro="registro_avvicinamento.jsonl", mappa=None, base=None, mirino=None, zone=None):
        self.drone, self.visione = drone, visione
        self.mirino = mirino                                 # mira veloce locale (missione con la persona)
        self.mappa, self.base = mappa, base                  # base = (x_m, y_m, direzione del muso) sulla pianta
        self.zone = zone or []                               # zones.json: ogni zona ha il suo punto principale
        self.zona = None                                     # zona dell'anomalia nel volo in corso
        self.quota_volo_cm = QUOTA_VOLO_CM
        self.usa_tof = True                                  # False con --senza-tof
        self.persona = None                                  # stima (x_m, y_m) sulla pianta
        self.percorso_mappa = None
        self.cartella_foto, self.file_registro = cartella_foto, file_registro
        self.atterra = threading.Event()
        self.fase = "a terra"
        self.opzioni = {"scambia": True, "negativo": False}
        self.lock = threading.RLock()
        self.in_volo = False
        self.in_ritorno = False
        self.batteria_bassa = False
        self.livello_batteria = 100
        self.pad_visto = None                                # all'ultimo atterraggio: True, False o None (non cercato)
        self.avanzamento = None                              # 'forward' in corso: (direzione, cm, ora d'inizio)
        self.ultima_batteria = 0.0
        self.ultimo_comando = time.time()
        self.x = self.y = 0.0
        self.yaw0 = 0.0
        self.tracce = [(0.0, 0.0)]
        self.lettore = None
        self.ultima_foto = None
        threading.Thread(target=self._keepalive, daemon=True).start()

    # --- registro e fasi ---
    def registra(self, evidenza, fase, **dati):
        riga = {"ora": time.strftime("%Y-%m-%d %H:%M:%S"), "fase": fase, "evidenza": evidenza,
                "posizione_cm": self.posizione(), **dati}
        with open(self.file_registro, "a", encoding="utf-8") as f:
            f.write(json.dumps(riga, ensure_ascii=False) + "\n")

    def cambia_fase(self, nome):
        self.fase = nome
        print(f"[VOLO] {nome}  (posizione stimata {self.posizione()} cm)")

    def posizione(self):
        return round(self.x), round(self.y)

    # --- conversioni tra sistema del pad (cm) e pianta (m) ---
    def in_mappa(self, x_cm=None, y_cm=None):
        if x_cm is None:
            x_cm, y_cm = self.x, self.y
        bx, by, b = self.base
        a = math.radians(b)
        return (bx + (x_cm * math.cos(a) - y_cm * math.sin(a)) / 100,
                by + (x_cm * math.sin(a) + y_cm * math.cos(a)) / 100)

    def dal_pad(self, x_m, y_m):
        bx, by, b = self.base
        a = math.radians(b)
        dx, dy = (x_m - bx) * 100, (y_m - by) * 100
        return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)

    def posa_mappa(self):
        """(x_m, y_m, direzione) per il disegno: legge lo yaw dallo stato senza mandare comandi."""
        if self.base is None:
            return None
        direzione = self.base[2] + (norma(-VERSO_YAW * (self.stato()["yaw"] - self.yaw0)) if self.in_volo else 0)
        x, y, avanzamento = self.x, self.y, self.avanzamento
        if avanzamento:                                      # a metà di un 'forward': strada stimata dal tempo
            a, cm, inizio = avanzamento
            fatti = min(cm, (time.time() - inizio) * VELOCITA)
            x, y = x + fatti * math.cos(a), y + fatti * math.sin(a)
        return (*self.in_mappa(x, y), direzione)

    # --- comandi: un lock, un invio per comando ---
    def atterra_sicuro(self, tentativi=5):
        """'land' finché il drone non risponde ok: se un comando precedente è ancora in corso (es. risposta persa
        per il Wi-Fi) il Tello risponde 'error' e il drone resterebbe in aria."""
        for _ in range(tentativi):
            try:
                with self.lock:
                    self._svuota()
                    risposta = str(self.drone.send_command_with_return("land", timeout=10)).strip()
            except Exception as e:
                risposta = f"errore: {e}"
            print(f"  'land' -> {risposta!r}")
            a_terra = self._a_terra()                        # es. già atterrato con il kill switch
            if risposta.lower().startswith("ok") or a_terra:
                if a_terra and not risposta.lower().startswith("ok"):
                    print("  Il drone risulta già a terra")
                if hasattr(self.drone, "is_flying"):
                    self.drone.is_flying = False             # altrimenti djitellopy manda un altro land alla chiusura
                return True
            time.sleep(1.0)
        print("\n!!! ATTERRAGGIO NON CONFERMATO: premi e (emergenza) o spegni il drone !!!\n")
        self.registra("misura", "atterraggio_non_confermato")
        return False

    def _a_terra(self):
        try:
            h = self.stato().get("h")
        except Exception:
            return False
        return isinstance(h, (int, float)) and h <= 5

    def _svuota(self):
        try:
            self.drone.get_own_udp_object()["responses"].clear()
        except Exception:
            pass

    def controlla(self):
        if self.atterra.is_set():
            raise Interruzione("richiesta di atterraggio")
        if self.in_volo and time.time() - self.ultima_batteria > INTERVALLO_BATTERIA_S:
            self._aggiorna_batteria()
        if self.batteria_bassa:
            raise Interruzione(f"batteria al {self.livello_batteria}% (minimo {BATTERIA_MIN}%)")
        if self.in_volo and not self.in_ritorno and self.livello_batteria < BATTERIA_RITORNO:
            raise Ritorno(f"batteria al {self.livello_batteria}%")

    def _aggiorna_batteria(self):
        self.ultima_batteria = time.time()
        try:
            self.livello_batteria = self.batteria()
        except Exception as e:
            print("Batteria non letta:", e)
        self.batteria_bassa = self.livello_batteria < BATTERIA_MIN

    def esegui(self, testo, timeout=None):
        """Manda il comando e vuole 'ok' (True). La risposta si aspetta per la durata prevista + MARGINE_RISPOSTA_S.
        Se si perde: salite, discese e rotazioni restituiscono False (chi chiama rimisura dallo stato e continua), gli
        altri comandi sollevano RispostaPersa. Dopo il kill switch solleva Interruzione; ogni altro errore RuntimeError."""
        self.controlla()
        durata = durata_prevista(testo)
        with self.lock:
            self._svuota()
            risposta = str(self.drone.send_command_with_return(
                testo, timeout=timeout or round(durata + MARGINE_RISPOSTA_S))).strip()
            self.ultimo_comando = time.time()
        print(f"  {testo!r} -> {risposta!r}")
        if risposta.lower().startswith("ok"):
            return True
        if self.atterra.is_set():
            raise Interruzione("richiesta di atterraggio")
        if risposta.startswith("Aborting"):                  # djitellopy: nessuna risposta entro il tempo
            if testo.split()[0] in COMANDI_RIMISURABILI:
                print("  Risposta persa: rimisuro dallo stato e continuo")
                return False
            raise RispostaPersa(f"comando {testo!r} senza risposta (Wi-Fi?)")
        raise RuntimeError(f"Comando {testo!r} fallito: {risposta}")

    def leggi(self, testo, valida=None, tentativi=3):
        ultima = ""
        for _ in range(tentativi):
            with self.lock:
                self._svuota()
                try:
                    ultima = str(self.drone.send_command_with_return(testo, timeout=3)).strip()
                except Exception as e:
                    ultima = f"errore: {e}"
                self.ultimo_comando = time.time()
            if valida is None or valida(ultima):
                return ultima
            time.sleep(0.3)
        raise RuntimeError(f"Risposta non valida a {testo!r}: {ultima!r}")

    def batteria(self):
        return int(self.leggi("battery?", lambda r: r.isdigit()))

    # --- kill switch (giro_punti.py) ---
    def atterra_subito(self):
        """Kill switch: rc 0 (ferma salite, rotazioni e passi in corso) e 'land' mandati subito, senza
        aspettare la risposta del comando in corso, che può essersi persa nel Wi-Fi. atterra_sicuro poi conferma."""
        self.atterra.set()
        if not self.in_volo:
            return
        try:
            self.drone.send_rc_control(0, 0, 0, 0)
            time.sleep(0.1)
            self.drone.send_command_without_return("land")
        except Exception as e:
            print("Kill switch: comandi non mandati:", e)

    def _keepalive(self):
        while True:
            time.sleep(0.5)
            if not self.in_volo or time.time() - self.ultimo_comando < INTERVALLO_KEEPALIVE_S:
                continue
            if not self.lock.acquire(timeout=0.1):
                continue
            try:
                self._aggiorna_batteria()
            finally:
                self.lock.release()

    def stato(self):
        return self.drone.get_current_state()

    def tof_cm(self):
        """Distanza davanti in cm (mediana delle letture valide su 3), None se oltre circa 120 cm o ToF disattivato.
        Se la prima lettura non vede nulla basta quella. 0 mm non è una distanza: il 5 novembre il sensore ha letto
        sempre 0 anche a terra con niente davanti (il giorno prima misurava 47-54 cm). Tre 0 di fila = guasto."""
        if not self.usa_tof:
            return None
        valori = []
        for i in range(3):
            risposta = self.leggi("EXT tof?", lambda r: r.lower().startswith("tof"))
            valori.append(int(re.findall(r"\d+", risposta)[-1]))
            if i == 0 and valori[0] > 1250:
                return None
        validi = sorted(v for v in valori if v > 0)
        if not validi:
            raise RuntimeError("sensore ToF frontale: 0 mm in 3 letture di fila, è coperto o guasto: "
                               "vola con --senza-tof")
        mm = validi[len(validi) // 2]
        return None if mm > 1250 else mm / 10

    def libero(self, cm):
        d = self.tof_cm()
        return d is None or d > cm + DISTANZA_SICUREZZA_CM, d

    # --- orientamento e movimento ---
    def direzione(self):
        time.sleep(PAUSA_YAW_S)
        return norma(-VERSO_YAW * (self.stato()["yaw"] - self.yaw0))

    def ruota_verso(self, obiettivo, tolleranza=TOLLERANZA_GRADI):
        fatte = tentativi = 0
        while fatte < 3 and tentativi < 6:
            tentativi += 1
            errore = norma(obiettivo - self.direzione())
            if abs(errore) <= tolleranza:
                return
            if self.esegui(f"{'ccw' if errore > 0 else 'cw'} {max(1, round(abs(errore)))}"):
                fatte += 1                                   # risposta persa: non conta, si rimisura lo yaw

    def avanza(self, cm):
        a = math.radians(self.direzione())
        self.avanzamento = (a, cm, time.time())              # per la posizione dal vivo in dashboard
        try:
            self.esegui(f"forward {int(cm)}")
        finally:
            self.avanzamento = None
        self.x += cm * math.cos(a)
        self.y += cm * math.sin(a)
        self._segna_traccia()

    def _segna_traccia(self):
        """Aggiunge il punto raggiunto; se prosegue in linea retta sostituisce l'ultimo (meno rotazioni al ritorno)."""
        p = (self.x, self.y)
        if len(self.tracce) >= 2:
            (x1, y1), (x2, y2) = self.tracce[-2], self.tracce[-1]
            prima = math.degrees(math.atan2(y2 - y1, x2 - x1))
            ora = math.degrees(math.atan2(p[1] - y2, p[0] - x2))
            if abs(norma(ora - prima)) < 3:
                self.tracce[-1] = p
                return
        self.tracce.append(p)

    def avanza_protetto(self, cm_totali, attesa_s=0.0):
        """Avanza con 'forward' (la distanza la misura il Tello). Senza ToF un comando per tratto, fino a 5 m; con il
        ToF passi da 1 m con lo spazio libero controllato prima di ognuno. Restituisce None se arriva, altrimenti la
        distanza dell'ostacolo che lo ha fermato."""
        rimasti = round(cm_totali)
        massimo = PASSO_CON_TOF_CM if self.usa_tof else PASSO_MAX_CM
        while rimasti >= PASSO_MIN_CM:
            passo = min(massimo, rimasti)
            fine = time.time() + attesa_s
            ok, d = self.libero(passo)
            while not ok and time.time() < fine:
                print(f"  Ostacolo a {d:.0f} cm: aspetto")
                time.sleep(0.5)
                self.controlla()
                ok, d = self.libero(passo)
            if not ok:
                return d
            self.avanza(passo)
            rimasti -= passo
        return None

    # --- Grok ---
    def guarda(self, funzione, *argomenti):
        """Chiama Grok in un thread mentre il drone resta in hovering (il keepalive lo tiene in aria)."""
        if funzione is None:
            return None
        frame = self.vista()
        pool = ThreadPoolExecutor(max_workers=1)
        futuro = pool.submit(funzione, frame, *argomenti)
        fine = time.time() + TIMEOUT_GROK_S
        try:
            while not futuro.done():
                self.controlla()                             # 'l' o batteria: si atterra senza aspettare Grok
                if time.time() > fine:
                    print(f"Grok non ha risposto entro {TIMEOUT_GROK_S} s: proseguo senza")
                    return None
                time.sleep(0.2)
        finally:
            pool.shutdown(wait=False)
        try:
            return futuro.result()
        except Exception as e:
            print("Grok non ha risposto:", e)
            return None

    def mira(self, descrizione):
        """Una sola correzione della direzione se la mira (locale, o Grok) vede il bersaglio.
        Restituisce True (visto), False (non visto) o None (mira non disponibile o senza risposta)."""
        cercatore = self.mirino or self.visione
        if not cercatore:
            return None
        r = self.guarda(cercatore.trova_bersaglio, descrizione)
        if not r:
            return None
        self.registra("giudizio_llm" if cercatore is self.visione else "interpretazione", "mira", **r)
        if r["visibile"]:
            correzione = (0.5 - r["posizione_orizzontale"]) * HFOV_GRADI
            if abs(correzione) >= CORREZIONE_MIN_GRADI:
                print(f"  Bersaglio a {correzione:+.0f} gradi: correggo la direzione")
                self.ruota_verso(self.direzione() + correzione)
        return r["visibile"]

    def guarda_anomalia(self, descrizione, persona=None, direzione=None):
        """Muso verso l'anomalia: verso la stima sulla pianta se c'è, altrimenti verso la direzione vista dal telefono
        (gradi dal muso al decollo, + = sinistra); poi la mira corregge una volta. Nessuna ricerca, nessun passo."""
        if persona is not None and self.base is not None:
            x, y = self.in_mappa()
            rotta = math.degrees(math.atan2(persona[1] - y, persona[0] - x)) - self.base[2]
        else:
            rotta = direzione or 0.0
        self.cambia_fase("muso verso l'anomalia")
        self.ruota_verso(rotta)
        return self.mira(descrizione)

    def vai_a_mappa(self, destinazione, attesa_s=ATTESA_OSTACOLO_ANDATA_S):
        """Percorso A* sulla pianta; un ostacolo non in pianta visto dal ToF viene segnato e il percorso ricalcolato."""
        for _ in range(MAX_DEVIAZIONI):
            da = self.in_mappa()
            percorso = self.mappa.percorso(da, destinazione)
            if percorso is None:
                vicino = self.mappa.punto_sicuro_vicino(*destinazione, da=da)
                percorso = vicino and self.mappa.percorso(da, vicino)
            if not percorso:
                raise Bloccato(f"nessun percorso sicuro sulla pianta verso {destinazione}")
            self.percorso_mappa = percorso
            self.registra("interpretazione", "percorso", percorso=[(round(x, 2), round(y, 2)) for x, y in percorso])
            ostacolo = None
            for punto in percorso[1:]:
                px, py = self.dal_pad(*punto)
                for _ in range(3):                           # il tratto e al massimo 2 correzioni del residuo:
                    resto = math.hypot(px - self.x, py - self.y)   # 5 gradi di rotazione su 7 m sono 60 cm di lato
                    if resto < PASSO_MIN_CM:
                        break
                    self.ruota_verso(math.degrees(math.atan2(py - self.y, px - self.x)), TOLLERANZA_ROTTA_GRADI)
                    ostacolo = self.avanza_protetto(resto, attesa_s=attesa_s)
                    if ostacolo is not None:
                        break
                if ostacolo is not None:
                    break
            if ostacolo is None:
                return
            a = math.radians(self.direzione())
            punto = self.in_mappa(self.x + (ostacolo + 15) * math.cos(a), self.y + (ostacolo + 15) * math.sin(a))
            print(f"  Ostacolo non in pianta in ({punto[0]:.2f}, {punto[1]:.2f}) m: lo segno e ricalcolo il percorso")
            self.registra("misura", "ostacolo_non_in_pianta", punto_m=[round(v, 2) for v in punto])
            self.mappa.aggiungi_ostacolo(*punto, raggio_m=0.3)
        raise Bloccato("troppi ostacoli imprevisti sul percorso")

    def ritorna(self):
        """Percorso A* fino al pad sulla pianta (con la persona e gli ostacoli visti all'andata).
        Se il drone si è solo girato sul posto non c'è strada da fare."""
        self.cambia_fase("ritorno al pad")
        if math.hypot(self.x, self.y) < PASSO_MIN_CM:
            return
        if self.mappa is None or self.base is None:
            raise Bloccato("senza pianta non so tornare al pad")
        self.vai_a_mappa(self.base[:2], attesa_s=ATTESA_OSTACOLO_S)

    def _cerca_pad(self):
        """Legge il pad dallo stato per ATTESA_PAD_S secondi. Restituisce l'ID (1-8) o None."""
        fine = time.time() + ATTESA_PAD_S
        while time.time() < fine:
            mid = self.stato().get("mid", -1)
            if isinstance(mid, int) and mid > 0:
                return mid
            time.sleep(0.1)
        return None

    def allinea_pad(self):
        """Sopra la posizione stimata del pad: lo cerca; se non lo vede sale (area di ricerca più larga) e riprova.
        Niente spostamenti laterali alla cieca: dietro il pad c'è il telefono.
        Le quote qui sono h, l'altezza sul punto di decollo (il pavimento del pad), non il ToF in basso: il 5 novembre
        alle 09:49 il drone era sopra un oggetto (h 70, ToF 29), il ToF ha detto 51 cm, è salito di 59 fino a 1,4 m e
        da lì il pad non si vede più."""
        self.cambia_fase("cerco il pad")
        self.pad_visto = False
        self.ruota_verso(0)
        self.porta_a_quota(QUOTA_PAD_CM, dal_decollo=True)
        pad = self._cerca_pad()
        if pad is None:
            self.cambia_fase("pad non visto: salgo per vedere un'area più larga")
            self.porta_a_quota(QUOTA_RICERCA_PAD_CM, dal_decollo=True)
            pad = self._cerca_pad()
        stato = self.stato()
        diagnosi = {"mid": stato.get("mid"), "altezza_h_cm": stato.get("h"), "tof_basso_cm": stato.get("tof")}
        if pad is None:
            print(f"Pad non visibile ({diagnosi}): atterro dove sono")
            self.registra("misura", "pad", visto=False, **diagnosi)
            return
        try:
            self.esegui(f"go 0 0 {QUOTA_PAD_CM} 25 m-2")
            self.pad_visto = True
            self.registra("misura", "pad", visto=True, **diagnosi)
        except RuntimeError as e:
            print("Allineamento sul pad non riuscito:", e)

    def porta_a_quota(self, obiettivo_cm, dal_decollo=False):
        """dal_decollo: misura con h (altezza sul punto di decollo) invece che con il ToF in basso."""
        for _ in range(4):
            attuale = self.stato().get("h", obiettivo_cm) if dal_decollo else self.quota_cm()
            differenza = obiettivo_cm - attuale
            if abs(differenza) < PASSO_MIN_CM:
                return
            if self.esegui(f"{'up' if differenza > 0 else 'down'} {min(500, round(abs(differenza)))}"):
                return                                       # risposta persa: si rimisura la quota e si riprende

    def quota_cm(self):
        """Altezza da terra: sensore ToF in basso se plausibile, altrimenti altezza dal decollo."""
        s = self.stato()
        tof = s.get("tof", 0)
        return tof if isinstance(tof, (int, float)) and 10 < tof < 400 else s.get("h", QUOTA_PAD_CM)

    # --- video e foto ---
    def vista(self):
        v = self.lettore.frame if self.lettore else None
        if v is None:
            return None
        if self.opzioni["scambia"]:
            v = cv2.cvtColor(v, cv2.COLOR_RGB2BGR)
        if self.opzioni["negativo"]:
            v = 255 - v
        return v.copy()

    def _avvia_video(self):
        """Video solo durante il volo: a terra con il video acceso il Tello si surriscalda.
        Se il video non parte si vola lo stesso: niente foto e niente mira di Grok, il ToF protegge comunque."""
        for comando in IMPOSTAZIONI_VIDEO:                   # se il firmware non li conosce si va avanti
            with self.lock:
                self._svuota()
                try:
                    risposta = str(self.drone.send_command_with_return(comando, timeout=3)).strip()
                except Exception as e:
                    risposta = f"errore: {e}"
            print(f"  {comando!r} -> {risposta!r}")
        try:
            for tentativo in range(2):                       # a volte il flusso non è pronto subito dopo streamon
                try:
                    with self.lock:
                        self._svuota()
                        self.drone.streamon()
                    self.lettore = self.drone.get_frame_read()
                    break
                except Exception:
                    if tentativo:
                        raise
                    print("  Video non partito: streamoff e secondo tentativo")
                    if hasattr(self.drone, "background_frame_read"):
                        self.drone.background_frame_read = None   # djitellopy terrebbe il lettore fallito
                    with self.lock:
                        self._svuota()
                        try:
                            self.drone.streamoff()
                        except Exception:
                            pass
                    time.sleep(1.5)
        except Exception as e:
            print(f"Video del drone non disponibile ({e}): volo senza foto e senza mira di Grok")
            self.registra("misura", "video_non_disponibile", errore=str(e))
            self.lettore = None
            return
        fine = time.time() + 5
        while self.vista() is None and time.time() < fine:
            time.sleep(0.1)

    def prepara_video(self):
        """Accende il video all'allarme, mentre l'operatore decide: al decollo è già pronto."""
        if self.lettore is None and not self.in_volo:
            self._avvia_video()

    def spegni_video_se_a_terra(self):
        if self.lettore is not None and not self.in_volo:
            self._ferma_video()

    def _ferma_video(self):
        try:
            with self.lock:
                self._svuota()
                self.drone.streamoff()
        except Exception:
            pass
        self.lettore = None

    def _video_vivo(self):
        """Il lettore di djitellopy decodifica in un thread che, se il decoder si chiude (5 novembre: 'Do not have
        enough frames for decoding' subito dopo streamon), muore in silenzio lasciando l'ultimo fotogramma: le foto
        sarebbero tutte vecchie o rovinate. Se è morto lo riavvia (streamoff, streamon, nuovo lettore)."""
        lavoro = getattr(self.lettore, "worker", None)
        if self.lettore is None or lavoro is None or lavoro.is_alive():
            return self.lettore is not None
        print("  Video del drone fermo (decoder chiuso): lo riavvio")
        self.registra("misura", "video_riavviato")
        try:
            self.lettore.stop()
        except Exception:
            pass
        if hasattr(self.drone, "background_frame_read"):
            self.drone.background_frame_read = None
        with self.lock:
            self._svuota()
            try:
                self.drone.streamoff()
            except Exception:
                pass
        self.lettore = None
        self._avvia_video()
        return self.lettore is not None

    def fotogramma_nitido(self, durata_s=None):
        """Il fotogramma con il punteggio più alto (qualita_foto: nitidezza divisa per blocchi^4, così i pacchetti video
        persi, i cui bordi netti sembrerebbero dettaglio, perdono) tra quelli nuovi arrivati in FINESTRA_FOTO_S.
        Restituisce (immagine, {nitidezza, blocchi, luce, punteggio}) o (None, {})."""
        if not self._video_vivo():
            return None, {}
        migliore, ultimo = None, None
        fine = time.time() + (FINESTRA_FOTO_S if durata_s is None else durata_s)
        while True:
            grezzo = self.lettore.frame if self.lettore else None
            if grezzo is not None and grezzo is not ultimo and grezzo.max() > 0:   # nuovo e non il nero iniziale
                ultimo = grezzo
                immagine = self.vista()
                misure = qualita_foto(immagine)
                if misure["luce"] >= 3 and (migliore is None or misure["punteggio"] > migliore[0]["punteggio"]):
                    migliore = (misure, immagine)           # nero = video congelato: scartato
            if time.time() >= fine and (migliore or time.time() >= fine + 2):
                break
            time.sleep(0.03)
        return (migliore[1], migliore[0]) if migliore else (None, {})

    def foto(self, nome):
        """Salva il fotogramma migliore corretto (migliora_foto) e, in originali/, quello com'era."""
        os.makedirs(os.path.join(self.cartella_foto, "originali"), exist_ok=True)
        grezza, misure = self.fotogramma_nitido()
        if grezza is None:                                   # video non arrivato: si continua senza foto
            print("  Nessun fotogramma dal drone: foto non salvata")
            self.registra("misura", "foto_mancante", nome=nome)
            return None, None
        file = f"{time.strftime('%Y%m%d_%H%M%S')}_{nome}.jpg"
        percorso = os.path.join(self.cartella_foto, file)
        cv2.imwrite(os.path.join(self.cartella_foto, "originali", file), grezza, [cv2.IMWRITE_JPEG_QUALITY, 95])
        immagine = migliora_foto(grezza)
        cv2.imwrite(percorso, immagine, [cv2.IMWRITE_JPEG_QUALITY, 95])
        self.ultima_foto = percorso
        print(f"  Foto {file}: nitidezza {misure['nitidezza']:.0f}, blocchi {misure['blocchi']:.2f}, "
              f"luce {misure['luce']:.0f}/255" + ("  (POCA LUCE)" if misure["luce"] < 60 else ""))
        self.registra("misura", "qualita_foto", foto=percorso, **{k: round(v, 2) for k, v in misure.items()})
        return percorso, immagine

    # --- pezzi comuni a missione e giro ---
    def _azzera(self):
        self.atterra.clear()
        self.batteria_bassa = self.in_ritorno = False
        self.livello_batteria, self.ultima_batteria = 100, 0.0
        self.x = self.y = 0.0
        self.tracce = [(0.0, 0.0)]
        self.persona, self.percorso_mappa, self.zona = None, None, None
        self.pad_visto = None

    def _decolla(self):
        self.cambia_fase("decollo")
        self.controlla()
        with self.lock:
            self._svuota()
            self.in_volo = True                              # prima dell'invio: un decollo senza risposta può essere avvenuto
            self.drone.takeoff()
            self.ultimo_comando = time.time()
        self.esegui(f"speed {VELOCITA}")
        self.porta_a_quota(self.quota_volo_cm)
        time.sleep(PAUSA_YAW_S)
        self.yaw0 = self.stato()["yaw"]

    def _rientra(self):
        self.in_ritorno = True
        self.ritorna()
        self.allinea_pad()

    @staticmethod
    def _errore_in_volo(e):
        if isinstance(e, Interruzione):
            print("Interruzione: atterro dove sono -", e)
        elif isinstance(e, Bloccato):
            print("Percorso bloccato: atterro dove sono -", e)
        else:
            print("Errore durante il volo:", e)

    def _atterra_e_chiudi(self, pianta_pulita):
        self.cambia_fase("atterraggio")
        if self.in_volo:
            self.atterra_sicuro()
        self.in_volo = False
        if pianta_pulita is not None:                        # persona e ostacoli visti valgono solo per questo volo
            self.mappa.occupata[:] = pianta_pulita
            self.mappa.aggiorna()
        self.registra("misura", "fine", tracce=[(round(x), round(y)) for x, y in self.tracce])

    # --- missione ---
    def esegui_missione(self, descrizione, persona=None, direzione=None):
        """persona = (x_m, y_m) sulla pianta stimato con il telefono, oppure None: allora il drone non si sposta,
        si gira verso 'direzione' (gradi dal muso al decollo, + = sinistra), fotografa e riatterra."""
        self._azzera()
        self.persona = persona
        punto = None
        if persona is not None and self.mappa is not None and self.base is not None:
            punto, self.zona, motivo = punto_principale(self.mappa, self.zone, persona, self.base)
            print(f"[VOLO] Anomalia in ({persona[0]:.1f}; {persona[1]:.1f}) m, zona {self.zona}: {motivo}")
            self.registra("interpretazione", "destinazione", persona_m=[round(v, 2) for v in persona], zona=self.zona,
                          punto_m=[round(v, 2) for v in punto] if punto else None, motivo=motivo)
        if self.lettore is None:                             # di solito già acceso all'allarme (prepara_video)
            self._avvia_video()
        pianta_pulita = self.mappa.occupata.copy() if self.mappa is not None else None
        pool = ThreadPoolExecutor(max_workers=1)
        descrizione_grok = None
        try:
            self._decolla()
            try:
                if punto is not None:
                    self.mappa.aggiungi_ostacolo(*persona, raggio_m=RAGGIO_PERSONA_M)   # il percorso le gira attorno
                    self.cambia_fase(f"verso la zona {self.zona}")
                    self.vai_a_mappa(punto)
                visto = self.guarda_anomalia(descrizione, persona, direzione)
                self.cambia_fase("foto")
                time.sleep(ATTESA_STABILIZZAZIONE_S)
                file_foto, frame = self.foto(f"zona_{self.zona or 'pad'}")
                self.registra("misura", "foto", foto=file_foto, zona=self.zona, persona_vista=visto)
                if self.visione and frame is not None:
                    descrizione_grok = pool.submit(self.visione.descrivi, frame, descrizione)
            except Ritorno as e:
                print("Batteria bassa: salto il resto e torno al pad -", e)
                self.registra("misura", "ritorno_anticipato", motivo=str(e))
            self._rientra()
        except Exception as e:
            self._errore_in_volo(e)
        finally:
            self._atterra_e_chiudi(pianta_pulita)
            if descrizione_grok is not None:
                try:
                    testo = descrizione_grok.result(timeout=60)
                except Exception as e:
                    testo = f"Errore nella chiamata a Grok: {e}"
                print("\nGrok:", testo, "\n")
                self.registra("giudizio_llm", "descrizione", foto=self.ultima_foto, grok=testo)
            pool.shutdown(wait=False)
            self._ferma_video()
            self.fase = "a terra"

    # --- giro di prova ---
    def _quattro_foto(self, i, nome):
        """Quattro foto ruotando sul posto di 90 gradi alla volta, verso est, nord, ovest e sud della pianta, partendo
        dalla direzione più vicina a quella d'arrivo. Restituisce [(lato, file), ...]."""
        lati = (("est", 0), ("nord", 90), ("ovest", 180), ("sud", 270))
        attuale = self.base[2] + self.direzione()
        primo = min(range(4), key=lambda k: abs(norma(lati[k][1] - attuale)))
        fatte = []
        for k in range(4):
            lato, gradi = lati[(primo + k) % 4]
            self.cambia_fase(f"tappa {nome}: foto verso {lato}")
            self.ruota_verso(norma(gradi - self.base[2]))
            time.sleep(ATTESA_STABILIZZAZIONE_S)
            file_foto, _ = self.foto(f"tappa{i}_{nome_file(nome)}_{lato}")
            self.registra("misura", "foto", foto=file_foto, tappa=nome, lato=lato)
            fatte.append((lato, file_foto))
        return fatte

    def esegui_giro(self, tappe, scatta=True, quattro_lati=False):
        """Prova di navigazione: dal pad a ogni tappa [(nome, (x_m, y_m)), ...] in sequenza sulla pianta (A*, 'forward',
        ToF se attivo), una foto per tappa se 'scatta' (quattro, una ogni 90 gradi, con 'quattro_lati'), poi ritorno
        al pad. Stessa sicurezza della missione. Restituisce le tappe raggiunte con la posizione stimata."""
        if self.mappa is None or self.base is None:
            raise ValueError("il giro richiede la pianta e il pad (mappa e base)")
        self._azzera()
        raggiunte = []
        if scatta and self.lettore is None:
            self._avvia_video()
        pianta_pulita = self.mappa.occupata.copy()
        try:
            self._decolla()
            try:
                for i, (nome, punto) in enumerate(tappe, 1):
                    self.cambia_fase(f"tappa {i} di {len(tappe)}: {nome}")
                    self.vai_a_mappa(punto)
                    stimata = self.in_mappa()
                    raggiunte.append((nome, stimata))
                    print(f"  Tappa {nome}: posizione stimata ({stimata[0]:.2f}; {stimata[1]:.2f}) m, "
                          f"a {math.dist(stimata, punto):.2f} m dal punto chiesto")
                    self.registra("interpretazione", "tappa", nome=nome, punto_m=[round(v, 2) for v in punto],
                                  stimata_m=[round(v, 2) for v in stimata])
                    if scatta and quattro_lati:
                        self._quattro_foto(i, nome)
                    elif scatta:
                        time.sleep(ATTESA_STABILIZZAZIONE_S)
                        file_foto, _ = self.foto(f"tappa{i}_{nome_file(nome)}")
                        self.registra("misura", "foto", foto=file_foto, tappa=nome)
            except Ritorno as e:
                print("Batteria bassa: salto le altre tappe e torno al pad -", e)
                self.registra("misura", "ritorno_anticipato", motivo=str(e))
            self._rientra()
        except Exception as e:
            self._errore_in_volo(e)
        finally:
            self._atterra_e_chiudi(pianta_pulita)
            self._ferma_video()
            self.fase = "a terra"
        return raggiunte

"""
Prova completa della centrale (centrale.py) senza drone, telefoni né rete: drone simulato, telecamere finte, Grok
finto, dashboard vera su una porta libera, comandata via HTTP come farebbe il browser.
    python prova_centrale.py
"""
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

import centrale as c
import giro_punti
import ispezione as isp
import sito
from simulatore import DroneSimulato

esiti = []


class GrokFinto:
    """Risponde come Grok: descrizione dell'evento della telecamera o verdetto sulle foto del drone."""

    def __init__(self, verdetto="ANOMALY DETECTED"):
        self.verdetto, self.chiamate = verdetto, []
        self.chat = self.completions = self

    def create(self, **argomenti):
        contenuto = argomenti["messages"][0]["content"]
        testo = contenuto if isinstance(contenuto, str) else contenuto[0]["text"]
        if "Comando dell'operatore" in testo:                # riga di comando: parte se Grok ha visto l'anomalia
            self.chiamate.append("comando")
            parti = '"anomalia_grok": true' in testo
            r = {"parti": parti, "risposta": "Ho visto un'anomalia: il drone parte." if parti else "Niente: resta fermo."}
        elif "agente di sicurezza" in testo:
            self.chiamate.append("evento")
            r = {"deviation_detected": True, "changes_detected": ["persona in piedi"], "reasoning": "prova",
                 "severity": "high"}
        else:
            self.chiamate.append("verdetto")
            r = {"esito": self.verdetto, "descrizione": "persona confermata dalle foto del drone", "foto": ["nord"],
                 "confidenza": 0.9}
        messaggio = type("M", (), {"content": json.dumps(r)})()
        return type("R", (), {"choices": [type("C", (), {"message": messaggio})()]})()


def nuova_centrale(grok=None):
    cartella = tempfile.mkdtemp(prefix="centrale_")
    isp.ATTESA_STABILIZZAZIONE_S = isp.PAUSA_YAW_S = 0.0
    mappa = giro_punti.prepara_pianta()
    drone = DroneSimulato(mappa, c.BASE, batteria=100)
    volo = isp.Ispezione(drone, None, cartella, os.path.join(cartella, "registro.jsonl"), mappa=mappa, base=c.BASE,
                         zone=sito.zone())
    volo.quota_volo_cm = giro_punti.QUOTA_GIRO_CM
    telecamere = [{"id": "cam1", "nome": "Camera 1", "url": "finta", "zona": "ZONE_7", "ruota": 0},
                  {"id": "cam2", "nome": "Camera 2", "url": "finta", "zona": "CENTER", "ruota": 0}]
    centrale = c.Centrale(telecamere, volo, drone, mappa, grok, finte=True, cartella=cartella)
    centrale.avvia()
    server = c.crea_server(centrale, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return centrale, server, drone, "http://127.0.0.1:%d" % server.server_address[1]


def chiedi(base, percorso, dati=None):
    corpo = None if dati is None else json.dumps(dati).encode()
    richiesta = urllib.request.Request(base + percorso, corpo, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(richiesta, timeout=10) as r:
            contenuto = r.read()
            return json.loads(contenuto) if r.headers.get_content_type() == "application/json" else contenuto
    except urllib.error.HTTPError as e:
        return {"http": e.code}


def aspetta(condizione, secondi=60):
    fine = time.time() + secondi
    while time.time() < fine:
        risultato = condizione()
        if risultato:
            return risultato
        time.sleep(0.2)
    return None


def caso(nome):
    def esegui(prova):
        print(f"\n=== {nome}")
        try:
            print("  OK", prova() or "")
            esiti.append(True)
        except AssertionError as e:
            print("  FALLITO:", e)
            esiti.append(False)
    return esegui


@caso("evento della telecamera -> Sì in dashboard -> volo, 4 foto, pad, verdetto, Grafana (events.json)")
def _():
    grok = GrokFinto()
    centrale, server, drone, base = nuova_centrale(grok)
    try:
        assert aspetta(lambda: all(t["stato"] == "online" for t in chiedi(base, "/status")["telecamere"]), 10), \
            "telecamere non online"
        time.sleep(c.RISCALDAMENTO_S + 0.5)
        assert b"<title>Centrale drone</title>" in chiedi(base, "/"), "pagina della dashboard mancante"
        assert chiedi(base, "/simula", {"camera_id": "cam1"})["status"] == "evento simulato"
        attesa = aspetta(lambda: chiedi(base, "/pending"), 10)
        assert attesa and attesa[0]["target_zone"] == "ZONE_7", f"nessuna decisione in attesa: {attesa}"
        assert aspetta(lambda: chiedi(base, "/pending")[0]["severity"] == "high", 5), "descrizione di Grok mancante"
        foto_camera = chiedi(base, "/frames/" + attesa[0]["photo"])
        assert isinstance(foto_camera, bytes) and foto_camera[:2] == b"\xff\xd8", "foto dell'allarme non servita"
        assert chiedi(base, "/decision", {"task_id": attesa[0]["task_id"], "decision": "yes"})["status"] == "dispatched"
        assert aspetta(lambda: chiedi(base, "/status")["drone_busy"], 10), "il drone non è partito"
        assert isinstance(chiedi(base, "/mappa.png"), bytes), "mappa non servita"
        stato = aspetta(lambda: (lambda s: s if not s["drone_busy"] and s["ultime_missioni"] else None)(
            chiedi(base, "/status")), 120)
        assert stato, "missione non finita"
        m = stato["ultime_missioni"][0]
        assert sorted(f["lato"] for f in m["foto"]) == ["est", "nord", "ovest", "sud"], m["foto"]
        assert m["verdetto"]["esito"] == "ANOMALY DETECTED", m["verdetto"]
        tipi = [a["tipo"] for a in chiedi(base, "/status")["avvisi"]]
        assert "grok_anomalia" in tipi and "anomalia_confermata" in tipi, f"avvisi per l'anomalia mancanti: {tipi}"
        assert b"mostraAvvisi" in chiedi(base, "/"), "la dashboard non mostra gli avvisi"
        assert math.dist((drone.x, drone.y), c.BASE[:2]) < 0.3 and not drone.in_volo, "non è tornato sul pad"
        assert not drone.urti, f"urti {drone.urti}"
        with open(centrale.file_eventi, encoding="utf-8") as f:
            nomi = [json.loads(r)["event"] for r in f]
        for atteso in ("camera_input", "awaiting_decision", "grok_analysis", "final_decision", "drone_dispatched",
                       "drone_photo", "mission_completed", "verdict"):
            assert atteso in nomi, f"evento {atteso} mancante in events.json"
        assert nomi.index("final_decision") < nomi.index("drone_dispatched") < nomi.index("verdict")
        assert any(chiedi(base, "/frames/" + f["photo"])[:2] == b"\xff\xd8" for f in m["foto"]), "foto del drone"
        return f"{len(nomi)} eventi, verdetto {m['verdetto']['esito']}"
    finally:
        centrale.chiudi()
        server.shutdown()


@caso("FALSO ALLARME di Grok: avviso solo se Grok ha visto l'anomalia all'allarme, mai «confermata»")
def _():
    grok = GrokFinto(verdetto="FALSO ALLARME")
    centrale, server, drone, base = nuova_centrale(grok)
    try:
        assert aspetta(lambda: all(t["stato"] == "online" for t in chiedi(base, "/status")["telecamere"]), 10), \
            "telecamere non online"
        time.sleep(c.RISCALDAMENTO_S + 0.5)
        chiedi(base, "/simula", {"camera_id": "cam2"})
        attesa = aspetta(lambda: chiedi(base, "/pending"), 10)
        assert attesa and aspetta(lambda: chiedi(base, "/pending")[0]["severity"] == "high", 5), "allarme non arrivato"
        assert chiedi(base, "/decision", {"task_id": attesa[0]["task_id"], "decision": "yes"})["status"] == "dispatched"
        stato = aspetta(lambda: (lambda s: s if not s["drone_busy"] and s["ultime_missioni"] else None)(
            chiedi(base, "/status")), 120)
        assert stato and stato["ultime_missioni"][0]["verdetto"]["esito"] == "FALSO ALLARME", "verdetto diverso"
        tipi = [a["tipo"] for a in stato["avvisi"]]
        assert "grok_anomalia" in tipi, f"Grok ha visto un'anomalia all'allarme ma non è stato avvisato: {tipi}"
        assert "anomalia_confermata" not in tipi, f"avviso di anomalia confermata con FALSO ALLARME: {tipi}"
        return f"avvisi {tipi}"
    finally:
        centrale.chiudi()


@caso("No in dashboard: il drone resta a terra; comando manuale: ci va")
def _():
    centrale, server, drone, base = nuova_centrale()
    try:
        aspetta(lambda: all(t["stato"] == "online" for t in chiedi(base, "/status")["telecamere"]), 10)
        time.sleep(c.RISCALDAMENTO_S + 0.5)
        chiedi(base, "/simula", {"camera_id": "cam2"})
        attesa = aspetta(lambda: chiedi(base, "/pending"), 10)
        assert attesa and attesa[0]["target_zone"] == "CENTER"
        assert chiedi(base, "/decision", {"task_id": attesa[0]["task_id"], "decision": "no"})["status"] == "rejected"
        time.sleep(1.5)
        assert "takeoff" not in drone.comandi, "è decollato dopo un No"
        assert chiedi(base, "/command", {"target_zone": "PIPPO"}).get("http") == 400, "zona inventata accettata"
        assert chiedi(base, "/command", {"target_zone": "HUB"})["status"] == "dispatched"
        stato = aspetta(lambda: (lambda s: s if not s["drone_busy"] and s["ultime_missioni"] else None)(
            chiedi(base, "/status")), 120)
        assert stato and stato["ultime_missioni"][0]["target_zone"] == "HUB" and len(stato["ultime_missioni"][0]["foto"]) == 4
        assert math.dist((drone.x, drone.y), c.BASE[:2]) < 0.3
    finally:
        centrale.chiudi()
        server.shutdown()


@caso("STOP dalla dashboard a metà volo: atterra dove si trova; EMERGENZA: motori spenti e niente più voli")
def _():
    centrale, server, drone, base = nuova_centrale()
    try:
        chiedi(base, "/command", {"target_zone": "ZONE_7"})
        assert aspetta(lambda: math.dist((drone.x, drone.y), c.BASE[:2]) > 1.5, 30), "non è partito"
        chiedi(base, "/stop", {})
        assert aspetta(lambda: not drone.in_volo, 15), "ancora in volo dopo STOP"
        lontano = math.dist((drone.x, drone.y), c.BASE[:2])
        assert lontano > 1.0, f"è tornato al pad invece di atterrare ({lontano:.2f} m)"
        aspetta(lambda: not chiedi(base, "/status")["drone_busy"], 15)
        chiedi(base, "/emergenza", {})
        assert chiedi(base, "/status")["bloccato"], "non bloccata dopo l'emergenza"
        assert chiedi(base, "/command", {"target_zone": "HUB"})["status"] == "refused_after_emergency"
        return f"atterrato a {lontano:.2f} m dal pad"
    finally:
        centrale.chiudi()
        server.shutdown()


@caso("zone delle telecamere all'avvio: Invio tiene la proposta, zona sbagliata richiesta, scelta ricordata")
def _():
    file = os.path.join(tempfile.mkdtemp(), "camere.json")
    risposte = iter(["", "pippo", "center"])
    camere = c.configura_telecamere(lambda _: next(risposte), file)
    assert [x["zona"] for x in camere] == [c.TELECAMERE[0]["zona"], "CENTER"], camere
    camere = c.configura_telecamere(lambda _: "", file)
    assert camere[1]["zona"] == "CENTER", "scelta non ricordata"


@caso("riga di comando: senza Grok «no» archivia e «manda» fa partire il drone; con «decidi tu» decide Grok")
def _():
    centrale, server, drone, base = nuova_centrale(None)
    try:
        assert aspetta(lambda: all(t["stato"] == "online" for t in chiedi(base, "/status")["telecamere"]), 10)
        time.sleep(c.RISCALDAMENTO_S + 0.5)
        chiedi(base, "/simula", {"camera_id": "cam2"})
        assert aspetta(lambda: chiedi(base, "/pending"), 10), "allarme non arrivato"
        vecchio = chiedi(base, "/pending")[0]["task_id"]
        r = chiedi(base, "/comando", {"testo": "no, lascia stare", "task_id": vecchio})
        assert r["azioni"] and "archiviato" in r["azioni"][0] and not chiedi(base, "/pending"), r
        r = chiedi(base, "/comando", {"testo": "manda il drone", "task_id": vecchio})   # allarme già deciso
        assert not r["azioni"] and "già stato deciso" in r["risposta"], r
        assert "takeoff" not in drone.comandi, "è decollato dopo un no scritto"
        chiedi(base, "/simula", {"camera_id": "cam1"})            # altra telecamera: nessuna pausa
        assert aspetta(lambda: chiedi(base, "/pending"), 10), "secondo allarme non arrivato"
        r = chiedi(base, "/comando", {"testo": "manda il drone per la camera 1"})
        assert r["azioni"] and "drone mandato" in r["azioni"][0], r
        assert aspetta(lambda: chiedi(base, "/status")["drone_busy"], 10), "il drone non è partito"
        assert chiedi(base, "/status")["dialogo"][0]["testo"] == "manda il drone per la camera 1"
    finally:
        centrale.chiudi()
    grok = GrokFinto()
    centrale, server, drone, base = nuova_centrale(grok)
    try:
        assert aspetta(lambda: all(t["stato"] == "online" for t in chiedi(base, "/status")["telecamere"]), 10)
        time.sleep(c.RISCALDAMENTO_S + 0.5)
        chiedi(base, "/simula", {"camera_id": "cam2"})
        assert aspetta(lambda: chiedi(base, "/pending"), 10), "allarme non arrivato"
        assert aspetta(lambda: chiedi(base, "/pending")[0]["severity"] == "high", 5), "analisi di Grok mancante"
        r = chiedi(base, "/comando", {"testo": "decidi tu"})
        assert r["fonte"] == "Grok" and r["azioni"] and "drone mandato" in r["azioni"][0], r
        assert "comando" in grok.chiamate
        assert aspetta(lambda: chiedi(base, "/status")["drone_busy"], 10), "il drone non è partito"
        with open(centrale.file_eventi, encoding="utf-8") as f:
            assert any(json.loads(riga)["event"] == "operator_command" for riga in f), "comando non registrato"
        return "parole chiave e Grok ok"
    finally:
        centrale.chiudi()


if __name__ == "__main__":
    print(f"\n{sum(esiti)} casi su {len(esiti)} superati")
    sys.exit(0 if all(esiti) else 1)

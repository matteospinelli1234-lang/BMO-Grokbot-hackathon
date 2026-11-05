"""
Genera grafana/dashboards/drone-konsol.json (la dashboard «Centrale drone» che Grafana carica da solo).
    python crea_grafana.py            scrive il file; poi  docker compose restart grafana  per caricarlo
    python crea_grafana.py --prova    prova prima ogni query su Loki (http://localhost:3100) e segnala gli errori
Ogni pannello legge gli eventi di centrale.py (dashboard/logs/events.json -> Alloy -> Loki).
"""
import json
import os
import sys
import urllib.parse
import urllib.request

DS = {"type": "loki", "uid": "loki"}
F = '{job="drone", event="%s"}'
pannelli = []


def conta(evento, filtro=""):
    """Numero di eventi nell'intervallo scelto in alto a destra (0 se nessuno)."""
    return f"sum(count_over_time({F % evento}{filtro} [$__range])) or vector(0)"


def media(evento, campo):
    return (f"avg(avg_over_time({F % evento} | json {campo} | unwrap {campo} | __error__=\"\" [$__range]))")


def stat(titolo, espressione, x, y, colore, unita="none", w=4, descrizione="", decimali=None):
    pannelli.append({
        "type": "stat", "title": titolo, "description": descrizione, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": 4},
        "targets": [{"refId": "A", "queryType": "instant", "expr": espressione}],
        "options": {"colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center",
                    "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
        "fieldConfig": {"defaults": {"unit": unita, "noValue": "-", "decimals": decimali,
                                     "color": {"mode": "fixed", "fixedColor": colore}}, "overrides": []}})


def serie(titolo, obiettivi, x, y, w, h, unita="none", barre=False, descrizione=""):
    pannelli.append({
        "type": "timeseries", "title": titolo, "description": descrizione, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": nome} for i, (e, nome) in enumerate(obiettivi)],
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "fieldConfig": {"defaults": {"unit": unita, "custom": {
            "drawStyle": "bars" if barre else "line", "fillOpacity": 60 if barre else 10, "pointSize": 6,
            "showPoints": "always", "spanNulls": 30000, "lineWidth": 2}}, "overrides": []}})


def righe(titolo, evento, formato, x, y, w, h, descrizione=""):
    """Pannello di log con una riga leggibile per evento (line_format) invece del JSON grezzo."""
    pannelli.append({
        "type": "logs", "title": titolo, "description": descrizione, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "expr": f'{F % evento} | json | line_format "{formato}"'}],
        "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True,
                    "dedupStrategy": "none", "prettifyLogMessage": False}})


def foto(titolo, evento, colonne, x, y, w, h):
    """Tabella con l'immagine (servita da centrale.py su localhost:5000) e alcune colonne dell'evento."""
    includi = {"Time": True, "Line": True, **{c: True for c in colonne}}
    nomi = {"Time": "Ora", "Line": "Foto", "target_zone": "Zona", "camera": "Telecamera", "lato": "Lato",
            "area_percento": "Area %", "luce": "Luce", "nitidezza": "Nitidezza"}
    pannelli.append({
        "type": "table", "title": titolo, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "expr": f'{F % evento} | json | line_format "http://localhost:5000/frames/{{{{.photo}}}}"'}],
        "transformations": [
            {"id": "extractFields", "options": {"source": "labels", "format": "auto", "replace": False}},
            {"id": "organize", "options": {"includeByName": includi, "renameByName": nomi,
                                           "indexByName": {"Time": 0, **{c: i + 1 for i, c in enumerate(colonne)},
                                                           "Line": len(colonne) + 1}}}],
        "options": {"cellHeight": "lg", "showHeader": True, "sortBy": [{"displayName": "Ora", "desc": True}]},
        "fieldConfig": {"defaults": {"custom": {"align": "left"}}, "overrides": [
            {"matcher": {"id": "byRegexp", "options": "^(Foto|Line)$"},
             "properties": [{"id": "custom.cellOptions", "value": {"type": "image"}}, {"id": "custom.width", "value": 170}]}]}})


# --- riga 1: cosa è successo ---
stat("Allarmi delle telecamere", conta("camera_input"), 0, 0, "blue", descrizione="cose grandi viste dalle telecamere fisse")
stat("Decisioni: Sì", conta("final_decision", ' | json decision | decision="yes"'), 4, 0, "green")
stat("Decisioni: No", conta("final_decision", ' | json decision | decision="no"'), 8, 0, "orange")
stat("Missioni completate", conta("mission_completed"), 12, 0, "purple")
stat("ANOMALY DETECTED", conta("verdict", ' | json esito | esito="ANOMALY DETECTED"'), 16, 0, "red",
     descrizione="Grok: le foto del drone confermano ciò che ha visto la telecamera")
stat("FALSO ALLARME", conta("verdict", ' | json esito | esito="FALSO ALLARME"'), 20, 0, "green",
     descrizione="Grok: le foto del drone non confermano l'allarme")
# --- riga 2: tempi e stato ---
stat("Decisione dell'operatore (media)", media("final_decision", "tempo_decisione_s"), 0, 4, "blue", "s", decimali=1,
     descrizione="dall'allarme al Sì/No in dashboard")
stat("Risposta di Grok (media)", 'avg(avg_over_time({job="drone", event=~"grok_analysis|verdict"} | json grok_s | '
     'unwrap grok_s | __error__="" [$__range]))', 4, 4, "blue", "s", decimali=1)
stat("Durata missione (media)", media("mission_completed", "durata_s"), 8, 4, "purple", "s", decimali=0)
stat("Batteria (ultima in volo)", f'max(last_over_time({F % "telemetry"} | json batteria | unwrap batteria | '
     f'__error__="" [$__range]))', 12, 4, "yellow", "percent")
stat("Luce media delle foto", media("drone_photo", "luce"), 16, 4, "yellow", decimali=0,
     descrizione="0-255: sotto 60 le foto sono buie (serve più luce in sala)")
stat("Errori delle telecamere", conta("camera_error"), 20, 4, "orange")
# --- riga 3: telemetria del drone e allarmi per zona ---
telemetria = lambda campo: (f'avg(avg_over_time({F % "telemetry"} | json {campo} | unwrap {campo} | __error__="" '
                            f'[$__interval]))')
serie("Telemetria in volo: batteria e temperatura", [(telemetria("batteria"), "batteria %"),
                                                     (telemetria("temperatura"), "temperatura °C")], 0, 8, 8, 8)
serie("Telemetria in volo: quota e velocità", [(telemetria("quota_cm"), "quota cm"),
                                               (telemetria("velocita_cm_s"), "velocità cm/s")], 8, 8, 8, 8)
serie("Allarmi per zona", [(f'sum by (target_zone) (count_over_time({F % "camera_input"} | json target_zone '
                            f'[$__interval]))', "{{target_zone}}")], 16, 8, 8, 8, barre=True)
# --- riga 4: Grok e decisioni, in parole ---
righe("Verdetti di Grok (dopo le 4 foto del drone)", "verdict",
      "{{.esito}} · {{.target_zone}} · {{.camera}} · confidenza {{.confidenza}} · si vede verso: {{.foto_con_anomalia}}"
      " · {{.descrizione}}", 0, 16, 12, 9)
righe("Analisi di Grok sulle telecamere", "grok_analysis",
      "{{.camera}} → {{.target_zone}} · anomalia {{.anomalia}} · gravità {{.severity}} · {{.grok_s}} s · "
      "{{.changes_detected}} · {{.reasoning}}", 12, 16, 12, 9)
righe("Decisioni dell'operatore", "final_decision",
      "{{.camera}} → {{.target_zone}}: {{.decision}} dopo {{.tempo_decisione_s}} s (Grok: anomalia {{.grok_anomalia}}, "
      "gravità {{.severity}}, priorità {{.priority}})", 0, 25, 12, 8)
righe("Missioni del drone", "mission_completed",
      "{{.target_zone}} ({{.camera}}): raggiunta {{.reached}}, {{.photos}} foto, {{.durata_s}} s, batteria "
      "{{.batteria_inizio}}% → {{.batteria_fine}}%, pad visto {{.pad_visto}}, luce media {{.luce_media}}", 12, 25, 12, 8)
# --- riga 5: foto e registro completo ---
foto("Foto delle telecamere (allarmi)", "camera_input", ["camera", "target_zone", "area_percento"], 0, 33, 9, 12)
foto("Foto del drone", "drone_photo", ["target_zone", "lato", "luce", "nitidezza"], 9, 33, 9, 12)
pannelli.append({"type": "logs", "title": "Tutti gli eventi", "datasource": DS, "gridPos": {"x": 18, "y": 33, "w": 6, "h": 12},
                 "targets": [{"refId": "A", "expr": '{job="drone"} | json | event!="telemetry"'}],
                 "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending"}})

for i, p in enumerate(pannelli, 1):
    p["id"] = i
dashboard = {"title": "Centrale drone", "uid": "drone-konsol", "time": {"from": "now-6h", "to": "now"},
             "refresh": "5s", "schemaVersion": 39, "timezone": "browser", "panels": pannelli,
             "description": "Telecamere fisse, decisioni dell'operatore, voli, foto e verdetti di Grok (centrale.py)"}


def prova():
    """Ogni query su Loki: stampa quante serie/righe restituisce o l'errore."""
    errori = 0
    for p in pannelli:
        for t in p["targets"]:
            espressione = t["expr"].replace("$__range", "6h").replace("$__interval", "1m")
            tipo = "query" if t.get("queryType") == "instant" else "query_range"
            url = f"http://localhost:3100/loki/api/v1/{tipo}?" + urllib.parse.urlencode(
                {"query": espressione, "limit": 5, "since": "6h"} if tipo == "query_range" else {"query": espressione})
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    risultato = json.load(r)["data"]["result"]
                print(f"  ok   {p['title'][:45]:<45} {len(risultato)} risultati")
            except urllib.error.HTTPError as e:
                errori += 1
                print(f"  ERRORE {p['title']}: {e.read().decode()[:200]}")
    return errori


if __name__ == "__main__":
    if "--prova" in sys.argv and prova():
        sys.exit("Query con errori: dashboard non scritta")
    percorso = os.path.join(os.path.dirname(os.path.abspath(__file__)), "grafana", "dashboards", "drone-konsol.json")
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=1)
    print(f"Scritta {percorso} ({len(pannelli)} pannelli). Grafana in Docker su Windows non si accorge da solo del "
          "file nuovo: nella cartella dashboard  docker compose restart grafana")

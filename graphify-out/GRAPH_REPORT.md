# Graph Report - BMO-Grokbot-hackathon  (2026-11-05)

## Corpus Check
- Corpus is ~48,722 words - fits in a single context window. You may not need a graph.

## Summary
- 724 nodes · 1635 edges · 30 communities (25 shown, 5 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 122 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Zone del team
- Simulatore 3D del team: rendering
- Prove della centrale e sito
- Zone editor del team
- Centrale: allarmi e decisioni
- Interruzioni ed eccezioni del volo
- Telecamera finta delle prove
- Grok finto delle prove
- Mondo 3D del simulatore
- Progetto finale e consegne
- Mappa: griglia e A*
- API SimulatedTello ed esempi
- Drone simulato (simulatore.py)
- Ispezione: video, batteria, ToF
- Grafana, Loki ed eventi
- Webhook e console del team
- Lettura di PAD2.step (mappa.py)
- Prove dei casi (prova_casi)
- Missione e giro: foto e atterraggio
- Mira e 4 foto
- Decollo, quota e pad
- Punto principale e risposta persa
- Volo a tratti e ritorno al pad
- CameraAnalysis.py (telecamera con Grok)
- Tappe del giro e posto operatore
- camera.py del team (telecamere con Grok)
- Qualita delle foto
- Giro di prova e avvio del drone
- README del team
- README del repository

## God Nodes (most connected - your core abstractions)
1. `Ispezione` - 56 edges
2. `SimulatedTello` - 37 edges
3. `Zone` - 33 edges
4. `Drone` - 33 edges
5. `DroneSimulato` - 32 edges
6. `Centrale` - 27 edges
7. `Obstacle` - 24 edges
8. `Mappa` - 24 edges
9. `Renderer` - 21 edges
10. `ZoneEditor` - 19 edges

## Surprising Connections (you probably didn't know these)
- `docker-compose del team: Loki, Alloy, Grafana` --semantically_similar_to--> `docker-compose della centrale: Loki, Alloy, Grafana`  [INFERRED] [semantically similar]
  bmo-grokbot-hackathon/log/docker-compose.yml → Hackaton_project/dashboard/docker-compose.yml
- `Operator Console del team (log/index.html)` --semantically_similar_to--> `Console operatore in italiano (dashboard/index.html)`  [INFERRED] [semantically similar]
  bmo-grokbot-hackathon/log/index.html → Hackaton_project/dashboard/index.html
- `drone-grok-pad2` --semantically_similar_to--> `Dipendenze del team: djitellopy, opencv < 5, numpy, av, pygame-ce`  [INFERRED] [semantically similar]
  Hackaton_project/pyproject.toml → bmo-grokbot-hackathon/requirements.txt
- `Mondo da PAD2.step: capannone 24 x 12 m, origine sul pad, yaw 0 = +y orario` --semantically_similar_to--> `PAD2.step letto senza librerie CAD -> griglia 10 cm -> A*`  [INFERRED] [semantically similar]
  bmo-grokbot-hackathon/PROGRAM.txt → Hackaton_project/HANDOFF_GROK.md
- `Zone di navigazione del simulatore (PAD, COLUMN_ROW, AISLE_EAST, ...)` --semantically_similar_to--> `Zone di zones.json: HUB, ZONE_7, UNDER_MEZZ, CENTER, AISLE_EAST, NORTH_OPEN`  [INFERRED] [semantically similar]
  bmo-grokbot-hackathon/PROGRAM.txt → Hackaton_project/HANDOFF_GROK.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Catena di una missione: allarme -> conferma -> coda -> volo -> 4 foto -> verdetto** — hackaton_project_centrale_centrale_sorveglia, hackaton_project_centrale_centrale_nuovo_allarme, hackaton_project_centrale_centrale_decisione, hackaton_project_centrale_centrale_metti_in_coda, hackaton_project_centrale_centrale_lavora, hackaton_project_ispezione_ispezione_esegui_giro, hackaton_project_centrale_verifica_con_grok [INFERRED 0.85]
- **Dashboard e statistiche** — hackaton_project_dashboard_index, hackaton_project_centrale_crea_server, hackaton_project_handoff_grok_eventi_dashboard, hackaton_project_dashboard_docker_compose, hackaton_project_dashboard_crea_grafana [INFERRED 0.85]
- **Sicurezza in volo** — hackaton_project_handoff_grok_stop_emergenza, hackaton_project_handoff_grok_soglie_batteria, hackaton_project_handoff_grok_risposta_persa, hackaton_project_handoff_grok_tof_guasto, hackaton_project_handoff_grok_lock_keepalive, hackaton_project_handoff_grok_conferma_umana [INFERRED 0.85]

## Communities (30 total, 5 thin omitted)

### Community 0 - "Zone del team"
Cohesion: 0.05
Nodes (65): Commander, Zone, main(), parse_args(), main(), parse_args(), _parse_go(), _bounds() (+57 more)

### Community 1 - "Simulatore 3D del team: rendering"
Cohesion: 0.05
Nodes (45): FrameReader, Drone, Camera, Renderer, _apply_keyboard_rc(), get_renderer(), get_shared_drone(), main() (+37 more)

### Community 10 - "Prove della centrale e sito"
Cohesion: 0.13
Nodes (14): GrokFinto, caso(), Centrale di sorveglianza (progetto finale, traccia D): telecamere FISSE, ognuna…, Prova completa della centrale (centrale.py) senza drone, telefoni né rete:…, Risponde come Grok: descrizione dell'evento della telecamera o verdetto sulle…, Configurazione del sito (capannone di PAD2.step), condivisa da centrale.py e…, glob, http_server (+6 more)

### Community 12 - "Zone editor del team"
Cohesion: 0.22
Nodes (8): ZoneEditor, _corner_world(), _replace_zone(), update_zone_bounds(), Path, Surface, Handle, Return a copy with updated AABB; optionally keep hover inside the box.

### Community 2 - "Centrale: allarmi e decisioni"
Cohesion: 0.05
Nodes (28): Centrale, UltimoFotogramma, VisioneGrok, adesso(), b64(), descrivi_evento(), differenza_dal_fondo(), macchia_piu_grande() (+20 more)

### Community 23 - "Interruzioni ed eccezioni del volo"
Cohesion: 0.22
Nodes (8): Bloccato, Interruzione, Ritorno, Exception, Chiama Grok in un thread mentre il drone resta in hovering (il keepalive lo…, Atterrare subito dove si è (tasto l, batteria minima)., Nessun passaggio libero: atterrare dove si è invece di forzare., Batteria sotto BATTERIA_RITORNO: si salta il resto della missione e si torna al…

### Community 3 - "Mondo 3D del simulatore"
Cohesion: 0.07
Nodes (37): SolidAABB, StepMap, Obstacle, Quad3, RoomGeometry, Segment3, _collect_points(), load_step_map() (+29 more)

### Community 5 - "Progetto finale e consegne"
Cohesion: 0.06
Nodes (33): CameraFinta, RispostaPersa, configura_telecamere(), crea_server(), do_GET(), do_POST(), handle_error(), _invia() (+25 more)

### Community 6 - "Mappa: griglia e A*"
Cohesion: 0.10
Nodes (16): Mappa, Ricalcola distanze dagli ostacoli e zona sicura (dopo l'aggiunta di un…, Come linea_sicura ma con un margine diverso da quello della mappa (es. ultimo…, Come linea_sicura ma senza margine: serve per sapere se da p si vede q…, Celle sicure collegate a (x, y)., Punto sicuro più vicino a (x, y); se 'da' è dato, solo tra quelli raggiungibili…, Punto sicuro più vicino raggiungibile in linea retta senza attraversare…, Porte e finestre del perimetro diventano pareti: il drone non può uscire… (+8 more)

### Community 7 - "API SimulatedTello ed esempi"
Cohesion: 0.10
Nodes (10): SimulatedTello, main(), main(), Any, Fly a 50 cm square in the 3D simulator using the Tello-like API., Headless smoke test: takeoff → move → land, assert motion., Height in centimetres (Tello SDK style)., Fly to a world-frame point (metres). Faces the target first so FPV looks ahead. (+2 more)

### Community 9 - "Ispezione: video, batteria, ToF"
Cohesion: 0.14
Nodes (9): Ispezione, Distanza davanti in cm (mediana delle letture valide su 3), None se oltre circa…, Video solo durante il volo: a terra con il video acceso il Tello si…, Accende il video all'allarme, mentre l'operatore decide: al decollo è già…, Il lettore di djitellopy decodifica in un thread che, se il decoder si chiude…, Il fotogramma con il punteggio più alto (qualita_foto: nitidezza divisa per…, Lettore video di djitellopy morto: riavvio e fotogramma piu nitido, Soglie batteria: 40% non parte, 30% torna, 20% atterra (+1 more)

### Community 11 - "Grafana, Loki ed eventi"
Cohesion: 0.11
Nodes (17): conta(), foto(), prova(), righe(), Genera grafana/dashboards/drone-konsol.json (la dashboard «Centrale drone» che…, Ogni query su Loki: stampa quante serie/righe restituisce o l'errore., Numero di eventi nell'intervallo scelto in alto a destra (0 se nessuno)., Pannello di log con una riga leggibile per evento (line_format) invece del JSON… (+9 more)

### Community 13 - "Webhook e console del team"
Cohesion: 0.21
Nodes (18): arrived(), command(), decision(), dispatch_or_queue(), frames(), get_pending(), index(), log_event() (+10 more)

### Community 14 - "Lettura di PAD2.step (mappa.py)"
Cohesion: 0.15
Nodes (17): carica_solidi(), punto(), _classifica(), _leggi_entita(), _prima_lista(), _riempi(), _riferimenti(), _segmenti_sezione() (+9 more)

### Community 15 - "Prove dei casi (prova_casi)"
Cohesion: 0.15
Nodes (11): caso(), conta_foto(), dal_pad(), lontano_dal_pad(), Casi del volo e delle telecamere in simulazione, sulla pianta vera con le zone…, Drone simulato per provare centrale.py e giro_punti.py senza hardware e senza…, Prove in simulazione: prova_casi 22 su 22, prova_centrale 4 su 4, cv2 (+3 more)

### Community 16 - "Missione e giro: foto e atterraggio"
Cohesion: 0.21
Nodes (5): land' finché il drone non risponde ok: se un comando precedente è ancora in…, Percorso A* fino al pad sulla pianta (con la persona e gli ostacoli visti…, Salva il fotogramma migliore corretto (migliora_foto) e, in originali/, quello…, persona = (x_m, y_m) sulla pianta stimato con il telefono, oppure None: allora…, Prova di navigazione: dal pad a ogni tappa [(nome, (x_m, y_m)), ...] in…

### Community 17 - "Mira e 4 foto"
Cohesion: 0.19
Nodes (8): nome_file(), norma(), Nome di tappa usabile in un nome di file: es. '(-2,5; 3,0)' -> '-2_5_3_0'., (x_m, y_m, direzione) per il disegno: legge lo yaw dallo stato senza mandare…, Una sola correzione della direzione se la mira (locale, o Grok) vede il…, Muso verso l'anomalia: verso la stima sulla pianta se c'è, altrimenti verso la…, Quattro foto ruotando sul posto di 90 gradi alla volta, verso est, nord, ovest…, 4 foto a 90 gradi dallo stesso punto (quattro_lati)

### Community 18 - "Decollo, quota e pad"
Cohesion: 0.23
Nodes (5): Manda il comando e vuole 'ok' (True). La risposta si aspetta per la durata…, Legge il pad dallo stato per ATTESA_PAD_S secondi. Restituisce l'ID (1-8) o…, Sopra la posizione stimata del pad: lo cerca; se non lo vede sale (area di…, dal_decollo: misura con h (altezza sul punto di decollo) invece che con il ToF…, Altezza da terra: sensore ToF in basso se plausibile, altrimenti altezza dal…

### Community 19 - "Punto principale e risposta persa"
Cohesion: 0.21
Nodes (11): punto_principale(), zona_della_persona(), scegli_punto_vista(), Missione semplice con il Tello Talent (RMTT), dal 5 novembre: 1. il drone parte…, Zona di zones.json che contiene la persona (a pari merito vince la priorità più…, Dove va il drone per guardare la persona: (punto (x, y) in m oppure None =…, Punto sicuro a 'distanza' dal bersaglio, raggiungibile da 'da', da cui il…, Missione semplice: punto principale della zona, foto, ritorno (+3 more)

### Community 20 - "Volo a tratti e ritorno al pad"
Cohesion: 0.20
Nodes (6): Aggiunge il punto raggiunto; se prosegue in linea retta sostituisce l'ultimo…, Avanza con 'forward' (la distanza la misura il Tello). Senza ToF un comando per…, Percorso A* sulla pianta; un ostacolo non in pianta visto dal ToF viene segnato…, Correzione ritorno al pad (lungo e a sinistra), Taratura con il passo forward 80, Volo continuo con rc (tolto)

### Community 21 - "CameraAnalysis.py (telecamera con Grok)"
Cohesion: 0.31
Nodes (9): ask_grok_with_baseline(), encode_frame(), log(), monitor_camera(), run_grok_async(), Executes API call in the background without freezing the video stream., Full capture / motion / alarm loop for one camera. Designed to run as a…, Resizes and encodes the frame to base64 for the API. (+1 more)

### Community 22 - "Tappe del giro e posto operatore"
Cohesion: 0.24
Nodes (9): anteprima(), _m(), posto_operatore(), Giro di prova a 1,70 m di quota su tutta la pianta: il drone va dal pad a una…, Percorso completo pad -> tappe -> pad sulla pianta, salvato in giro_punti.png.…, Dove sta chi pilota: tra 2 e 4 m dal pad, non dentro un ostacolo, il più…, Posto dell'operatore (tra 2 e 4 m dal pad, lontano dal percorso), ctypes (+1 more)

### Community 24 - "camera.py del team (telecamere con Grok)"
Cohesion: 0.47
Nodes (8): ask_grok_with_baseline(), encode_frame(), log(), monitor_camera(), prep(), run_grok_async(), send(), requests

### Community 26 - "Qualita delle foto"
Cohesion: 0.40
Nodes (5): migliora_foto(), qualita_foto(), Misure di un fotogramma. nitidezza: varianza del laplaciano a metà risoluzione…, Correzione leggera per una sala poco illuminata (5 novembre: luce 23-56 su 255,…, Qualita foto: nitidezza, blocchi H.264, luce; miglioramento leggero

### Community 4 - "Giro di prova e avvio del drone"
Cohesion: 0.06
Nodes (39): pianta_con_zone(), prepara_drone(), comode(), con_i_tasti(), leggi_tappe(), lunghezza(), main(), ordina_tappe() (+31 more)

## Knowledge Gaps
- **9 isolated node(s):** `docker-compose del team: Loki, Alloy, Grafana`, `Taratura con il passo forward 80`, `Posto dell'operatore (tra 2 e 4 m dal pad, lontano dal percorso)`, `README della cartella del team (segnaposto)`, `README del repository BMO-Grokbot-hackathon` (+4 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 253 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Ispezione` connect `Ispezione: video, batteria, ToF` to `Centrale: allarmi e decisioni`, `Giro di prova e avvio del drone`, `Missione e giro: foto e atterraggio`, `Mira e 4 foto`, `Decollo, quota e pad`, `Punto principale e risposta persa`, `Volo a tratti e ritorno al pad`, `Interruzioni ed eccezioni del volo`?**
  _High betweenness centrality (0.169) - this node is a cross-community bridge._
- **Why does `Mappa` connect `Mappa: griglia e A*` to `Giro di prova e avvio del drone`, `Tappe del giro e posto operatore`, `Lettura di PAD2.step (mappa.py)`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Why does `SimulatedTello` connect `API SimulatedTello ed esempi` to `Zone del team`, `Simulatore 3D del team: rendering`?**
  _High betweenness centrality (0.076) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `SimulatedTello` (e.g. with `go_to_zone()` and `sync_map()`) actually correct?**
  _`SimulatedTello` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 11 inferred relationships involving `Zone` (e.g. with `ordered_zones()` and `_zone_viz()`) actually correct?**
  _`Zone` has 11 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Drone` (e.g. with `run_app()` and `_apply_keyboard_rc()`) actually correct?**
  _`Drone` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `docker-compose del team: Loki, Alloy, Grafana`, `Taratura con il passo forward 80`, `Posto dell'operatore (tra 2 e 4 m dal pad, lontano dal percorso)` to the rest of the system?**
  _9 weakly-connected nodes found - possible documentation gaps or missing edges._
# Centrale drone (Grok Hackathon, traccia D)

Telecamere fisse (IP Webcam) assegnate alle zone del capannone; quando una vede qualcosa di grande la dashboard chiede
all'operatore se mandare il Tello Talent. Il drone vola dal pad alla zona a 1,70 m, scatta 4 foto (una ogni 90 gradi),
torna al pad e Grok confronta telecamera e foto: ANOMALY DETECTED o FALSO ALLARME.

    python centrale.py --camere-finte        tutto simulato (in dashboard «Simula evento»)
    python centrale.py --arm --senza-tof     drone vero
    python giro_punti.py HUB ZONE_7          giro di prova sulla pianta (simulato; --arm --senza-tof per il drone vero)
    python prova_casi.py                     prove del volo e delle telecamere
    python prova_centrale.py                 prova completa con la dashboard

Dashboard: http://localhost:5000. Grafana: `cd dashboard`, `docker compose up -d`, http://localhost:3000/d/drone-konsol.
La chiave xAI va in XAI_API_KEY o in `~/chiave_xai.txt`, mai nel progetto. Dettagli e storia: HANDOFF_GROK.md.

| File | Cosa fa |
| --- | --- |
| centrale.py | programma finale: telecamere, dashboard, coda delle missioni, Grok |
| ispezione.py | volo: percorsi sulla pianta, foto, ritorno e atterraggio sul pad, sicurezza |
| giro_punti.py | pianta a 1,70 m, controlli a terra del drone, tasti SPAZIO/BACKSPACE, giro di prova |
| mappa.py, sito.py, zones.json, PAD2.step | pianta del capannone, pad, zone |
| simulatore.py | drone simulato |
| dashboard/ | console operatore (index.html), Grafana + Loki + Alloy |

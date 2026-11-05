## Repository

- `Hackaton_project/` è il progetto drone + Grok (prima in `Documenti\computer vision`, spostato qui il 5 novembre 2026):
  leggere `Hackaton_project/README.md` e `Hackaton_project/HANDOFF_GROK.md` prima di cambiare il volo.
- `bmo-grokbot-hackathon/` (simulatore 3D, zone editor, console webhook) e `CameraAnalysis.py` sono del team.
- Il grafo di graphify copre tutto il repository e sta in `graphify-out/` nella radice: lanciare `graphify` dalla
  radice. Da `Hackaton_project/` aggiungere `--graph ../graphify-out/graph.json` a query/path/explain, e non lanciare
  `graphify update .` lì dentro (creerebbe un secondo grafo).
- Foto, registri dei voli e immagini non vanno nel grafo (`.graphifyignore`) né nel repository (`Hackaton_project/.gitignore`).
- Dopo modifiche ai documenti (soprattutto `HANDOFF_GROK.md`) i concetti del grafo vanno riestratti con `/graphify . --update`;
  `graphify update .` aggiorna solo il codice.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

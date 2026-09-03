# docs/

Indice della documentazione di `roboreason-ros2`. Le cartelle sono divise per
**come si usa un documento**, non per argomento.

| Cartella | Cosa contiene | Quando si apre |
|---|---|---|
| `guide/` | Come si installa e si fa girare il sistema | Quando devi far funzionare qualcosa |
| `reference/` | Come funziona un sottosistema, nel dettaglio | Quando devi modificarlo senza romperlo |
| `history/` | Record datati di cosa è successo | Quando devi capire perché il codice è così |
| `paper/` | Sorgenti LaTeX di quello che diventa un PDF | Quando scrivi per qualcuno fuori dal team |
| `archive/` | Materiale tenuto ma non mantenuto, fuori da git | Quasi mai |

## Cosa c'è dentro

**`guide/`**
- `operator-guide.md` — guida operativa completa: modalità, launch file, GUI.
- `docker-setup.md` — setup del container. Non tracciato da git, vedi nota sotto.

**`reference/`**
- `grasp-geometry-pipeline.md` — come vengono calcolate geometria di presa e di rilascio nelle tre modalità.

**`history/`** — file append only. Non si riscrive il passato, si aggiunge in fondo.
- `session-context.md` — il registro tecnico sessione per sessione. È lungo: cercaci dentro il sottosistema che ti serve prima di leggerlo tutto.
- `audit-2026-07-08.md` — audit read only del codice. I finding critici sono tracciati come bug su Jira, non qui.

**`paper/`**
- `report/` — report tecnico, LaTeX più PDF compilato.
- `planning/` — i tre outline candidati per il paper. Si scrive l'outline B, A e C sono registrati ma non perseguiti.

## Regola: cosa resta `.md` e cosa diventa `.pdf`

Questa è la distinzione che generava confusione, e ha una risposta secca.

**Resta `.md`, e si legge su GitHub:** tutto quello che serve a chi lavora sul
codice, cioè `guide/`, `reference/` e `history/`. GitHub renderizza il markdown
già bene, quindi non c'è nessun motivo di convertirlo. Se una persona esterna
deve leggerlo, la si aggiunge come lettore del repo: costa zero e le arriva
sempre la versione aggiornata.

**Nasce già in LaTeX e diventa `.pdf`:** tutto quello che è destinato a qualcuno
fuori dal team, quindi `paper/`. Un documento che dovrà finire su Teams o essere
letto dal professore **non si scrive in markdown per poi convertirlo**: si scrive
direttamente in `paper/`, si compila, e il PDF va su SharePoint. La conversione a
mano è il punto in cui le versioni divergono.

**Overleaf** ospita solo il paper in scrittura collaborativa. Non è un posto dove
leggere markdown, e non è un archivio.

Se capita un caso isolato in cui un `.md` esistente serve davvero come PDF una
volta sola, si usa `pandoc` e il PDF si considera usa e getta, non una copia da
mantenere allineata.

## Nota su `docker-setup.md`

È escluso da git tramite `.gitignore` da prima di questo riordino. La scelta è
stata preservata, non rivalutata. Se il contenuto non ha niente di specifico
della macchina, conviene tracciarlo: è esattamente il tipo di documento che serve
a chi entra nel progetto.

## Cosa non sta qui

Il backlog e lo stato del lavoro stanno su **Jira**, progetto `ROBOAI`. Vedi
`CLAUDE.md` alla radice del repo per la mappa completa delle fonti.

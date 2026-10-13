# Modalità AGENT: agent VLM in closed loop

Branch `ouroboros`, issue ROBOAI-36 (implementa ROBOAI-23 e ROBOAI-24).
È una versione alternativa del planner e **non va mergiata in main**: vive su
un branch suo perché cambia il modo in cui plan ed esecuzione si parlano.

## L'idea in breve

Nelle modalità LLM, VLM e VLM_LLM il modello scrive tutto il plan in una volta,
Python lo corregge senza dirglielo (altezze, spacing delle release, grasp
width, compensazione della profondità) e il plan manager lo esegue alla cieca.
Se un pick fallisce nessuno se ne accorge.

In modalità AGENT c'è **un'unica conversazione per task** con un VLM che usa il
function calling nativo:

1. **Bozza.** Il modello guarda l'immagine e chiama `submit_plan` con i passi ad
   alto livello ("pick the red cube", "place it on the tray"). Nessuna
   coordinata. La GUI la mostra prima che qualcosa si muova.
2. **Esecuzione.** La stessa conversazione continua. Il modello misura con la
   depth camera, agisce una sequenza di skill alla volta, dopo ogni azione
   riceve un'immagine nuova, e se qualcosa è andato storto ripianifica.
   Termina chiamando `finish`.

Le correzioni deterministiche non spariscono: diventano **tool** che il modello
sceglie di usare, e di cui vede il risultato. Il modello non tocca mai una
coordinata: legge una bbox dall'immagine, il tool di percezione misura e
restituisce un id (`o1` per un oggetto, `a1` per un'area, `s1` per uno spot
libero), e `pick` e `place` prendono quell'id. Prima le azioni prendevano i
numeri, e copiarli era un failure mode a sé: Qwen su Groq perdeva i segni
meno in ogni tool call.

## I tool

| Tool | Cosa fa | Da dove viene |
|---|---|---|
| `detect_objects` | segmenta dalla depth tutto quello che sta sul tavolo: id, colore (dall'RGB), dimensioni; il tavolo e i tray come aree | nuovo: il modello deve solo scegliere l'id giusto, non localizzare |
| `look` | immagine nuova | |
| `inspect_object(bbox)` | top centre, altezza, larghezza, `grasp_point` a metà corpo, `held_height_below_grasp_m`, `looks_empty` | compensazione di profondità e grasp width di `vlm_planner_node` |
| `inspect_area(bbox)` | quota della superficie ed estensione xy | lookup della superficie di `_fix_release_height` |
| `find_free_spot(bbox, footprint_m)` | il punto libero migliore dentro un'area, lontano da oggetti già presenti e dai bordi | `distribute_zone_releases`, ma misurato dalla depth invece che dedotto dal plan |
| `pick(object)` | approach, pick, risalita sull'oggetto misurato | sequenza fissa di skill dell'executor |
| `place(target)` | approach, release (che risale da sola) su uno spot, un'area o sopra un oggetto | idem |
| `move_home` | braccio in home, quasi fuori dall'inquadratura | |
| `update_plan`, `report_progress` | rivedono la bozza e segnano i passi fatti nella GUI | |
| `finish(success, summary)` | chiude il task | |

**Rete di sicurezza.** `pick` e `place` rifiutano coordinate fisicamente
impossibili (sotto il tavolo, fuori dai `workspace.limits` di
`scene_mock.json`, hover oltre il limite in z) e restituiscono al modello il
motivo, senza correggere niente e senza muovere il robot.

**Stato del gripper.** Il gripper non ha sensori. Ogni risultato di azione dice
però cosa gli è stato comandato per ultimo (chiuso dopo un pick, aperto dopo un
place), e `inspect_object` segnala quando ha misurato qualcosa molto sopra il
tavolo, cioè il braccio o l'oggetto che tiene. Senza queste due indicazioni il
modello scambiava il cubo appeso al polso in home per un cubo spinto sul
tavolo.

## Architettura

```
GUI ── plan_task ──▶ agent_node ── get_image / deproject ──▶ camera_services_node
    ── execute_plan ▶    │
    ── cancel_*  ──▶     └──────── execute_skill ─────────▶ ur5_skill_executor_node
```

`agent_node` prende il posto sia del planner sia del plan manager, sugli stessi
nomi di servizio (`plan_task`, `execute_plan`, `cancel_execution`,
`cancel_planning`), quindi il flusso della GUI non cambia: plan, poi execute,
con Stop attivo in tutte e due le fasi. In modalità AGENT
`gui_stack.launch.py` non lancia il plan manager.

Codice, tutto in `robo_reason_planner`:

- `agent/tool_chat.py`: chat multi turno con tool calling per Groq e Nebius
  (dialetto OpenAI), con retry sugli errori transitori
- `agent/geometry.py`: la geometria come funzioni pure
- `agent/tools.py`: schema e implementazione dei tool, sopra un backend
  astratto (camera, deproject, executor)
- `agent/session.py`: le due fasi, il pruning delle immagini (restano nel
  contesto solo le ultime `AGENT_KEEP_IMAGES`), i budget, la cancellazione
- `agent_node.py`: il backend ROS e i servizi

Ogni task scrive una cartella DebugRun con `mode=AGENT`: `transcript.json` (ogni
messaggio, ogni tool call con argomenti e risultato, latenza e token di ogni
chiamata al modello) e `frames/` con tutte le immagini che il modello ha visto.

## Configurazione

In `config.py`, sovrascrivibili con `ROBOREASON_*`:

| Campo | Default | |
|---|---|---|
| `AGENT_MODEL_NAME` | `mistral/ministral-14b-latest` | modello con immagini e tool calling (fino al 2026-10-13 era `nebius/kimi-k3`, budget Nebius esaurito) |
| `AGENT_MAX_MODEL_CALLS` | 60 | per task, bozza inclusa |
| `AGENT_MAX_DURATION_S` | 900 | budget della fase di esecuzione |
| `AGENT_KEEP_IMAGES` | 2 | immagini tenute nel contesto |
| `EXECUTE_TIMEOUT_S` | 900 | alzato da 300: l'esecuzione include le chiamate al modello |

Il parametro ROS `coords` (`auto`, `pixel`, `norm1000`) gestisce i modelli Qwen,
che danno le bbox in coordinate normalizzate 0..1000 qualunque sia la
risoluzione: con `auto` vengono riscalate per i modelli Qwen.

## Avvio

Il branch vive nel worktree `/root/ws/ws_ouroboros`, con un workspace colcon
suo: va avviato da lì, non da `/root/ws/install` (che è `main`). Build una
volta sola; con `--symlink-install` le modifiche Python non richiedono rebuild.

```bash
cd /root/ws/ws_ouroboros
colcon build --symlink-install --base-paths src
```

Dalla GUI, in un terminale nuovo del container (la GUI di `main` sulla stessa
porta va chiusa prima):

```bash
source /opt/ros/humble/setup.bash
source /root/ws/ws_ouroboros/install/setup.bash
source /root/ws/.bashrc_docker          # MISTRAL_API_KEY e le altre chiavi
ROBOREASON_PROFILE=sim ros2 run robo_reason_gui gui_node   # sim, porta 8081
# robot vero: senza ROBOREASON_PROFILE, porta 8080
```

Poi mode **AGENT**: il menu dei modelli ha una lista propria (`AGENT_MODELS` in
`options.py`, perché il client dell'agente raggiunge provider che i client
LLM/VLM non supportano) e parte da `mistral` / `ministral-14b-latest`.
Il resto è il flusso solito: Plan mostra la bozza, Execute avvia il ciclo
chiuso, la traccia live elenca le chiamate, Stop interrompe anche una chiamata
al modello in corso. Ogni run scrive `transcript.json` e `frames/` nella
cartella debug.

A mano, in simulazione:

```bash
ros2 launch robo_reason_sim sim.launch.py gui:=false
ros2 launch robo_reason_bringup gui_stack.launch.py profile:=sim mode:=AGENT \
    mock_robot:=false mock_camera:=false model_name:=mistral/ministral-14b-latest
```

## Fault injection in simulazione

Il gripper shim ha due parametri, spenti di default, per provare il recupero
dagli errori:

```bash
ros2 param set /sim/gripper_shim_node fail_next_grasps 1          # il prossimo grasp scivola
ros2 param set /sim/gripper_shim_node drop_next_grasp_after_s 2.0 # il cubo cade durante il trasporto
```

## Valutazione

Vedi `benchmark/agent_sim/`: i sei task del benchmark (stessa formulazione di
`benchmark/PLAN.md`) più varianti con guasti, valutati in automatico sulle pose
di ground truth di Gazebo, non sul giudizio del modello.

### Risultati della notte del 2026-10-12

Simulazione Gazebo, verdetto sulle pose di ground truth. Ogni riga dei CSV ha
commit, modello, scenario, sub task completati, tempi e numero di chiamate.

| Modello | Scenari superati | Note |
|---|---|---|
| `nebius/kimi-k3` | 10/10 in due giri completi (`results_first_pass.csv` e `results.csv`) | chiamate da 1 a 2 s; esecuzione mediana circa 2 minuti, da 51 s per `sort_easy` a 4 minuti per `pp_hard` |
| `nebius/deepseek-v4.1-flash` | 3/3 (`pp_easy`, `pp_hard`, `sort_easy`) | poi il budget gratuito Nebius è finito (HTTP 402) |
| `groq/qwen3.8-27b` | non valutabile | il free tier Groq ha un limite di 7000 input token al minuto e una sola chiamata di esecuzione ne usa circa 7000 |

Costo: da 60 mila a 470 mila input token per task (cresce con la
conversazione), circa 2 milioni per un giro dei 10 scenari.

Cosa ha mostrato la valutazione, e cosa è stato corretto strada facendo:

* un grasp fallito viene notato (il vecchio bbox risulta ancora occupato) e
  ripetuto; un cubo che cade durante il trasporto viene ritrovato e ripreso;
  un cubo spostato a metà task viene preso dalla nuova posizione
* il modello confondeva il cubo appeso al polso in home con un cubo sul
  tavolo: da qui lo stato del gripper nei risultati e la nota sulle letture
  alte in `inspect_object`
* il modello si inventava le posizioni della fila in `sort_hard`: da qui
  `find_free_spot` con `count` e `arrangement='line'`
* le larghezze erano sempre 0: `local_plane_centroid` del camera node fa
  collassare i pixel di bordo sul centro della faccia, ora la larghezza viene
  da bbox, intrinseci e TF. I bbox dei modelli sono larghi (includono le facce
  laterali), quindi la larghezza è sovrastimata, circa 6.7 cm per un cubo da
  4.5 cm
* posizioni oltre il reach mandavano l'IK in errore: ora vengono rifiutate e
  `find_free_spot` non le propone più

## Provider e consumo di token

Misurato su Groq per una chiamata di esecuzione: immagine 640x480 circa 1800
token, schema dei tool circa 1600, system prompt circa 650, più la history che
cresce a ogni turno. Contromisure, tutte in `config.py`:

| Campo | Default | Effetto |
|---|---|---|
| `AGENT_KEEP_IMAGES` | 1 | solo l'ultima immagine resta nel contesto |
| `AGENT_IMAGE_WIDTH` | 448 | immagine inviata ridotta (Groq ridimensiona da solo, altri contano per risoluzione) |
| `AGENT_HISTORY_TURNS` | 8 | i turni più vecchi vengono accorciati, e di più se ci si avvicina al limite per richiesta |
| `AGENT_MAX_INPUT_TOKENS` | 300000 | budget per task |
| `AGENT_TPM_LIMITS` | groq 8000 | pacing lato client dei token al minuto |
| `AGENT_MAX_REQUEST_TOKENS` | groq 6500 | limite della singola richiesta |

In più: schema dei tool più corto (meno 27%), e la stessa azione fallita tre
volte di fila chiude il task invece di continuare a spendere.

**Groq free tier** (qwen3.8-27b, unico modello vision): 8000 token al minuto,
7000 per richiesta, **200000 al giorno**. Funziona tecnicamente (coordinate
0..1000 relative alla larghezza su entrambi gli assi, gestite da
`coords=norm1000`), ma basta per 2 o 3 task semplici al giorno, ciascuno di
circa 10 minuti per via del pacing. Utile solo come smoke test.

**Provider già cablati in `ToolChatClient`**, basta la chiave in
`{PROVIDER}_API_KEY` (e il dominio permesso dal sandbox di rete, se c'è):
`gemini`, `mistral`, `openrouter`, `github`, `cloudflare` (più
`CLOUDFLARE_ACCOUNT_ID`). Free tier verificati a ottobre 2026:

| Provider | Free tier | Vision e tool | Note |
|---|---|---|---|
| Mistral (piano Experiment) | circa 1 miliardo di token al mese per modello, 1 richiesta al secondo | Mistral Small 3.2, Medium | richiede verifica del telefono e consenso al training sui dati |
| Google Gemini (Flash) | poche decine o centinaia di richieste al giorno, limiti visibili solo in AI Studio | sì | Pro non è più nel free tier |
| Cloudflare Workers AI | 10000 neuron al giorno, cioè circa 240 mila input token con qwen3.8 o 400 mila con Llama 4 Scout | sì | stesso modello di Groq |
| GitHub Models | 50 richieste al giorno, 8000 token per richiesta | GPT 4.1 | ci sta solo con la compattazione |
| OpenRouter free | 50 richieste al giorno senza credito | variabile | i modelli free cambiano spesso |

### Mistral free tier (2026-10-13)

Con il piano Experiment di Mistral solo i modelli Ministral sono abilitati
(Medium e Small rispondono con limite 0 richieste al minuto). `ministral-14b`
localizza male: bbox spostate da 30 a 200 px. Da qui tre aggiunte, che
servono a qualunque modello debole e anche al robot vero:

* `detect_objects`: la localizzazione la fa la geometria sulla depth (altezze
  relative all'altezza dominante, quindi indifferenti a offset di
  calibrazione e al pavimento della stanza nell'inquadratura; la base del
  robot è esclusa; il colore viene dall'immagine). Il modello sceglie l'id.
  In AGENT lo snapping del camera node sui piani degli oggetti è spento
  (`LOCAL_PLANE_CENTROID_ENABLED=false`), perché gonfiava ogni oggetto di 25 px.
* **Controllo automatico dopo pick e place**: il braccio torna in home, la
  depth guarda, e il risultato dice se l'oggetto ha lasciato il suo posto o
  se è arrivato, con la lista aggiornata degli oggetti e id nuovi. Sostituisce
  il sensore che il gripper non ha. Prima di questo Ministral dichiarava
  successo su tutti e quattro gli scenari con guasti senza controllare.
* `inspect_object` legge prima il punto esatto: se è vuoto lo dice e propone
  l'oggetto più vicino, invece di agganciarlo in silenzio.

Risultato con `mistral/ministral-14b-latest`: **9 scenari su 10**, compresi
tutti e quattro quelli con guasti. L'unico fallito è `arith_hard` (1/4), per
un errore di ragionamento del modello (ha considerato il cubo bianco, valore
3, maggiore di 3): è proprio il failure mode che il benchmark misura, e non va
corretto nel codice. Costo per task da 14 mila a 59 mila input token (con
kimi-k3, prima di queste ottimizzazioni, da 60 mila a 470 mila), circa 1 a 3
minuti di esecuzione.

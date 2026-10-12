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
sceglie di usare, e di cui vede il risultato. Il modello non fa mai aritmetica
sulla geometria: legge una bbox dall'immagine, chiede al tool la posizione
metrica e passa quel numero, invariato, a `pick` o `place`.

## I tool

| Tool | Cosa fa | Da dove viene |
|---|---|---|
| `look` | immagine nuova | |
| `inspect_object(bbox)` | top centre, altezza, larghezza, `grasp_point` a metà corpo, `held_height_below_grasp_m`, `looks_empty` | compensazione di profondità e grasp width di `vlm_planner_node` |
| `inspect_area(bbox)` | quota della superficie ed estensione xy | lookup della superficie di `_fix_release_height` |
| `find_free_spot(bbox, footprint_m)` | il punto libero migliore dentro un'area, lontano da oggetti già presenti e dai bordi | `distribute_zone_releases`, ma misurato dalla depth invece che dedotto dal plan |
| `pick(grasp_point, grasp_width)` | approach, pick, risalita | sequenza fissa di skill dell'executor |
| `place(position, held_height)` | approach, release (che risale da sola) | idem |
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
| `AGENT_MODEL_NAME` | `nebius/kimi-k3` | modello con immagini e tool calling |
| `AGENT_MAX_MODEL_CALLS` | 60 | per task, bozza inclusa |
| `AGENT_MAX_DURATION_S` | 900 | budget della fase di esecuzione |
| `AGENT_KEEP_IMAGES` | 2 | immagini tenute nel contesto |
| `EXECUTE_TIMEOUT_S` | 900 | alzato da 300: l'esecuzione include le chiamate al modello |

Il parametro ROS `coords` (`auto`, `pixel`, `norm1000`) gestisce i modelli Qwen,
che danno le bbox in coordinate normalizzate 0..1000 qualunque sia la
risoluzione: con `auto` vengono riscalate per i modelli Qwen.

## Avvio

Dalla GUI: mode **AGENT**, modello fra quelli vision (`kimi-k3` è il default).
A mano, in simulazione:

```bash
ros2 launch robo_reason_sim sim.launch.py gui:=false
ros2 launch robo_reason_bringup gui_stack.launch.py profile:=sim mode:=AGENT \
    mock_robot:=false mock_camera:=false model_name:=nebius/kimi-k3
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

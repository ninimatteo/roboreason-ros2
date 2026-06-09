related: [[3000_Meeting]] 
tags: #meeting, #project, #llm, #vlm, #robotics, #ros2, #ur5

---
---

Data: [[22-04-2026]]

# LLM/VLM per Manipolazione Robotica su UR5 — Documento di Progetto

## Partecipanti

|Nome|Ruolo|
|---|---|
|Matteo|Implementazione ROS2 a basso livello, skill primitive sul robot fisico|
|Filippo Favali|Integrazione LLM/VLM nella rete ROS2, reasoning e planning|
|Filippo Bernabei|Integrazione LLM/VLM nella rete ROS2, reasoning e planning|

---

## Overview del Progetto

L'obiettivo è testare e confrontare LLM (Large Language Models) e VLM (Vision-Language Models) come moduli di **pianificazione ad alto livello** per un braccio robotico UR5 a banco, in task di manipolazione reale.

Il sistema segue il paradigma **Sense → Plan → Act**:

- **Sense**: percezione della scena (proxy manuale nella Fase 1, rete neurale nella Fase 2)
- **Plan**: LLM o VLM genera una sequenza di skill primitive da eseguire
- **Act**: nodo ROS2 esegue le skill sul robot fisico

Il progetto si articola in **due fasi principali**, con un'estensione futura per obstacle avoidance.

---

## Architettura del Sistema

```
┌─────────────────────────────────────────────────────────┐
│                     FASE 1 — LLM                        │
│                                                         │
│  [Perception Proxy]                                     │
│  (misura manuale, frame base robot)                     │
│         │                                               │
│         ▼                                               │
│  [LLM Planner]  ──── skill set Ψ + osservazione ────►  │
│  (es. FHP, CoT-SC, ReAct…)                              │
│         │                                               │
│         ▼                                               │
│  [Nodo ROS2] ──► UR5 fisico (a banco)                   │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                     FASE 2 — VLM                        │
│                                                         │
│  [RealSense Camera]                                     │
│  (posizione TBD: eye-in-hand o eye-to-hand)             │
│         │                                               │
│         ▼                                               │
│  [VLM Planner]  ──── immagine + skill set Ψ ─────────► │
│  (perception integrata nel modello)                     │
│         │                                               │
│         ▼                                               │
│  [Nodo ROS2] ──► UR5 fisico (a banco)                   │
└─────────────────────────────────────────────────────────┘
```

**Nota**: La Fase 2 utilizza la stessa infrastruttura ROS2 della Fase 1. Il cambio riguarda solo il modulo di planning (LLM → VLM) e il modulo di percezione (proxy → camera reale).

---

## Setup Hardware

|Componente|Dettaglio|
|---|---|
|Braccio robotico|UR5, montaggio a banco (fisso)|
|End-effector|Robotiq 2F (gripper a due dita)|
|Camera|Intel RealSense (posizione TBD)|
|Frame di riferimento|Base del robot (coordinate fisse)|

### Posizionamento camera (aperto)

- **Eye-to-hand**: camera fissa esterna che guarda il workspace dall'alto o di lato → visione globale della scena, stabile
- **Eye-in-hand**: camera montata sull'EE → necessaria per la skill _Search_, ma introduce complessità di calibrazione
- **Opzione ibrida**: una RealSense fissa + eventualmente una seconda sull'EE (richiedendo prestito da altri ricercatori del gruppo)

> **Da decidere** prima di iniziare la Fase 2.

---

## Skill Primitive (Ψ)

Il set di skill definisce le capacità del robot esposte al pianificatore LLM/VLM. Ogni skill è un algoritmo di controllo a basso livello implementato nel nodo ROS2.

---

### ψ₁ — `approach(target_position, offset=0.1, approach_direction)`

**Descrizione**: Muove l'EE (End-Effector) nell'intorno di un punto target, fermandosi a una distanza di offset (default 10cm). Questa skill è generica: può essere usata sia per avvicinarsi a un oggetto prima di prenderlo, sia come semplice `move_to` in un punto dello spazio operativo.

**Parametri**:

- `target_position`: posizione [x, y, z] nel frame della base robot
- `offset`: raggio della "bolla" di approccio (default: 0.1 m, variabile)
- `approach_direction`: direzione di avvicinamento (dall'alto `z`, laterale `x` o `y`)

**Output**: EE posizionato a `offset` dal target, nella direzione specificata, pronto per la skill successiva.

---

### ψ₂ — `pick(target_position, grasp_axis, come_back=False)`

**Descrizione**: Completa il movimento di presa iniziato da `approach`. Esegue il movimento finale verso l'oggetto lungo l'asse specificato, chiude il gripper, e opzionalmente torna alla posizione di arrivo dell'`approach` precedente.

**Euristica di presa**:

- `grasp_axis = z`: presa dall'alto. L'EE scende lungo z con orientamento fisso (pinza verticale). Usata per oggetti accessibili dall'alto.
- `grasp_axis = x` o `y`: presa laterale. L'asse z dell'EE rimane verticale (fisso); il robot avanza nel piano (x,y) nella direzione ottimale per afferrare il lato dell'oggetto.

**Parametri**:

- `target_position`: posizione [x, y, z] dell'oggetto
- `grasp_axis`: asse di avvicinamento finale (`z`, `x`, `y`)
- `come_back`: se `True`, dopo la presa torna alla posizione di offset dell'`approach` precedente; se `False`, si ferma dove ha preso l'oggetto

---

### ψ₃ — `release(release_position, come_back=False)`

**Descrizione**: Apre il gripper e rilascia l'oggetto in una posizione target. Il rilascio avviene lungo il piano z (l'EE si abbassa fino alla quota target, rilascia, risale).

**Parametri**:

- `release_position`: posizione [x, y, z] dove rilasciare l'oggetto
- `come_back`: se `True`, dopo il rilascio torna alla posizione precedente al `release`; se `False`, rimane in posizione

---

### ψ₄ — `search()` _(opzionale / TBD)_

**Descrizione**: Se la camera è eye-in-hand e il campo visivo non copre l'intera scena, questa skill esegue una traiettoria di esplorazione per trovare gli oggetti sul tavolo.

**Stato**: Non ancora definita. Possibili implementazioni:

- Traiettoria di scan predefinita (es. sweep sul workspace)
- Sostituita completamente da `approach(known_position)` se la scena è nota a priori
- Necessaria solo in Fase 2 con camera eye-in-hand

> **Da decidere** in funzione della scelta del posizionamento camera.

---

## Task Benchmark

I task sono definiti a priori per garantire una baseline riproducibile e un confronto diretto LLM vs VLM.

### Task primari

|ID|Task|Skill coinvolte|Complessità|
|---|---|---|---|
|T1|**Pick & Place**: prendi oggetto A, mettilo in posizione B|approach, pick, approach, release|Bassa|
|T2|**Sorting**: separa oggetti per colore/tipo in zone distinte|approach, pick, release × N|Media|
|T3|**Stacking**: impila oggetti in ordine|approach, pick, approach, release × N|Media|
|T4|**Aritmetica con blocchetti**: conta/raggruppa blocchetti per eseguire operazioni semplici (es. 2+3)|approach, pick, release × N|Media-Alta|

### Task aggiuntivi proposti _(da valutare)_

|ID|Task|Note|
|---|---|---|
|T5|**Set the table**: disponi oggetti in posizioni predefinite (es. piatto, bicchiere, posata)|Task lifted goal, richiede ragionamento spaziale|
|T6|**Unstack & reorder**: smonta una pila e ricostruiscila in ordine diverso|Richiede pianificazione multi-step|
|T7|**Bin picking con categoria**: metti tutti gli oggetti rossi nel contenitore A, gli altri nel B|Richiede ragionamento su attributi, utile per Fase 2 VLM|
|T8|**Assembly semplice**: inserisci un oggetto in un supporto/slot|Richiede precisione, test del controllo fine|

---

## Metriche di Valutazione _(aperte)_

Ispirate al framework di Filippo Favali (RO-MAN 2025):

|Metrica|Descrizione|
|---|---|
|**Task Success Rate (TSR)**|% di sub-task completati correttamente sul totale richiesti|
|**Task Safety (TS)**|Il robot ha causato danni/collisioni? (binario)|
|**Action Efficiency (AETS)**|Rapporto sub-task completati / azioni totali eseguite|
|**Planning latency**|Tempo di generazione del piano da parte del modello|
|**N. skill calls**|Numero di skill invocate per completare il task|

> Le metriche saranno definite in dettaglio prima dei primi esperimenti.

---

## Collegamento con RoboReason Lab

Il framework **RoboReason Lab** (Favali et al., 2025) è il punto di partenza naturale per questo progetto. È già testato in simulazione con UR5 + Robotiq85 in CoppeliaSim.

### Cosa riusare

- **Architettura planning**: EmbodiedAgent con metodologie di reasoning modulari (AA, FHP, ReAct, SR, CoT-SC, ToT)
- **LLMClient**: interfaccia GROQ per accesso ai modelli
- **Struttura skill**: interfaccia astratta skill → parametri → esecuzione
- **Metriche**: TSR, TS, AETS già implementate

### Cosa adattare / riscrivere

- **Execution component**: sostituire CoppeliaSim con nodo ROS2 per UR5 fisico a banco
- **Skill set**: le skill del progetto (approach, pick, release, search) differiscono da quelle del framework originale (navigate, pick, place, hold) perché manca la base mobile e l'approccio è esplicitato come skill separata
- **Perception component**: in Fase 1, proxy manuale (dict con coordinate); in Fase 2, RealSense → struttura dati equivalente
- **Euristiche di presa**: da implementare ex-novo per Robotiq 2F su UR5 a banco

> **Azione**: Filippo F. e Filippo B. valutano la fattibilità del porting di RoboReason Lab su ROS2.

---

## Estensione Futura — Obstacle Avoidance

Nella seconda iterazione del progetto si prevede di integrare algoritmi di obstacle avoidance direttamente nel nodo ROS2, in modo trasparente rispetto al pianificatore LLM/VLM.

|Algoritmo|Acronimo|Caratteristica principale|
|---|---|---|
|Model Predictive Control|MPC|Ottimizzazione su orizzonte finito, adatto a vincoli noti|
|Control Barrier Functions|CBF|Garanzie formali di safety, integrabile con controller esistenti|
|Model Predictive Path Integral|MPPI|Ottimizzazione stocastica, adatto a ambienti dinamici|

> La scelta dell'algoritmo dipenderà dai risultati della Fase 1/2 e dai vincoli computazionali del setup.

---

## To-Do List

### Matteo

- [ ] Implementare skill `approach` su ROS2 con parametro offset e direzione
- [ ] Implementare skill `pick` con euristica grasp_axis (z, x, y) e flag come_back
- [ ] Implementare skill `release` con flag come_back
- [ ] Definire e implementare il nodo ROS2 di interfaccia (ricezione piano → esecuzione skill)
- [ ] Definire il formato dello skill call (JSON? ROS2 service? action server?)
- [ ] Decidere posizionamento camera RealSense con il team
- [ ] Raccogliere misure manuali per il perception proxy (Fase 1)

### Filippo Favali + Filippo Bernabei

- [ ] Valutare porting di RoboReason Lab su ROS2 (da CoppeliaSim a hardware reale)
- [ ] Definire interfaccia LLM → nodo ROS2 (formato del piano generato)
- [ ] Scegliere metodologia di reasoning di partenza (suggerito: FHP o CoT-SC per semplicità iniziale)
- [ ] Scegliere modello LLM iniziale (es. LLaMA 3.3 70B via GROQ)
- [ ] Fase 2: integrare VLM in sostituzione dell'LLM + perception proxy

### Team (decisioni condivise)

- [ ] Definire formato della perception proxy (dict JSON con pose 6DoF? solo XYZ centroide?)
- [ ] Definire lista task definitiva per il benchmark (da T1–T4 + selezione da T5–T8)
- [ ] Definire metriche di valutazione operative
- [ ] Decidere posizionamento camera (eye-to-hand / eye-in-hand / ibrido)
- [ ] Decidere se implementare skill `search` o sostituirla con `approach`

---

## Note e Osservazioni

- La **Fase 1 è un'istantanea**: la perception proxy misura la condizione iniziale della scena una sola volta. Il pianificatore non può richiedere aggiornamenti percettivi iterativi in questa fase.
- Il confronto LLM vs VLM è valido solo se i **task e le condizioni iniziali sono identici** nelle due fasi.
- Dalla letteratura (Favali et al., RO-MAN 2025): **CoT-SC è il metodo più robusto** in termini di safety e task success rate. FHP è il più efficiente in token. Si consiglia di partire con FHP per semplicità, poi confrontare con CoT-SC.
- La dimensione del modello LLM ha **impatto marginale** sulle performance: modelli piccoli (17B) possono essere comparabili a modelli grandi (120B) in un framework ben strutturato.
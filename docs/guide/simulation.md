# Simulazione Gazebo (virtual test environment)

Un ambiente dove far girare tutta la pipeline senza il robot: planner, plan
manager ed executor sono gli stessi del laboratorio, cambiano solo il driver
del robot e quello della camera, sostituiti da Gazebo. Issue: ROBOAI-33.

Non è un gemello fisico fedele, ed è voluto: serve a vedere che plan fa il
modello, come viene eseguito e come si comporta il sistema quando qualcosa va
storto.

## Avvio dalla GUI

```bash
ROBOREASON_PROFILE=sim ros2 run robo_reason_gui gui_node
```

e poi http://localhost:8081. La GUI avviata con il profilo `sim` ha il badge
**SIMULATION** nell'header e una card **Simulation** al posto delle card UR
driver e Camera: Gazebo sostituisce entrambi i driver. Si preme Start (con o
senza finestra di Gazebo), si aspetta il LED verde, poi si avvia lo stack come
sempre, con "Mock robot" e "Mock camera" già disattivati.

La GUI della simulazione usa la porta 8081 (`GUI_PORT_SIM`), quella del robot
la 8080: per il digital twin si aprono **due GUI**, una per profilo, che
convivono. Ognuna, quando avvia il suo stack, ripulisce solo i processi
rimasti nel proprio namespace, mai quelli dell'altra.

## Avvio a mano

Due comandi, in due terminali del container:

```bash
ros2 launch robo_reason_sim sim.launch.py
ros2 launch robo_reason_bringup gui_stack.launch.py profile:=sim \
    mode:=VLM mock_robot:=false mock_camera:=false model_name:=nebius/kimi-k2.6
```

Il primo avvia Gazebo con la scena, il secondo lo stack RoboReason sopra.
`gui:=false` sul primo lo fa girare senza finestra.

## Cosa c'è nella scena

La world viene **generata a ogni avvio da `scene_mock.json`**: tavolo, tray e
cubi stanno esattamente dove li vede la modalità LLM. Per cambiare la scena si
modifica `scene_mock.json`, oppure si passa `scene:=/percorso/altra_scena.json`.

Il frame del mondo coincide con il frame "base" del controller UR, quello in
cui lavorano l'IK dell'executor e `scene_mock.json`. Le coordinate della scena,
quelle di Gazebo e quelle dell'IK sono quindi le stesse.

Camera, posa del robot e tolleranza del gripper stanno in
`src/robo_reason_sim/config/sim.yaml`.

## Gripper "magico"

Non c'è un gripper fisico simulato. `gripper_shim_node` serve lo stesso
servizio `set_io` del driver UR, quindi l'executor non sa di essere in
simulazione:

- **pin alto (close):** il cubo più vicino viene agganciato al polso **solo se**
  il suo centro è entro `grasp_tolerance_m` (3,5 cm) dal TCP. Altrimenti il
  gripper si chiude sul vuoto e il cubo resta dov'è.
- **pin basso (open):** il cubo viene sganciato e cade su quello che c'è sotto.

Un cubo con un altro cubo appoggiato sopra **non** si può prendere: scendendo
dall'alto le dita incontrerebbero prima quello sopra. Se il TCP è vicino a
entrambi viene preso solo quello sopra (`robo_reason_sim/grasp_rules.py`).

Come sul robot vero, il comando IO riesce sempre: l'executor riporta success
anche quando il grasp è mancato. Lo si vede solo nell'immagine successiva, che
è esattamente il caso che un closed loop deve gestire. Il log del nodo dice
cosa è successo (`Grasped ...` oppure `Closed on nothing ...`).

## Reset della scena

Il pulsante **Reset scene** nella card Simulation (o il servizio
`/sim/reset_simulation`) rilascia il gripper, riporta il braccio in home e
rimette ogni cubo nella posa iniziale di `scene_mock.json`. È disabilitato
mentre un comando è in corso.

## Nomi e namespace

Tutto gira nel namespace `/sim` (profilo `sim` di
`robo_reason_bringup/interfaces.yaml`): `/sim/plan_task`, `/sim/joint_states`,
`/sim/camera/color/image_raw` e così via. Per questo simulazione e robot vero
possono girare **insieme** nello stesso grafo ROS (digital twin) senza
collisioni di nomi. Anche il TF della simulazione resta dentro `/sim`.

Topic utili in più rispetto al robot vero:

- `/sim/ground_truth/poses`: la posa vera di ogni oggetto, da Gazebo. Utile
  per verificare in automatico se un task è riuscito.

## Differenze dal robot vero

- **Calibration:** niente ChArUco. La posa della camera è nota esattamente e
  arriva al camera node tramite `FIXED_CAMERA_POSE`, impostata da
  `gui_stack.launch.py` quando il profilo è `sim`.
- **Clock:** lo stack non usa il tempo di simulazione. Il camera node giudica
  se un frame è vecchio da quando l'ha ricevuto (orologio monotonic dell'host),
  non dal timestamp del frame, che in Gazebo è in tempo simulato. Con
  `use_sim_time` ogni nodo riceveva `/clock` a 1000 Hz e restava al 60, 80% di
  CPU anche da fermo.
- **Controller:** `joint_trajectory_controller` invece dello
  `scaled_joint_trajectory_controller` (in simulazione non c'è speed scaling).
  I controller girano a 100 Hz invece dei 500 del driver vero, quindi anche
  `joint_states` esce a 100 Hz.
  Le tolleranze in `src/robo_reason_sim/config/ur_controllers.yaml` decidono
  quando una trajectory va in abort, l'equivalente simulato di un protective
  stop: stringendole si provocano abort voluti.
- **Fisica e aspetto:** cubi e tray sono box colorati, il robot non ha un
  gripper visibile. Non servono fedeltà fisica né fotorealismo.

## Problemi noti

- **Intrinseci della camera:** il sensore RGB-D di Gazebo Fortress pubblica in
  `camera_info` gli intrinseci di default (320x240) qualunque sia la
  risoluzione. La world li dichiara esplicitamente per questo; non togliere il
  blocco `<lens><intrinsics>` da `world_builder.py`.
- **Pacchetti in `install/` di package rimossi:** il launch di Gazebo scansiona
  tutti i package installati e si blocca su quelli il cui sorgente non esiste
  più (è successo con `robo_reason_real` e `robo_reason_prompts`). Se ricompare
  un errore `package '...' not found`, cancellare `install/<package>` e
  `build/<package>`.
- **Più simulazioni sulla stessa macchina:** il `ROS_DOMAIN_ID` isola ROS ma
  **non** Gazebo, che ha i suoi canali. Per questo `sim.launch.py` imposta
  `IGN_PARTITION` a `roboreason_domain<ROS_DOMAIN_ID>` quando non è già
  impostato (ROBOAI-41): due simulazioni in domini diversi non si vedono anche
  in Gazebo. Un `IGN_PARTITION` esplicito vince. Per interrogare Gazebo a mano
  (`ign topic`, `ign service`) va esportata la stessa partizione. Le GUI
  ripuliscono solo i processi rimasti nel proprio dominio e nella propria
  partizione.
- **Finestra di Gazebo vuota:** di solito c'è un altro server Gazebo con lo
  stesso mondo nella stessa partizione, per esempio un headless rimasto da una
  sessione precedente avviata prima di ROBOAI-41. `ign service -i --service
  /world/roboreason/scene/info` mostra due provider; va chiuso quello vecchio.

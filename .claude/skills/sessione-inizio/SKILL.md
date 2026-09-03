---
name: sessione-inizio
description: Apre una sessione di lavoro su RoboAI. Recupera lo stato da Jira, sceglie il task, lo mette In Progress e avvia il timer Clockify. Usare quando l'utente dice che sta iniziando a lavorare, che riprende da dove aveva lasciato, o invoca /sessione-inizio.
---

# Inizio sessione

Obiettivo: in un solo comando l'utente sa a che punto era, sceglie su cosa
lavorare, e il tracciamento parte da solo.

Non chiedere conferme superflue. L'unica domanda che serve davvero è quale task
prendere, e solo quando non è ovvio.

## 1. Ricostruisci lo stato, da Jira

Jira è l'unica fonte dello stato del lavoro. Non leggere file locali per sapere
cosa c'è da fare.

Cerca in quest'ordine e fermati al primo risultato utile:

1. **C'è già qualcosa In Progress?** Allora la sessione precedente non era stata
   chiusa, o si riprende quella.
   ```
   project = ROBOAI AND status = "In Progress" AND assignee = currentUser()
   ```
2. **Cosa scade prima?**
   ```
   project = ROBOAI AND statusCategory != Done AND assignee = currentUser() ORDER BY duedate ASC
   ```

Controlla anche se il task candidato è bloccato da qualcosa di aperto: le
description dei task su questo progetto dichiarano le dipendenze in chiaro
(per esempio la raccolta dati è bloccata dal restart gate). Se è bloccato, dillo
e proponi lo sbloccante.

## 2. Presenta la situazione, corta

Tre righe, non un report:

- dove eravamo rimasti (task In Progress, oppure l'ultimo chiuso)
- cosa scade adesso, con la data
- il task proposto, e perché quello

Poi chiedi conferma solo se ci sono due candidati plausibili. Se il prossimo è
ovvio per scadenza e dipendenze, proponilo e vai avanti.

## 3. Metti il task In Progress

Se non lo è già:

```
transitionJiraIssue  →  transition id "21"  (In Progress)
```

Gli id delle transition su questo progetto: `11` To Do, `21` In Progress,
`31` In Review, `41` Done. Sono globali, valgono per tutti i tipi di issue.

## 4. Avvia Clockify

```bash
.claude/scripts/clockify.sh start "ROBOAI-xx breve descrizione"
```

La descrizione inizia sempre con la chiave dell'issue, così i report Clockify
sono riconciliabili con Jira.

Se lo script fallisce per mancanza di API key, dillo in una riga e vai avanti
lo stesso: la sessione non si blocca per il tracciamento del tempo. Non chiedere
mai all'utente di incollare la chiave in chat.

## 5. Prepara il terreno

Se il task tocca il codice, apri il branch con la convenzione del progetto:

```bash
git checkout -b feature/ROBOAI-xx-descrizione-breve
```

Solo per lavoro su codice. Per doc e chore si lavora direttamente su `main`.

Poi leggi la description completa dell'issue e i file `docs/` che cita, così
parti con il contesto giusto invece di ricostruirlo a metà lavoro.

## Regola da rispettare sempre

Se è in corso una raccolta dati di benchmark, vale il **code freeze**: non si
modifica codice che cambia il comportamento a runtime, nemmeno per sistemare un
bug noto. Se il task scelto violerebbe il freeze, fermati e segnalalo.

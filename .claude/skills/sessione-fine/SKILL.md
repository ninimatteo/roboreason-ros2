---
name: sessione-fine
description: Chiude un task o una giornata di lavoro su RoboAI. Ferma Clockify, registra il tempo e un commento su Jira, aggiorna il registro tecnico, fa transitare l'issue e propone il prossimo task. Usare quando l'utente dice che ha finito, che chiude per oggi, o invoca /sessione-fine.
---

# Fine sessione

Due casi distinti, chiedi quale se non è chiaro dal contesto:

- **Task finito**: si chiude il task e si può passare al prossimo.
- **Giornata finita**: si stacca, il task resta In Progress e si riprende domani.

Il grosso della procedura è comune. Le differenze sono ai punti 4 e 5.

## 1. Ferma il timer

```bash
.claude/scripts/clockify.sh stop
```

Annota la durata che stampa: serve al punto 3.

## 2. Raccogli cosa è stato fatto

Non chiederlo all'utente, ricavalo:

```bash
git log --oneline main..HEAD
git status --short
```

Più quello che è successo in questa conversazione. Riassumi in tre o quattro
punti concreti, con i nomi dei file toccati. Se c'è lavoro non committato,
segnalalo: è la cosa che si perde più facilmente fra una sessione e l'altra.

## 3. Scrivi su Jira

Un commento sull'issue con cosa è stato fatto e cosa resta, più il worklog con
la durata letta al punto 1. Se il lavoro è già stato committato, la via più
pulita è la smart commit nel messaggio:

```
ROBOAI-xx #comment cosa è stato fatto #time 3h
```

Altrimenti usa `addCommentToJiraIssue` e `addWorklogToJiraIssue`.

Il commento va scritto con le convenzioni del progetto: italiano con i termini
tecnici in inglese, niente trattini lunghi.

## 4. Fai transitare l'issue

**Se il task è finito**, chiedi quale dei due:

- **In Review** (transition `31`) quando serve che qualcun altro guardi, o quando
  è un risultato che va validato prima di dichiararlo chiuso.
- **Done** (transition `41`) quando è chiuso e verificato.

Non decidere da solo fra i due: la differenza è di sostanza, non di forma.

**Se è finita solo la giornata**, non toccare lo stato. L'issue resta In Progress
ed è esattamente così che la sessione di domani la ritrova.

## 5. Aggiorna il registro tecnico

Solo se è successo qualcosa che vale fra un mese: una decisione, un bug con la
sua causa, un risultato sperimentale, un vincolo scoperto.

Aggiungi in fondo a `docs/history/session-context.md`. È un file append only:
non si riscrive quello che c'era, si aggiunge.

Il lavoro di routine non ci va. Se il resoconto è già tutto nel commento Jira,
questo passaggio si salta.

## 6. Proponi il prossimo

**Se il task è finito**, cerca il prossimo per scadenza e dipendenze:

```
project = ROBOAI AND statusCategory != Done AND assignee = currentUser() ORDER BY duedate ASC
```

Proponine uno solo, con la scadenza e il motivo. Chiedi se partire subito: in
caso affermativo, prosegui con la skill `sessione-inizio`.

**Se è finita la giornata**, chiudi con due righe: dove si è arrivati e qual è la
prima cosa da fare domani. Niente altro.

## Come si riprende domani, anche da un'altra chat

Non serve nessun file di stato locale, e non va creato: sarebbe una copia di
Jira destinata a divergere. Il contesto si ricostruisce da solo perché sta in
tre posti vivi:

- **Jira** tiene il task In Progress e i commenti di questa sessione.
- **`docs/history/session-context.md`** tiene il record tecnico.
- **`CLAUDE.md`** dice a ogni nuova sessione dove guardare.

Domani basta `/sessione-inizio` in qualsiasi chat.

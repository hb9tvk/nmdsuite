#!/usr/bin/env python
"""Apply DE / FR / IT translations to the locale ``django.po`` files.

The ``TRANSLATIONS`` table below is the single source of truth. Run after
``manage.py makemessages -l de -l fr -l it`` to refresh the ``msgstr``
lines in all three locale files; then run ``manage.py compilemessages``
to produce the binary ``.mo`` files.

Workflow when adding new strings:
1. Add ``{% trans "..." %}`` / ``gettext_lazy("...")`` markers in code.
2. Run ``makemessages`` — populates the ``msgid`` lines.
3. Add the new entries to ``TRANSLATIONS`` here.
4. Run this script → rewrites every ``django.po``.
5. ``compilemessages``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

LANGS = ("de", "fr", "it")
ROOT = Path(__file__).resolve().parent.parent
LOCALE_DIR = ROOT / "locale"

# --- Translations table ----------------------------------------------------------------------
# Format: ``msgid: (de, fr, it)``. Multi-line msgids are written as
# Python strings; newlines are preserved when serialised back to .po.
TRANSLATIONS: dict[str, tuple[str, str, str]] = {
    # admin_module messages
    "Subject": ("Betreff", "Objet", "Oggetto"),
    "Message": ("Nachricht", "Message", "Messaggio"),
    "No active contest.": ("Kein aktiver Contest.", "Aucun contest actif.", "Nessun contest attivo."),
    "Registration closed.": ("Anmeldung geschlossen.", "Inscription fermée.", "Iscrizione chiusa."),
    "Log submission opened.": ("Log-Einreichung geöffnet.", "Soumission des journaux ouverte.", "Invio dei log aperto."),
    "Log submission closed; %(n)d pending logs were auto-submitted.": (
        "Log-Einreichung geschlossen; %(n)d ausstehende Logs wurden automatisch eingereicht.",
        "Soumission fermée ; %(n)d journaux en attente ont été soumis automatiquement.",
        "Invio chiuso; %(n)d log in attesa sono stati inviati automaticamente.",
    ),
    "Results published.": ("Resultate veröffentlicht.", "Résultats publiés.", "Risultati pubblicati."),
    "Registration reopened.": ("Anmeldung wieder geöffnet.", "Inscription rouverte.", "Iscrizione riaperta."),
    "Reverted to registration closed.": (
        "Zurückgesetzt auf Anmeldung geschlossen.",
        "Retour à l'inscription fermée.",
        "Tornato a iscrizione chiusa.",
    ),
    "Log submission reopened; %(n)d auto-submitted logs unlocked.": (
        "Log-Einreichung wieder geöffnet; %(n)d automatisch eingereichte Logs entsperrt.",
        "Soumission rouverte ; %(n)d journaux auto-soumis déverrouillés.",
        "Invio riaperto; %(n)d log auto-inviati sbloccati.",
    ),
    "Results unpublished.": (
        "Resultate zurückgezogen.",
        "Publication des résultats annulée.",
        "Pubblicazione dei risultati annullata.",
    ),
    "Nothing to revert from this state.": (
        "Aus diesem Zustand gibt es nichts zurückzusetzen.",
        "Rien à annuler depuis cet état.",
        "Nulla da annullare da questo stato.",
    ),
    "Invalid year.": ("Ungültiges Jahr.", "Année non valide.", "Anno non valido."),
    "Archived previous contests and deactivated participant accounts. NMD %(y)d seeded.": (
        "Frühere Contests archiviert und Teilnehmerkonten deaktiviert. NMD %(y)d angelegt.",
        "Contests précédents archivés et comptes participants désactivés. NMD %(y)d créé.",
        "Contest precedenti archiviati e account partecipanti disattivati. NMD %(y)d creato.",
    ),
    "This callsign is already registered for the current contest.": (
        "Dieses Rufzeichen ist bereits für den aktuellen Contest angemeldet.",
        "Cet indicatif est déjà inscrit pour le contest en cours.",
        "Questo nominativo è già iscritto al contest in corso.",
    ),
    "Registered %(call)s on behalf.": (
        "%(call)s stellvertretend angemeldet.",
        "%(call)s inscrit pour le compte de l'opérateur.",
        "%(call)s iscritto per conto dell'operatore.",
    ),
    "Station data updated for %(call)s.": (
        "Stationsdaten für %(call)s aktualisiert.",
        "Données station mises à jour pour %(call)s.",
        "Dati stazione aggiornati per %(call)s.",
    ),
    "Please choose a file to upload.": (
        "Bitte eine Datei zum Hochladen auswählen.",
        "Veuillez choisir un fichier à téléverser.",
        "Selezionare un file da caricare.",
    ),
    "Only .csv and .nmd files are supported.": (
        "Nur .csv- und .nmd-Dateien werden unterstützt.",
        "Seuls les fichiers .csv et .nmd sont acceptés.",
        "Sono supportati solo i file .csv e .nmd.",
    ),
    "Imported %(count)d QSO entries from %(name)s.": (
        "%(count)d QSO-Einträge aus %(name)s importiert.",
        "%(count)d QSO importés depuis %(name)s.",
        "Importati %(count)d QSO da %(name)s.",
    ),
    "Station description updated.": (
        "Stationsbeschreibung aktualisiert.",
        "Description de station mise à jour.",
        "Descrizione stazione aggiornata.",
    ),
    "Location updated from the file; altitude and canton refreshed from Swisstopo.": (
        "Standort aus Datei übernommen; Höhe und Kanton von Swisstopo aktualisiert.",
        "Position reprise du fichier ; altitude et canton actualisés depuis Swisstopo.",
        "Posizione aggiornata dal file; altitudine e cantone aggiornati da Swisstopo.",
    ),
    "The coordinates in the file are outside Switzerland or could not be parsed — the registered location was kept.": (
        "Die Koordinaten in der Datei liegen ausserhalb der Schweiz oder sind ungültig — der registrierte Standort bleibt erhalten.",
        "Les coordonnées du fichier sont hors de Suisse ou illisibles — la position enregistrée a été conservée.",
        "Le coordinate del file sono fuori dalla Svizzera o illeggibili — la posizione registrata è stata mantenuta.",
    ),
    "Already submitted; nothing to do.": (
        "Bereits eingereicht; nichts zu tun.",
        "Déjà soumis ; rien à faire.",
        "Già inviato; nulla da fare.",
    ),
    "Submitted log on behalf of %(call)s.": (
        "Log stellvertretend für %(call)s eingereicht.",
        "Journal soumis pour le compte de %(call)s.",
        "Log inviato per conto di %(call)s.",
    ),
    "Not currently submitted; nothing to release.": (
        "Derzeit nicht eingereicht; nichts freizugeben.",
        "Pas soumis actuellement ; rien à débloquer.",
        "Non attualmente inviato; nulla da sbloccare.",
    ),
    "Released submission for %(call)s.": (
        "Einreichung für %(call)s freigegeben.",
        "Soumission débloquée pour %(call)s.",
        "Invio sbloccato per %(call)s.",
    ),
    "Please choose a backup file to upload.": (
        "Bitte eine Backup-Datei zum Hochladen auswählen.",
        "Veuillez choisir un fichier de sauvegarde à téléverser.",
        "Selezionare un file di backup da caricare.",
    ),
    "Sent %(sent)d of %(total)d messages; %(failed)d failed.": (
        "%(sent)d von %(total)d Nachrichten versendet; %(failed)d fehlgeschlagen.",
        "%(sent)d sur %(total)d messages envoyés ; %(failed)d en échec.",
        "Inviati %(sent)d di %(total)d messaggi; %(failed)d falliti.",
    ),
    "Sent %(sent)d messages.": (
        "%(sent)d Nachrichten versendet.",
        "%(sent)d messages envoyés.",
        "Inviati %(sent)d messaggi.",
    ),
    "Updated invalid-callsign list: %(added)d added, %(removed)d removed. Scoring has been re-run.": (
        "Liste ungültiger Rufzeichen aktualisiert: %(added)d hinzugefügt, %(removed)d entfernt. Wertung wurde neu berechnet.",
        "Liste des indicatifs invalides mise à jour : %(added)d ajoutés, %(removed)d retirés. Le scoring a été recalculé.",
        "Lista nominativi non validi aggiornata: %(added)d aggiunti, %(removed)d rimossi. Il punteggio è stato ricalcolato.",
    ),
    "No changes to the invalid-callsign list.": (
        "Keine Änderungen an der Liste ungültiger Rufzeichen.",
        "Aucun changement à la liste des indicatifs invalides.",
        "Nessuna modifica alla lista dei nominativi non validi.",
    ),
    "Re-run scoring is only available after log submission has closed.": (
        "Neuwertung ist erst nach Schliessen der Log-Einreichung möglich.",
        "Le recalcul du scoring n'est possible qu'après la clôture de la soumission des journaux.",
        "Il ricalcolo del punteggio è disponibile solo dopo la chiusura dell'invio dei log.",
    ),
    "Scoring re-run. %(n)d QSO records updated.": (
        "Wertung neu berechnet. %(n)d QSO-Datensätze aktualisiert.",
        "Scoring recalculé. %(n)d enregistrements QSO mis à jour.",
        "Punteggio ricalcolato. %(n)d record QSO aggiornati.",
    ),

    # core/models.py — contest states & QSO labels
    "Registration open": ("Anmeldung offen", "Inscription ouverte", "Iscrizione aperta"),
    "Registration closed": ("Anmeldung geschlossen", "Inscription fermée", "Iscrizione chiusa"),
    "Log submission open": ("Log-Einreichung offen", "Soumission des journaux ouverte", "Invio dei log aperto"),
    "Log submission closed": ("Log-Einreichung geschlossen", "Soumission des journaux fermée", "Invio dei log chiuso"),
    "Scored": ("Gewertet", "Évalué", "Valutato"),
    "Results published": ("Resultate veröffentlicht", "Résultats publiés", "Risultati pubblicati"),
    "Archived": ("Archiviert", "Archivé", "Archiviato"),
    "UTC date of the contest": ("UTC-Datum des Contests", "Date UTC du contest", "Data UTC del contest"),
    "Contest start (06:00 UTC)": ("Contest-Beginn (06:00 UTC)", "Début du contest (06:00 UTC)", "Inizio del contest (06:00 UTC)"),
    "Contest end (09:59:59 UTC)": ("Contest-Ende (09:59:59 UTC)", "Fin du contest (09:59:59 UTC)", "Fine del contest (09:59:59 UTC)"),
    "Boundary between H1 and H2 (08:00 UTC)": (
        "Grenze zwischen H1 und H2 (08:00 UTC)",
        "Limite entre H1 et H2 (08:00 UTC)",
        "Confine tra H1 e H2 (08:00 UTC)",
    ),
    "CW + SSB": ("CW + SSB", "CW + SSB", "CW + SSB"),
    "Operator/station callsign without /P": (
        "Bediener-/Stationsrufzeichen ohne /P",
        "Indicatif opérateur/station sans /P",
        "Nominativo operatore/stazione senza /P",
    ),
    "Original easting/longitude as entered": (
        "Original eingegebene Ostkoordinate/Längengrad",
        "Coordonnée est/longitude saisie d'origine",
        "Coordinata est/longitudine inserita originale",
    ),
    "Original northing/latitude as entered": (
        "Original eingegebene Nordkoordinate/Breitengrad",
        "Coordonnée nord/latitude saisie d'origine",
        "Coordinata nord/latitudine inserita originale",
    ),
    "Friendly location name (village, locality, summit name, …) — the named counterpart of the coordinates": (
        "Standortname (Ortschaft, Flurname, Gipfelname, …) — die namentliche Entsprechung der Koordinaten",
        "Nom convivial de l'emplacement (localité, lieu-dit, nom du sommet, …) — le pendant nommé des coordonnées",
        "Nome del luogo (località, toponimo, nome della vetta, …) — il corrispettivo nominale delle coordinate",
    ),
    "2-letter Swiss canton code": (
        "2-Buchstaben-Code des Kantons",
        "Code à 2 lettres du canton suisse",
        "Codice di 2 lettere del cantone svizzero",
    ),
    "Total station weight (grams) — used as ranking tiebreaker": (
        "Gesamtgewicht der Station (Gramm) — als Stichentscheid in der Rangliste verwendet",
        "Poids total de la station (grammes) — utilisé comme critère de départage du classement",
        "Peso totale della stazione (grammi) — usato come spareggio nella classifica",
    ),
    "Set when the operator finalises and locks their log + station description": (
        "Wird gesetzt, wenn der Operator sein Log und die Stationsbeschreibung abschliesst und sperrt",
        "Défini quand l'opérateur finalise et verrouille son journal et sa description de station",
        "Impostato quando l'operatore finalizza e blocca il proprio log e la descrizione della stazione",
    ),
    "True if submitted_at was set by admin's 'close log submission' (rather than by the operator). Used to scope the un-submit when reverting the LOGS_CLOSED → LOGS_OPEN transition.": (
        "Wahr, wenn submitted_at durch den Admin-Schritt 'Log-Einreichung schliessen' gesetzt wurde (statt durch den Operator). Wird verwendet, um den Un-Submit beim Zurücksetzen LOGS_CLOSED → LOGS_OPEN einzugrenzen.",
        "Vrai si submitted_at a été défini par l'action admin « fermer la soumission » (et non par l'opérateur). Utilisé pour cibler la dé-soumission lors du retour LOGS_CLOSED → LOGS_OPEN.",
        "Vero se submitted_at è stato impostato dall'azione admin «chiudere l'invio» (non dall'operatore). Usato per limitare l'annullamento dell'invio quando si torna da LOGS_CLOSED a LOGS_OPEN.",
    ),
    "Unmatched NMD QSO": ("Kein Log von Gegenstation", "QSO NMD non apparié", "QSO NMD non appaiato"),
    "Full NMD match": ("Vollständiges NMD QSO", "Appariement NMD complet", "Appaiamento NMD completo"),
    "NMD match with text errors": (
        "NMD QSO mit Textfehler",
        "Appariement NMD avec erreurs de texte",
        "Appaiamento NMD con errori di testo",
    ),
    "Swiss non-NMD QSO": ("QSO mit HB9", "QSO suisse hors NMD", "QSO svizzero non-NMD"),
    "DX QSO": ("DX-QSO", "QSO DX", "QSO DX"),
    "Admin-accepted unmatched": (
        "Admin-akzeptiert ohne Paarung",
        "Non apparié accepté par admin",
        "Non appaiato accettato dall'admin",
    ),
    "Duplicate (deducted)": ("Doppel (abgezogen)", "Doublon (déduit)", "Duplicato (dedotto)"),
    "Possibly wrong remote callsign": (
        "Falsches Gegen-Rufzeichen",
        "Indicatif distant erroné",
        "Nominativo remoto errato",
    ),
    "Admin-flagged invalid callsign": (
        "Vom Admin als ungültig markiertes Rufzeichen",
        "Indicatif marqué invalide par l'admin",
        "Nominativo segnalato non valido dall'admin",
    ),
    "1 = 06–08 UTC, 2 = 08–10 UTC": ("1 = 06–08 UTC, 2 = 08–10 UTC", "1 = 06–08 UTC, 2 = 08–10 UTC", "1 = 06–08 UTC, 2 = 08–10 UTC"),
    "Free-text actor (e.g. 'system')": (
        "Freitext-Akteur (z. B. 'system')",
        "Acteur en texte libre (p. ex. « system »)",
        "Attore in testo libero (es. «system»)",
    ),
    "Queued": ("In Warteschlange", "En file", "In coda"),
    "Sent": ("Gesendet", "Envoyé", "Inviato"),
    "Failed": ("Fehlgeschlagen", "Échec", "Fallito"),

    # portal/forms.py
    "UTC (HHMM)": ("UTC (HHMM)", "UTC (HHMM)", "UTC (HHMM)"),
    "Callsign": ("Rufzeichen", "Indicatif", "Nominativo"),
    "RST sent": ("RST gesendet", "RST envoyé", "RST inviato"),
    "Text sent": ("Text gesendet", "Texte envoyé", "Testo inviato"),
    "RST received": ("RST empfangen", "RST reçu", "RST ricevuto"),
    "Text received": ("Text empfangen", "Texte reçu", "Testo ricevuto"),
    "Output power": ("Sendeleistung", "Puissance d'émission", "Potenza d'uscita"),
    "Weight (g)": ("Gewicht (g)", "Poids (g)", "Peso (g)"),

    # portal/station_service.py — component labels
    "Transceiver": ("Transceiver", "Émetteur-récepteur", "Ricetrasmettitore"),
    "Power supply": ("Stromversorgung", "Alimentation", "Alimentazione"),
    "Headphones / speaker": ("Kopfhörer / Lautsprecher", "Casque / haut-parleur", "Cuffie / altoparlante"),
    "Key / paddle / microphone": ("Taste / Paddle / Mikrofon", "Manipulateur / paddle / microphone", "Tasto / paddle / microfono"),
    "Antenna / matching unit": ("Antenne / Anpassung", "Antenne / unité d'accord", "Antenna / accordatore"),
    "Feedline": ("Speiseleitung", "Câble d'alimentation", "Linea di alimentazione"),
    "Masts / counterweights": ("Masten / Gegengewichte", "Mâts / contrepoids", "Pali / contrappesi"),
    "Guying / insulators": ("Abspannung / Isolatoren", "Haubans / isolateurs", "Tiranti / isolatori"),
    "PC and accessories": ("PC und Zubehör", "PC et accessoires", "PC e accessori"),
    "Additional station component": (
        "Zusätzliche Stationskomponente",
        "Composant de station supplémentaire",
        "Componente stazione aggiuntivo",
    ),

    # portal/submit_service.py — submission validation messages
    "Your log is empty — submit at least one QSO before finalising.": (
        "Dein Log ist leer — reiche mindestens einen QSO ein, bevor du abschliesst.",
        "Ton journal est vide — soumets au moins un QSO avant de finaliser.",
        "Il tuo log è vuoto — inserisci almeno un QSO prima di finalizzare.",
    ),
    "Output power (Watt) is required in the station data.": (
        "Die Sendeleistung (Watt) ist in den Stationsdaten erforderlich.",
        "La puissance d'émission (Watt) est requise dans les données station.",
        "La potenza d'uscita (Watt) è obbligatoria nei dati della stazione.",
    ),
    "Total station weight must be greater than 0 g.": (
        "Das Gesamtgewicht der Station muss grösser als 0 g sein.",
        "Le poids total de la station doit être supérieur à 0 g.",
        "Il peso totale della stazione deve essere maggiore di 0 g.",
    ),
    "Station data is missing required component: %(label)s.": (
        "In den Stationsdaten fehlt die erforderliche Komponente: %(label)s.",
        "Composant requis manquant dans les données station : %(label)s.",
        "Manca un componente obbligatorio nei dati stazione: %(label)s.",
    ),
    "%(n)d QSO row(s) have invalid fields (UTC, RST, text length, etc.).": (
        "%(n)d QSO-Zeile(n) enthalten ungültige Felder (UTC, RST, Textlänge usw.).",
        "%(n)d ligne(s) QSO ont des champs invalides (UTC, RST, longueur du texte, etc.).",
        "%(n)d riga/righe QSO contengono campi non validi (UTC, RST, lunghezza testo, ecc.).",
    ),
    "%(n)d QSO(s) look like duplicates and will be deducted at scoring.": (
        "%(n)d QSO(s) erscheinen als Duplikate und werden bei der Wertung abgezogen.",
        "%(n)d QSO semblent être des doublons et seront déduits lors du scoring.",
        "%(n)d QSO sembrano duplicati e saranno dedotti dal punteggio.",
    ),
    "Total station weight exceeds the 6 kg contest limit.": (
        "Das Gesamtgewicht der Station überschreitet das Contest-Limit von 6 kg.",
        "Le poids total de la station dépasse la limite contest de 6 kg.",
        "Il peso totale della stazione supera il limite contest di 6 kg.",
    ),

    # portal/views.py
    "You are not registered for the current contest.": (
        "Du bist nicht für den aktuellen Contest angemeldet.",
        "Tu n'es pas inscrit au contest en cours.",
        "Non sei iscritto al contest in corso.",
    ),
    "Your log has been submitted; further changes are not possible.": (
        "Dein Log wurde eingereicht; weitere Änderungen sind nicht möglich.",
        "Ton journal a été soumis ; aucune modification supplémentaire n'est possible.",
        "Il tuo log è stato inviato; ulteriori modifiche non sono possibili.",
    ),
    "Your participation has been cancelled. You can register again any time before the contest.": (
        "Deine Teilnahme wurde abgemeldet. Du kannst dich jederzeit vor dem Contest neu anmelden.",
        "Ta participation a été annulée. Tu peux te réinscrire à tout moment avant le contest.",
        "La tua partecipazione è stata annullata. Puoi iscriverti nuovamente in qualsiasi momento prima del contest.",
    ),
    "The coordinates in the file are outside Switzerland or could not be parsed — your registered location was kept.": (
        "Die Koordinaten in der Datei liegen ausserhalb der Schweiz oder sind ungültig — dein registrierter Standort bleibt erhalten.",
        "Les coordonnées du fichier sont hors de Suisse ou illisibles — ta position enregistrée a été conservée.",
        "Le coordinate del file sono fuori dalla Svizzera o illeggibili — la tua posizione registrata è stata mantenuta.",
    ),
    "Station data saved.": ("Stationsdaten gespeichert.", "Données station enregistrées.", "Dati stazione salvati."),
    "Your log has already been submitted.": (
        "Dein Log wurde bereits eingereicht.",
        "Ton journal a déjà été soumis.",
        "Il tuo log è già stato inviato.",
    ),
    "Your log has been submitted. A confirmation email is on its way.": (
        "Dein Log wurde eingereicht. Eine Bestätigungs-E-Mail ist unterwegs.",
        "Ton journal a été soumis. Un e-mail de confirmation est en route.",
        "Il tuo log è stato inviato. Una mail di conferma è in arrivo.",
    ),
    "The participant list will be available once registration closes.": (
        "Die Teilnehmerliste wird verfügbar, sobald die Anmeldung geschlossen ist.",
        "La liste des participants sera disponible dès la fermeture de l'inscription.",
        "L'elenco partecipanti sarà disponibile alla chiusura delle iscrizioni.",
    ),
    "Your ADIF download will be available once you submit your log.": (
        "Der ADIF-Download wird verfügbar, sobald du dein Log eingereicht hast.",
        "Le téléchargement ADIF sera disponible une fois ton journal soumis.",
        "Il download ADIF sarà disponibile dopo l'invio del tuo log.",
    ),

    # registration/constants.py — Swiss cantons
    "Aargau": ("Aargau", "Argovie", "Argovia"),
    "Appenzell Innerrhoden": ("Appenzell Innerrhoden", "Appenzell Rhodes-Intérieures", "Appenzello Interno"),
    "Appenzell Ausserrhoden": ("Appenzell Ausserrhoden", "Appenzell Rhodes-Extérieures", "Appenzello Esterno"),
    "Bern": ("Bern", "Berne", "Berna"),
    "Basel-Landschaft": ("Basel-Landschaft", "Bâle-Campagne", "Basilea Campagna"),
    "Basel-Stadt": ("Basel-Stadt", "Bâle-Ville", "Basilea Città"),
    "Fribourg": ("Freiburg", "Fribourg", "Friborgo"),
    "Genève": ("Genf", "Genève", "Ginevra"),
    "Glarus": ("Glarus", "Glaris", "Glarona"),
    "Graubünden": ("Graubünden", "Grisons", "Grigioni"),
    "Jura": ("Jura", "Jura", "Giura"),
    "Luzern": ("Luzern", "Lucerne", "Lucerna"),
    "Neuchâtel": ("Neuenburg", "Neuchâtel", "Neuchâtel"),
    "Nidwalden": ("Nidwalden", "Nidwald", "Nidvaldo"),
    "Obwalden": ("Obwalden", "Obwald", "Obvaldo"),
    "St. Gallen": ("St. Gallen", "Saint-Gall", "San Gallo"),
    "Schaffhausen": ("Schaffhausen", "Schaffhouse", "Sciaffusa"),
    "Solothurn": ("Solothurn", "Soleure", "Soletta"),
    "Schwyz": ("Schwyz", "Schwytz", "Svitto"),
    "Thurgau": ("Thurgau", "Thurgovie", "Turgovia"),
    "Ticino": ("Tessin", "Tessin", "Ticino"),
    "Uri": ("Uri", "Uri", "Uri"),
    "Vaud": ("Waadt", "Vaud", "Vaud"),
    "Valais": ("Wallis", "Valais", "Vallese"),
    "Zug": ("Zug", "Zoug", "Zugo"),
    "Zürich": ("Zürich", "Zurich", "Zurigo"),

    # registration/forms.py
    "Your contest callsign. We strip a trailing /P automatically — the portable suffix is implicit on NMD.": (
        "Dein Contest-Rufzeichen. Ein anhängendes /P entfernen wir automatisch — beim NMD ist es implizit.",
        "Ton indicatif contest. Un /P final est retiré automatiquement — au NMD il est implicite.",
        "Il tuo nominativo contest. Una /P finale viene rimossa automaticamente — al NMD è implicita.",
    ),
    "First name": ("Vorname", "Prénom", "Nome"),

    # QRB proximity warning (registration form)
    "Another station is registered nearby.": (
        "In der Nähe ist bereits eine Station angemeldet.",
        "Une autre station est inscrite à proximité.",
        "Un'altra stazione è già iscritta nelle vicinanze.",
    ),
    "Operating stations too close together can cause receiver overload from the neighbour's strong signal. We recommend picking a location at least 3 km away from every other station.": (
        "Zu nahe beieinander betriebene Stationen können den Empfänger durch das starke Signal des Nachbarn übersteuern. Wir empfehlen, einen Standort mindestens 3 km entfernt von jeder anderen Station zu wählen.",
        "Des stations trop proches peuvent provoquer une saturation du récepteur par le signal puissant du voisin. Nous recommandons de choisir un emplacement à au moins 3 km de toute autre station.",
        "Stazioni operative troppo vicine possono causare il sovraccarico del ricevitore dovuto al forte segnale del vicino. Consigliamo di scegliere una postazione ad almeno 3 km da ogni altra stazione.",
    ),
    "I'm aware of the nearby station(s) and want to register anyway.": (
        "Ich bin mir der nahegelegenen Station(en) bewusst und möchte mich trotzdem anmelden.",
        "Je suis conscient·e de la ou des stations à proximité et je souhaite m'inscrire malgré tout.",
        "Sono consapevole della/e stazione/i vicina/e e voglio iscrivermi comunque.",
    ),
    "Pick a different location": (
        "Anderen Standort wählen",
        "Choisir un autre emplacement",
        "Scegli un'altra posizione",
    ),
    "Keep this location anyway": (
        "Diesen Standort trotzdem behalten",
        "Conserver cet emplacement malgré tout",
        "Mantieni comunque questa posizione",
    ),
    "{call} — {dist} m away": (
        "{call} — {dist} m entfernt",
        "{call} — à {dist} m",
        "{call} — a {dist} m",
    ),
    "Another station is registered within %(km)s km of your chosen location. Please consider moving further away to avoid receiver overload, or tick the box to confirm you're aware of the conflict.": (
        "In weniger als %(km)s km von deinem Standort ist eine andere Station angemeldet. Erwäge, weiter weg zu ziehen, um Empfänger-Übersteuerung zu vermeiden, oder bestätige, dass du den Konflikt kennst.",
        "Une autre station est inscrite à moins de %(km)s km de l'emplacement choisi. Envisage de t'éloigner davantage pour éviter la saturation du récepteur, ou coche la case pour confirmer que tu es au courant.",
        "Un'altra stazione è iscritta entro %(km)s km dalla tua posizione. Considera di spostarti più lontano per evitare il sovraccarico del ricevitore, oppure spunta la casella per confermare di esserne consapevole.",
    ),
    "First name(s)": ("Vorname(n)", "Prénom(s)", "Nome(i)"),
    "If multi-operator station: list all first names joined with '+'.": (
        "Falls Mehrmannstation: Alle Vornamen mit '+' verbunden.",
        "Si station multiopérateur : tous les prénoms reliés par « + ».",
        "Se stazione con più operatori: tutti i nomi uniti con '+'.",
    ),
    "Email": ("E-Mail", "E-mail", "E-mail"),
    "Station type": ("Stationstyp", "Type de station", "Tipo di stazione"),
    "Single-operator station": (
        "Einmannstation",
        "Station opérateur individuel",
        "Stazione con un solo operatore",
    ),
    "Multi-operator station": (
        "Mehrmannstation",
        "Station multiopérateur",
        "Stazione con più operatori",
    ),
    "Yes": ("Ja", "Oui", "Sì"),
    "Station chief callsign": (
        "Rufzeichen des Stationsleiters",
        "Indicatif du chef de station",
        "Nominativo del capo stazione",
    ),
    "Required only for multi-operator stations.": (
        "Nur für Multi-Operator-Stationen erforderlich.",
        "Requis uniquement pour les stations multi-opérateurs.",
        "Richiesto solo per stazioni multi-operatore.",
    ),
    "Location name": ("Standortname", "Nom du lieu", "Nome del luogo"),
    "Friendly name for your station's location (village, locality, summit name, …).": (
        "Beschreibender Name für den Stationsstandort (Ortschaft, Flurname, Gipfelname, …).",
        "Nom convivial de l'emplacement de ta station (localité, lieu-dit, nom du sommet, …).",
        "Nome del luogo della tua stazione (località, toponimo, nome della vetta, …).",
    ),
    "Easting": ("Ostkoordinate", "Coordonnée est", "Coordinata est"),
    "CH1903 e.g. 660000 — also accepts CH1903+ (2660000) or WGS84 (8.2275)": (
        "CH1903 z. B. 660000 — auch CH1903+ (2660000) oder WGS84 (8.2275)",
        "CH1903 p. ex. 660000 — accepte aussi CH1903+ (2660000) ou WGS84 (8.2275)",
        "CH1903 es. 660000 — accetta anche CH1903+ (2660000) o WGS84 (8.2275)",
    ),
    "Northing": ("Nordkoordinate", "Coordonnée nord", "Coordinata nord"),
    "CH1903 e.g. 190000 — also accepts CH1903+ (1190000) or WGS84 (46.8182)": (
        "CH1903 z. B. 190000 — auch CH1903+ (1190000) oder WGS84 (46.8182)",
        "CH1903 p. ex. 190000 — accepte aussi CH1903+ (1190000) ou WGS84 (46.8182)",
        "CH1903 es. 190000 — accetta anche CH1903+ (1190000) o WGS84 (46.8182)",
    ),
    "Altitude (m a.s.l.)": ("Höhe (m ü. M.)", "Altitude (m s.m.)", "Altitudine (m s.l.m.)"),
    "Filled automatically from Swisstopo when you pick a location on the map.": (
        "Wird automatisch von Swisstopo gefüllt, wenn du einen Standort auf der Karte wählst.",
        "Renseigné automatiquement depuis Swisstopo quand tu choisis un emplacement sur la carte.",
        "Compilato automaticamente da Swisstopo quando selezioni un luogo sulla mappa.",
    ),
    "Canton": ("Kanton", "Canton", "Cantone"),
    "— select —": ("— auswählen —", "— sélectionner —", "— selezionare —"),
    "CW": ("CW", "CW", "CW"),
    "SSB": ("SSB", "SSB", "SSB"),
    "Remarks": ("Bemerkungen", "Remarques", "Note"),
    "Not a recognizable callsign.": (
        "Kein erkennbares Rufzeichen.",
        "Indicatif non reconnaissable.",
        "Nominativo non riconosciuto.",
    ),
    "Below 800 m — contest rules require minimum 800 m a.s.l.": (
        "Unter 800 m — die Contest-Regeln verlangen mindestens 800 m ü. M.",
        "Sous 800 m — le règlement du contest exige au minimum 800 m s.m.",
        "Sotto i 800 m — il regolamento del contest richiede almeno 800 m s.l.m.",
    ),
    "Please provide the station chief callsign for multi-operator stations.": (
        "Bitte das Rufzeichen des Stationsleiters für Multi-Operator-Stationen angeben.",
        "Indique l'indicatif du chef de station pour les stations multi-opérateurs.",
        "Inserisci il nominativo del capo stazione per stazioni multi-operatore.",
    ),
    "Select at least one operating mode (CW or SSB).": (
        "Wähle mindestens eine Betriebsart (CW oder SSB).",
        "Sélectionne au moins un mode (CW ou SSB).",
        "Seleziona almeno un modo (CW o SSB).",
    ),

    # templates/admin_module/audit_log.html + scoring/review.html shared labels
    "Audit log": ("Audit-Log", "Journal d'audit", "Log di controllo"),
    "Action": ("Aktion", "Action", "Azione"),
    "(all)": ("(alle)", "(tous)", "(tutti)"),
    "Actor (username)": ("Akteur (Benutzername)", "Acteur (nom d'utilisateur)", "Attore (nome utente)"),
    "Target contains": ("Ziel enthält", "Cible contient", "Destinatario contiene"),
    "Filter": ("Filtern", "Filtrer", "Filtra"),
    "Reset": ("Zurücksetzen", "Réinitialiser", "Reimposta"),
    "No entries match.": ("Keine Einträge gefunden.", "Aucune entrée correspondante.", "Nessuna voce corrispondente."),
    "%(total)s entries": ("%(total)s Einträge", "%(total)s entrées", "%(total)s voci"),
    "When (UTC)": ("Wann (UTC)", "Quand (UTC)", "Quando (UTC)"),
    "Actor": ("Akteur", "Acteur", "Attore"),
    "Target": ("Ziel", "Cible", "Destinatario"),
    "Contest": ("Contest", "Contest", "Contest"),
    "Payload": ("Nutzlast", "Charge utile", "Payload"),
    "Previous": ("Zurück", "Précédent", "Precedente"),
    "Page %(n)s of %(total)s": ("Seite %(n)s von %(total)s", "Page %(n)s sur %(total)s", "Pagina %(n)s di %(total)s"),
    "Next": ("Weiter", "Suivant", "Successivo"),
    "Back to administration": ("Zurück zur Administration", "Retour à l'administration", "Torna all'amministrazione"),

    # Backup template
    "Backup / restore": ("Backup / Wiederherstellung", "Sauvegarde / restauration", "Backup / ripristino"),
    "Administration": ("Administration", "Administration", "Amministrazione"),
    "Restore complete in this worker.": (
        "Wiederherstellung in diesem Worker abgeschlossen.",
        "Restauration terminée dans ce worker.",
        "Ripristino completato in questo worker.",
    ),
    "\n                The container runs multiple gunicorn workers; the others still\n                have open file handles to the previous database file. Restart\n                the container (e.g. <code>docker compose restart</code>) to\n                make sure every worker is using the restored database. You\n                will need to log in again.\n            ": (
        "\n                Der Container betreibt mehrere gunicorn-Worker; die anderen\n                haben noch offene Datei-Handles zur vorherigen Datenbank. Starte\n                den Container neu (z. B. <code>docker compose restart</code>),\n                damit alle Worker die wiederhergestellte Datenbank verwenden.\n                Du musst dich danach neu anmelden.\n            ",
        "\n                Le conteneur fait tourner plusieurs workers gunicorn ; les autres\n                gardent encore des handles ouverts sur l'ancienne base. Redémarre\n                le conteneur (p. ex. <code>docker compose restart</code>) pour\n                que tous les workers utilisent la base restaurée. Il faudra\n                te reconnecter.\n            ",
        "\n                Il container avvia più worker gunicorn; gli altri hanno ancora\n                handle aperti sul precedente database. Riavvia il container\n                (es. <code>docker compose restart</code>) affinché tutti i\n                worker usino il database ripristinato. Sarà necessario\n                effettuare nuovamente l'accesso.\n            ",
    ),
    "Backup": ("Backup", "Sauvegarde", "Backup"),
    "Download a transaction-consistent SQLite snapshot of the current database. Safe to run any time — does not pause writes.": (
        "Lade einen transaktionskonsistenten SQLite-Snapshot der aktuellen Datenbank herunter. Jederzeit sicher — pausiert keine Schreibvorgänge.",
        "Télécharge un instantané SQLite cohérent transactionnellement de la base actuelle. Sûr à tout moment — n'interrompt pas les écritures.",
        "Scarica un'istantanea SQLite coerente dal punto di vista transazionale del database attuale. Sicuro in qualsiasi momento — non blocca le scritture.",
    ),
    "Download backup": ("Backup herunterladen", "Télécharger la sauvegarde", "Scarica backup"),
    "Restore": ("Wiederherstellen", "Restaurer", "Ripristina"),
    "Uploads a previously-downloaded SQLite file and replaces the live database with it. The previous database is moved aside as a .bak file on the server.": (
        "Lädt eine zuvor heruntergeladene SQLite-Datei hoch und ersetzt damit die laufende Datenbank. Die bisherige Datenbank wird als .bak-Datei auf dem Server beiseite gelegt.",
        "Téléverse un fichier SQLite précédemment téléchargé et remplace la base active. L'ancienne base est mise de côté sous forme de fichier .bak sur le serveur.",
        "Carica un file SQLite scaricato in precedenza e sostituisce con esso il database attivo. Il precedente database viene messo da parte come file .bak sul server.",
    ),
    "This REPLACES the entire database with the uploaded file. All current data will be moved aside as a .bak file. After restore, the container should be restarted to ensure all workers pick up the new data. Continue?": (
        "Dies ERSETZT die gesamte Datenbank durch die hochgeladene Datei. Alle aktuellen Daten werden als .bak-Datei beiseite gelegt. Nach der Wiederherstellung sollte der Container neu gestartet werden, damit alle Worker die neuen Daten erhalten. Fortfahren?",
        "Cela REMPLACE entièrement la base par le fichier téléversé. Toutes les données actuelles seront mises de côté en .bak. Après la restauration, redémarre le conteneur pour que tous les workers prennent en compte les nouvelles données. Continuer ?",
        "Questo SOSTITUISCE l'intero database con il file caricato. Tutti i dati attuali saranno spostati come file .bak. Dopo il ripristino, riavvia il container affinché tutti i worker utilizzino i nuovi dati. Continuare?",
    ),
    "Backup file (.sqlite3):": ("Backup-Datei (.sqlite3):", "Fichier de sauvegarde (.sqlite3) :", "File di backup (.sqlite3):"),
    "Restore from file": ("Aus Datei wiederherstellen", "Restaurer depuis le fichier", "Ripristina dal file"),
    "Cancel": ("Abbrechen", "Annuler", "Annulla"),

    # Bulk email
    "Bulk email": ("Massen-E-Mail", "E-mail en masse", "Email di massa"),
    "Bulk email — NMD %(year)s": ("Massen-E-Mail — NMD %(year)s", "E-mail en masse — NMD %(year)s", "Email di massa — NMD %(year)s"),
    "Recipients": ("Empfänger", "Destinataires", "Destinatari"),
    "Show recipient list": ("Empfängerliste anzeigen", "Afficher la liste des destinataires", "Mostra elenco destinatari"),
    "Plain text. You can use {callsign} and {first_name} in subject or body; each recipient sees their own values.": (
        "Reiner Text. Du kannst {callsign} und {first_name} im Betreff oder Text verwenden; jeder Empfänger sieht seine eigenen Werte.",
        "Texte brut. Tu peux utiliser {callsign} et {first_name} dans l'objet ou le corps ; chaque destinataire voit ses propres valeurs.",
        "Testo semplice. Puoi usare {callsign} e {first_name} nell'oggetto o nel corpo; ogni destinatario vede i propri valori.",
    ),
    "Send now": ("Jetzt senden", "Envoyer maintenant", "Invia ora"),

    # Fixstation Review
    "Fixstation Review": ("Fixstation-Überprüfung", "Vérification des fixstations", "Verifica fixstation"),
    "\n            Non-NMD remote callsigns that were logged by only 1 or 2 participants.\n            Use the external lookups to verify each callsign. Tick the ones that\n            don't check out — flagged callsigns score as <strong>0 points</strong>\n            (deducted from every NMD station that logged them).\n        ": (
        "\n            Nicht-NMD-Gegenrufzeichen, die nur von 1 oder 2 Teilnehmern geloggt wurden.\n            Verwende die externen Datenbanken zur Überprüfung. Markiere diejenigen, die\n            nicht passen — markierte Rufzeichen werden mit <strong>0 Punkten</strong>\n            gewertet (von jeder NMD-Station abgezogen, die sie geloggt hat).\n        ",
        "\n            Indicatifs distants hors NMD enregistrés par 1 ou 2 participants seulement.\n            Utilise les bases externes pour vérifier chaque indicatif. Coche ceux qui\n            ne tiennent pas la route — les indicatifs marqués comptent pour\n            <strong>0 point</strong> (déduits de chaque station NMD qui les a loggés).\n        ",
        "\n            Nominativi remoti non-NMD registrati da soli 1 o 2 partecipanti.\n            Usa le banche dati esterne per verificare ogni nominativo. Spunta quelli\n            che non convincono — i nominativi segnalati valgono <strong>0 punti</strong>\n            (dedotti da ogni stazione NMD che li ha registrati).\n        ",
    ),
    "No suspicious callsigns found. Every non-NMD remote callsign was logged by 3 or more participants.": (
        "Keine verdächtigen Rufzeichen gefunden. Jedes Nicht-NMD-Gegenrufzeichen wurde von 3 oder mehr Teilnehmern geloggt.",
        "Aucun indicatif suspect trouvé. Chaque indicatif distant hors NMD a été enregistré par 3 participants ou plus.",
        "Nessun nominativo sospetto trovato. Ogni nominativo remoto non-NMD è stato registrato da 3 o più partecipanti.",
    ),
    "Logged by": ("Geloggt von", "Enregistré par", "Registrato da"),
    "NMD stations": ("NMD-Stationen", "Stations NMD", "Stazioni NMD"),
    "External lookup": ("Externe Suche", "Recherche externe", "Ricerca esterna"),
    "Invalid?": ("Ungültig?", "Invalide ?", "Non valido?"),
    "NMD station — likely missing /P": (
        "NMD-Station — vermutlich fehlendes /P",
        "Station NMD — /P probablement manquant",
        "Stazione NMD — probabilmente manca /P",
    ),
    "Save and re-score": ("Speichern und neu werten", "Enregistrer et recalculer", "Salva e ricalcola"),
    "Saving rebuilds the invalid-callsign list and re-runs the scoring pipeline.": (
        "Beim Speichern wird die Liste ungültiger Rufzeichen neu aufgebaut und die Wertung neu berechnet.",
        "L'enregistrement reconstruit la liste des indicatifs invalides et relance le scoring.",
        "Il salvataggio ricostruisce l'elenco dei nominativi non validi e ricalcola il punteggio.",
    ),

    # Admin index
    "No active contest. Seed one via": ("Kein aktiver Contest. Erstelle einen via", "Aucun contest actif. Créer via", "Nessun contest attivo. Crearne uno tramite"),
    "Year": ("Jahr", "Année", "Anno"),
    "Date": ("Datum", "Date", "Data"),
    "State": ("Zustand", "État", "Stato"),
    "Contest lifecycle": ("Contest-Lebenszyklus", "Cycle de vie du contest", "Ciclo di vita del contest"),
    "Each step is one-way; the audit log records every transition.": (
        "Jeder Schritt ist einbahnstrassig; das Audit-Log erfasst jeden Übergang.",
        "Chaque étape est à sens unique ; le journal d'audit enregistre chaque transition.",
        "Ogni passaggio è unidirezionale; il log di audit registra ogni transizione.",
    ),
    "Close registration? No new participants can sign up after this.": (
        "Anmeldung schliessen? Danach können sich keine neuen Teilnehmer mehr eintragen.",
        "Fermer l'inscription ? Aucun nouveau participant ne pourra s'inscrire après cela.",
        "Chiudere l'iscrizione? Dopo questo nessun nuovo partecipante potrà iscriversi.",
    ),
    "Close registration": ("Anmeldung schliessen", "Fermer l'inscription", "Chiudere l'iscrizione"),
    "Open log submission": ("Log-Einreichung öffnen", "Ouvrir la soumission des journaux", "Aprire l'invio dei log"),
    "Close log submission? Pending logs will be auto-submitted in their current state.": (
        "Log-Einreichung schliessen? Ausstehende Logs werden im aktuellen Zustand automatisch eingereicht.",
        "Fermer la soumission ? Les journaux en attente seront soumis automatiquement dans leur état actuel.",
        "Chiudere l'invio? I log in attesa saranno inviati automaticamente nel loro stato attuale.",
    ),
    "Close log submission (auto-submit pending)": (
        "Log-Einreichung schliessen (ausstehende automatisch einreichen)",
        "Fermer la soumission (auto-soumettre en attente)",
        "Chiudere l'invio (auto-invio in attesa)",
    ),
    "Publish results? Participants will be able to see their scoring breakdown.": (
        "Resultate veröffentlichen? Teilnehmer können dann ihre Wertung im Detail einsehen.",
        "Publier les résultats ? Les participants pourront voir le détail de leur scoring.",
        "Pubblicare i risultati? I partecipanti potranno vedere il dettaglio del proprio punteggio.",
    ),
    "Publish results": ("Resultate veröffentlichen", "Publier les résultats", "Pubblicare i risultati"),
    "Contest published. Use the 'Setup new contest' form below when ready to start a new edition.": (
        "Contest veröffentlicht. Verwende das Formular 'Neuen Contest einrichten' unten, wenn du eine neue Ausgabe starten möchtest.",
        "Contest publié. Utilise le formulaire « Nouveau contest » ci-dessous quand tu es prêt à démarrer une nouvelle édition.",
        "Contest pubblicato. Usa il modulo «Nuovo contest» qui sotto quando sei pronto a iniziare una nuova edizione.",
    ),
    "Revert to the previous state? If reverting 'log submission closed', the auto-submitted logs will be unlocked so operators can edit again.": (
        "In den vorherigen Zustand zurückkehren? Beim Zurücksetzen von 'Log-Einreichung geschlossen' werden die automatisch eingereichten Logs wieder entsperrt.",
        "Revenir à l'état précédent ? Si tu annules « soumission fermée », les journaux auto-soumis seront déverrouillés.",
        "Tornare allo stato precedente? Annullando «invio chiuso», i log auto-inviati saranno sbloccati.",
    ),
    "Revert to previous state": ("Zum vorherigen Zustand zurückkehren", "Revenir à l'état précédent", "Tornare allo stato precedente"),
    "Setup new contest": ("Neuen Contest einrichten", "Nouveau contest", "Nuovo contest"),
    "Archives the current contest and deactivates all non-staff accounts. Participants will need to re-register.": (
        "Archiviert den aktuellen Contest und deaktiviert alle Nicht-Staff-Konten. Teilnehmer müssen sich neu anmelden.",
        "Archive le contest actuel et désactive tous les comptes non-staff. Les participants devront se réinscrire.",
        "Archivia il contest attuale e disattiva tutti gli account non-staff. I partecipanti dovranno iscriversi nuovamente.",
    ),
    "This archives the current contest AND deactivates every participant account. Are you sure?": (
        "Dies archiviert den aktuellen Contest UND deaktiviert alle Teilnehmerkonten. Bist du sicher?",
        "Cela archive le contest actuel ET désactive chaque compte participant. Es-tu sûr ?",
        "Questo archivia il contest attuale E disattiva ogni account partecipante. Sei sicuro?",
    ),
    "Year:": ("Jahr:", "Année :", "Anno:"),
    "Archive + create": ("Archivieren + erstellen", "Archiver + créer", "Archivia + crea"),
    "Participants": ("Teilnehmer", "Participants", "Partecipanti"),
    "Registered (incl. cancelled)": ("Angemeldet (inkl. abgemeldet)", "Inscrits (y compris annulés)", "Iscritti (inclusi annullati)"),
    "Active": ("Aktiv", "Actifs", "Attivi"),
    "Logs submitted": ("Logs eingereicht", "Journaux soumis", "Log inviati"),
    "Pending submission": ("Einreichung ausstehend", "Soumission en attente", "Invio in attesa"),
    "Cancelled": ("Abgemeldet", "Annulés", "Annullati"),
    "Tools": ("Werkzeuge", "Outils", "Strumenti"),
    "on-behalf registration and editing": (
        "Stellvertretende Anmeldung und Bearbeitung",
        "Inscription et édition pour le compte d'un opérateur",
        "Iscrizione e modifica per conto",
    ),
    "Participant list (PDF)": ("Teilnehmerliste (PDF)", "Liste des participants (PDF)", "Elenco partecipanti (PDF)"),
    "Participant list (CSV)": ("Teilnehmerliste (CSV)", "Liste des participants (CSV)", "Elenco partecipanti (CSV)"),
    "Participant list preview (PDF)": (
        "Vorschau Teilnehmerliste (PDF)",
        "Aperçu de la liste des participants (PDF)",
        "Anteprima elenco partecipanti (PDF)",
    ),
    "Participant list preview (CSV)": (
        "Vorschau Teilnehmerliste (CSV)",
        "Aperçu de la liste des participants (CSV)",
        "Anteprima elenco partecipanti (CSV)",
    ),
    "review the layout before closing registration; attached to the participants' close-of-registration email": (
        "Layout prüfen, bevor die Anmeldung geschlossen wird; wird der entsprechenden E-Mail an die Teilnehmer angehängt",
        "Vérifier la mise en page avant de clore les inscriptions ; jointe à l'e-mail de clôture envoyé aux participants",
        "Verifica il layout prima di chiudere le iscrizioni; viene allegato all'e-mail di chiusura inviata ai partecipanti",
    ),
    "same data in the format dedicated logging software expects": (
        "Dieselben Daten im Format, das spezialisierte Logging-Programme erwarten",
        "Mêmes données au format attendu par les logiciels de journal dédiés",
        "Stessi dati nel formato richiesto dai programmi di logging dedicati",
    ),
    "send a message to all active participants": (
        "Eine Nachricht an alle aktiven Teilnehmer senden",
        "Envoyer un message à tous les participants actifs",
        "Inviare un messaggio a tutti i partecipanti attivi",
    ),
    "download a SQLite snapshot or restore from one": (
        "SQLite-Snapshot herunterladen oder daraus wiederherstellen",
        "Télécharger un instantané SQLite ou restaurer depuis l'un d'eux",
        "Scaricare un'istantanea SQLite o ripristinare da una di esse",
    ),
    "Scoring review": ("Wertungsüberprüfung", "Vérification du scoring", "Verifica punteggio"),
    "per-QSO status, points, suspected calls": (
        "Pro QSO: Status, Punkte, verdächtige Rufzeichen",
        "Par QSO : statut, points, indicatifs suspects",
        "Per QSO: stato, punti, nominativi sospetti",
    ),
    "Fixstation review": ("Fixstation-Überprüfung", "Vérification des fixstations", "Verifica fixstation"),
    "verify suspicious non-NMD callsigns against external databases": (
        "Verdächtige Nicht-NMD-Rufzeichen gegen externe Datenbanken prüfen",
        "Vérifier les indicatifs hors NMD suspects via des bases externes",
        "Verificare nominativi non-NMD sospetti tramite banche dati esterne",
    ),
    "every admin/system action with filters": (
        "Jede Admin-/System-Aktion mit Filtern",
        "Chaque action admin/système avec filtres",
        "Ogni azione admin/sistema con filtri",
    ),
    "Django admin": ("Django-Admin", "Admin Django", "Admin Django"),
    "low-level data inspection (registrations, QSOs, emails, etc.)": (
        "Datenbank-Detailansicht (Anmeldungen, QSOs, E-Mails usw.)",
        "Inspection bas niveau des données (inscriptions, QSO, e-mails, etc.)",
        "Ispezione a basso livello dei dati (iscrizioni, QSO, e-mail, ecc.)",
    ),
    "Recent activity": ("Letzte Aktivität", "Activité récente", "Attività recente"),
    "Full audit log →": ("Vollständiges Audit-Log →", "Journal d'audit complet →", "Log di controllo completo →"),

    # Participant detail
    "On-behalf editing surface. Changes are audited with you as the actor.": (
        "Stellvertretende Bearbeitungsoberfläche. Änderungen werden mit dir als Akteur protokolliert.",
        "Surface d'édition pour le compte d'un opérateur. Les changements sont audités avec toi comme acteur.",
        "Interfaccia di modifica per conto. Le modifiche sono registrate con te come attore.",
    ),
    "Registration": ("Anmeldung", "Inscription", "Iscrizione"),
    "Name": ("Name", "Nom", "Nome"),
    "Location": ("Standort", "Lieu", "Luogo"),
    "Altitude": ("Höhe", "Altitude", "Altitudine"),
    "Coordinates (WGS84)": ("Koordinaten (WGS84)", "Coordonnées (WGS84)", "Coordinate (WGS84)"),
    "Coordinates (CH1903)": ("Koordinaten (CH1903)", "Coordonnées (CH1903)", "Coordinate (CH1903)"),
    "Multi-op": ("Multi-Op", "Multi-op", "Multi-op"),
    "yes": ("ja", "oui", "sì"),
    "chief:": ("Leiter:", "chef :", "capo:"),
    "no": ("nein", "non", "no"),
    "Modes": ("Betriebsarten", "Modes", "Modi"),
    "Registered at": ("Angemeldet am", "Inscrit le", "Iscritto il"),
    "Status": ("Status", "Statut", "Stato"),
    "Submitted at": ("Eingereicht am", "Soumis le", "Inviato il"),
    "auto-submitted": ("automatisch eingereicht", "auto-soumis", "auto-inviato"),
    "Cancelled at": ("Abgemeldet am", "Annulé le", "Annullato il"),
    "Log": ("Log", "Journal", "Log"),
    "QSO entries": ("QSO-Einträge", "Entrées QSO", "Voci QSO"),
    "Total station weight": ("Gesamtgewicht der Station", "Poids total de la station", "Peso totale della stazione"),
    "Actions": ("Aktionen", "Actions", "Azioni"),
    "Edit station data": ("Stationsdaten bearbeiten", "Modifier les données station", "Modifica dati stazione"),
    "Log entry / upload": ("Log-Eingabe / Hochladen", "Saisie du journal / téléversement", "Inserimento log / upload"),
    "Release this submission so the operator can edit again?": (
        "Diese Einreichung freigeben, damit der Operator wieder bearbeiten kann?",
        "Débloquer cette soumission pour permettre à l'opérateur de la modifier à nouveau ?",
        "Sbloccare questo invio così che l'operatore possa modificarlo di nuovo?",
    ),
    "Release submission": ("Einreichung freigeben", "Débloquer la soumission", "Sbloccare l'invio"),
    "Submit this participant's log on their behalf? No confirmation email is sent.": (
        "Das Log dieses Teilnehmers stellvertretend einreichen? Es wird keine Bestätigungs-E-Mail gesendet.",
        "Soumettre le journal de ce participant pour son compte ? Aucun e-mail de confirmation n'est envoyé.",
        "Inviare il log di questo partecipante per suo conto? Non viene inviata alcuna mail di conferma.",
    ),
    "Submit log on behalf": (
        "Log stellvertretend einreichen",
        "Soumettre le journal pour son compte",
        "Inviare il log per conto",
    ),

    # Log entry / station / participant_register / participant_station
    "Log entry": ("Log-Eingabe", "Saisie du journal", "Inserimento log"),
    "Edit": ("Bearbeiten", "Modifier", "Modifica"),
    "Downloads and results": (
        "Downloads und Resultate",
        "Téléchargements et résultats",
        "Download e risultati",
    ),

    # Report + pictures (F3)
    "Report and Pictures": ("Teilnehmerbericht", "Rapport et photos", "Rapporto e foto"),
    "Participant report": ("Teilnehmerbericht", "Rapport du participant", "Rapporto del partecipante"),
    "Tell us how your contest went — band conditions, interesting QSOs, mishaps, photos from the location. Everything here stays editable for the whole season; the magazine team will harvest reports after the results are published.": (
        "Erzähl uns, wie dein Contest lief — Bandbedingungen, interessante QSOs, Pannen, Fotos vom Standort. Alles hier bleibt während der ganzen Saison editierbar; das Magazin-Team sammelt die Berichte nach der Veröffentlichung der Resultate ein.",
        "Raconte-nous comment ton contest s'est passé — propagation, QSO marquants, péripéties, photos de l'emplacement. Tout reste modifiable durant toute la saison ; l'équipe du magazine récupérera les rapports après la publication des résultats.",
        "Raccontaci com'è andato il tuo contest — propagazione, QSO interessanti, imprevisti, foto dalla postazione. Tutto qui resta modificabile per l'intera stagione; la redazione della rivista raccoglierà i rapporti dopo la pubblicazione dei risultati.",
    ),
    "Up to 4096 characters.": (
        "Bis zu 4096 Zeichen.",
        "Jusqu'à 4096 caractères.",
        "Fino a 4096 caratteri.",
    ),
    "Save report": ("Bericht speichern", "Enregistrer le rapport", "Salva il rapporto"),
    "Report saved.": ("Bericht gespeichert.", "Rapport enregistré.", "Rapporto salvato."),
    "Pictures": ("Bilder", "Photos", "Foto"),
    "Up to 6 pictures (JPEG, PNG or WebP, max 5 MB each). Originals are stored at full resolution.": (
        "Bis zu 6 Bilder (JPEG, PNG oder WebP, max. 5 MB pro Bild). Originale werden in voller Auflösung gespeichert.",
        "Jusqu'à 6 photos (JPEG, PNG ou WebP, 5 Mo max chacune). Les originaux sont conservés en pleine résolution.",
        "Fino a 6 foto (JPEG, PNG o WebP, max 5 MB ciascuna). Gli originali sono conservati a piena risoluzione.",
    ),
    "Picture %(n)s": ("Bild %(n)s", "Photo %(n)s", "Foto %(n)s"),
    "Delete this picture?": (
        "Dieses Bild löschen?",
        "Supprimer cette photo ?",
        "Eliminare questa foto?",
    ),
    "Delete picture": ("Bild löschen", "Supprimer la photo", "Elimina foto"),
    "Picture deleted.": ("Bild gelöscht.", "Photo supprimée.", "Foto eliminata."),
    "Upload picture": ("Bild hochladen", "Téléverser une photo", "Carica foto"),
    "Picture uploaded.": ("Bild hochgeladen.", "Photo téléversée.", "Foto caricata."),
    "Caption (max 50 characters)": (
        "Bildunterschrift (max. 50 Zeichen)",
        "Légende (50 caractères max.)",
        "Didascalia (max 50 caratteri)",
    ),
    "Please choose an image to upload.": (
        "Bitte wähle ein Bild zum Hochladen.",
        "Veuillez choisir une image à téléverser.",
        "Seleziona un'immagine da caricare.",
    ),
    "All 6 picture slots are in use. Delete one to upload a new picture.": (
        "Alle 6 Bildplätze sind belegt. Lösche zuerst ein Bild, um ein neues hochzuladen.",
        "Les 6 emplacements de photos sont utilisés. Supprime-en une pour téléverser une nouvelle photo.",
        "Tutti i 6 spazi per le foto sono occupati. Eliminane una per caricarne una nuova.",
    ),
    "Participant reports": (
        "Teilnehmerberichte",
        "Rapports des participants",
        "Rapporti dei partecipanti",
    ),
    "post-contest writeups + photos for the magazine team": (
        "Berichte und Fotos nach dem Contest, für das Magazin-Team",
        "Comptes-rendus et photos post-contest, pour l'équipe du magazine",
        "Resoconti e foto dopo il contest, per la redazione della rivista",
    ),
    "Read-only view of every active participant's report text and uploaded pictures. Participants edit on their own portal page.": (
        "Schreibgeschützte Übersicht über Berichte und Bilder aller aktiven Teilnehmer. Die Teilnehmer selbst bearbeiten ihre Inhalte im Portal.",
        "Vue en lecture seule des rapports et photos de chaque participant actif. Les participants modifient leurs contenus depuis leur portail.",
        "Vista in sola lettura dei rapporti e delle foto di ogni partecipante attivo. I partecipanti modificano i propri contenuti dal portale.",
    ),
    "No reports submitted yet.": (
        "Bisher keine Berichte vorhanden.",
        "Aucun rapport pour l'instant.",
        "Nessun rapporto finora.",
    ),
    "No text submitted.": (
        "Kein Text eingereicht.",
        "Aucun texte saisi.",
        "Nessun testo inserito.",
    ),
    "%(call)s picture %(n)s": (
        "%(call)s Bild %(n)s",
        "Photo %(n)s de %(call)s",
        "Foto %(n)s di %(call)s",
    ),
    "Download every uploaded participant picture as a tar.gz, mirroring the on-disk layout. Separate from the SQLite snapshot so the (potentially large) image set can be archived independently.": (
        "Lade alle hochgeladenen Teilnehmerbilder als tar.gz herunter, in derselben Verzeichnisstruktur wie auf der Festplatte. Getrennt vom SQLite-Snapshot, damit der (möglicherweise grosse) Bilderbestand unabhängig archiviert werden kann.",
        "Téléchargez toutes les photos déposées par les participants sous forme d'un tar.gz, avec la même arborescence que sur le disque. Séparé du snapshot SQLite pour pouvoir archiver indépendamment l'ensemble (potentiellement volumineux) des images.",
        "Scarica tutte le foto caricate dai partecipanti come tar.gz, mantenendo la stessa struttura su disco. Separato dallo snapshot SQLite per archiviare in modo indipendente l'insieme (potenzialmente grande) di immagini.",
    ),
    "Download pictures": ("Bilder herunterladen", "Télécharger les photos", "Scarica le foto"),
    "Log entry — %(callsign)s": ("Log-Eingabe — %(callsign)s", "Saisie du journal — %(callsign)s", "Inserimento log — %(callsign)s"),
    "On-behalf editing. The portal's post-submit lock is bypassed here so staff can amend a submitted log if needed.": (
        "Stellvertretende Bearbeitung. Die Portal-Sperre nach Einreichung wird hier umgangen, damit Staff bei Bedarf nachbessern kann.",
        "Édition pour le compte d'un opérateur. Le verrou post-soumission du portail est contourné ici pour permettre au staff d'amender un journal soumis si nécessaire.",
        "Modifica per conto. Il blocco post-invio del portale viene aggirato qui per permettere allo staff di correggere un log inviato se necessario.",
    ),
    "\n                Log was submitted on %(when)s UTC. Edits will alter the submitted log.\n            ": (
        "\n                Log wurde am %(when)s UTC eingereicht. Bearbeitungen verändern das eingereichte Log.\n            ",
        "\n                Le journal a été soumis le %(when)s UTC. Les modifications altèrent le journal soumis.\n            ",
        "\n                Il log è stato inviato il %(when)s UTC. Le modifiche alterano il log inviato.\n            ",
    ),
    "Upload .nmd or .csv file": (
        ".nmd- oder .csv-Datei hochladen",
        "Téléverser un fichier .nmd ou .csv",
        "Carica un file .nmd o .csv",
    ),
    "Replaces the QSO list and (if present in the file) the station description.": (
        "Ersetzt die QSO-Liste und (falls in der Datei enthalten) die Stationsbeschreibung.",
        "Remplace la liste des QSO et (si présent dans le fichier) la description de la station.",
        "Sostituisce l'elenco QSO e (se presente nel file) la descrizione della stazione.",
    ),
    "This replaces the existing QSO list and station description. Continue?": (
        "Dies ersetzt die bestehende QSO-Liste und Stationsbeschreibung. Fortfahren?",
        "Cela remplace la liste QSO existante et la description de station. Continuer ?",
        "Questo sostituisce l'elenco QSO esistente e la descrizione della stazione. Continuare?",
    ),
    "Upload": ("Hochladen", "Téléverser", "Carica"),
    "Back to participant": ("Zurück zum Teilnehmer", "Retour au participant", "Torna al partecipante"),
    "Register on behalf": ("Stellvertretend anmelden", "Inscrire pour le compte d'un opérateur", "Iscrivere per conto"),
    "Same registration form the operator would see. Bypasses the contest's registration-open state, so this works even after registration has been closed. Audited under your account.": (
        "Dasselbe Anmeldeformular wie für den Operator. Umgeht den Anmeldestatus des Contests und funktioniert daher auch nach Anmeldeschluss. Wird unter deinem Konto protokolliert.",
        "Le même formulaire d'inscription que verrait l'opérateur. Contourne l'état d'inscription du contest, donc fonctionne même après la fermeture. Audité sous ton compte.",
        "Lo stesso modulo d'iscrizione che vedrebbe l'operatore. Aggira lo stato di apertura del contest, quindi funziona anche dopo la chiusura delle iscrizioni. Registrato sotto il tuo account.",
    ),
    "Operator": ("Operator", "Opérateur", "Operatore"),
    "Station type": ("Stationstyp", "Type de station", "Tipo di stazione"),
    "Operating modes": ("Betriebsarten", "Modes opératoires", "Modi operativi"),
    "Other": ("Sonstiges", "Autre", "Altro"),
    "Register": ("Anmelden", "Inscrire", "Iscrivere"),
    "Station data": ("Stationsdaten", "Données station", "Dati stazione"),
    "Station data — %(callsign)s": ("Stationsdaten — %(callsign)s", "Données station — %(callsign)s", "Dati stazione — %(callsign)s"),
    "On-behalf editing. The portal's post-submit lock is bypassed here.": (
        "Stellvertretende Bearbeitung. Die Portal-Sperre nach Einreichung wird hier umgangen.",
        "Édition pour le compte d'un opérateur. Le verrou post-soumission du portail est contourné ici.",
        "Modifica per conto. Il blocco post-invio del portale viene aggirato qui.",
    ),
    "\n                Log was submitted on %(when)s UTC. Edits will alter the submitted data.\n            ": (
        "\n                Log wurde am %(when)s UTC eingereicht. Bearbeitungen verändern die eingereichten Daten.\n            ",
        "\n                Le journal a été soumis le %(when)s UTC. Les modifications altèrent les données soumises.\n            ",
        "\n                Il log è stato inviato il %(when)s UTC. Le modifiche alterano i dati inviati.\n            ",
    ),
    "Operator information": ("Operatorinformationen", "Informations opérateur", "Informazioni operatore"),
    "Email address": ("E-Mail-Adresse", "Adresse e-mail", "Indirizzo e-mail"),
    "Station equipment": ("Stationsausrüstung", "Équipement de station", "Equipaggiamento stazione"),
    "List every piece of equipment that counts toward the contest weight limit (6 kg).": (
        "Liste jedes Ausrüstungsstück auf, das zum Contest-Gewichtslimit (6 kg) zählt.",
        "Liste chaque équipement comptant pour la limite de poids du contest (6 kg).",
        "Elenca ogni equipaggiamento che conta ai fini del limite di peso del contest (6 kg).",
    ),
    "Watt": ("Watt", "Watt", "Watt"),
    "Total exceeds the 6000 g contest limit.": (
        "Gesamtgewicht überschreitet das Contest-Limit von 6000 g.",
        "Le total dépasse la limite contest de 6000 g.",
        "Il totale supera il limite contest di 6000 g.",
    ),
    "Save station data": ("Stationsdaten speichern", "Enregistrer les données station", "Salva dati stazione"),
    "Callsign contains:": ("Rufzeichen enthält:", "Indicatif contient :", "Nominativo contiene:"),
    "Status:": ("Status:", "Statut :", "Stato:"),
    "Any": ("Beliebig", "N'importe lequel", "Qualsiasi"),
    "Pending": ("Ausstehend", "En attente", "In attesa"),
    "Submitted": ("Eingereicht", "Soumis", "Inviato"),
    "Clear": ("Leeren", "Effacer", "Cancella"),
    "Edit": ("Bearbeiten", "Modifier", "Modifica"),
    "No participants match.": ("Keine Teilnehmer gefunden.", "Aucun participant correspondant.", "Nessun partecipante corrispondente."),

    # Base template / login / etc.
    "Portal": ("Portal", "Portail", "Portale"),
    "Logout": ("Abmelden", "Déconnexion", "Disconnetti"),
    "Login": ("Login", "Connexion", "Accesso"),
    # Topnav link — distinct from the "Registration" section heading in
    # the admin participant detail page (which stays as "Anmeldung" etc.).
    "Sign up": ("Registrieren", "S'inscrire", "Registrati"),
    "NMD %(year)s — %(date)s": ("NMD %(year)s — %(date)s", "NMD %(year)s — %(date)s", "NMD %(year)s — %(date)s"),

    # Location fieldset
    "\n            Pick your station on the map — or enter coordinates directly\n            in WGS84, CH1903, or CH1903+. The system is detected automatically.\n            Existing registrations are shown in green.\n        ": (
        "\n            Wähle deine Station auf der Karte — oder gib die Koordinaten direkt\n            in WGS84, CH1903 oder CH1903+ ein. Das System wird automatisch erkannt.\n            Bestehende Anmeldungen sind grün dargestellt.\n        ",
        "\n            Choisis ta station sur la carte — ou entre directement les coordonnées\n            en WGS84, CH1903 ou CH1903+. Le système est détecté automatiquement.\n            Les inscriptions existantes apparaissent en vert.\n        ",
        "\n            Seleziona la tua stazione sulla mappa — o inserisci direttamente le\n            coordinate in WGS84, CH1903 o CH1903+. Il sistema è rilevato automaticamente.\n            Le iscrizioni esistenti sono mostrate in verde.\n        ",
    ),
    "Your station": ("Deine Station", "Ta station", "La tua stazione"),
    "Coordinates could not be parsed.": (
        "Koordinaten konnten nicht verarbeitet werden.",
        "Impossible d'interpréter les coordonnées.",
        "Impossibile interpretare le coordinate.",
    ),
    "Coordinates are outside Switzerland.": (
        "Koordinaten liegen ausserhalb der Schweiz.",
        "Les coordonnées sont hors de Suisse.",
        "Le coordinate sono fuori dalla Svizzera.",
    ),
    "Altitude from Swisstopo: {n} m": (
        "Höhe von Swisstopo: {n} m",
        "Altitude depuis Swisstopo : {n} m",
        "Altitudine da Swisstopo: {n} m",
    ),
    "This module is not implemented yet.": (
        "Dieses Modul ist noch nicht implementiert.",
        "Ce module n'est pas encore implémenté.",
        "Questo modulo non è ancora implementato.",
    ),

    # QSO app / QSO form / QSO rows
    "UTC": ("UTC", "UTC", "UTC"),
    "Mode": ("Mode", "Mode", "Modo"),
    "RST recv": ("RST empf.", "RST reçu", "RST ric."),
    "Update": ("Aktualisieren", "Mettre à jour", "Aggiorna"),
    "Save": ("Speichern", "Enregistrer", "Salva"),
    "Looks like a duplicate of another QSO in this log. Saving is allowed — fix the time or callsign if this was a typo.": (
        "Sieht aus wie ein Duplikat eines anderen QSO in diesem Log. Speichern ist erlaubt — korrigiere Zeit oder Rufzeichen, falls es ein Tippfehler war.",
        "Ressemble à un doublon d'un autre QSO de ce journal. L'enregistrement est autorisé — corrige l'heure ou l'indicatif s'il s'agissait d'une faute de frappe.",
        "Sembra un duplicato di un altro QSO in questo log. Il salvataggio è consentito — correggi l'ora o il nominativo se è un refuso.",
    ),
    "Registered NMD callsign is %(correct)s. Saving is allowed, but the QSO will not score unless the callsign matches exactly (per the on-air /P rule).": (
        "Das registrierte NMD-Rufzeichen lautet %(correct)s. Speichern ist erlaubt, aber der QSO wird nicht gewertet, wenn das Rufzeichen nicht exakt übereinstimmt (gemäss /P-Regel auf der Frequenz).",
        "L'indicatif NMD enregistré est %(correct)s. L'enregistrement est autorisé, mais le QSO ne comptera que si l'indicatif correspond exactement (selon la règle /P en émission).",
        "Il nominativo NMD registrato è %(correct)s. Il salvataggio è consentito, ma il QSO non verrà conteggiato a meno che il nominativo non coincida esattamente (regola /P in trasmissione).",
    ),
    "Delete this QSO?": ("Diesen QSO löschen?", "Supprimer ce QSO ?", "Eliminare questo QSO?"),
    "No QSOs in the submitted log.": (
        "Keine QSOs im eingereichten Log.",
        "Aucun QSO dans le journal soumis.",
        "Nessun QSO nel log inviato.",
    ),
    "No QSOs yet — add one using the form above.": (
        "Noch keine QSOs — füge oben einen hinzu.",
        "Aucun QSO pour l'instant — ajoutes-en un avec le formulaire ci-dessus.",
        "Ancora nessun QSO — aggiungine uno con il modulo qui sopra.",
    ),

    # Cancel / dashboard / etc.
    "Unsubscribe": ("Anmeldung annullieren", "Annuler l'inscription", "Annulla iscrizione"),
    "\n            You're about to unsubscribe from NMD %(year)s as %(callsign)s.\n        ": (
        "\n            Du bist im Begriff, deine Anmeldung als %(callsign)s für den NMD %(year)s zu annullieren.\n        ",
        "\n            Tu es sur le point d'annuler ton inscription au NMD %(year)s en tant que %(callsign)s.\n        ",
        "\n            Stai per annullare la tua iscrizione al NMD %(year)s come %(callsign)s.\n        ",
    ),
    "Your QSO log and station data — if any — will be removed. You can register again any time before the contest.": (
        "Dein QSO-Log und deine Stationsdaten — falls vorhanden — werden gelöscht. Du kannst dich jederzeit vor dem Contest neu anmelden.",
        "Ton journal QSO et tes données station — s'ils existent — seront supprimés. Tu peux te réinscrire à tout moment avant le contest.",
        "Il tuo log QSO e i dati stazione — se presenti — saranno rimossi. Puoi iscriverti nuovamente in qualsiasi momento prima del contest.",
    ),
    "Yes, unsubscribe": (
        "Ja, Anmeldung annullieren",
        "Oui, annuler l'inscription",
        "Sì, annulla iscrizione",
    ),
    "Keep my registration": ("Anmeldung behalten", "Conserver mon inscription", "Mantieni l'iscrizione"),
    "Portal — %(callsign)s": ("Portal — %(callsign)s", "Portail — %(callsign)s", "Portale — %(callsign)s"),
    "There is no active contest at the moment.": (
        "Im Moment gibt es keinen aktiven Contest.",
        "Aucun contest n'est actif pour le moment.",
        "Al momento non c'è alcun contest attivo.",
    ),
    "\n                You are not registered for NMD %(year)s.\n            ": (
        "\n                Du bist nicht für NMD %(year)s angemeldet.\n            ",
        "\n                Tu n'es pas inscrit au NMD %(year)s.\n            ",
        "\n                Non sei iscritto al NMD %(year)s.\n            ",
    ),
    "Register now": ("Jetzt anmelden", "S'inscrire maintenant", "Iscriviti ora"),
    "Registration is closed.": ("Anmeldung ist geschlossen.", "L'inscription est fermée.", "L'iscrizione è chiusa."),
    "\n                Registered for NMD %(year)s on %(date)s.\n            ": (
        "\n                Angemeldet für NMD %(year)s am %(date)s.\n            ",
        "\n                Inscrit au NMD %(year)s le %(date)s.\n            ",
        "\n                Iscritto al NMD %(year)s il %(date)s.\n            ",
    ),
    "\n                    Your log was submitted on %(when)s UTC. No further changes are possible.\n                ": (
        "\n                    Dein Log wurde am %(when)s UTC eingereicht. Weitere Änderungen sind nicht möglich.\n                ",
        "\n                    Ton journal a été soumis le %(when)s UTC. Aucune modification supplémentaire n'est possible.\n                ",
        "\n                    Il tuo log è stato inviato il %(when)s UTC. Ulteriori modifiche non sono possibili.\n                ",
    ),
    "Registration data": ("Anmeldedaten", "Données d'inscription", "Dati d'iscrizione"),
    "No": ("Nein", "Non", "No"),
    "Download log (ADIF)": ("Log herunterladen (ADIF)", "Télécharger le journal (ADIF)", "Scarica log (ADIF)"),
    "View your scoring": ("Wertung ansehen", "Voir ton scoring", "Visualizza il tuo punteggio"),
    "This replaces your existing QSO list and station description. Continue?": (
        "Dies ersetzt deine bestehende QSO-Liste und Stationsbeschreibung. Fortfahren?",
        "Cela remplace ta liste QSO existante et la description de station. Continuer ?",
        "Questo sostituisce il tuo elenco QSO esistente e la descrizione della stazione. Continuare?",
    ),
    "An .nmd file carries both the QSO log and the station description; uploading replaces both.": (
        "Eine .nmd-Datei enthält sowohl das QSO-Log als auch die Stationsbeschreibung; das Hochladen ersetzt beides.",
        "Un fichier .nmd contient à la fois le journal QSO et la description de station ; le téléversement remplace les deux.",
        "Un file .nmd contiene sia il log QSO che la descrizione della stazione; il caricamento sostituisce entrambi.",
    ),
    "Finalise": ("Abschliessen", "Finaliser", "Finalizza"),
    "When your log and station data are complete, submit them — submission is final.": (
        "Wenn dein Log und deine Stationsdaten vollständig sind, reiche sie ein — die Einreichung ist endgültig.",
        "Quand ton journal et tes données station sont complets, soumets-les — la soumission est définitive.",
        "Quando il tuo log e i dati stazione sono completi, inviali — l'invio è definitivo.",
    ),
    "Submit log": ("Log einreichen", "Soumettre le journal", "Invia log"),

    # Email templates
    "Log received": ("Log erhalten", "Journal reçu", "Log ricevuto"),
    "Hello %(user)s,": ("Hallo %(user)s,", "Bonjour %(user)s,", "Ciao %(user)s,"),
    "You're receiving this email because someone requested a password reset for your account at the NMD Contest Suite.": (
        "Du erhältst diese E-Mail, weil jemand ein Zurücksetzen deines Passworts für dein NMD-Contest-Suite-Konto angefordert hat.",
        "Tu reçois cet e-mail parce que quelqu'un a demandé la réinitialisation du mot de passe de ton compte sur NMD Contest Suite.",
        "Stai ricevendo questa mail perché qualcuno ha richiesto il reset della password del tuo account su NMD Contest Suite.",
    ),
    "Click the link below to choose a new password:": (
        "Klicke auf den Link unten, um ein neues Passwort zu wählen:",
        "Clique sur le lien ci-dessous pour choisir un nouveau mot de passe :",
        "Clicca sul link qui sotto per scegliere una nuova password:",
    ),
    "If you didn't request this, you can ignore this email.": (
        "Falls du das nicht angefordert hast, kannst du diese E-Mail ignorieren.",
        "Si tu n'es pas à l'origine de cette demande, ignore cet e-mail.",
        "Se non hai richiesto tu questa azione, puoi ignorare questa mail.",
    ),
    "Reset your NMD password": ("NMD-Passwort zurücksetzen", "Réinitialiser ton mot de passe NMD", "Reimposta la tua password NMD"),

    # log_entry / login / password reset / scoring
    "Your log has been submitted. The log below is shown read-only.": (
        "Dein Log wurde eingereicht. Das untenstehende Log wird schreibgeschützt angezeigt.",
        "Ton journal a été soumis. Le journal ci-dessous est affiché en lecture seule.",
        "Il tuo log è stato inviato. Il log qui sotto è mostrato in sola lettura.",
    ),
    "Anything you type is saved as-is. Invalid fields are highlighted in red so you can fix them later.": (
        "Alles, was du eintippst, wird so gespeichert. Ungültige Felder sind rot markiert, damit du sie später korrigieren kannst.",
        "Ce que tu tapes est enregistré tel quel. Les champs invalides sont surlignés en rouge pour que tu puisses les corriger plus tard.",
        "Tutto ciò che digiti viene salvato così com'è. I campi non validi sono evidenziati in rosso così potrai correggerli più tardi.",
    ),
    "Back to portal": ("Zurück zum Portal", "Retour au portail", "Torna al portale"),
    "Password": ("Passwort", "Mot de passe", "Password"),
    "Sign in": ("Anmelden", "Se connecter", "Accedi"),
    "Forgot your password?": ("Passwort vergessen?", "Mot de passe oublié ?", "Password dimenticata?"),
    "Password reset": ("Passwort zurücksetzen", "Réinitialiser le mot de passe", "Reimposta password"),
    "Enter the email address associated with your account; we'll send a reset link.": (
        "Gib die E-Mail-Adresse deines Kontos ein; wir senden dir einen Reset-Link.",
        "Saisis l'adresse e-mail associée à ton compte ; nous t'enverrons un lien de réinitialisation.",
        "Inserisci l'indirizzo e-mail associato al tuo account; ti invieremo un link di reset.",
    ),
    "Send reset link": ("Reset-Link senden", "Envoyer le lien", "Invia link di reset"),
    "Password updated": ("Passwort aktualisiert", "Mot de passe mis à jour", "Password aggiornata"),
    "Back to login": ("Zurück zum Login", "Retour à la connexion", "Torna al login"),
    "Choose a new password": ("Neues Passwort wählen", "Choisir un nouveau mot de passe", "Scegli una nuova password"),
    "Save new password": ("Neues Passwort speichern", "Enregistrer le nouveau mot de passe", "Salva nuova password"),
    "This password reset link is invalid or has already been used.": (
        "Dieser Reset-Link ist ungültig oder bereits verwendet worden.",
        "Ce lien de réinitialisation est invalide ou a déjà été utilisé.",
        "Questo link di reset non è valido o è già stato utilizzato.",
    ),
    "Request a new link": ("Neuen Link anfordern", "Demander un nouveau lien", "Richiedi un nuovo link"),
    "Email sent": ("E-Mail gesendet", "E-mail envoyé", "Email inviata"),
    "If an account exists for the address you gave us, a reset link is on its way.": (
        "Falls für die angegebene Adresse ein Konto existiert, ist ein Reset-Link unterwegs.",
        "Si un compte existe pour l'adresse fournie, un lien de réinitialisation est en route.",
        "Se esiste un account per l'indirizzo fornito, un link di reset è in arrivo.",
    ),

    # Scoring view
    "Your scoring": ("Deine Wertung", "Ton scoring", "Il tuo punteggio"),
    "\n            Your scoring — %(callsign)s (NMD %(year)s)\n        ": (
        "\n            Deine Wertung — %(callsign)s (NMD %(year)s)\n        ",
        "\n            Ton scoring — %(callsign)s (NMD %(year)s)\n        ",
        "\n            Il tuo punteggio — %(callsign)s (NMD %(year)s)\n        ",
    ),
    "Results have not been published yet. This page will show your per-QSO scoring once the administrator publishes the final ranking.": (
        "Die Resultate sind noch nicht veröffentlicht. Diese Seite zeigt deine QSO-Wertung, sobald der Administrator die finale Rangliste freigibt.",
        "Les résultats n'ont pas encore été publiés. Cette page affichera le scoring de chaque QSO dès que l'administrateur publiera le classement final.",
        "I risultati non sono ancora pubblicati. Questa pagina mostrerà il punteggio per ogni QSO non appena l'amministratore pubblicherà la classifica finale.",
    ),
    "Total points": ("Punkte total", "Points totaux", "Punti totali"),
    "Total": ("Total", "Total", "Totale"),
    "Combined total": ("Gesamtsumme", "Total combiné", "Totale combinato"),
    "NMD QSOs": ("NMD-QSOs", "QSO NMD", "QSO NMD"),
    "HB9 QSOs": ("HB9-QSOs", "QSO HB9", "QSO HB9"),
    "DX QSOs": ("DX-QSOs", "QSO DX", "QSO DX"),
    "Total QSOs": ("QSOs total", "QSO totaux", "QSO totali"),
    "QSO log with scoring": ("QSO-Log mit Wertung", "Journal QSO avec scoring", "Log QSO con punteggio"),
    "No QSOs logged.": ("Keine QSOs eingetragen.", "Aucun QSO enregistré.", "Nessun QSO registrato."),
    "Peer": ("Gegenstation", "Pair", "Pari"),
    "Half": ("Halbzeit", "Demi-période", "Mezza ora"),
    "Text": ("Text", "Texte", "Testo"),
    "Pts": ("Pkt.", "Pts", "Pt"),
    "paired: %(call)s @ %(time)s": ("gepaart: %(call)s @ %(time)s", "apparié : %(call)s @ %(time)s", "appaiato: %(call)s @ %(time)s"),
    "not scored": ("nicht gewertet", "non scoré", "non valutato"),
    "Sufficient NMD match": ("Akzeptiertes NMD QSO", "Appariement NMD suffisant", "Appaiamento NMD sufficiente"),
    "Your log has been submitted. No further changes are possible.": (
        "Dein Log wurde eingereicht. Weitere Änderungen sind nicht möglich.",
        "Ton journal a été soumis. Aucune modification supplémentaire n'est possible.",
        "Il tuo log è stato inviato. Ulteriori modifiche non sono possibili.",
    ),
    "Submit log — %(callsign)s": ("Log einreichen — %(callsign)s", "Soumettre le journal — %(callsign)s", "Invia log — %(callsign)s"),
    "This action is final.": ("Diese Aktion ist endgültig.", "Cette action est définitive.", "Questa azione è definitiva."),
    "After submission you cannot change your QSO log or station data.": (
        "Nach der Einreichung kannst du dein QSO-Log und deine Stationsdaten nicht mehr ändern.",
        "Après la soumission, tu ne peux plus modifier ton journal QSO ni tes données station.",
        "Dopo l'invio non puoi più modificare il tuo log QSO né i dati stazione.",
    ),
    "Summary": ("Zusammenfassung", "Résumé", "Riepilogo"),
    "QSOs in log": ("QSOs im Log", "QSO dans le journal", "QSO nel log"),
    "Submission is blocked.": ("Einreichung blockiert.", "Soumission bloquée.", "Invio bloccato."),
    "Fix the following first:": ("Bitte zuerst Folgendes beheben:", "Corrige d'abord ce qui suit :", "Correggi prima quanto segue:"),
    "Please review before submitting.": ("Bitte vor dem Einreichen überprüfen.", "Vérifie avant de soumettre.", "Verifica prima di inviare."),
    "These don't block submission — you can still proceed, but issues remain in the submitted record.": (
        "Diese blockieren die Einreichung nicht — du kannst fortfahren, aber die Probleme bleiben im eingereichten Datensatz.",
        "Ceci ne bloque pas la soumission — tu peux continuer, mais les problèmes resteront dans l'enregistrement soumis.",
        "Questi non bloccano l'invio — puoi procedere, ma i problemi resteranno nel record inviato.",
    ),
    "Confirm and submit": ("Bestätigen und einreichen", "Confirmer et soumettre", "Conferma e invia"),
    "I confirm that I have strictly complied with the contest regulations as well as the licence and radio-traffic rules, and that I accept the jury's decision.": (
        "Ich bestätige, dass das Wettbewerbsreglement sowie die Konzessions- und Radioverkehrsvorschriften genau eingehalten wurden, und dass ich mich dem Entscheid der Jury unterziehe.",
        "Je confirme avoir bien respecté le règlement du concours, ainsi que les prescriptions sur les concessions et les règles de trafic, et je m'en remets à la décision du jury.",
        "Confermo di aver seguito alla lettera i regolamenti, del Contest e della Concessione, e le Prescrizioni del traffico radio; inoltre dichiaro di attenermi alle decisioni della giuria.",
    ),
    "Please confirm the contest-rules statement before submitting.": (
        "Bitte bestätige die Wettbewerbserklärung, bevor du das Log einreichst.",
        "Veuillez confirmer la déclaration relative au règlement avant de soumettre.",
        "Conferma la dichiarazione del regolamento prima di inviare.",
    ),

    # Public ranking
    "Public ranking": ("Öffentliche Rangliste", "Classement public", "Classifica pubblica"),
    "live results page as participants see it": (
        "Ergebnisseite, wie sie die Teilnehmer sehen",
        "Page des résultats telle que la voient les participants",
        "Pagina dei risultati come la vedono i partecipanti",
    ),
    "Ranking preview": ("Vorschau Rangliste", "Aperçu du classement", "Anteprima della classifica"),
    "what the public page would show right now (admin-only)": (
        "Was die öffentliche Seite jetzt zeigen würde (nur für Admins)",
        "Ce que la page publique afficherait maintenant (admin uniquement)",
        "Cosa mostrerebbe la pagina pubblica adesso (solo admin)",
    ),
    "Ranking (PDF)": ("Rangliste (PDF)", "Classement (PDF)", "Classifica (PDF)"),
    "downloadable ranking for the club magazine": (
        "Herunterladbare Rangliste für die Vereinszeitschrift",
        "Classement téléchargeable pour la revue du club",
        "Classifica scaricabile per la rivista del club",
    ),
    "Admin preview — results not yet published. Reflects the most recent scoring run.": (
        "Admin-Vorschau — Resultate noch nicht veröffentlicht. Zeigt den Stand der letzten Wertung.",
        "Aperçu admin — résultats non encore publiés. Reflète le dernier calcul de scoring.",
        "Anteprima admin — risultati non ancora pubblicati. Riflette l'ultima esecuzione di scoring.",
    ),
    "Rank": ("Rang", "Rang", "Posizione"),
    "Altitude (m)": ("Höhe (m)", "Altitude (m)", "Altitudine (m)"),
    "QSO": ("QSO", "QSO", "QSO"),
    "Points": ("Punkte", "Points", "Punti"),
    "NMD": ("NMD", "NMD", "NMD"),
    "HB": ("HB", "HB", "HB"),
    "EU": ("EU", "EU", "EU"),
    "No participants.": ("Keine Teilnehmer.", "Aucun participant.", "Nessun partecipante."),
    "NMD %(year)s — Ranking": ("NMD %(year)s — Rangliste", "NMD %(year)s — Classement", "NMD %(year)s — Classifica"),
    "National Mountain Day %(year)s — Ranking": (
        "National Mountain Day %(year)s — Rangliste",
        "National Mountain Day %(year)s — Classement",
        "National Mountain Day %(year)s — Classifica",
    ),
    "Contest held %(date)s.": ("Contest am %(date)s.", "Contest tenu le %(date)s.", "Contest tenuto il %(date)s."),
    "Results published %(when)s UTC.": ("Resultate veröffentlicht am %(when)s UTC.", "Résultats publiés le %(when)s UTC.", "Risultati pubblicati il %(when)s UTC."),
    # These three trilingual labels appear together on the ranking page — keep
    # their msgid forms as-is so the heading reads "X / Y / Z" in every locale.
    "CW ranking": ("CW-Rangliste", "CW-Rangliste", "CW-Rangliste"),
    "Classement CW": ("Classement CW", "Classement CW", "Classement CW"),
    "Classifica CW": ("Classifica CW", "Classifica CW", "Classifica CW"),
    "SSB ranking": ("SSB-Rangliste", "SSB-Rangliste", "SSB-Rangliste"),
    "Classement SSB": ("Classement SSB", "Classement SSB", "Classement SSB"),
    "Classifica SSB": ("Classifica SSB", "Classifica SSB", "Classifica SSB"),
    "Données stations": ("Données stations", "Données stations", "Données stations"),
    "Dati stazioni": ("Dati stazioni", "Dati stazioni", "Dati stazioni"),
    "Total weight (g)": ("Gesamtgewicht (g)", "Poids total (g)", "Peso totale (g)"),

    # registration/closed.html, registration/index.html, success.html
    "Registration is closed": ("Anmeldung ist geschlossen", "L'inscription est fermée", "L'iscrizione è chiusa"),
    "\n                Registration for NMD %(year)s is no longer accepting new sign-ups.\n            ": (
        "\n                Die Anmeldung für NMD %(year)s nimmt keine neuen Eintragungen mehr an.\n            ",
        "\n                L'inscription au NMD %(year)s n'accepte plus de nouveaux participants.\n            ",
        "\n                L'iscrizione al NMD %(year)s non accetta più nuovi partecipanti.\n            ",
    ),
    "Already registered participants can still update their data via the participant portal.": (
        "Bereits angemeldete Teilnehmer können ihre Daten weiterhin über das Teilnehmerportal aktualisieren.",
        "Les participants déjà inscrits peuvent toujours mettre à jour leurs données via le portail participant.",
        "I partecipanti già iscritti possono ancora aggiornare i loro dati tramite il portale partecipanti.",
    ),
    "NMD %(year)s — Registration confirmation": (
        "NMD %(year)s — Anmeldebestätigung",
        "NMD %(year)s — Confirmation d'inscription",
        "NMD %(year)s — Conferma di iscrizione",
    ),
    "Register for the NMD": ("Für den NMD anmelden", "S'inscrire au NMD", "Iscriviti al NMD"),
    "Register for NMD %(year)s": ("Für den NMD %(year)s anmelden", "S'inscrire au NMD %(year)s", "Iscrizione al NMD %(year)s"),
    "\n            The contest takes place on %(date)s, 06:00–09:59 UTC.\n        ": (
        "\n            Der Contest findet am %(date)s von 06:00 bis 09:59 UTC statt.\n        ",
        "\n            Le contest a lieu le %(date)s, de 06:00 à 09:59 UTC.\n        ",
        "\n            Il contest si svolge il %(date)s, dalle 06:00 alle 09:59 UTC.\n        ",
    ),
    "Submit registration": ("Anmeldung absenden", "Soumettre l'inscription", "Invia iscrizione"),
    "Registration submitted": ("Anmeldung eingereicht", "Inscription soumise", "Iscrizione inviata"),
    "Thank you!": ("Vielen Dank!", "Merci !", "Grazie!"),
    "\n            <strong>%(callsign)s</strong> is registered for the upcoming NMD.\n        ": (
        "\n            <strong>%(callsign)s</strong> ist für den kommenden NMD angemeldet.\n        ",
        "\n            <strong>%(callsign)s</strong> est inscrit pour le prochain NMD.\n        ",
        "\n            <strong>%(callsign)s</strong> è iscritto al prossimo NMD.\n        ",
    ),
    "We've sent a confirmation email containing your portal credentials.": (
        "Wir haben eine Bestätigungs-E-Mail mit deinen Portal-Zugangsdaten gesendet.",
        "Nous avons envoyé un e-mail de confirmation contenant tes identifiants pour le portail.",
        "Abbiamo inviato una mail di conferma con le credenziali per il portale.",
    ),
    "We've sent a confirmation email. Use your existing password to sign in to the portal — or request a reset if you've forgotten it.": (
        "Wir haben eine Bestätigungs-E-Mail gesendet. Verwende dein bisheriges Passwort, um dich am Portal anzumelden — oder fordere ein Reset an, falls du es vergessen hast.",
        "Nous avons envoyé un e-mail de confirmation. Utilise ton mot de passe existant pour te connecter au portail — ou demande un reset si tu l'as oublié.",
        "Abbiamo inviato una mail di conferma. Usa la tua password attuale per accedere al portale — o richiedi un reset se l'hai dimenticata.",
    ),
    "Go to the participant portal": (
        "Zum Teilnehmerportal",
        "Aller au portail participant",
        "Vai al portale partecipanti",
    ),

    # scoring/review.html
    "No contest selected.": ("Kein Contest ausgewählt.", "Aucun contest sélectionné.", "Nessun contest selezionato."),
    "No participants in this contest yet — import a legacy DB or seed test data first.": (
        "Noch keine Teilnehmer in diesem Contest — importiere zuerst eine alte DB oder Testdaten.",
        "Aucun participant dans ce contest pour l'instant — importe d'abord une ancienne base ou des données de test.",
        "Ancora nessun partecipante in questo contest — importa prima un vecchio DB o dati di test.",
    ),
    "Call": ("Rufzeichen", "Indicatif", "Nominativo"),
    "Pick a participant on the left to see their QSO log with status.": (
        "Wähle links einen Teilnehmer, um dessen QSO-Log mit Status zu sehen.",
        "Choisis un participant à gauche pour voir son journal QSO avec statut.",
        "Seleziona un partecipante a sinistra per vedere il suo log QSO con stato.",
    ),
}


# Plural-form translations: msgid → {lang: (singular, plural)}.
PLURALS: dict[str, dict[str, tuple[str, str]]] = {
    "\n                %(n)s active participant will receive this message.\n            ": {
        "de": (
            "\n                %(n)s aktiver Teilnehmer erhält diese Nachricht.\n            ",
            "\n                %(n)s aktive Teilnehmer erhalten diese Nachricht.\n            ",
        ),
        "fr": (
            "\n                %(n)s participant actif recevra ce message.\n            ",
            "\n                %(n)s participants actifs recevront ce message.\n            ",
        ),
        "it": (
            "\n                %(n)s partecipante attivo riceverà questo messaggio.\n            ",
            "\n                %(n)s partecipanti attivi riceveranno questo messaggio.\n            ",
        ),
    },
    "Send this message to %(n)s participant?": {
        "de": (
            "Diese Nachricht an %(n)s Teilnehmer senden?",
            "Diese Nachricht an %(n)s Teilnehmer senden?",
        ),
        "fr": (
            "Envoyer ce message à %(n)s participant ?",
            "Envoyer ce message à %(n)s participants ?",
        ),
        "it": (
            "Inviare questo messaggio a %(n)s partecipante?",
            "Inviare questo messaggio a %(n)s partecipanti?",
        ),
    },
    "%(n)s participant.": {
        "de": ("%(n)s Teilnehmer.", "%(n)s Teilnehmer."),
        "fr": ("%(n)s participant.", "%(n)s participants."),
        "it": ("%(n)s partecipante.", "%(n)s partecipanti."),
    },
}


# --- Parser / serialiser ----------------------------------------------------------------------

_UNQUOTE_MAP = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r"}


def _unquote(line: str) -> str:
    """Return the content of a `"..."` line, with .po escape sequences resolved."""
    if not (line.startswith('"') and line.endswith('"')):
        raise ValueError(f"Expected quoted line, got: {line!r}")
    inner = line[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\" and i + 1 < len(inner):
            out.append(_UNQUOTE_MAP.get(inner[i + 1], inner[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _quote(text: str) -> str:
    """Wrap ``text`` as a single-line .po quoted string with proper escapes."""
    out = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{out}"'


def translate_po(po_text: str, lang: str) -> str:
    """Read a makemessages-generated .po body, replace every msgstr with the
    translation for ``lang`` from TRANSLATIONS/PLURALS. Returns the new body."""
    lines = po_text.splitlines()
    out: list[str] = []
    i = 0
    current_msgid: str | None = None
    current_plural: str | None = None
    missing: list[str] = []

    def read_quoted(start: int, prefix: str) -> tuple[str, int]:
        first = lines[start]
        assert first.startswith(prefix + ' "'), f"{prefix!r} at line {start + 1}: {first!r}"
        text = _unquote(first[len(prefix) + 1:])
        j = start + 1
        while j < len(lines) and lines[j].startswith('"'):
            text += _unquote(lines[j])
            j += 1
        return text, j

    while i < len(lines):
        line = lines[i]
        if line.startswith('msgid '):
            current_msgid, j = read_quoted(i, "msgid")
            current_plural = None
            # If we have a translation for this msgid, strip any preceding
            # `#, fuzzy` flag and `#|` previous-msgid hint that makemessages
            # left behind — build_translations.py IS the review step those
            # markers are waiting for, and leaving them in makes Django
            # fall back to the English msgid at runtime.
            if current_msgid in TRANSLATIONS or current_msgid in PLURALS:
                block_start = len(out)
                while block_start > 0 and out[block_start - 1].startswith("#"):
                    block_start -= 1
                kept: list[str] = []
                for cm in out[block_start:]:
                    if cm.startswith("#|"):
                        continue
                    if cm.startswith("#, ") and "fuzzy" in cm:
                        rest = [
                            f.strip()
                            for f in cm[3:].split(",")
                            if f.strip() and f.strip() != "fuzzy"
                        ]
                        if rest:
                            kept.append("#, " + ", ".join(rest))
                        continue
                    kept.append(cm)
                out[block_start:] = kept
            for k in range(i, j):
                out.append(lines[k])
            i = j
            continue
        if line.startswith('msgid_plural '):
            current_plural, j = read_quoted(i, "msgid_plural")
            for k in range(i, j):
                out.append(lines[k])
            i = j
            continue
        if line.startswith('msgstr '):
            # Single-form msgstr. Skip its old content, emit translated version.
            _old, j = read_quoted(i, "msgstr")
            if current_msgid == "":
                # The header. Preserve as-is, but rewrite Language: line.
                for k in range(i, j):
                    line_k = lines[k]
                    if 'Language:' in line_k:
                        line_k = re.sub(r'"Language: [^"]*\\n"', f'"Language: {lang}\\\\n"', line_k)
                    out.append(line_k)
                i = j
            else:
                trans = TRANSLATIONS.get(current_msgid)
                if trans is None:
                    missing.append(current_msgid)
                    out.append('msgstr ""')
                else:
                    idx = LANGS.index(lang)
                    out.append(f'msgstr {_quote(trans[idx])}')
                i = j
                current_msgid = None
            continue
        if line.startswith('msgstr['):
            m = re.match(r'^msgstr\[(\d+)\] ', line)
            assert m, f"Bad plural msgstr at line {i + 1}: {line!r}"
            idx = int(m.group(1))
            _old, j = read_quoted(i, f"msgstr[{idx}]")
            plural_map = PLURALS.get(current_msgid or "")
            if plural_map is None or lang not in plural_map:
                missing.append(f"[plural] {current_msgid}")
                out.append(f'msgstr[{idx}] ""')
            else:
                forms = plural_map[lang]
                value = forms[idx] if idx < len(forms) else ""
                out.append(f'msgstr[{idx}] {_quote(value)}')
            i = j
            # Don't reset current_msgid until we leave the plural block.
            # The next non-msgstr line will reset via the next msgid.
            continue
        # Default: pass through.
        out.append(line)
        i += 1

    if missing:
        print(f"  warning ({lang}): {len(missing)} string(s) without translation:", file=sys.stderr)
        for m in missing[:20]:
            print(f"    - {m!r}", file=sys.stderr)
        if len(missing) > 20:
            print(f"    … {len(missing) - 20} more", file=sys.stderr)

    return "\n".join(out) + ("\n" if po_text.endswith("\n") else "")


# --- Main ------------------------------------------------------------------------------------


def main() -> int:
    template_path = LOCALE_DIR / "de" / "LC_MESSAGES" / "django.po"
    if not template_path.is_file():
        print(f"ERROR: {template_path} not found. Run makemessages first.", file=sys.stderr)
        return 1
    template = template_path.read_text(encoding="utf-8")

    for lang in LANGS:
        target = LOCALE_DIR / lang / "LC_MESSAGES" / "django.po"
        out = translate_po(template, lang)
        target.write_text(out, encoding="utf-8")
        print(f"  wrote {target}")
    print(f"Done. Run `python manage.py compilemessages` next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

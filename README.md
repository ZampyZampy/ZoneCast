# ZoneCast — sistema di filodiffusione e PA su IP

Software web per gestire un impianto di diffusione sonora/PA basato su
altoparlanti IP (nativamente testato su **Fanvil A233**, ma pensato per
restare agnostico rispetto al brand — dropdown con Fanvil, Algo,
CyberData, Grandstream, Axis, Tiptel, Akuvox e opzione libera "Altro").
Permette di censire altoparlanti, raggrupparli in zone, caricare file
audio, riprodurli on-demand e schedularli nel tempo con calendario
festività configurabile per paese.

## Funzionalità principali

- **Diffusione multicast RTP** verso zone, singoli altoparlanti o tutta
  la flotta, con cronologia delle riproduzioni.
- **Schedulazioni** ricorrenti con calendario festività per paese
  (Italia, Stati Uniti, Regno Unito, Francia, Germania, Spagna,
  Giappone, Cina) — escludi, includi sempre o riproduci solo nei giorni
  festivi.
- **Analisi audio automatica** dei file caricati, con avviso se i bassi
  sono dominanti (poco adatti alle trombe PA) e suggerimento di
  amplificazione quando c'è margine prima della distorsione.
- **Provisioning automatico** dei device supportati (vedi sezione 2) e
  backup/ripristino della loro configurazione.
- **Due ruoli utente**: amministratore (accesso completo, incluse le
  sezioni Utenti/Sistema/Log/Archivio backup, più le azioni dirette sul
  device come "Applica ora" e "Backup") e operatore (riproduzione,
  gestione di altoparlanti/zone/media/schedulazioni).
- **2FA** opzionale per singolo utente (TOTP + codici di recupero),
  **export/import completo** dell'installazione (database, media,
  backup, chiave di cifratura) per migrazioni tra macchine.
- **Pannello Sistema** (solo admin): rete (IP/DNS/gateway con periodo di
  prova), orario/NTP, monitoraggio risorse macchina (disco/RAM/CPU),
  changelog e numero di versione.
- **Interfaccia multilingua** (italiano, inglese, cinese, giapponese,
  tedesco, spagnolo, francese) con tema chiaro/scuro/automatico
  (basato su alba/tramonto reali), a scelta di ogni utente.

## 1. Architettura

```
┌─────────────┐      HTTPS/REST      ┌──────────────────────────┐
│  Browser     │ ───────────────────▶ │  FastAPI (backend)       │
│  (dashboard  │ ◀─────────────────── │  - auth a sessione       │
│  vanilla JS) │                      │  - CRUD zone/speaker     │
└─────────────┘                      │  - upload media          │
                                      │  - scheduler (APScheduler)│
                                      └──────────┬────────────────┘
                                                 │ RTP / G.711 (UDP multicast)
                                                 ▼
                                   ┌───────────────────────────────┐
                                   │   LAN — gruppi multicast        │
                                   │   239.x.x.x per zona/speaker    │
                                   └───────────────────────────────┘
                                      │            │            │
                                 Fanvil A233   Fanvil A233   Fanvil A233
                                 (zona 1)      (zona 1)      (zona 2)
```

**Stack scelto**
- **Backend**: Python 3.11 + FastAPI (async, ottimo per orchestrare I/O di rete/scheduler) + SQLAlchemy 2.0 + APScheduler.
- **Frontend**: HTML/Bootstrap 5 + JavaScript vanilla (nessuna build step, un solo container da deployare, dashboard SPA-like con `fetch()`).
- **Database**: SQLite di default (`data/zonecast.db`), portabile e sufficiente per questo volume di dati; il connection string è configurabile, quindi passare a PostgreSQL è solo un cambio di `DATABASE_URL`.
- **Autenticazione**: sessione lato server (cookie firmato via `SessionMiddleware`), password con bcrypt.
- **Festività italiane**: libreria [`holidays`](https://pypi.org/project/holidays/) (`holidays.Italy()`), calcolate dinamicamente (gestisce anche la Pasqua) — nessuna tabella statica da mantenere.

### Perché multicast RTP verso i Fanvil (e non solo HTTP/SIP)

I Fanvil A233 (come la generalità degli speaker IP da paging — Algo,
Cyberdata, Grandstream GSC, ecc.) implementano nativamente il
**Multicast Paging**: dal web UI del singolo dispositivo si configura una
lista di *gruppi multicast* che il device resta in ascolto (in ricezione
RTP) e riproduce **immediatamente e in perfetto sincrono** non appena
arriva un flusso audio su quell'indirizzo. Per il nostro caso d'uso
(riproduzione singola / per zona / su tutti, anche simultanea) è la
soluzione più robusta perché:

- **Sincronia reale**: tutti gli speaker di una zona ricevono lo stesso
  flusso UDP nello stesso istante — niente sfasamenti come si avrebbero
  chiamando N speaker singolarmente via SIP.
- **Nessuna gestione di stato per dispositivo**: non serve tenere
  registrazioni SIP, dialoghi, retry di chiamata — si manda un flusso
  UDP e i device in ascolto lo riproducono.
- **Scalabile**: aggiungere un nuovo speaker è solo "aggiungilo al
  gruppo multicast della zona", il backend non cambia.

Per questo motivo il backend implementa **da zero** un mittente
RTP/G.711 (`app/services/rtp_multicast.py`): pacchettizza l'audio a 8kHz
mono in frame G.711 da 20ms, costruisce l'header RTP (seq/timestamp/SSRC)
e lo invia via UDP con `IP_MULTICAST_TTL` configurabile, temporizzato in
tempo reale.

**Modello di targeting**: ogni *Speaker* ha un proprio gruppo multicast
dedicato (per il "riproduci sul singolo altoparlante"), ogni *Zona* ha il
proprio gruppo condiviso dai suoi speaker, ed esiste un gruppo
"all-call" globale a cui tutti gli speaker sono iscritti. Riprodurre un
file è quindi sempre la stessa identica operazione lato server: uno
stream RTP verso un solo indirizzo multicast — la differenza la fa solo
quali device sono, sul proprio web UI, in ascolto su quell'indirizzo.

**Fallback HTTP CGI**: `app/services/fanvil_http.py` espone un client
best-effort per il CGI di controllo remoto Fanvil (protetto da Digest
Auth con le stesse credenziali dell'admin web) da usare per estensioni
come controlli di raggiungibilità, o per invocare azioni specifiche una
volta verificata la sintassi CGI esatta per il proprio firmware presso
il supporto Fanvil (varia tra le famiglie di prodotto e non è
documentata pubblicamente in modo univoco) — l'endpoint è
intenzionalmente configurabile e non hard-coded per non promettere una
sintassi non verificata. Per la riproduzione audio vera e propria non
serve: se ne occupa il percorso multicast.

**SIP paging come alternativa**: è un'opzione architetturalmente valida
(originare una chiamata verso l'interno SIP dello speaker, che risponde
automaticamente) ma richiede una registrazione SIP per device e un vero
stack SIP/RTP lato server (es. tramite un PBX come Asterisk). Non è
implementata qui perché il multicast copre meglio proprio il caso
"riproduzione simultanea su più speaker", ma l'architettura a servizi
(`app/services/`) rende semplice aggiungere `sip_paging.py` in futuro se
serve raggiungere uno speaker che non supporta il multicast.

## 2. Provisioning dei Fanvil A233 e gestione delle zone da software

L'appartenenza a una zona **si gestisce interamente dalla dashboard** di
ZoneCast (assegnando/cambiando la zona di uno speaker, o modificando
l'indirizzo multicast di una zona): non serve più aprire il web UI del
singolo Fanvil per aggiungere/rimuovere manualmente le voci della lista
multicast paging a ogni cambio.

**Come funziona**: ogni speaker deve avere sempre in ascolto tre gruppi
multicast — il proprio (targeting singolo), quello della zona a cui
appartiene (se assegnata) e quello globale "all-call" (comune a tutti).
Quando in dashboard cambi la zona di uno speaker, o l'indirizzo di una
zona, il backend (`app/services/multicast_provisioning.py`) ricalcola
questa lista e la scrive **in background, subito**, sul dispositivo —
scrivendo solo i singoli parametri `paging.multicast_addr.N` /
`paging.multicast_label.N` / `paging.multicast_priority.N` via CGI
(`app/services/fanvil_http.py`), con le credenziali admin già salvate
nell'anagrafica dello speaker.

**Perché non un resync/riconfigurazione completa**: un resync
farebbe ririchiedere al device l'intero file di configurazione, e
qualunque impostazione non presente in quel file (account SIP, rete,
ecc.) rischia — a seconda del firmware — di essere sovrascritta o
azzerata; se il device è già provisionato da un altro sistema (es. un
PBX) per il SIP, ridirigere il suo URL di auto-provisioning verso
ZoneCast interromperebbe anche quello. Scrivendo **solo** i parametri
`paging.*` uno per uno, per costruzione non si tocca mai nient'altro, e
l'eventuale sistema di provisioning già in uso per il SIP resta
intatto.

**Uso pratico dalla dashboard** (tab Altoparlanti):
- **Anteprima**: mostra esattamente quali voci `paging.*` verrebbero
  scritte sul device, senza inviare nulla — utile per verificare prima
  di applicare.
- **Applica ora**: forza la (ri)scrittura immediata, utile dopo un
  ripristino del device o se una modifica sul campo è stata annullata a
  mano.
- Il push automatico in background scatta solo quando cambiano i campi
  che incidono sulla lista paging (zona assegnata, oppure indirizzo/porta
  multicast propri o della zona) — modifiche a nome, ubicazione,
  credenziali, ecc. non generano scritture inutili sul device.

**Setup iniziale per ciascuno speaker** (una tantum, dal web UI Fanvil):
1. Abilitare il multicast paging e impostare il codec coerente con
   `RTP_PAYLOAD_TYPE` (default `0` = G.711 µ-law/PCMU; `8` per A-law/PCMA).
2. Inserire nell'anagrafica dello speaker su ZoneCast le credenziali
   admin del web UI del device (`http_username`/`http_password`) — sono
   quelle usate per il canale CGI di scrittura.
3. Fare un primo **Applica ora** dalla dashboard e verificare sul web UI
   del device che le tre voci compaiano correttamente.

> ⚠️ **Verificare prima di usarlo in produzione**: la sintassi esatta del
> CGI di scrittura parametro (`ConfigManApp.com?key=...&value=...` in
> `fanvil_http.py`) non è documentata pubblicamente in modo univoco e
> varia tra famiglie di firmware — è un punto di partenza plausibile, non
> una certezza. Prima di applicarlo su tutta la flotta: esportare/salvare
> la configurazione di UN device non critico dal suo web UI, testare
> "Applica ora" su quello, e confrontare la configurazione esportata
> prima/dopo per assicurarsi che solo le voci di paging siano cambiate.
> Se la sintassi non corrisponde al proprio firmware, il push fallirà in
> modo innocuo (errore 502 in dashboard, nessuna scrittura) e si può
> sempre configurare manualmente come fallback (vedi indirizzi mostrati
> in "Anteprima").

> Nota rete: gli indirizzi multicast (`239.0.0.0/8`, range
> "administratively scoped") devono essere raggiungibili tra il server e
> gli switch/VLAN dove risiedono gli speaker — verificare che l'IGMP
> snooping/querier sia attivo sugli switch di rete se gli speaker sono
> su segmenti diversi dal server.

## 3. Struttura del database

| Tabella          | Scopo |
|-------------------|-------|
| `users`            | Account di accesso alla dashboard (ruoli `admin`/`operator`) |
| `zones`            | Raggruppamenti logici di speaker, con proprio gruppo multicast |
| `speakers`         | Anagrafica altoparlanti: IP, credenziali web, zona, gruppo multicast dedicato, stato |
| `media`            | File audio caricati (originale + versione PCM 8kHz pre-convertita per lo streaming) |
| `schedules`        | Regole di programmazione: media, target, orario, giorni, intervallo date, regola festività, paese del calendario festività |
| `playback_logs`    | Storico delle riproduzioni (manuali e schedulate), con stato ed eventuali errori |

Vedi `app/models.py` per i dettagli dei campi.

### Migrazioni schema (Alembic)

Le modifiche allo schema si gestiscono con Alembic invece che a mano:

```bash
alembic revision --autogenerate -m "descrizione della modifica"
alembic upgrade head
```

`render_as_batch` è attivo nell'`env.py` incluso, quindi anche le
modifiche che SQLite non supporta direttamente (es. cambiare tipo o
nullabilità di una colonna) vengono gestite automaticamente con la
tecnica "ricrea la tabella e ricopia i dati" — niente più script
manuali per queste operazioni. Su un'installazione già esistente non
ancora tracciata da Alembic, allinearla una tantum senza eseguire
nulla con `alembic stamp head`.

## 4. Avvio in locale (senza Docker)

Richiede Python 3.11+ (testato anche su 3.13/3.14 — il backport
`audioop-lts` in `requirements.txt` copre la rimozione di `audioop`
dalla stdlib a partire da Python 3.13) e **ffmpeg** nel PATH (usato per
convertire gli upload in PCM 8kHz mono per lo streaming G.711).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # Linux/Mac

pip install -r requirements.txt
copy .env.example .env            # Windows: copy — Linux/Mac: cp
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Apri `http://localhost:8000` — utente iniziale: quello impostato in
`.env` (`DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD`, default
`admin`/`admin`). **Cambiare subito la password** creando un nuovo
utente admin e disattivando/eliminando quello di default, oppure con:

```bash
python scripts/reset_admin_password.py admin "NuovaPasswordSicura!"
```

## 5. Deployment con Docker

### Linux (consigliato per produzione)

```bash
cp .env.example .env   # personalizzare SECRET_KEY, credenziali, ecc.
docker compose up -d --build
```

`docker-compose.yml` usa `network_mode: host`, indispensabile perché il
traffico multicast RTP esca realmente sulla LAN fisica dove risiedono i
Fanvil (il bridge Docker di default fa NAT e non instrada il multicast
verso la rete esterna).

### Windows

Docker Desktop su Windows **non supporta** `network_mode: host`. Due
opzioni:

- **Opzione consigliata per uso in produzione su LAN**: eseguire
  l'applicazione nativamente su Windows (sezione 4, senza Docker) — è un
  singolo processo Python, non serve containerizzarlo per forza se il
  server è dedicato.
- **Opzione Docker su Windows**: usare il motore WSL2 di Docker Desktop
  con una rete `macvlan` agganciata all'adattatore fisico, in modo che
  il container ottenga un IP reale sulla LAN (necessario per il
  multicast). Esempio:
  ```bash
  docker network create -d macvlan \
    --subnet=192.168.187.0/24 --gateway=192.168.187.1 \
    -o parent=eth0 zonecast-lan
  ```
  poi collegare il servizio `zonecast` a `zonecast-lan` invece che a
  `network_mode: host` nel compose file (vedi commenti nel file).

La variante bridge/`ports: 8000:8000` inclusa (commentata) in
`docker-compose.yml` è utile solo per sviluppare la dashboard — l'audio
non raggiungerà gli speaker finché il container non ha un percorso di
uscita multicast reale.

### Deployment nativo (senza Docker) — Ubuntu Server LTS dedicato

Per una VM/macchina dedicata solo a ZoneCast, un'installazione nativa
evita completamente i problemi di rete multicast/NAT del bridge Docker
(l'app parla direttamente con le interfacce dell'host) e le limitazioni
AppArmor/D-Bus sull'NTP — al costo di gestire Python/systemd a mano
invece di un'immagine. Su Ubuntu Server 24.04+/26.04 LTS:

```bash
git clone https://github.com/ZampyZampy/ZoneCast.git /tmp/zonecast-src && cd /tmp/zonecast-src
sudo ./deploy/install_ubuntu.sh
```

Installa l'app in `/opt/zonecast`, crea un utente di sistema dedicato
non privilegiato (`zonecast`), un virtualenv, e un servizio systemd
(`deploy/zonecast.service`, con `CAP_SYS_TIME` concessa in modo nativo
per l'impostazione manuale dell'orologio). Vedi i commenti in
`deploy/install_ubuntu.sh` per i dettagli. Gestione del servizio:

```bash
sudo systemctl status zonecast
sudo journalctl -u zonecast -f
sudo systemctl restart zonecast
```

Per trasferire su questa nuova macchina tutti i dati di un'installazione
esistente (altoparlanti, zone, schedulazioni, utenti, media, backup),
usa l'export completo da Sistema > "Esporta configurazione completa"
sull'installazione di origine, poi, sulla nuova macchina, **prima** del
primo avvio del servizio:

```bash
sudo systemctl stop zonecast   # se già avviato dall'installer
cd /opt/zonecast
sudo -u zonecast venv/bin/python -m app.tools.import_bundle /percorso/export.zcbundle
sudo systemctl start zonecast
```

## 6. Note operative

- I file audio vengono convertiti all'upload in una copia PCM 8kHz mono
  16-bit (`media/<id>.pcm8k.wav`), formato richiesto per l'encoding
  G.711/RTP — l'originale resta comunque salvato.
- Lo scheduler (APScheduler, timezone `Europe/Rome` di default) ricarica
  i job all'avvio e li aggiorna a ogni modifica via API; ogni job, al
  momento dell'esecuzione, verifica anche la regola festività e
  l'eventuale intervallo di date, quindi non serve ricalcolare nulla
  quando si edita una schedulazione.
- Aggiungere un nuovo speaker/marca in futuro richiede solo: provisioning
  del gruppo multicast sul device (se supporta multicast paging
  standard) + censimento nella dashboard — nessuna modifica al backend.
- Versione e changelog dell'applicativo installato sono consultabili da
  Sistema > Informazioni (aggiornati a ogni release — vedi
  `app/version.py`).

## 7. Documentazione

- [`docs/ZoneCast_Guida_Deploy.pdf`](docs/ZoneCast_Guida_Deploy.pdf) —
  guida tecnica al deployment su macchina vergine: requisiti,
  architettura, installazione (nativa/Docker), migrazione, sicurezza,
  gestione quotidiana, risoluzione dei problemi comuni.
- [`docs/ZoneCast_Manuale_Utente.pdf`](docs/ZoneCast_Manuale_Utente.pdf)
  — manuale illustrato per l'utente finale, con screenshot di ogni
  sezione della dashboard.
- [`docs/zonecast_install_kit.tar.gz`](docs/zonecast_install_kit.tar.gz)
  — pacchetto pronto per un'installazione nativa su macchina senza
  accesso diretto a questo repository (contiene `app/`, `deploy/`,
  `requirements.txt`, `Dockerfile`, `docker-compose.yml`,
  `.env.example`, `README.md`).

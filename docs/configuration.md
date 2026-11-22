# Configuration and files

## Server options — `rx_config.json`

Created in the data folder at the first start. Restart the container after a change.

| Key | Default | Meaning |
|---|---|---|
| `ore_tra_aggiornamenti_maxi` | 6 | hours between two MAXI cycles |
| `ore_tra_aggiornamenti_gbm` | 24 | hours between two Fermi GBM cycles |
| `minuti_tra_notizie` | 30 | minutes between two reads of ATel and GCN |
| `secondi_tra_richieste` | 1.0 | pause between two downloads, to be gentle with the archives |
| `soglia_avviso_mCrab` | 30 | a **new** outburst is added to `eventi.json` (and notified) only if at least this bright |
| `giorni_per_dire_nuova` | 21 | an outburst is "new" for this many days after its start |
| `swift_bat_al_giro` | 40 | Swift/BAT histories tried per 5-minute cycle (done once) |

## Files written by the server (data folder)

```
Raggi_X/
    programma/          the program (and _python/, the libraries installed at the first start)
    index.html          the web page (copied from programma/ at every start)
    mappa.png           sky map
    grafici/            plots generated on request, kept 6 h
    dati/               served to the desktop app, same names as the desktop cache:
        stato.json          heartbeat, written every minute (the desktop app uses it to know the server is there)
        sorgenti.json       every source: state, today, usual, ratio, hardness, outburst start/peak,
                            Fermi GBM and Swift/BAT names, description (Italian), linked news
        eventi.json         new outbursts (the notifier reads this)
        notizie.json        last 400 news items (ATel + GCN)
        maxi_elenco.html    MAXI source list
        maxi/<ID>_1day.dat  MAXI daily light curves
        gbm_elenco.html, gbm/<NAME>.txt       Fermi GBM
        bat/<Name>.lc.txt                      Swift/BAT history
        notizie/            raw news files
    rx_config.json      options (above)
    rx_stato.json       internal state (times of the last cycles, GCN number, BAT sources already tried)
    registro.txt        log (rotated at 2 MB)
```

The web server answers on port 8000 inside the container: `/` (page), `/dati/...` (files above),
`/grafico/<MAXI-ID>.png?giorni=60|365|1826|0` (plot drawn on request; 0 = whole mission). `programma/` and
`rx_stato.json` are not served.

## Command line

Server (`nas/servizio_rx.py`):

| Command | |
|---|---|
| `python3 servizio_rx.py [FOLDER]` | run forever (default folder `/dati`, or `$RX_DATI`) |
| `python3 servizio_rx.py FOLDER --una-volta [--max N]` | one cycle with the real archives (optionally only N sources), then exit |
| `python3 servizio_rx.py FOLDER --solo-pagina` | only serve the page of FOLDER on port 8097 |
| `python3 servizio_rx.py --prova` | offline self-test with simulated archives (20 checks) |

Desktop (`pc/raggi_x_pc.py`, installed as `RaggiX`): no option = window; `--autotest`; `--diagnostica`.

Notifier (`pc/avvisatore_rx.py`): no option = run in background (checks every 10 minutes); `--prova`;
`--indirizzo`.

## Desktop files

| File | |
|---|---|
| `~/.config/raggi_x.json` | window settings (text size, period, Swift/BAT on/off, window size) and `nas` = server address |
| `~/.config/raggi_x_avvisatore.json` | notifier: server address and the events already notified |
| `~/.cache/raggi_x/` | data downloaded by the desktop app when the server is not reachable |
| `~/Scrivania/Astronomia Analizzati/Raggi_X/` | PNG files saved with "Salva immagine" (`Scrivania` = Desktop in Italian) |

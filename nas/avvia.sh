#!/bin/bash
# Raggi X dai buchi neri 1.0 - avvio del servizio nel container Docker del NAS
# © 2026 Edoardo Battistel
# Al primo avvio installa le librerie in /dati/programma/_python (restano sul NAS:
# i riavvii successivi sono immediati). Poi avvia il servizio a priorità minima.
umask 000                                   # file leggibili e cancellabili anche dal PC
cd /dati/programma || exit 1
export HOME=/dati/programma/_home PYTHONUNBUFFERED=1 MPLCONFIGDIR=/dati/programma/_home/mpl
mkdir -p "$HOME" "$MPLCONFIGDIR"
VENV=/dati/programma/_python
if [ ! -f "$VENV/.pronto_1.0" ]; then
    echo "Primo avvio: installo le librerie (2-5 minuti, serve Internet)…"
    until python -m venv "$VENV" && \
          "$VENV/bin/pip" install --no-cache-dir --upgrade pip && \
          "$VENV/bin/pip" install --no-cache-dir -r requisiti.txt; do
        echo "Installazione non riuscita (Internet assente?): riprovo tra 10 minuti"
        sleep 600
    done
    touch "$VENV/.pronto_1.0"
fi
exec nice -n 19 ionice -c 3 "$VENV/bin/python" servizio_rx.py /dati

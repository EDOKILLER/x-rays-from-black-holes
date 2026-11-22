#!/bin/bash
# Raggi X dai buchi neri 1.0 - installa la parte del PC (Ubuntu)
# © 2026 Edoardo Battistel
#
# Uso (dalla cartella pc di questa versione):   bash installa.sh
#  - copia il programma (finestra), il motore, l'avvisatore e l'icona;
#  - chiede l'indirizzo della pagina del NAS (di solito http://192.168.1.135:8097);
#  - mette nel menu e sulla Scrivania «Raggi X dai buchi neri» (la finestra);
#  - fa partire l'avvisatore delle nuove esplosioni a ogni accesso.
set -e
QUI="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/raggi_x"
ICONE="$HOME/.local/share/icons"
mkdir -p "$DEST" "$ICONE" "$HOME/.local/bin" "$HOME/.config/autostart" "$HOME/.local/share/applications"

# controllo delle librerie (la finestra usa il Python del sistema)
MANCANO=$(python3 -c "
m=[]
for n in ('numpy','matplotlib','tkinter'):
    try: __import__(n)
    except Exception: m.append(n)
print(' '.join(m))")
if [ -n "$MANCANO" ]; then
    echo "ATTENZIONE: mancano queste librerie di Python: $MANCANO"
    echo "  installale con:  sudo apt install python3-numpy python3-matplotlib python3-tk"
fi

cp "$QUI/raggi_x_pc.py" "$QUI/motore_rx.py" "$QUI/avvisatore_rx.py" "$DEST/"
cp "$QUI/raggi_x.png" "$ICONE/raggi_x.png"

# indirizzo del NAS (finestra di zenity); vale per l'avvisatore e per la finestra
python3 "$DEST/avvisatore_rx.py" --indirizzo
INDIRIZZO=$(python3 -c "import json,pathlib;print(json.loads((pathlib.Path.home()/'.config/raggi_x_avvisatore.json').read_text())['indirizzo'])")

# comando per la finestra
cat > "$HOME/.local/bin/RaggiX" <<EOF
#!/bin/bash
exec python3 "$DEST/raggi_x_pc.py" "\$@"
EOF
chmod +x "$HOME/.local/bin/RaggiX"

# voce nel menu (il nome del file e StartupWMClass = classe della finestra Tk «Raggix»)
cat > "$HOME/.local/share/applications/raggix.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Raggi X dai buchi neri
Comment=Esplosioni nel cielo X: buchi neri, stelle di neutroni, galassie attive (© 2026 Edoardo Battistel)
Exec=$HOME/.local/bin/RaggiX
Icon=$ICONE/raggi_x.png
Terminal=false
Categories=Education;Science;Astronomy;
StartupWMClass=Raggix
EOF
chmod +x "$HOME/.local/share/applications/raggix.desktop"

# avvio automatico dell'avvisatore all'accesso
cat > "$HOME/.config/autostart/raggi_x_avvisatore.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Raggi X – avvisatore delle esplosioni
Comment=Avvisa quando nel cielo X si accende una nuova esplosione (© 2026 Edoardo Battistel)
Exec=python3 $DEST/avvisatore_rx.py
Icon=$ICONE/raggi_x.png
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=40
NoDisplay=true
EOF

# copia sul desktop (se esiste la cartella Scrivania o Desktop)
for D in "$HOME/Scrivania" "$HOME/Desktop"; do
    if [ -d "$D" ]; then
        cp "$HOME/.local/share/applications/raggix.desktop" "$D/"
        chmod +x "$D/raggix.desktop"
        gio set "$D/raggix.desktop" metadata::trusted true 2>/dev/null || true
        break
    fi
done
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

# avvia subito l'avvisatore (senza aspettare il prossimo accesso)
pkill -f "raggi_x/avvisatore_rx.py" 2>/dev/null || true
nohup python3 "$DEST/avvisatore_rx.py" > "$DEST/avvisatore.log" 2>&1 &

echo
echo "Fatto."
echo "  Finestra:    «Raggi X dai buchi neri» nel menu e sulla Scrivania (oppure: RaggiX)"
echo "  Pagina:      $INDIRIZZO/"
echo "  Avvisatore:  attivo (registro: $DEST/avvisatore.log)"
echo "  Prova della notifica:  python3 $DEST/avvisatore_rx.py --prova"
echo "  Prova della finestra:  RaggiX --autotest"

# Installation

The program has two parts that work together but can also be used separately:

| Part | Where | What it needs |
|---|---|---|
| **Server** (`nas/`) | an always-on Linux machine with Docker (a NAS, a mini PC, a Raspberry Pi 4/5 with 64-bit OS) | Docker, ~0.5 GB of disk, Internet access |
| **Desktop app + notifier** (`pc/`) | a Linux desktop (tested on Ubuntu with GNOME) | Python 3, `numpy`, `matplotlib`, `tkinter`, `notify-send` |

The desktop app also works **without** the server: it then downloads the data of the selected sources directly
from the archives (slower, and no notifications).

## 0. Check your network (optional, recommended)

```bash
python3 tools/prova_rete_raggi_x.py
```

It tries ~30 URLs of MAXI, Fermi GBM, Swift/BAT, ATel, GCN and HEASARC with a 40-second timeout and writes
`risultati_prova_rete.txt` plus a `campioni/` folder with the beginning of every answer. Only the Python standard
library is used.

## 1. Try the server on your desktop (optional)

```bash
cd nas
python3 servizio_rx.py --prova                                 # offline self-test, must end with 20/20
python3 servizio_rx.py ~/raggi_x_test --una-volta --max 25     # one real cycle on 25 sources (2-4 min)
python3 servizio_rx.py ~/raggi_x_test --solo-pagina            # open http://localhost:8097, Ctrl+C to stop
```

## 2. Run the server with Docker

1. Create a folder for the data on the server, for example `/volume1/data/Raggi_X`, and inside it a sub-folder
   `programma/` with the six files of `nas/`:
   ```
   Raggi_X/
       programma/
           servizio_rx.py  motore_rx.py  index.html  avvia.sh  requisiti.txt  docker-compose.yml
   ```
2. Edit `docker-compose.yml`:
   * the left side of the `volumes` line must be **your** `Raggi_X` folder
     (the file contains the path used on the author's UGREEN NAS, `/volume1/Ugreen NAS/Raggi_X`);
   * the left side of `ports` (`8097`) must be a free port on your server.
3. Start it — from a terminal on the server:
   ```bash
   cd /path/to/Raggi_X/programma
   docker compose up -d
   ```
   or from your NAS's Docker app ("Project → Create", paste the compose file).
4. Check the log (`docker logs -f raggi-x` or the Docker app). On the first start the container installs `numpy`
   and `matplotlib` into `programma/_python` (2–5 minutes, kept for later restarts), then you should see:
   ```
   Pagina web attiva sulla porta 8000 del container (8097 sul NAS)
   Elenco MAXI: 457 sorgenti; 1 Crab = 3.49 ph/s/cm²
   ...
   Giro MAXI finito: ...           (after 20-30 minutes)
   Giro Fermi GBM finito: ...
   ```
5. Open `http://<server-address>:8097` in any browser on your network. The page fills up while the first cycle
   runs (every 25 sources).

The container runs with `nice 19` / `ionice idle`, one CPU and 1 GB of memory at most, and restarts by itself
(`restart: unless-stopped`).

## 3. Desktop app and notifier (Ubuntu / GNOME)

```bash
sudo apt install python3-numpy python3-matplotlib python3-tk libnotify-bin zenity   # if missing
cd pc
bash installa.sh
```

`installa.sh`:

* asks for the address of the server page (e.g. `http://192.168.1.135:8097`) with a zenity dialog;
* copies the program to `~/.local/share/raggi_x/` and the icon to `~/.local/share/icons/raggi_x.png`;
* creates the command `~/.local/bin/RaggiX` and the launcher **"Raggi X dai buchi neri"** in the menu and on the
  desktop (`raggix.desktop`, `StartupWMClass=Raggix` so that GNOME shows the right icon in the dock);
* starts the **notifier** now and at every login (`~/.config/autostart/raggi_x_avvisatore.desktop`).

Tests:

```bash
RaggiX --autotest                                             # opens and closes a window, must end with TUTTO OK
python3 ~/.local/share/raggi_x/avvisatore_rx.py --prova       # a test notification
python3 ~/.local/share/raggi_x/avvisatore_rx.py --indirizzo   # change the server address
RaggiX --diagnostica                                          # network timings (Fermi GBM with and without IPv4)
```

## 4. Updating

* **Server:** replace the files in `programma/` (usually only `servizio_rx.py` and `motore_rx.py`) and restart the
  container. Libraries are not reinstalled; to force it delete `programma/_python`. Data already downloaded are kept,
  and outbursts already notified are not notified again.
* **Desktop:** run `bash installa.sh` again from the new `pc/` folder.

`motore_rx.py` is the same file in `nas/` and `pc/`: keep the two copies identical.

## Uninstalling the desktop part

```bash
pkill -f raggi_x/avvisatore_rx.py
rm -r ~/.local/share/raggi_x ~/.local/bin/RaggiX ~/.local/share/icons/raggi_x.png \
      ~/.local/share/applications/raggix.desktop ~/.config/autostart/raggi_x_avvisatore.desktop \
      ~/.config/raggi_x.json ~/.config/raggi_x_avvisatore.json ~/.cache/raggi_x
```

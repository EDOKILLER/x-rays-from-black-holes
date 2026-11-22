# How it works

All the science lives in [`motore_rx.py`](../nas/motore_rx.py) (the same file is used by the server and by the
desktop app). Function names are in Italian; the main ones are given below.

## Units: mCrab

MAXI light curves are in photons s⁻¹ cm⁻² (2–20 keV). To make numbers comparable the program converts them into
**mCrab**, thousandths of the Crab Nebula, the standard candle of X-ray astronomy: the MAXI light curve of the Crab
(`J0534+220`) is downloaded first and the median of its last two years is set to 1000 mCrab (`crab_maxi`). With the
data of November 2026 this gives 1 Crab ≈ 3.49 ph s⁻¹ cm⁻².

* Fermi GBM Earth-occultation light curves are already in mCrab.
* Swift/BAT Transient Monitor rates (count cm⁻² s⁻¹, 15–50 keV) are converted with 1 Crab = 0.220 count cm⁻² s⁻¹.

## State of a source (`stato_sorgente`)

For every source, on the daily MAXI light curve (2–20 keV):

* **today** = weighted mean of the last 3 days;
* **usual level** = median of the previous year, excluding the last 14 days;
* **variability** = 1.4826 × median absolute deviation of that year (a robust standard deviation), or the typical
  error bar if larger;
* **z** = (today − usual) / √(variability² + error²), **ratio** = today / usual ("Volte il solito" in the page).

| State (Italian label) | Rule |
|---|---|
| **NUOVA ESPLOSIONE** — new outburst | in outburst (below) and the outburst started ≤ 21 days ago (`giorni_per_dire_nuova`) |
| **in esplosione** — in outburst | z ≥ 5 and ratio ≥ 3 (or the source is normally invisible) |
| **in aumento** — rising | z ≥ 3 and ratio ≥ 1.5 |
| **in calo** — fading | z ≤ −3 and ratio ≤ 0.67 |
| **calma** — usual level | none of the above |
| **troppo debole** — too faint | today < 3σ and the usual level < 3× the typical error |
| **dati vecchi** — old data | no MAXI measurement for more than 14 days (MAXI does not see every source every day) |
| **pochi dati** — few data | fewer than 30 useful days |

### Start and peak of an outburst (`esplosione_in_corso`)

The light curve is binned in 3-day means. Going backwards from the last bin, the outburst continues as long as
the bins are above *usual + 3σ* (taking the error bar into account), allowing gaps in the data up to 10 days.
The first bin of that run is the **start**; the brightest bin is the **peak**. Example (real MAXI data):
H 1743-322 → start 14 Aug 2026, peak ≈ 950 mCrab on 24 Aug 2026.

Only **new** outbursts brighter than `soglia_avviso_mCrab` (default 30 mCrab) are written to `eventi.json`, and only
those produce a desktop notification. Each event has the id `<MAXI-ID>_<MJD of start>`, so an outburst is notified
once.

## Hardness and the hardness–intensity diagram (`serie_durezza`, `disegna`)

Hardness = flux(4–10 keV) / flux(2–4 keV), computed only on days where both bands are detected at ≥ 3σ. The plot
has three panels (English labels):

1. the light curve: MAXI 2–20 keV, plus Fermi GBM 25–50 keV and Swift/BAT 15–50 keV when available;
2. the hardness ratio versus time;
3. the hardness–intensity diagram, coloured by time, with a star on the latest day.

During an outburst a black-hole binary usually starts in the **hard state** (right of the diagram), brightens,
moves to the **soft state** (left, the hot accretion disc dominates) and returns to the hard state while it fades:
the "q" track. Long views are re-binned (7 days for up to 5 years, 14 days for the whole mission).

## Sky map (`disegna_mappa`)

All sources on an Aitoff projection in galactic coordinates (conversion from RA/Dec with the IAU galactic pole, no
external library). Colour = state, size = flux (log). Sources in outburst are labelled.

## Source types (`tipo_da_nome`)

The type shown in the tables comes from a short built-in list of favourites (Cyg X-1, GX 339-4, GRS 1915+105,
MAXI J1820+070, 4U 1630-47, H 1743-322, LMC X-1, LMC X-3, Cyg X-3, Swift J1727.8-1613, Sco X-1, Aql X-1, Her X-1,
Cen X-3, Vela X-1, Mrk 421, Crab); for all other sources it is guessed from the name (galaxy catalogues → active
galaxy, `GX`/`4U`/`XTE`/… → X-ray binary, `MAXI J…`/`Swift J…` → transient X-ray binary, …). It is indicative only.

## News (`leggi_atel`, `leggi_gcn`, `citata_in`)

Every 30 minutes the server reads the ATel RSS feed and the new GCN circulars (JSON, starting from the highest
number on the GCN circulars page, at most 60 per cycle). A news item is linked to a source if a normalised form of
its name (letters and digits only, e.g. `h1743322`) appears in the title or in the first 300 characters. A table of
alternative names (`ALTRI_NOMI`: MXB 1730-335 / Rapid Burster, 4U 1728-34 / Slow Burster, RX J0440.9+4431, HZ Her,
V1333 Aql, …) catches the most common aliases.

## Blended sources

MAXI's point-spread function is wide (≈ 1.5°). MAXI's own source list marks two entries as blended:
`GX 354-0_Slow-Burster with Rapid-Burster` and `H 1730-333_Rapid-Burster with Slow-Burster`. Since version 1.1 the
comment after `_` is removed from the name and shown in the description, and a note (`NOTE_SORGENTI`) explains that a
sudden brightening of GX 354-0 is almost always the Rapid Burster, which goes into outburst every 80–105 days.

## Network behaviour

* Every request has a timeout (30 s, 60–90 s for Fermi GBM).
* Files are cached on disk with their `Last-Modified` date and requested again with `If-Modified-Since`; MAXI is
  refreshed every 6 h, Fermi GBM every 24 h, Swift/BAT once (the mission is over).
* If an archive does not answer, the server **waits and retries the same source** (1, 2, 5, 10, 20, 30 min) instead
  of skipping it; an HTTP error other than 404 is retried once after 1 minute.
* **IPv6:** from the author's network, IPv6 connections to the NASA Marshall servers of Fermi GBM
  (`batse.msfc.nasa.gov`, `gammaray.msfc.nasa.gov`) never succeed, so every request waited 40–90 s before falling
  back to IPv4 (measured: 90.7 s normally, 0.6 s with IPv4 only). For these two hosts the program asks only for
  IPv4 addresses (`solo_ipv4`). `RaggiX --diagnostica` measures both on your network.
* The desktop app first asks the server (`/dati/stato.json`, 3 s timeout); if it is not there it uses the archives
  directly with its own cache in `~/.cache/raggi_x` (MAXI 3 h, GBM 12 h, BAT 30 days).

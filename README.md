# YTS Searcher

Paste in a batch of movie titles (with or without a year) and check whether
each one is on YTS.

```bash
pip install -r requirements.txt
python yts_search.py
```

Paste titles one per line - blank lines between them are fine and are
ignored - then type `done` on its own line (or press Ctrl+D) to finish, e.g.:

```
World Breaker 2025

The Sheep Detectives

Driver's Ed 2025
done
```

For each title:

- **Found** - prompts for which quality you want (built from whatever
  qualities/codecs YTS actually lists for that title, plus an "Any" option),
  and stores its magnet link / torrent URL. If you didn't include a year and
  more than one YTS entry shares that exact title, you'll be asked which
  year you meant before quality selection.
- **Not found on YTS** - falls back to checking
  [dvdsreleasedates.com](https://www.dvdsreleasedates.com/) to report
  **Not out yet** (release date is in the future) or **Not found**
  (nowhere to be found).

At the end you can optionally:

- Download `.torrent` files for the found movies into `torrent_files/`
  (same "stored URL first, `yts.gg` hash fallback" approach `yts_check.py`
  uses).
- Save all results to a text file, one per entry, formatted as:

```
World Breaker (2025)
<>
The Sheep Detectives (2026)
_not out yet_
Driver's Ed (2025)
<>
```

Found titles get the year from the matched YTS entry; "not out yet" titles
get the real release year scraped from dvdsreleasedates.com - neither uses
what you typed. Titles that are nowhere to be found show `- Not Found`
instead of a year.

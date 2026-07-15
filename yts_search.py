import os
import re
import sys
from urllib.parse import quote_plus

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TORRENT_DIR = os.path.join(BASE_DIR, "torrent_files")

YTS_API_URL = "https://movies-api.accel.li/api/v2/list_movies.json"
DVD_SEARCH_URL = "https://www.dvdsreleasedates.com/search/"
DVD_BASE_URL = "https://www.dvdsreleasedates.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Same tracker list yts_check.py uses to build magnet links.
TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://open.demonii.com:1337/announce",
    "https://tracker.moeblog.cn:443/announce",
    "udp://open.dstud.io:6969/announce",
    "udp://tracker.srv00.com:6969/announce",
    "https://tracker.zhuqiy.com:443/announce",
    "https://tracker.pmman.tech:443/announce",
    "udp://tracker.openbittorrent.com:80/announce",
    "udp://tracker.zer0day.to:1337/announce",
    "udp://exodus.desync.com:6969/announce",
]

YEAR_RE = re.compile(r"^(?P<title>.*?)\s*\(?(?P<year>(?:19|20)\d{2})\)?\s*$")


def normalise_title(title):
    return re.sub(r"[^a-z0-9]", "", title.lower())


def parse_query_line(line):
    line = line.strip()
    match = YEAR_RE.match(line)
    if match and match.group("title"):
        return match.group("title").strip(), int(match.group("year"))
    return line, None


def read_titles():
    print("Paste movie titles, one per line (year optional). Blank lines between")
    print("titles are fine and get ignored. Type 'done' on its own line (or press")
    print("Ctrl+D) when finished:\n")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        stripped = line.strip()
        if stripped.lower() == "done":
            break
        if stripped:
            lines.append(line)
    return [parse_query_line(line) for line in lines]


def _titles_match(norm_a, norm_b):
    return bool(norm_a) and norm_a == norm_b


def prompt_year_choice(title, matches):
    matches = sorted(matches, key=lambda m: m.get("year", 0))
    print(f"\nMultiple YTS matches found for '{title}':")
    for i, m in enumerate(matches, start=1):
        print(f"  {i}. {m['title']} ({m['year']})")

    while True:
        choice = input(f"Which year did you mean? [1-{len(matches)}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            return matches[int(choice) - 1]
        print("Invalid choice, try again.")


def find_yts_match(title, year, max_pages=3):
    norm_query = normalise_title(title)
    if not norm_query:
        return None

    # Including the year in query_term (when known) makes YTS's own search
    # ranking surface the exact title much higher - without it, common
    # single-word titles can bury the real match past the first page.
    query_term = f"{title} {year}" if year else title

    matches = []
    seen_ids = set()

    for page in range(1, max_pages + 1):
        try:
            r = requests.get(
                YTS_API_URL,
                params={"query_term": query_term, "limit": 50, "page": page},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            break

        movies = data.get("data", {}).get("movies", []) or []
        if not movies:
            break

        for m in movies:
            if year and m.get("year") != year:
                continue
            if not _titles_match(norm_query, normalise_title(m.get("title", ""))):
                continue
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            matches.append(m)

        # A year was already given, so the first match is unambiguous -
        # no need to keep paging just to look for others.
        if year and matches:
            break
        if len(movies) < 50:
            break

    if not matches:
        return None
    if len(matches) == 1 or year:
        return matches[0]

    return prompt_year_choice(title, matches)


def check_dvd_release_status(title):
    not_found = {"status": "not_found"}
    try:
        r = requests.post(
            DVD_SEARCH_URL,
            data={"searchStr": title},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
    except requests.RequestException:
        return not_found

    # An exact single match redirects straight to the movie's detail page
    # instead of showing a results list.
    if re.match(r"^https://www\.dvdsreleasedates\.com/movies/\d+/", r.url):
        detail_html = r.text
    else:
        # Scope candidate links to the actual results section, not the
        # "Similar DVD Releases" / "Most Requested" sidebar links, which use
        # the same markup elsewhere on the page.
        start = r.text.find("Search results for")
        end = r.text.find("id='rightcolumn'")
        if start == -1:
            return not_found
        results_section = r.text[start: end if end != -1 else len(r.text)]

        norm_query = normalise_title(title)
        candidates = re.findall(
            r"<a style='color:#000;' href='(/movies/\d+/[^']+)'>([^<]+)</a>", results_section
        )
        match_href = None
        for href, candidate_title in candidates:
            if _titles_match(norm_query, normalise_title(candidate_title)):
                match_href = href
                break
        if not match_href:
            return not_found

        try:
            detail = requests.get(DVD_BASE_URL + match_href, headers={"User-Agent": USER_AGENT}, timeout=15)
            detail.raise_for_status()
        except requests.RequestException:
            return not_found
        detail_html = detail.text

    h2 = re.search(r"<h2>(.*?)</h2>", detail_html, re.DOTALL)
    if not h2:
        return not_found
    status = re.search(r"class='(future|past)\s*'", h2.group(1))
    if not (status and status.group(1) == "future"):
        return not_found

    # The year must come from the anchor right next to the movie's own name --
    # the page also links to /new-movies-YYYY/ in the nav bar and a "browse by
    # year" list, which a plain page-wide search would grab instead.
    title_year = re.search(
        r"<span itemprop='name'>(?P<title>.*?)</span>\s*\(<a class='(?:future|past)' href='/new-movies-(?P<year>\d{4})/'>",
        detail_html,
    )
    return {
        "status": "not_out_yet",
        "title": title_year.group("title").strip() if title_year else title,
        "year": int(title_year.group("year")) if title_year else None,
    }


def build_magnet(hash_str, title):
    tr = "&tr=".join(quote_plus(t) for t in TRACKERS)
    return f"magnet:?xt=urn:btih:{hash_str}&dn={quote_plus(title)}&tr={tr}"


def quality_label(torrent):
    codec = (torrent.get("video_codec") or "").strip()
    quality = (torrent.get("quality") or "").strip()
    return f"{codec} {quality}".strip() if codec else quality


def prompt_quality_choice(movie, torrents):
    print(f"\nAvailable qualities for {movie['title']} ({movie['year']}):")
    options = []
    seen = set()
    for t in sorted(torrents, key=lambda t: int(t.get("size_bytes", 0)), reverse=True):
        label = quality_label(t)
        if label in seen:
            continue
        seen.add(label)
        options.append((label, t))

    for i, (label, t) in enumerate(options, start=1):
        print(f"  {i}. {label} ({t.get('size', 'unknown size')})")
    any_index = len(options) + 1
    print(f"  {any_index}. Any / whatever's available")

    while True:
        choice = input(f"Select [1-{any_index}]: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= any_index:
            choice = int(choice)
            break
        print("Invalid choice, try again.")

    if choice == any_index:
        return max(torrents, key=lambda t: int(t.get("size_bytes", 0)))
    return options[choice - 1][1]


def process_title(title, year):
    label = f"{title} ({year})" if year else title
    print(f"\nSearching: {label}")

    if not normalise_title(title):
        print("  -> Not found (no searchable title)")
        return {"query": label, "status": "not_found"}

    movie = find_yts_match(title, year)
    if not movie:
        dvd_result = check_dvd_release_status(title)
        if dvd_result["status"] == "not_out_yet":
            print("  -> Not out yet")
            return {
                "query": label,
                "status": "not_out_yet",
                "title": dvd_result["title"],
                "year": dvd_result["year"],
            }
        print("  -> Not found")
        return {"query": label, "status": "not_found"}

    torrents = movie.get("torrents", []) or []
    if not torrents:
        print("  -> Found on YTS but no torrents listed - treating as not found")
        return {"query": label, "status": "not_found"}

    print(f"  -> Found: {movie['title']} ({movie['year']})")
    chosen = prompt_quality_choice(movie, torrents)
    magnet = build_magnet(chosen["hash"], f"{movie['title']} ({movie['year']})")

    return {
        "query": label,
        "status": "found",
        "title": movie["title"],
        "year": movie["year"],
        "quality": quality_label(chosen),
        "size": chosen.get("size", "unknown"),
        "magnet": magnet,
        "torrent_url": chosen.get("url", ""),
    }


def _safe_filename(title):
    return re.sub(r"[^\w\s\-]", "", title).strip()


def download_torrents(found_results):
    os.makedirs(TORRENT_DIR, exist_ok=True)
    downloaded = skipped = failed = 0

    for item in found_results:
        filename = _safe_filename(f"{item['title']} ({item['year']})") + ".torrent"
        dest = os.path.join(TORRENT_DIR, filename)
        if os.path.exists(dest):
            skipped += 1
            continue

        hash_match = re.search(r"urn:btih:([a-fA-F0-9]{40})", item["magnet"], re.IGNORECASE)
        gg_url = f"https://yts.gg/torrent/download/{hash_match.group(1).upper()}" if hash_match else None
        urls = list(dict.fromkeys(u for u in [item.get("torrent_url"), gg_url] if u))

        for url in urls:
            try:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(r.content)
                downloaded += 1
                break
            except requests.RequestException:
                continue
        else:
            print(f"  failed: {item['title']}")
            failed += 1

    print(f"\nSaved {downloaded} .torrent file(s) to: {TORRENT_DIR}")
    if skipped:
        print(f"Skipped: {skipped} (already downloaded)")
    if failed:
        print(f"Failed:  {failed}")


def save_results_txt(results, path):
    with open(path, "w", encoding="utf-8") as f:
        for item in results:
            if item["status"] == "found":
                f.write(f"{item['title']} ({item['year']})\n<>\n")
            elif item["status"] == "not_out_yet":
                year_part = f" ({item['year']})" if item.get("year") else ""
                f.write(f"{item['title']}{year_part}\n_not out yet_\n")
            else:
                f.write(f"{item['query']} - Not Found\n<>\n")
    print(f"Saved to {path}")


def print_summary(results):
    print("\n" + "=" * 50)
    print("Summary")
    print("=" * 50)
    for r in results:
        if r["status"] == "found":
            print(f"{r['query']}: Found - {r['title']} ({r['year']}) [{r['quality']}]")
        elif r["status"] == "not_out_yet":
            print(f"{r['query']}: Not out yet")
        else:
            print(f"{r['query']}: Not found")


def main():
    print("YTS Searcher")
    print("=" * 50)

    queries = read_titles()
    if not queries:
        print("No titles entered.")
        return

    results = [process_title(title, year) for title, year in queries]
    print_summary(results)

    found_results = [r for r in results if r["status"] == "found"]
    if found_results and input("\nDownload .torrent files for found movies? [y/N]: ").strip().lower() == "y":
        download_torrents(found_results)

    if input("Save results to a text file? [y/N]: ").strip().lower() == "y":
        filename = input("Filename [yts_search_results.txt]: ").strip() or "yts_search_results.txt"
        path = filename if os.path.isabs(filename) else os.path.join(BASE_DIR, filename)
        save_results_txt(results, path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)

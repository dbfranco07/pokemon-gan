"""Scrapes base Pokemon and fan-made fusion sprites from fusiondex.org.

Site structure (confirmed by hand on 2026-06-29):
  - "/" lists all 572 base Pokemon, one page, no pagination.
  - "/<slug>/" is a base Pokemon's page. Its Gallery section lists every
    custom art alternate for that Pokemon. Its Fusions section lists all
    572 possible head-fusions and 572 possible body-fusions; only the ones
    with class "has-custom-dex-entry" have real fan art (the rest are
    procedurally generated placeholders under /dn/auto/, which we skip).
  - "/<head>+<body>/" is a fusion's page. Its Gallery section lists every
    fan-art alternate for that specific fusion. The last entry is always
    the auto-generated fallback (class "sprite-variant-auto") -- skipped.

Scraping runs in two resumable phases:
  Phase A walks every base Pokemon page, downloads its gallery images, and
  queues every (head, body) pair that has custom fusion art.
  Phase B walks the queued fusion pages and downloads their gallery images.

Progress is tracked so Ctrl+C is always safe: re-running the script picks
up where it left off without re-downloading or duplicating CSV rows.
"""

import argparse
import csv
import logging
import os
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

BASE_URL = "https://fusiondex.org"
USER_AGENT = "Mozilla/5.0 (compatible; pokemon-gan-research-scraper/1.0)"

DEFAULT_IMAGES_DIR = "images/fusiondex"
CHECKPOINT_DIR = "checkpoint"
SPRITES_CSV = os.path.join(CHECKPOINT_DIR, "fusiondex_sprites.csv")
FUSION_QUEUE_CSV = os.path.join(CHECKPOINT_DIR, "fusiondex_fusion_queue.csv")
BASE_DONE_FILE = os.path.join(CHECKPOINT_DIR, "fusiondex_base_done.txt")
FUSION_DONE_FILE = os.path.join(CHECKPOINT_DIR, "fusiondex_fusion_done.txt")

CSV_FIELDS = [
    "timestamp", "category", "head", "body", "name",
    "dex_id", "sprite_id", "artists", "image_url", "local_path",
]


def build_session():
    """Returns a requests Session with retries and a polite User-Agent."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=5, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_soup(session, path, delay):
    """Fetches a page relative to BASE_URL and returns parsed soup."""
    url = urljoin(BASE_URL, path)
    response = session.get(url, timeout=30)
    response.raise_for_status()
    time.sleep(delay)
    return BeautifulSoup(response.text, "html.parser")


def load_lines(path):
    """Returns the set of lines in path, or an empty set if it doesn't exist."""
    if not os.path.exists(path):
        return set()
    with open(path, "r") as f:
        return {line.strip() for line in f if line.strip()}


def append_line(path, line):
    """Appends a single line to path, flushing immediately."""
    with open(path, "a") as f:
        f.write(line + "\n")
        f.flush()


def load_written_keys(csv_path):
    """Returns the set of (category, head, body, sprite_id) already in the CSV."""
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, "r", newline="") as f:
        for row in csv.DictReader(f):
            keys.add((row["category"], row["head"], row["body"], row["sprite_id"]))
    return keys


def open_csv_writer(csv_path):
    """Opens csv_path for appending, writing the header if it's new."""
    is_new = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
    f = open(csv_path, "a", newline="")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if is_new:
        writer.writeheader()
        f.flush()
    return f, writer


def parse_page_header(soup):
    """Returns (name, dex_id) from a base Pokemon or fusion detail page."""
    header = soup.select_one("article.dex-entry > header > h2")
    dex_id_span = header.select_one(".dex-id")
    dex_id = dex_id_span.get_text(strip=True).lstrip("#")
    name = header.get_text(strip=True).replace(dex_id_span.get_text(strip=True), "").strip()
    return name, dex_id


def parse_gallery(soup):
    """Returns custom (non-auto) sprite entries from a page's Gallery section.

    Each entry is a dict with sprite_id, artists (list of names), image_url.
    """
    gallery = soup.select_one("section.gallery")
    if gallery is None:
        return []

    entries = []
    for article in gallery.select("article.sprite-preview"):
        if "sprite-variant-auto" in article.get("class", []):
            continue
        img = article.select_one("img")
        sprite_id = article.select_one(".sprite-id").get_text(strip=True).lstrip("#")
        artists = [a.get_text(strip=True) for a in article.select(".artists a")]
        entries.append({
            "sprite_id": sprite_id,
            "artists": artists,
            "image_url": img["src"],
        })
    return entries


def parse_fusion_links(soup):
    """Returns fusion pairs with custom art from a base Pokemon's Fusions section.

    Each entry is a dict with head, body, dex_id, name.
    """
    fusions_section = None
    for section in soup.select("section.dex"):
        h2 = section.select_one("header h2")
        if h2 and h2.get_text(strip=True) == "Fusions":
            fusions_section = section
            break
    if fusions_section is None:
        return []

    pairs = []
    for article in fusions_section.select("article.dex-entry-preview.has-custom-dex-entry"):
        head, body = article.get("data-head"), article.get("data-body")
        if not head or not body:
            continue
        dex_id = article.select_one(".dex-id").get_text(strip=True).lstrip("#")
        name = article.select_one("h3").get_text(strip=True)
        pairs.append({"head": head, "body": body, "dex_id": dex_id, "name": name})
    return pairs


def discover_base_pokemon(session, delay):
    """Returns [{slug, name, dex_id}] for every base Pokemon listed on '/'."""
    soup = get_soup(session, "/", delay)
    pokemon = []
    for article in soup.select("section.dex article.dex-entry-preview"):
        link = article.select_one("h3 a")
        slug = link["href"].strip("/")
        name = link.get_text(strip=True)
        dex_id = article.select_one(".dex-id").get_text(strip=True).lstrip("#")
        pokemon.append({"slug": slug, "name": name, "dex_id": dex_id})
    return pokemon


def download_image(session, url, dest_path):
    """Downloads url to dest_path unless it's already there."""
    if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
        return
    response = session.get(url, timeout=30)
    response.raise_for_status()
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as f:
        f.write(response.content)


def save_gallery_entries(session, writer, csv_file, written_keys,
                          category, head, body, name, dex_id, entries, image_dir):
    """Downloads and records every gallery entry not already in written_keys."""
    count = 0
    for entry in entries:
        key = (category, head, body, entry["sprite_id"])
        if key in written_keys:
            continue
        local_path = os.path.join(image_dir, f"{entry['sprite_id']}.png")
        download_image(session, entry["image_url"], local_path)
        writer.writerow({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "category": category,
            "head": head,
            "body": body,
            "name": name,
            "dex_id": dex_id,
            "sprite_id": entry["sprite_id"],
            "artists": ";".join(entry["artists"]),
            "image_url": entry["image_url"],
            "local_path": local_path,
        })
        csv_file.flush()
        written_keys.add(key)
        count += 1
    return count


def run_phase_base(session, delay, writer, csv_file, written_keys, limit, images_dir):
    """Walks base Pokemon pages: downloads their gallery, queues fusion pairs."""
    base_done = load_lines(BASE_DONE_FILE)
    queue_is_new = not os.path.exists(FUSION_QUEUE_CSV) or os.path.getsize(FUSION_QUEUE_CSV) == 0

    queued_pairs = set()
    if not queue_is_new:
        with open(FUSION_QUEUE_CSV, "r", newline="") as f:
            for row in csv.DictReader(f):
                queued_pairs.add((row["head"], row["body"]))

    pokemon_list = discover_base_pokemon(session, delay)
    remaining = [p for p in pokemon_list if p["slug"] not in base_done]
    if limit is not None:
        remaining = remaining[:limit]
    logging.info(f"Phase A: {len(remaining)} / {len(pokemon_list)} base Pokemon left to scrape")

    queue_file = open(FUSION_QUEUE_CSV, "a", newline="")
    queue_writer = csv.DictWriter(queue_file, fieldnames=["head", "body", "dex_id", "name"])
    if queue_is_new:
        queue_writer.writeheader()
        queue_file.flush()

    try:
        for pokemon in remaining:
            slug = pokemon["slug"]
            soup = get_soup(session, f"/{slug}/", delay)
            name, dex_id = parse_page_header(soup)

            entries = parse_gallery(soup)
            image_dir = os.path.join(images_dir, "base", slug)
            n_images = save_gallery_entries(
                session, writer, csv_file, written_keys,
                "base", slug, slug, name, dex_id, entries, image_dir,
            )

            pairs = parse_fusion_links(soup)
            n_new_pairs = 0
            for pair in pairs:
                key = (pair["head"], pair["body"])
                if key in queued_pairs:
                    continue
                queue_writer.writerow(pair)
                queue_file.flush()
                queued_pairs.add(key)
                n_new_pairs += 1

            append_line(BASE_DONE_FILE, slug)
            logging.info(
                f"[base] {name} ({slug}): {n_images} sprite(s) downloaded, "
                f"{n_new_pairs} new fusion pair(s) queued"
            )
    finally:
        queue_file.close()


def run_phase_fusions(session, delay, writer, csv_file, written_keys, limit, images_dir):
    """Walks queued fusion pages and downloads their gallery images."""
    if not os.path.exists(FUSION_QUEUE_CSV):
        logging.info("Phase B: no fusion queue found yet, run phase A first")
        return

    fusion_done = load_lines(FUSION_DONE_FILE)
    with open(FUSION_QUEUE_CSV, "r", newline="") as f:
        all_pairs = list(csv.DictReader(f))

    remaining = [p for p in all_pairs if f"{p['head']}+{p['body']}" not in fusion_done]
    if limit is not None:
        remaining = remaining[:limit]
    logging.info(f"Phase B: {len(remaining)} / {len(all_pairs)} fusions left to scrape")

    for pair in remaining:
        head, body = pair["head"], pair["body"]
        soup = get_soup(session, f"/{head}+{body}/", delay)
        name, dex_id = parse_page_header(soup)

        entries = parse_gallery(soup)
        image_dir = os.path.join(images_dir, "fusions", f"{head}+{body}")
        n_images = save_gallery_entries(
            session, writer, csv_file, written_keys,
            "fusion", head, body, name, dex_id, entries, image_dir,
        )

        append_line(FUSION_DONE_FILE, f"{head}+{body}")
        logging.info(f"[fusion] {name} ({head}+{body}): {n_images} sprite(s) downloaded")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images-dir", type=str, default=DEFAULT_IMAGES_DIR,
                         help=f"Directory to save downloaded images to (default: {DEFAULT_IMAGES_DIR})")
    parser.add_argument("--delay", type=float, default=0.5,
                         help="Seconds to sleep between HTTP requests (default: 0.5)")
    parser.add_argument("--max-base", type=int, default=None,
                         help="Limit how many base Pokemon pages to process this run")
    parser.add_argument("--max-fusions", type=int, default=None,
                         help="Limit how many fusion pages to process this run")
    parser.add_argument("--skip-base", action="store_true",
                         help="Skip phase A (base Pokemon + queueing fusions)")
    parser.add_argument("--skip-fusions", action="store_true",
                         help="Skip phase B (scraping queued fusion pages)")
    args = parser.parse_args()

    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    os.makedirs(args.images_dir, exist_ok=True)

    session = build_session()
    written_keys = load_written_keys(SPRITES_CSV)
    csv_file, writer = open_csv_writer(SPRITES_CSV)

    try:
        if not args.skip_base:
            run_phase_base(session, args.delay, writer, csv_file, written_keys,
                            args.max_base, args.images_dir)
        if not args.skip_fusions:
            run_phase_fusions(session, args.delay, writer, csv_file, written_keys,
                               args.max_fusions, args.images_dir)
        logging.info("Done.")
    except KeyboardInterrupt:
        logging.warning("Interrupted by user. Progress is saved -- rerun the script to continue.")
    finally:
        csv_file.close()


if __name__ == "__main__":
    main()

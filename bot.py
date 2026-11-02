"""
Bot d'alertes Mercari Japan + Yahoo Auctions Japan -> Discord webhook.
Configuration : refs.json, photos dans refs/, variable DISCORD_WEBHOOK.
IMPORTANT : les sélecteurs HTML des sites peuvent changer et les sites peuvent limiter
les requêtes. Ce code ne garantit pas que les sites livreront toujours leurs annonces.
"""
import io
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from sentence_transformers import SentenceTransformer, util

BASE_DIR = Path(__file__).resolve().parent
REFS_FILE = BASE_DIR / "refs.json"
SEEN_FILE = BASE_DIR / "seen.json"

WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()
THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.82"))
EVERY = max(60, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))
REQUEST_TIMEOUT = 25
MAX_DISCORD_CONTENT = 1800

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("listing-alerts")

session = requests.Session()
session.headers.update(UA)
retry = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset(["GET", "POST"]),
    respect_retry_after_header=True,
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)
session.mount("http://", adapter)


def load_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Impossible de lire %s : %s", path.name, exc)
        return default


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def send(text, photo_url=None):
    if not WEBHOOK:
        log.error("DISCORD_WEBHOOK absent. Configure cette variable avant de lancer le bot.")
        return False

    payload = {"content": text[:MAX_DISCORD_CONTENT]}
    if photo_url and str(photo_url).startswith(("https://", "http://")):
        payload["embeds"] = [{"image": {"url": photo_url}}]

    try:
        response = session.post(WEBHOOK, json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code not in (200, 204):
            log.error("Discord a refusé le message (HTTP %s): %s",
                      response.status_code, response.text[:300])
            return False
        return True
    except requests.RequestException as exc:
        log.error("Erreur d'envoi Discord : %s", exc)
        return False


def embed_image_bytes(raw):
    with Image.open(io.BytesIO(raw)) as image:
        image = image.convert("RGB")
        return model.encode(image, convert_to_tensor=True)


def fetch_image(url):
    if not url:
        raise ValueError("URL d'image absente")
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    if not response.headers.get("content-type", "").lower().startswith("image/"):
        raise ValueError("La réponse reçue n'est pas une image")
    return response.content


def yahoo(keyword, max_price):
    url = (
        "https://auctions.yahoo.co.jp/search/search"
        f"?p={quote(keyword)}&s1=new&o1=d&aucmaxprice={int(max_price)}"
    )
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    out = []

    # Les sélecteurs peuvent évoluer avec le site : si la page change, vérifier ici.
    for li in soup.select("li.Product"):
        a = li.select_one("h3.Product__title a")
        img = li.select_one("img.Product__imageData")
        price_el = li.select_one(".Product__priceValue")
        if not a or not a.get("href"):
            continue
        image_url = (img.get("src") or img.get("data-src")) if img else None
        out.append({
            "source": "Yahoo Auctions Japan",
            "url": urljoin("https://auctions.yahoo.co.jp", a["href"]),
            "img": urljoin("https://auctions.yahoo.co.jp", image_url) if image_url else None,
            "title": a.get_text(" ", strip=True),
            "price": price_el.get_text(" ", strip=True) if price_el else "Prix non lu",
        })
    return out


def mercari(page, keyword, max_price):
    url = (
        "https://jp.mercari.com/search"
        f"?keyword={quote(keyword)}&sort=created_time&order=desc"
        f"&price_max={int(max_price)}&status=on_sale"
    )
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_selector('li[data-testid="item-cell"]', timeout=12000)
    except PlaywrightTimeoutError:
        log.warning("Aucune carte Mercari détectée. Le site a peut-être changé ou bloqué la page.")
        return []

    out = []
    for li in page.query_selector_all('li[data-testid="item-cell"]'):
        a = li.query_selector("a")
        img = li.query_selector("img")
        if not a:
            continue
        href = a.get_attribute("href")
        image_url = None
        if img:
            image_url = img.get_attribute("src") or img.get_attribute("data-src")
        title = (img.get_attribute("alt") or "").strip() if img else ""
        # Ne pas supposer que la première ligne du texte est le prix.
        text = li.inner_text() or ""
        price = next((line.strip() for line in text.splitlines()
                      if "¥" in line or "円" in line), "Prix non lu")
        out.append({
            "source": "Mercari Japan",
            "url": urljoin("https://jp.mercari.com", href),
            "img": urljoin("https://jp.mercari.com", image_url) if image_url else None,
            "title": title or "Titre non lu",
            "price": price,
        })
    return out


def main():
    if not WEBHOOK:
        raise SystemExit(
            "DISCORD_WEBHOOK n'est pas défini. Configure le webhook Discord dans les variables d'environnement."
        )
    refs = load_json(REFS_FILE, [])
    if not isinstance(refs, list) or not refs:
        raise SystemExit("refs.json est vide ou invalide.")

    seen = set(load_json(SEEN_FILE, []))
    log.info("Chargement du modèle CLIP (premier lancement : téléchargement possible)...")
    global model
    model = SentenceTransformer("clip-ViT-B-32")
    embeddings = {}

    if os.getenv("SEND_STARTUP_MESSAGE", "0") == "1":
        if not send("🟢 Bot démarré : surveillance Mercari Japan + Yahoo Auctions Japan."):
            log.warning("Le message de démarrage n'a pas pu être envoyé.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA["User-Agent"], locale="ja-JP")
        try:
            while True:
                started = time.time()
                for ref in refs:
                    try:
                        file_path = (BASE_DIR / ref["file"]).resolve()
                        if not file_path.is_relative_to(BASE_DIR.resolve()):
                            log.warning("Chemin de référence refusé : %s", ref.get("file"))
                            continue
                        if not file_path.is_file():
                            log.warning("Photo de référence introuvable : %s", file_path)
                            continue
                        if file_path not in embeddings:
                            embeddings[file_path] = embed_image_bytes(file_path.read_bytes())

                        keyword = str(ref["keyword"])
                        max_price = int(ref["max_price"])
                        listings = []
                        for scraper in (
                            lambda: yahoo(keyword, max_price),
                            lambda: mercari(page, keyword, max_price),
                        ):
                            try:
                                listings.extend(scraper())
                            except Exception as exc:
                                log.warning("Échec d'une recherche pour %r : %s", keyword, exc)

                        for item in listings:
                            url = item.get("url")
                            image_url = item.get("img")
                            if not url or url in seen or not image_url:
                                continue
                            try:
                                raw = fetch_image(image_url)
                                score = float(util.cos_sim(
                                    embeddings[file_path], embed_image_bytes(raw)
                                ).item())
                            except (requests.RequestException, ValueError, UnidentifiedImageError, OSError) as exc:
                                log.debug("Image non analysée (%s) : %s", url, exc)
                                continue
                            except Exception as exc:
                                log.debug("Erreur de comparaison (%s) : %s", url, exc)
                                continue

                            # Marquer comme traitée après analyse réussie, même si elle ne matche pas,
                            # afin d'éviter de recalculer la même annonce à chaque scan.
                            seen.add(url)
                            if score >= THRESHOLD:
                                message = (
                                    f"**{item.get('source', 'Annonce')}**\n"
                                    f"{item.get('title', 'Titre non lu')}\n"
                                    f"Prix : {item.get('price', 'Non lu')}\n"
                                    f"Similarité image : {score:.0%}\n{url}"
                                )
                                send(message, image_url)
                            time.sleep(0.4)
                        save_json(SEEN_FILE, sorted(seen))
                    except Exception:
                        log.exception("Erreur pendant le traitement de la référence %s", ref)

                elapsed = time.time() - started
                log.info("Scan terminé en %.1f s.", elapsed)
                if os.getenv("RUN_ONCE", "0") == "1":
                    log.info("Mode GitHub Actions : un seul scan, arrêt du programme.")
                    break
                log.info("Pause de %d s avant le prochain scan.", EVERY)
                time.sleep(max(5, EVERY - elapsed))
        finally:
            browser.close()


if __name__ == "__main__":
    main()

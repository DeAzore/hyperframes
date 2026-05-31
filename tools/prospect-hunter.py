"""
Prospect Hunter - Sud de France
--------------------------------
Trouve des business avec des sites web moches/vieux/non-mobile
pour leur proposer : site mobile-first + dashboard + outils automatisés

Usage:
    pip install scrapling httpx
    python prospect-hunter.py

Le résultat est exporté dans prospects.csv
"""

import asyncio
import csv
import re
import time
from datetime import datetime
from urllib.parse import quote

import httpx

try:
    from scrapling.fetchers import AsyncFetcher
    SCRAPLING_AVAILABLE = True
except ImportError:
    SCRAPLING_AVAILABLE = False
    print("⚠️  Scrapling non trouvé — pip install scrapling")


# ── Secteurs ciblés (business avec de l'argent, sites souvent vieux) ──────────

SECTEURS = {
    "8622C": "Médecine esthétique / laser",
    "9602B": "Institut de beauté / spa",
    "8623Z": "Dentiste / orthodontiste",
    "8690A": "Kiné / ostéo",
    "5510Z": "Hôtel indépendant",
    "7111Z": "Architecte",
    "6910Z": "Avocat / notaire",
    "4775Z": "Opticien",
    "5610A": "Restaurant gastronomique",
    "6820B": "Immobilier prestige",
}

# ── Départements Sud de France ─────────────────────────────────────────────────

DEPARTEMENTS = [
    "06",  # Alpes-Maritimes (Nice)
    "13",  # Bouches-du-Rhône (Marseille, Aix)
    "83",  # Var (Toulon, Saint-Tropez)
    "84",  # Vaucluse (Avignon)
    "34",  # Hérault (Montpellier)
    "30",  # Gard (Nîmes)
    "31",  # Haute-Garonne (Toulouse)
    "66",  # Pyrénées-Orientales (Perpignan)
    "11",  # Aude (Carcassonne)
    "64",  # Pyrénées-Atlantiques (Biarritz, Pau)
    "40",  # Landes (Hossegor, Dax)
]

MAX_PAR_SECTEUR = 50  # entreprises récupérées par secteur/département


# ── 1. Récupération des entreprises via l'API gouvernementale ─────────────────

async def get_entreprises(client: httpx.AsyncClient, code_naf: str, departement: str) -> list[dict]:
    """Cherche des entreprises actives par secteur et département."""
    url = "https://recherche-entreprises.api.gouv.fr/search"
    params = {
        "activite_principale": code_naf,
        "departement": departement,
        "etat_administratif": "A",  # actives uniquement
        "per_page": MAX_PAR_SECTEUR,
        "page": 1,
    }
    try:
        r = await client.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("results", [])
    except Exception as e:
        print(f"  ⚠️  Erreur API pour {code_naf}/{departement}: {e}")
        return []


def extraire_infos(entreprise: dict) -> dict:
    """Extrait les champs utiles d'une entrée SIRENE."""
    siege = entreprise.get("siege", {})
    return {
        "nom": entreprise.get("nom_complet", ""),
        "siret": siege.get("siret", ""),
        "adresse": siege.get("adresse", ""),
        "ville": siege.get("commune", ""),
        "code_postal": siege.get("code_postal", ""),
        "site_web": siege.get("url", "") or entreprise.get("url", "") or "",
        "telephone": siege.get("telephone", ""),
        "secteur": SECTEURS.get(entreprise.get("activite_principale", ""), ""),
        "date_creation": entreprise.get("date_creation", ""),
        "tranche_effectif": entreprise.get("tranche_effectif_salarie", ""),
    }


# ── 2. Recherche du site web si absent dans SIRENE ───────────────────────────

async def chercher_site_web(client: httpx.AsyncClient, nom: str, ville: str) -> str:
    """Tente de trouver le site web via DuckDuckGo (pas de clé API requise)."""
    query = quote(f"{nom} {ville} site officiel")
    url = f"https://html.duckduckgo.com/html/?q={query}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ProspectHunter/1.0)"}
    try:
        r = await client.get(url, headers=headers, timeout=10, follow_redirects=True)
        # Cherche le premier résultat qui ressemble à un vrai site
        urls = re.findall(r'href="(https?://(?!duckduckgo)[^"]+)"', r.text)
        for u in urls[:5]:
            if not any(x in u for x in ["facebook", "linkedin", "instagram", "pagesjaunes", "societe.com", "sirene"]):
                return u
    except Exception:
        pass
    return ""


# ── 3. Analyse du site avec Scrapling ────────────────────────────────────────

async def analyser_site(url: str) -> dict:
    """
    Visite le site et calcule un score de vétusté (0-100).
    Plus le score est élevé, plus le site est mauvais → prospect chaud.
    """
    resultat = {
        "url_analysee": url,
        "accessible": False,
        "ssl": url.startswith("https"),
        "mobile_friendly": False,
        "annee_copyright": None,
        "vieux_jquery": False,
        "mise_en_page_tables": False,
        "doctype_vieux": False,
        "score_vetuste": 0,
        "verdict": "",
    }

    if not url:
        resultat["verdict"] = "Pas de site trouvé"
        resultat["score_vetuste"] = 100  # prospect maximal
        return resultat

    if not url.startswith("http"):
        url = "https://" + url

    try:
        fetcher = AsyncFetcher(auto_match=False)
        page = await fetcher.get(url, stealthy_headers=True, timeout=15)
        html = str(page.html_content) if hasattr(page, "html_content") else str(page)
        resultat["accessible"] = True
    except Exception as e:
        resultat["verdict"] = f"Site inaccessible ({e})"
        resultat["score_vetuste"] = 60
        return resultat

    score = 0

    # SSL
    if not url.startswith("https"):
        score += 25

    # Viewport meta (mobile-first)
    if 'name="viewport"' not in html and "name='viewport'" not in html:
        resultat["mobile_friendly"] = False
        score += 30
    else:
        resultat["mobile_friendly"] = True

    # Vieux jQuery
    jquery_match = re.search(r'jquery[.-](\d+)\.(\d+)', html, re.IGNORECASE)
    if jquery_match:
        major = int(jquery_match.group(1))
        if major < 3:
            resultat["vieux_jquery"] = True
            score += 15

    # Mise en page avec des tables (signe des années 2000)
    tables = len(re.findall(r'<table', html, re.IGNORECASE))
    if tables > 5:
        resultat["mise_en_page_tables"] = True
        score += 20

    # Doctype HTML4 ou XHTML
    if re.search(r'<!DOCTYPE html PUBLIC', html, re.IGNORECASE):
        resultat["doctype_vieux"] = True
        score += 10

    # Année dans le copyright
    copyright_match = re.search(r'©\s*(\d{4})|copyright\s+(\d{4})', html, re.IGNORECASE)
    if copyright_match:
        annee = int(copyright_match.group(1) or copyright_match.group(2))
        resultat["annee_copyright"] = annee
        if annee < 2019:
            score += 20
        elif annee < 2022:
            score += 10

    resultat["score_vetuste"] = min(score, 100)

    if score >= 60:
        resultat["verdict"] = "🔥 Prospect chaud — site très vétuste"
    elif score >= 35:
        resultat["verdict"] = "⚡ Prospect tiède — peut mieux faire"
    else:
        resultat["verdict"] = "✅ Site correct — passer"

    return resultat


# ── 4. Pipeline principal ─────────────────────────────────────────────────────

async def main():
    if not SCRAPLING_AVAILABLE:
        print("❌ Installe Scrapling d'abord : pip install scrapling")
        return

    print("🚀 Prospect Hunter — Sud de France")
    print(f"   {len(SECTEURS)} secteurs × {len(DEPARTEMENTS)} départements\n")

    resultats = []

    async with httpx.AsyncClient() as client:
        for code_naf, label_secteur in SECTEURS.items():
            for dept in DEPARTEMENTS:
                print(f"📍 {label_secteur} — dép. {dept}...")
                entreprises = await get_entreprises(client, code_naf, dept)

                for e in entreprises:
                    infos = extraire_infos(e)

                    # Trouver le site web
                    if not infos["site_web"] and infos["nom"]:
                        infos["site_web"] = await chercher_site_web(
                            client, infos["nom"], infos["ville"]
                        )
                        await asyncio.sleep(1)  # respecter les rate limits

                    # Analyser le site
                    print(f"   🔍 {infos['nom']} — {infos['site_web'] or 'pas de site'}")
                    analyse = await analyser_site(infos["site_web"])
                    await asyncio.sleep(0.5)

                    resultats.append({**infos, **analyse})

    # ── Export CSV ────────────────────────────────────────────────────────────
    if not resultats:
        print("Aucun résultat trouvé.")
        return

    # Trier par score décroissant (prospects les plus chauds en premier)
    resultats.sort(key=lambda x: x["score_vetuste"], reverse=True)

    fichier = f"prospects_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    champs = [
        "verdict", "score_vetuste", "nom", "secteur", "ville", "code_postal",
        "adresse", "telephone", "site_web", "url_analysee",
        "ssl", "mobile_friendly", "annee_copyright", "vieux_jquery",
        "mise_en_page_tables", "doctype_vieux", "date_creation",
        "tranche_effectif", "siret",
    ]

    with open(fichier, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=champs, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(resultats)

    chauds = sum(1 for r in resultats if r["score_vetuste"] >= 60)
    print(f"\n✅ {len(resultats)} entreprises analysées")
    print(f"🔥 {chauds} prospects chauds")
    print(f"📄 Export : {fichier}")


if __name__ == "__main__":
    asyncio.run(main())

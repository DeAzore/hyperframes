#!/usr/bin/env python3
"""
prospect-finder.py — Trouve des TPE/PME dans les Bouches-du-Rhône avec des sites
web à refaire, en utilisant l'API SIRENE (data.gouv.fr) + Scrapling.

Usage:
  python3 scripts/prospect-finder.py
  python3 scripts/prospect-finder.py --naf 8690D --ville Marseille --limit 30
  python3 scripts/prospect-finder.py --naf 9602B --output prospects.json

Codes NAF utiles:
  8690D  Autres activités de santé humaine (centres laser, bien-être)
  9602B  Soins de beauté (instituts, spas)
  9604Z  Entretien corporel (salles de sport, sauna)
  4322B  Installation de chauffage, climatisation (artisans)
  4321A  Travaux d'installation électrique (électriciens)
  4322A  Travaux d'installation d'eau et de gaz (plombiers)
  5610A  Restauration traditionnelle
  5621Z  Services des traiteurs
  8621Z  Activité des médecins généralistes
  8623Z  Activité des chirurgiens-dentistes
  8690A  Ambulances
  7021Z  Conseil en relations publiques
  4120A  Construction de maisons individuelles
"""

import argparse
import json
import time
import sys
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError

# ── Config ───────────────────────────────────────────────────────────────────

SIRENE_API = "https://recherche-entreprises.api.gouv.fr/search"
DEPARTEMENT = "13"  # Bouches-du-Rhône

SCORE_WEIGHTS = {
    "has_website": 30,       # a-t-il un site ?
    "https": 10,             # HTTPS ?
    "not_403": 10,           # site accessible ?
    "has_h1": 10,            # structure HTML de base
    "has_meta_desc": 10,     # SEO minimal
    "has_phone_on_site": 5,  # téléphone visible
    "has_cta": 5,            # bouton d'action
    "mobile_viewport": 5,    # responsive
    "has_social": 5,         # réseaux sociaux
    "modern_look": 10,       # pas de tables/frames/flash
}
MAX_SCORE = sum(SCORE_WEIGHTS.values())  # 100

# ── SIRENE Search ─────────────────────────────────────────────────────────────

def search_sirene(naf_code: str, ville: str | None, limit: int) -> list[dict]:
    """Cherche des entreprises via l'API recherche-entreprises.api.gouv.fr."""
    params = {
        "code_naf": naf_code,
        "code_departement": DEPARTEMENT,
        "per_page": min(limit, 25),
        "page": 1,
    }
    if ville:
        params["q"] = ville

    url = f"{SIRENE_API}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "prospect-finder/1.0 (contact@deazore.fr)"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("results", [])
    except URLError as e:
        print(f"⚠  SIRENE API error: {e}", file=sys.stderr)
        return []


def extract_website(company: dict) -> str | None:
    """Extrait l'URL du site web depuis un résultat SIRENE."""
    # L'API retourne les liens dans `complements` ou `siege`
    siege = company.get("siege", {})
    for key in ("site_internet", "url", "website"):
        val = siege.get(key) or company.get(key)
        if val:
            url = val.strip()
            if not url.startswith("http"):
                url = "https://" + url
            return url
    return None


# ── Website Scoring with Scrapling ───────────────────────────────────────────

def score_website(url: str) -> tuple[int, dict[str, bool], str]:
    """
    Tente de fetcher le site avec Scrapling et retourne (score, détails, erreur).
    Score de 0 (site nul) à 100 (site excellent).
    """
    details: dict[str, bool] = {k: False for k in SCORE_WEIGHTS}
    error = ""

    # Un site existe = déjà +30
    details["has_website"] = True

    if url.startswith("https://"):
        details["https"] = True

    try:
        from scrapling.fetchers import Fetcher
        page = Fetcher.get(url, stealthy_headers=True, timeout=15)
    except Exception as e:
        error = f"fetch error: {e}"
        score = sum(SCORE_WEIGHTS[k] for k, v in details.items() if v)
        return score, details, error

    # Site accessible
    details["not_403"] = True

    try:
        # H1 présent
        h1 = page.css("h1::text").get()
        details["has_h1"] = bool(h1 and h1.strip())

        # Meta description
        meta = page.css('meta[name="description"]::attr(content)').get()
        details["has_meta_desc"] = bool(meta and len(meta.strip()) > 20)

        # Téléphone visible
        body_text = " ".join(page.css("body ::text").getall())
        import re
        details["has_phone_on_site"] = bool(
            re.search(r"0[1-9][\s.\-]?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}", body_text)
        )

        # CTA (bouton ou lien d'action)
        cta_keywords = ["rendez-vous", "devis", "contact", "réserver", "appeler", "prendre rdv"]
        all_links = " ".join(page.css("a::text, button::text").getall()).lower()
        details["has_cta"] = any(kw in all_links for kw in cta_keywords)

        # Viewport meta = responsive
        viewport = page.css('meta[name="viewport"]').get()
        details["mobile_viewport"] = bool(viewport)

        # Réseaux sociaux
        all_hrefs = " ".join(page.css("a::attr(href)").getall()).lower()
        details["has_social"] = any(
            s in all_hrefs for s in ["facebook.com", "instagram.com", "linkedin.com", "tiktok.com"]
        )

        # Look moderne : pas de <table> pour la mise en page, pas de <frameset>
        html_str = page.css("html").get() or ""
        tables = len(page.css("table"))
        frames = len(page.css("frameset, frame"))
        details["modern_look"] = tables < 5 and frames == 0

    except Exception as e:
        error = f"parse error: {e}"

    score = sum(SCORE_WEIGHTS[k] for k, v in details.items() if v)
    return score, details, error


# ── Report ────────────────────────────────────────────────────────────────────

def opportunity_label(score: int) -> str:
    if score <= 30:
        return "🔴 CIBLE PRIORITAIRE — site catastrophique"
    if score <= 50:
        return "🟠 BONNE CIBLE — site faible, refonte facile à vendre"
    if score <= 70:
        return "🟡 CIBLE MOYENNE — améliorations ciblées"
    return "🟢 Site correct — pas prioritaire"


def format_report(prospects: list[dict]) -> str:
    lines = [
        "=" * 70,
        "RAPPORT PROSPECTS — Bouches-du-Rhône",
        "=" * 70,
        "",
    ]
    sorted_prospects = sorted(prospects, key=lambda p: p["score"])
    for p in sorted_prospects:
        lines += [
            f"{'─' * 60}",
            f"  {p['name']}",
            f"  📍 {p.get('city', '?')} | 📞 {p.get('phone', 'N/A')}",
            f"  🌐 {p.get('website', 'AUCUN SITE')}",
            f"  Score : {p['score']}/{MAX_SCORE}  →  {opportunity_label(p['score'])}",
        ]
        if p.get("error"):
            lines.append(f"  ⚠  {p['error']}")
        details = p.get("details", {})
        missing = [k for k, v in details.items() if not v]
        if missing:
            lines.append(f"  Manque : {', '.join(missing)}")
        lines.append("")
    lines += ["=" * 70, f"Total : {len(prospects)} entreprises analysées", ""]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Trouve des prospects avec mauvais site web dans le 13")
    parser.add_argument("--naf", default="8690D", help="Code NAF (défaut: 8690D)")
    parser.add_argument("--ville", default=None, help="Filtrer par ville (ex: Marseille)")
    parser.add_argument("--limit", type=int, default=20, help="Nombre max d'entreprises (défaut: 20)")
    parser.add_argument("--output", default=None, help="Fichier JSON de sortie")
    parser.add_argument("--score-max", type=int, default=60,
                        help="N'afficher que les sites avec score <= N (défaut: 60)")
    args = parser.parse_args()

    print(f"\n🔍 Recherche SIRENE : NAF={args.naf}, dép.13, ville={args.ville or 'toutes'}")
    companies = search_sirene(args.naf, args.ville, args.limit)
    print(f"   {len(companies)} entreprises trouvées\n")

    prospects = []
    for i, company in enumerate(companies, 1):
        name = company.get("nom_complet") or company.get("nom_raison_sociale", "?")
        siege = company.get("siege", {})
        city = siege.get("libelle_commune", "")
        phone = siege.get("telephone", "")
        website = extract_website(company)

        print(f"[{i}/{len(companies)}] {name} ({city})", end="")

        if not website:
            print(" → pas de site")
            score, details, error = 0, {k: False for k in SCORE_WEIGHTS}, "no website"
        else:
            print(f" → {website}", end="", flush=True)
            score, details, error = score_website(website)
            print(f" → {score}/{MAX_SCORE}")
            time.sleep(0.5)  # soyons polis

        prospects.append({
            "name": name,
            "city": city,
            "phone": phone,
            "website": website,
            "score": score,
            "details": details,
            "error": error,
            "siren": company.get("siren", ""),
        })

    # Filtrer par score
    targets = [p for p in prospects if p["score"] <= args.score_max]
    print(f"\n{format_report(targets)}")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(prospects, f, ensure_ascii=False, indent=2)
        print(f"💾 Données complètes sauvegardées dans {args.output}")


if __name__ == "__main__":
    main()

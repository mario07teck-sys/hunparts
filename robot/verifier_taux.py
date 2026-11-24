"""Robot des taux URSSAF de Hunparts.

Deux fois par mois, GitHub lance ce programme. Il demande au service de calcul de l'URSSAF (mon-entreprise.urssaf.fr)
ce qu'un micro-entrepreneur paie aujourd'hui, en déduit les taux, et les compare à taux_urssaf.json : le fichier que
chaque Hunparts relit une fois par mois.

  - mêmes taux : il note seulement la date du contrôle, une fois par mois ;
  - taux changés et lecture sûre : il corrige le fichier et l'annonce ;
  - lecture impossible ou douteuse : il ne touche à rien et prévient.

Il ne devine jamais : une réponse incomplète, un montant nul, un taux hors bornes, un saut trop grand ou deux lectures
qui ne se recoupent pas valent « lecture douteuse ». La part ACRE est seulement surveillée : si elle change, il prévient
sans rien écrire, parce que Hunparts l'applique selon la date de création de l'entreprise et doit être adapté d'abord.
Usage : python3 robot/verifier_taux.py [--essai]     (--essai : compare et raconte, sans rien écrire)
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import pathlib
import sys
import time
import traceback
import urllib.error
import urllib.request

URL = "https://mon-entreprise.urssaf.fr/api/v1/evaluate"
FICHIER = pathlib.Path(__file__).resolve().parent.parent / "taux_urssaf.json"
AE = "dirigeant . auto-entrepreneur . "
CC = AE + "cotisations et contributions . "
# quatre montants à payer, puis les deux taux que le service affiche lui-même : ils doivent raconter la même chose
MONTANTS = [CC + "cotisations", CC + "CFP", CC + "TFC", AE + "impôt . versement libératoire . montant"]
AFFICHES = [CC + "cotisations . vente restauration hébergement . taux", CC + "cotisations . service BIC . taux"]
ACRE = [AE + "Acre . taux Acre"]
# chiffre d'affaires de calcul : 1 000 000 € par mois, pour qu'un taux de 0,015 % donne encore un nombre entier d'euros
CA_AN = 12_000_000
PAUSE = 20           # secondes entre deux questions : le service refuse les demandes trop rapprochées (réponse 429)
ECRITS = ("vente", "service", "chambres", "vfl")       # ce que le robot a le droit de corriger
# par famille de taux : (minimum, maximum, plus grand écart cru sans relecture humaine)
BORNES = {"cotisations": (0.5, 59.99, 5), "chambres": (0.01, 4.99, 1), "vfl": (0.01, 9.99, 2), "acre": (0.05, 1, 0)}


class Illisible(Exception):
    """Le service de l'URSSAF n'a pas donné une réponse sûre : on ne touche à rien."""


def demander(expressions: list[str], situation: dict, essais: int = 4, attente: int = 30) -> list[dict]:
    """Pose une question au service de calcul. Réessaie s'il est occupé (429), en panne passagère ou si la ligne coupe."""
    corps = json.dumps({"expressions": expressions, "situation": situation}).encode("utf-8")
    for n in range(essais):
        req = urllib.request.Request(URL, data=corps, headers={"Content-Type": "application/json", "User-Agent": "robot-taux"})
        try:
            with urllib.request.urlopen(req, timeout=45) as rep:
                d = json.loads(rep.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == essais - 1:
                raise Illisible(f"le service répond « {e.code} »")
        except (OSError, http.client.HTTPException, ValueError) as e:      # ligne coupée, délai dépassé, réponse tronquée
            if n == essais - 1:
                raise Illisible(f"le service ne répond pas ({type(e).__name__} : {e})")
        else:
            if not isinstance(d, dict) or d.get("situationError") or not isinstance(d.get("evaluate"), list):
                raise Illisible(f"le service refuse la question : {str(d)[:300]}")
            return d["evaluate"]
        time.sleep(attente * (n + 1))
    raise Illisible("le service ne répond pas")


def _situation(jour: dt.date, nature: str, service: int, vente: int) -> dict:
    return {
        "date": jour.strftime("%d/%m/%Y"),                       # sinon le service calcule à sa propre date de référence
        "entreprise . catégorie juridique": "'EI'",
        "entreprise . catégorie juridique . EI . auto-entrepreneur": "oui",
        "entreprise . activité . nature": f"'{nature}'",
        "entreprise . date de création": "01/01/2021",           # ancienne : pas d'ACRE dans ce calcul
        "établissement . commune . département": "'Rhône'",      # métropole, hors Alsace-Moselle
        "entreprise . chiffre d'affaires . service BIC": f"{service} €/an",
        "entreprise . chiffre d'affaires . vente restauration hébergement": f"{vente} €/an",
        "entreprise . chiffre d'affaires . service BNC": "0 €/an",
        AE + "impôt . versement libératoire": "oui",
    }


def _nombres(expressions: list[str], reponses: list) -> list[tuple[float, dict]]:
    """(valeur, unité) de chaque réponse ; refuse une réponse absente, vide, nulle ou incomplète."""
    if not isinstance(reponses, list) or len(reponses) != len(expressions):
        raise Illisible("réponse incomplète du service")
    out = []
    for e, r in zip(expressions, reponses):
        v, manque = r.get("nodeValue"), r.get("missingVariables")
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise Illisible(f"pas de nombre pour « {e} » (reçu : {v!r})")
        if manque:      # le service a calculé avec des valeurs par défaut : le résultat peut être faux sans en avoir l'air
            raise Illisible(f"calcul incomplet pour « {e} » : il manque {sorted(manque)[:3]}")
        if v <= 0:      # tout micro-entrepreneur paie chacune de ces parts : un zéro veut dire que la règle ne s'applique plus
            raise Illisible(f"montant nul pour « {e} »")
        out.append((float(v), r.get("unit") or {}))
    return out


def _lire_reponse(reponses: list) -> tuple[list[float], list[float]]:
    """Réponse à MONTANTS + AFFICHES -> ([cotisations, formation, chambre, versement libératoire] en % du chiffre
    d'affaires, [taux affiché des ventes, taux affiché des prestations])."""
    nombres = _nombres(MONTANTS + AFFICHES, reponses)
    calcules = []
    for (v, unite), e in zip(nombres, MONTANTS):
        par = unite.get("denominators")
        if unite.get("numerators") != ["€"] or par not in (["mois"], ["an"]):
            raise Illisible(f"unité inattendue pour « {e} » : {unite}")
        calcules.append(round(v * (12 if par == ["mois"] else 1) / CA_AN * 100, 4))
    affiches = []
    for (v, unite), e in zip(nombres[len(MONTANTS):], AFFICHES):
        if unite.get("numerators") != ["%"] or unite.get("denominators"):
            raise Illisible(f"unité inattendue pour « {e} » : {unite}")
        affiches.append(round(v, 4))
    return calcules, affiches


def lire_urssaf(jour: dt.date, demander=demander, pause: int = PAUSE) -> dict:
    """Les taux du jour, calculés par le service de l'URSSAF, dans la forme de taux_urssaf.json."""
    lus, affiches = {}, set()
    for nature in ("artisanale", "commerciale"):
        for sorte in ("vente", "service"):
            sit = _situation(jour, nature, CA_AN if sorte == "service" else 0, CA_AN if sorte == "vente" else 0)
            calcules, aff = _lire_reponse(demander(MONTANTS + AFFICHES, sit))
            if calcules[0] != aff[sorte == "service"]:      # le montant calculé et le taux affiché : deux chemins, un seul taux
                raise Illisible(f"le taux affiché ({aff[sorte == 'service']}) ne correspond pas au montant calculé ({calcules[0]}) pour « {sorte} »")
            lus[nature, sorte] = calcules
            affiches.add(tuple(aff))
            time.sleep(pause)
    # ACRE : une entreprise créée ce mois-ci, qui y a droit ; le service donne la part des cotisations qu'elle paie
    sit = {**_situation(jour, "artisanale", CA_AN, 0), "entreprise . date de création": jour.replace(day=1).strftime("%d/%m/%Y"),
           "dirigeant . exonérations . ACRE": "oui"}
    (part, unite), = _nombres(ACRE, demander(ACRE, sit))
    if unite.get("numerators") != ["%"] or unite.get("denominators"):
        raise Illisible(f"unité inattendue pour l'ACRE : {unite}")
    av, asv = lus["artisanale", "vente"], lus["artisanale", "service"]
    cv, cs = lus["commerciale", "vente"], lus["commerciale", "service"]
    # les cotisations et le versement libératoire ne dépendent pas du métier : les lectures doivent se recouper
    if len(affiches) != 1 or (av[0], av[3], asv[0], asv[3]) != (cv[0], cv[3], cs[0], cs[3]):
        raise Illisible("artisan et commerçant ne donnent pas les mêmes cotisations : calcul à revoir")
    return {"vente": av[0], "service": asv[0],
            "chambres": {"artisan": [round(av[1] + av[2], 4), round(asv[1] + asv[2], 4)],
                         "commercant": [round(cv[1] + cv[2], 4), round(cs[1] + cs[2], 4)]},
            "vfl": [av[3], asv[3]], "acre": round(part / 100, 4)}


def _plat(t: dict) -> dict:
    """Les taux suivis, à plat : {(famille, libellé lisible): valeur}."""
    return {("cotisations", "ventes"): t["vente"], ("cotisations", "prestations"): t["service"],
            ("chambres", "formation et chambre de métiers, ventes"): t["chambres"]["artisan"][0],
            ("chambres", "formation et chambre de métiers, prestations"): t["chambres"]["artisan"][1],
            ("chambres", "formation et chambre de commerce, ventes"): t["chambres"]["commercant"][0],
            ("chambres", "formation et chambre de commerce, prestations"): t["chambres"]["commercant"][1],
            ("vfl", "versement libératoire, ventes"): t["vfl"][0], ("vfl", "versement libératoire, prestations"): t["vfl"][1],
            ("acre", "part des cotisations payée pendant l'ACRE"): t["acre"]}


def fr(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def comparer(fichier: dict, lus: dict) -> list[tuple[str, float, float]]:
    """[(libellé, ancien, nouveau)] des taux qui ont changé. Illisible si un taux lu est hors bornes, saute trop, ou si
    la part ACRE a bougé (elle ne se corrige pas toute seule)."""
    avant, changes = _plat(fichier), []
    for (famille, lib), neuf in _plat(lus).items():
        mini, maxi, saut = BORNES[famille]
        vieux = float(avant[famille, lib])
        if not mini <= neuf <= maxi:
            raise Illisible(f"taux hors bornes pour « {lib} » : {fr(neuf)}")
        if abs(neuf - vieux) <= 1e-9:
            continue
        if famille == "acre":
            raise Illisible(f"la part ACRE a changé ({fr(vieux)} → {fr(neuf)}). Hunparts l'applique selon la date de création "
                            "de l'entreprise : il faut l'adapter avant de corriger le fichier")
        if abs(neuf - vieux) > saut or not vieux / 2 <= neuf <= vieux * 2:
            raise Illisible(f"écart trop grand pour « {lib} » : {fr(vieux)} → {fr(neuf)}. Si c'est un vrai changement, le fichier "
                            "est à corriger à la main")
        changes.append((lib, vieux, neuf))
    return changes


def traiter(texte: str, lus: dict, jour: dt.date) -> tuple[str | None, list]:
    """(nouveau texte du fichier, ou None s'il n'y a rien à écrire ; changements). Lève Illisible si rien n'est sûr."""
    fichier = json.loads(texte)
    changes = comparer(fichier, lus)
    neuf = dict(fichier)
    if changes:
        neuf.update({k: lus[k] for k in ECRITS}, annee=jour.year, maj=jour.isoformat())
    elif str(fichier.get("verifie", ""))[:7] == jour.isoformat()[:7]:
        return None, []                       # déjà contrôlé ce mois-ci : pas de nouvelle écriture
    neuf["verifie"] = jour.isoformat()        # date du dernier contrôle réussi (Hunparts ne la lit pas)
    sortie = json.dumps(neuf, indent=2, ensure_ascii=False) + "\n"
    relu = json.loads(sortie)                 # ce que le robot n'a pas le droit de corriger ne doit pas avoir bougé
    for k in fichier:
        if k not in (*ECRITS, "annee", "maj", "verifie") and relu.get(k) != fichier[k]:
            raise Illisible(f"le champ « {k} » aurait été modifié : rien n'est écrit")
    return sortie, changes


def _sortie_github(**valeurs) -> None:
    chemin = os.environ.get("GITHUB_OUTPUT")
    if chemin:
        with open(chemin, "a", encoding="utf-8") as f:
            for k, v in valeurs.items():
                f.write(f"{k}={v}\n")


def main(argv: list[str], lire=lire_urssaf, fichier: pathlib.Path = FICHIER, jour: dt.date | None = None) -> int:
    essai, jour = "--essai" in argv, jour or dt.date.today()
    message = fichier.parent / "message.md"
    try:
        lus = lire(jour)
        sortie, changes = traiter(fichier.read_text(encoding="utf-8"), lus, jour)
    except Exception as e:  # noqa: BLE001 - quoi qu'il arrive : ne rien écrire, et le dire
        if not isinstance(e, Illisible):
            traceback.print_exc()
        print(f"LECTURE DOUTEUSE : {e}")
        message.write_text(
            "@mario07teck-sys Le robot n'a pas pu confirmer les taux de l'URSSAF de façon sûre. Il n'a donc **rien modifié** : "
            "Hunparts continue avec les taux du fichier actuel.\n\n"
            "À faire : demander à Claude de regarder (« le robot des taux URSSAF a un doute »), puis fermer ce message.\n\n"
            f"Détail technique, à lui transmettre tel quel : {type(e).__name__} : {e}\n", encoding="utf-8")
        _sortie_github(etat="illisible")
        return 1
    print("Taux lus à l'URSSAF le", jour.strftime("%d/%m/%Y"), ":", json.dumps(lus, ensure_ascii=False))
    if changes:
        lignes = "\n".join(f"- {lib} : {fr(a)} → **{fr(b)}**" for lib, a, b in changes)
        print("TAUX CHANGÉS :\n" + lignes)
        message.write_text(
            f"@mario07teck-sys Les taux de l'URSSAF ont changé. Le robot a corrigé le fichier le {jour.strftime('%d/%m/%Y')} :\n\n"
            f"{lignes}\n\n(en % du chiffre d'affaires)\n\n"
            "Rien à faire : chaque Hunparts prendra les nouveaux taux à son prochain contrôle mensuel, sauf si ses taux ont été "
            "modifiés à la main dans les Réglages (il affichera alors un bandeau). Tu peux fermer ce message.\n", encoding="utf-8")
    else:
        print("Taux inchangés." + ("" if sortie else " Contrôle déjà noté ce mois-ci : rien à écrire."))
    if essai:
        print("ESSAI : rien n'a été écrit dans le fichier des taux.")
        _sortie_github(etat="essai")
        return 0
    if sortie:
        fichier.write_text(sortie, encoding="utf-8", newline="\n")
    _sortie_github(etat="change" if changes else "controle" if sortie else "rien")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

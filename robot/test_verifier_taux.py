"""Contrôles du robot des taux, sans internet : un faux service de calcul répond à la place de l'URSSAF.
Lancé par GitHub avant chaque passage du robot : s'il échoue, le robot ne tourne pas.
Usage : python3 robot/test_verifier_taux.py"""
import copy
import datetime as dt
import io
import json
import pathlib
import sys
import tempfile
import urllib.error

ICI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ICI))
import verifier_taux as v  # noqa: E402

JOUR = dt.date(2026, 11, 24)
VRAI = {"vente": 12.3, "service": 21.2, "cfp": {"artisanale": 0.3, "commerciale": 0.1},
        "tfc": {"artisanale": (0.22, 0.48), "commerciale": (0.015, 0.044)}, "vfl": (1.0, 1.7), "acre": 75}
FICHIER = {"annee": 2026, "vente": 12.3, "service": 21.2,
           "chambres": {"artisan": [0.52, 0.78], "commercant": [0.115, 0.144]}, "vfl": [1.0, 1.7],
           "acre": 0.75, "acre_avant_2026_07": 0.5, "maj": "2026-10-15", "sources": "pages officielles", "explication": "texte libre"}
SUIVIS = ("vente", "service", "chambres", "vfl", "acre")
ok = total = 0


def verifier(nom, cond, vu=""):
    global ok, total
    total += 1
    ok += bool(cond)
    print(("OK    " if cond else "ECHEC ") + nom + ("" if cond else f"  (lu : {vu})"))


def faux_service(taux=VRAI, abime=None, vus=None):
    """Répond comme le service de l'URSSAF : des montants en € par mois (versement libératoire : par an), des taux en %."""
    def demander(expressions, situation):
        if vus is not None:
            vus.append(situation)
        euros = lambda cle: float(situation[cle].split()[0])
        s, ve = euros("entreprise . chiffre d'affaires . service BIC"), euros("entreprise . chiffre d'affaires . vente restauration hébergement")
        nature = situation["entreprise . activité . nature"].strip("'")
        mois = lambda x: {"nodeValue": round(x / 12), "unit": {"numerators": ["€"], "denominators": ["mois"]}, "missingVariables": {}}
        pct = lambda x: {"nodeValue": x, "unit": {"numerators": ["%"], "denominators": []}, "missingVariables": {}}
        rep = {v.CC + "cotisations": mois(s * taux["service"] / 100 + ve * taux["vente"] / 100),
               v.CC + "CFP": mois((s + ve) * taux["cfp"][nature] / 100),
               v.CC + "TFC": mois(ve * taux["tfc"][nature][0] / 100 + s * taux["tfc"][nature][1] / 100),
               v.AE + "impôt . versement libératoire . montant": {"nodeValue": ve * taux["vfl"][0] / 100 + s * taux["vfl"][1] / 100,
                                                                   "unit": {"numerators": ["€"], "denominators": ["an"]}, "missingVariables": {}},
               v.AFFICHES[0]: pct(taux["vente"]), v.AFFICHES[1]: pct(taux["service"]), v.ACRE[0]: pct(taux["acre"])}
        out = [copy.deepcopy(rep[e]) for e in expressions]
        return abime(out, situation) if abime else out
    return demander


def lire(taux=VRAI, abime=None, vus=None):
    return lambda jour: v.lire_urssaf(jour, demander=faux_service(taux, abime, vus), pause=0)


def passage(contenu, lecteur, *args):
    """Un passage du robot sur un fichier temporaire -> (code de sortie, fichier relu, message écrit, sortie GitHub)."""
    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "taux_urssaf.json"
    f.write_text(json.dumps(contenu, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sortie = d / "github_output"
    v.os.environ["GITHUB_OUTPUT"] = str(sortie)
    ecran, erreurs, sys.stdout, sys.stderr = sys.stdout, sys.stderr, io.StringIO(), io.StringIO()   # le récit du robot n'est pas le sujet ici
    try:
        code = v.main(list(args), lire=lecteur, fichier=f, jour=JOUR)
    finally:
        sys.stdout, sys.stderr = ecran, erreurs
        v.os.environ.pop("GITHUB_OUTPUT", None)
    m = d / "message.md"
    return code, json.loads(f.read_text(encoding="utf-8")), m.read_text(encoding="utf-8") if m.exists() else "", \
        sortie.read_text(encoding="utf-8") if sortie.exists() else ""


def refuse(nom, contenu=FICHIER, dit="", **k):
    code, relu, msg, github = passage(contenu, lire(**k))
    verifier(nom, code == 1 and relu == contenu and "rien modifié" in msg and "etat=illisible" in github and dit in msg, (code, msg[-260:], github))
    return msg


# --- lecture : les taux du fichier se retrouvent à partir des montants calculés
vus = []
lus = lire(vus=vus)(JOUR)
verifier("les montants du service redonnent exactement les taux du fichier", lus == {k: FICHIER[k] for k in SUIVIS}, lus)
verifier("cinq questions : artisan et commerçant, ventes et prestations, puis l'ACRE", len(vus) == 5)
verifier("toujours à la date du jour, pas à la date de référence du service", all(s["date"] == "24/11/2026" for s in vus))
verifier("ACRE demandée pour une entreprise créée ce mois-ci",
         vus[4]["entreprise . date de création"] == "01/11/2026" and vus[4]["dirigeant . exonérations . ACRE"] == "oui"
         and all("dirigeant . exonérations . ACRE" not in s for s in vus[:4]))
reelle = json.loads((ICI / "reponse_reelle.json").read_text(encoding="utf-8"))     # gardée telle que le service l'a rendue le 24/11/2026
verifier("une vraie réponse du service (prestations d'un artisan) se lit comme prévu",
         reelle["questions"] == v.MONTANTS + v.AFFICHES and v._lire_reponse(reelle["evaluate"]) == ([21.2, 0.3, 0.48, 1.7], [12.3, 21.2]),
         v._lire_reponse(reelle["evaluate"]))

# --- taux inchangés
code, relu, msg, github = passage(FICHIER, lire())
verifier("inchangés : seule la date du contrôle est notée", code == 0 and relu == {**FICHIER, "verifie": "2026-11-24"} and not msg and "etat=controle" in github, (relu, github))
code, relu, msg, github = passage({**FICHIER, "verifie": "2026-11-05"}, lire())
verifier("inchangés, déjà contrôlés ce mois-ci : rien n'est écrit", code == 0 and relu["verifie"] == "2026-11-05" and "etat=rien" in github, github)
code, relu, msg, github = passage({**FICHIER, "verifie": "2026-10-20"}, lire())
verifier("inchangés, contrôle du mois dernier : la date est remise à jour", relu["verifie"] == "2026-11-24" and "etat=controle" in github)

# --- taux changés
neuf = {**VRAI, "service": 21.5, "tfc": {**VRAI["tfc"], "commerciale": (0.016, 0.044)}}
code, relu, msg, github = passage(FICHIER, lire(neuf))
attendu = {**FICHIER, "service": 21.5, "chambres": {"artisan": [0.52, 0.78], "commercant": [0.116, 0.144]},
           "annee": 2026, "maj": "2026-11-24", "verifie": "2026-11-24"}
verifier("changés : le fichier prend les nouveaux taux, le reste ne bouge pas", code == 0 and relu == attendu and "etat=change" in github, relu)
verifier("changés : le message dit quoi, en clair", "prestations : 21,2 → **21,5**" in msg and "chambre de commerce, ventes : 0,115 → **0,116**" in msg
         and "ventes : 12,3" not in msg and "ACRE" not in msg, msg)
verifier("la part ACRE et les textes ne sont jamais touchés par le robot",
         (relu["acre"], relu["acre_avant_2026_07"]) == (0.75, 0.5) and relu["sources"] == FICHIER["sources"] and relu["explication"] == FICHIER["explication"])
code, relu, msg, github = passage(FICHIER, lire(neuf), "--essai")
verifier("essai : rien n'est écrit, même si les taux ont changé", code == 0 and relu == FICHIER and "etat=essai" in github, (relu, github))


# --- lectures douteuses : rien n'est écrit, un message part
def sans_nombre(out, sit):
    out[0]["nodeValue"] = None
    return out


def incomplet(out, sit):
    out[-1]["missingVariables"] = {"entreprise . catégorie juridique": 1}
    return out


def autre_unite(out, sit):
    out[0]["unit"] = {"numerators": ["€"], "denominators": ["trimestre"]}
    return out


def chambre_nulle(out, sit):
    if len(out) > 1:
        out[2]["nodeValue"] = 0          # la taxe de chambre ne s'applique plus : la somme resterait plausible (0,3 %)
    return out


def affiche_different(out, sit):
    if len(out) > 1:
        out[5]["nodeValue"] = 21.3       # le taux affiché ne dit plus la même chose que le montant
    return out


def commercant_different(out, sit):
    if "commerciale" in sit["entreprise . activité . nature"] and len(out) > 1:
        out[0]["nodeValue"] = round(out[0]["nodeValue"] * 1.01)       # 1 % de plus, montant et taux affichés d'accord entre eux
        out[4]["nodeValue"], out[5]["nodeValue"] = round(out[4]["nodeValue"] * 1.01, 4), round(out[5]["nodeValue"] * 1.01, 4)
    return out


refuse("pas de nombre dans la réponse", abime=sans_nombre, dit="pas de nombre")
m = refuse("calcul incomplet (valeurs par défaut du service)", abime=incomplet, dit="il manque")
verifier("le message s'adresse au propriétaire et dit quoi faire", m.startswith("@mario07teck-sys") and "demander à Claude" in m and "Détail technique" in m, m)
refuse("unité inattendue", abime=autre_unite, dit="unité inattendue")
refuse("une part tombe à zéro alors que la somme reste plausible", abime=chambre_nulle, dit="montant nul")
refuse("versement libératoire lu à zéro", taux={**VRAI, "vfl": (0, 1.7)}, dit="montant nul")
refuse("taux affiché et montant calculé qui ne se recoupent pas", abime=affiche_different, dit="ne correspond pas")
refuse("artisan et commerçant qui ne se recoupent pas", abime=commercant_different, dit="artisan et commerçant")
refuse("réponse plus courte que la question", abime=lambda out, sit: out[:-1], dit="incomplète")
refuse("saut trop grand (prestations 21,2 -> 40)", taux={**VRAI, "service": 40}, dit="écart trop grand")
refuse("taux qui double (versement libératoire 1 -> 2,5)", taux={**VRAI, "vfl": (2.5, 1.7)}, dit="écart trop grand")
refuse("taux hors bornes (ventes à 0,2)", taux={**VRAI, "vente": 0.2}, dit="hors bornes")
m = refuse("la part ACRE change : prévenir, ne rien écrire", taux={**VRAI, "acre": 80}, dit="la part ACRE a changé (0,75 → 0,8)")
refuse("ACRE qui change en même temps qu'un autre taux : rien n'est écrit non plus", taux={**VRAI, "acre": 80, "service": 21.5}, dit="ACRE")
refuse("fichier des taux abîmé", contenu={"vente": 12.3})


def plante(jour):
    raise RuntimeError("panne imprévue")


code, relu, msg, github = passage(FICHIER, plante)
verifier("panne imprévue du programme : rien n'est écrit, un message part", code == 1 and relu == FICHIER and "panne imprévue" in msg and "etat=illisible" in github)

# --- le service occupé (429) ou la ligne qui coupe : on attend et on recommence ; une vraie erreur arrête tout de suite
appels, dodo = [], []
v.time.sleep = dodo.append


class Reponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def faux_urlopen(pannes):
    def ouvrir(req, timeout=0):
        appels.append(req)
        if len(appels) <= len(pannes):
            raise pannes[len(appels) - 1]
        return Reponse(json.dumps({"evaluate": [{"nodeValue": 1}]}).encode("utf-8"))
    return ouvrir


occupe = lambda: urllib.error.HTTPError(v.URL, 429, "Too Many Requests", {}, None)
v.urllib.request.urlopen = faux_urlopen([occupe(), occupe()])
verifier("service occupé deux fois : la troisième demande passe", v.demander(["x"], {}) == [{"nodeValue": 1}] and dodo == [30, 60], dodo)
verifier("rien dans la demande ne nomme le logiciel ni son propriétaire", appels[0].get_header("User-agent") == "robot-taux")
appels.clear(), dodo.clear()
v.urllib.request.urlopen = faux_urlopen([ConnectionResetError("ligne coupée"), v.http.client.IncompleteRead(b""), TimeoutError()])
verifier("ligne coupée, réponse tronquée, délai dépassé : la quatrième demande passe", v.demander(["x"], {}) == [{"nodeValue": 1}] and len(appels) == 4, len(appels))
appels.clear()
v.urllib.request.urlopen = faux_urlopen([ConnectionResetError("ligne coupée")] * 4)
try:
    v.demander(["x"], {})
    verifier("ligne coupée quatre fois : lecture douteuse", False)
except v.Illisible as e:
    verifier("ligne coupée quatre fois : lecture douteuse, sans insister davantage", "ne répond pas" in str(e) and len(appels) == 4, (str(e), len(appels)))
appels.clear()
v.urllib.request.urlopen = faux_urlopen([urllib.error.HTTPError(v.URL, 404, "Not Found", {}, None)])
try:
    v.demander(["x"], {})
    verifier("adresse introuvable (404) : lecture douteuse", False)
except v.Illisible as e:
    verifier("adresse introuvable (404) : lecture douteuse, sans insister", "404" in str(e) and len(appels) == 1, (str(e), len(appels)))
v.urllib.request.urlopen = lambda req, timeout=0: Reponse(json.dumps({"situationError": {"message": "règle inconnue"}}).encode("utf-8"))
try:
    v.demander(["x"], {})
    verifier("question refusée par le service : lecture douteuse", False)
except v.Illisible as e:
    verifier("question refusée par le service : lecture douteuse", "refuse" in str(e))


# --- le fichier écrit reste accepté par Hunparts : copie de cotisations.taux_valides (le logiciel n'est pas dans ce dépôt ;
#     la vraie fonction est contrôlée avec le robot par tests/test_robot_taux.py, côté Hunparts)
def taux_valides(t):
    try:
        return all(0 <= float(t[k]) < 60 for k in ("vente", "service", "acre", "acre_avant_2026_07")) \
            and all(len(t["chambres"][s]) == 2 and all(0 <= float(x) < 5 for x in t["chambres"][s]) for s in ("artisan", "commercant")) \
            and len(t["vfl"]) == 2 and all(0 <= float(x) < 10 for x in t["vfl"])
    except (KeyError, TypeError, ValueError):
        return False


verifier("le fichier corrigé reste lisible par Hunparts", taux_valides(attendu) and taux_valides({**FICHIER, "verifie": "2026-11-24"}))
print(f"\n==> {ok}/{total} cas corrects")
sys.exit(0 if ok == total else 1)

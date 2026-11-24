# hunparts
Hunparts - installateurs et mises à jour (logiciel protégé par licence)

## Taux URSSAF
`taux_urssaf.json` contient les taux de cotisations des micro-entrepreneurs. Chaque Hunparts le relit une fois par mois.

Le 5 et le 20 de chaque mois, un robot (`robot/verifier_taux.py`, lancé par GitHub) demande les taux du jour au service
de calcul de l'URSSAF, mon-entreprise.urssaf.fr, et les compare au fichier :

- taux inchangés : il note la date du contrôle dans le fichier (`verifie`) ;
- taux changés : il corrige le fichier et ouvre un message dans l'onglet Issues ;
- lecture impossible ou douteuse : il ne modifie rien et ouvre un message.

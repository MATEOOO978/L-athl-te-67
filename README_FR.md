# Bot d'alertes Yahoo Auctions Japan + Mercari Japan vers Discord

## Important
Discord ne peut pas exécuter le code Python lui-même. Il faut lancer le programme sur un ordinateur/serveur/hébergement compatible ; Discord reçoit ensuite les alertes via un webhook. L'hébergement gratuit 24 h/24 n'est pas garanti et dépend des offres et limites du service choisi.

## Contenu
- `bot.py` : programme principal
- `refs.json` : mots-clés japonais et prix maximum en yens
- `refs/` : photos de référence
- `requirements.txt` : dépendances Python

## Configuration
1. Crée un webhook dans les paramètres du salon Discord où tu veux les alertes.
2. Configure `DISCORD_WEBHOOK` comme variable d'environnement sur la machine qui exécute le bot. Ne partage jamais ce lien : toute personne qui le possède peut publier dans ce salon.
3. Installe Python, les dépendances, puis Chromium pour Playwright :
   `pip install -r requirements.txt`
   `playwright install chromium`
4. Lance `python bot.py`.

Variables facultatives :
- `SIMILARITY_THRESHOLD=0.82` : seuil de similarité image.
- `SCAN_INTERVAL_SECONDS=300` : intervalle entre les scans (minimum 60 secondes).
- `LOG_LEVEL=INFO` : niveau des logs.

## Limites à connaître
- Les pages HTML et sélecteurs de Yahoo Auctions / Mercari peuvent changer, et les sites peuvent limiter ou bloquer les requêtes. Vérifie leur règlement et utilise une fréquence raisonnable.
- Un score CLIP élevé signifie que les images se ressemblent visuellement, pas que l'article est authentique.
- Le prix est filtré par le site en yens ; les frais de livraison et éventuels frais d'intermédiaire ne sont pas inclus.
- Les annonces sans image accessible ne seront pas comparées.
- Ce code a été renforcé contre certaines erreurs, mais n'a pas pu être validé de bout en bout contre les sites en direct dans cet environnement.


## Utilisation avec GitHub Actions (scan toutes les 5 minutes)
Le fichier `.github/workflows/main.yml` lance un scan unique selon une planification de 5 minutes. `RUN_ONCE=1` est bien pris en charge par `bot.py` : le programme s'arrête après le scan. Le travail dispose de 12 minutes maximum pour absorber le premier téléchargement du modèle et l'installation de Chromium ; si un scan prend plus de 5 minutes, les lancements peuvent prendre du retard. Le fichier `seen.json` est commité dans le dépôt pour éviter de renvoyer les mêmes annonces à chaque exécution.

Dans GitHub, ajoute le webhook dans **Settings → Secrets and variables → Actions → New repository secret**, avec le nom exact `DISCORD_WEBHOOK`. Ne mets jamais l'URL du webhook dans le code.

GitHub peut retarder ou parfois ignorer une exécution planifiée en période de forte charge. Le délai n'est donc pas garanti exactement à 5 minutes. Le premier lancement peut aussi être plus long (installation et téléchargement du modèle CLIP).


**Dépôt public :** tout le code et les photos de référence déposés dans un dépôt public sont visibles par tout le monde. Le webhook Discord doit rester exclusivement dans les Secrets GitHub et ne jamais apparaître dans un fichier.

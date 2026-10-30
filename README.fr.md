# proxy_lc0

Un petit proxy en Python qui fait tourner Lc0 (Leela Chess Zero) sur une
machine distante via OpenSSH natif, et l'expose à n'importe quelle interface
UCI locale (Nibbler, Arena, Lucas Chess, ...) comme si le moteur était
installé localement.

Usage typique : l'interface tourne sur un portable Windows sans carte
graphique adaptée, tandis que Lc0 tourne sur un Mac Apple Silicon (backend
Metal) ou sur une machine Linux équipée d'un GPU.

Projet compagnon de
[proxy_stockfish](https://github.com/deuza/proxy_stockfish).

La version anglaise de ce document se trouve dans
[README.md](README.md).

## Fonctionnement

```text
+-------------+  stdin   +-----------+   ssh    +------------------------+
|  GUI UCI    | -------> |           | -------> |  Lc0 sur la machine    |
|  (Nibbler)  | <------- | proxy_lc0 | <------- |  distante (Metal...)   |
|             |  stdout  |           |          |                        |
|  infobox    | <------- |           | <------- |  stderr de ssh et Lc0  |
+-------------+  stderr  +-----------+          +------------------------+
```

L'interface lance le proxy comme un moteur UCI local. Le proxy lance le
client `ssh` du système, qui exécute Lc0 sur la machine distante, puis
relaie les lignes UCI dans les deux sens. Il n'attend jamais de réponse du
moteur : l'interface garde la maîtrise complète du dialogue UCI.

En plus de ce relais simple, le proxy fait trois choses :

- Avant la première commande susceptible de préparer ou de lancer une
  recherche (`isready`, `ucinewgame`, `position` ou `go`), il envoie les
  options de `config.json` qui décrivent la machine distante : fichier de
  journal, réseau, dossiers Syzygy, taille du cache et limite de RAM. Lc0
  charge son réseau au premier `ucinewgame`, `position` ou `go` : le
  réseau configuré est donc le premier chargé.
- Il ignore les commandes `setoption` de l'interface pour `WeightsFile`,
  `SyzygyPath` et `LogFile` : ce sont des chemins sur l'ordinateur de
  l'interface, qui n'ont aucun sens sur la machine distante.
- Il recopie la sortie d'erreur de ssh et de Lc0 sur sa propre sortie
  d'erreur, pour que l'interface puisse l'afficher (Nibbler la montre dans
  son infobox). Un message macOS connu et inoffensif est tenu à l'écart de
  l'interface et ne va que dans le journal (voir Dépannage).

Toutes les autres commandes et options passent telles quelles. Si la
connexion ou le moteur meurt, le proxy s'arrête : l'interface voit un
moteur arrêté plutôt qu'un moteur muet.

## Prérequis

Sur l'ordinateur de l'interface :

- Le client OpenSSH (`ssh` dans le PATH). Windows 10 (1809 ou ultérieur)
  et Windows 11 le proposent comme fonctionnalité facultative, à ajouter
  depuis les Paramètres si besoin. Vérifiez avec `ssh -V`.
- Python 3.8 ou ultérieur, uniquement pour lancer le script ou construire
  l'exécutable. Le proxy n'utilise que la bibliothèque standard de Python.

Sur la machine distante :

- Lc0 et un fichier de réseau. Le proxy a été développé avec Lc0 0.32.1.
- Un serveur SSH avec authentification par clé. Sous macOS : Réglages
  Système > Général > Partage, option Session à distance (Remote Login).
- Facultatif : les tables Syzygy.

## Préparation de la machine distante

L'exemple ci-dessous est un Mac avec Homebrew, adaptez les chemins à votre
machine.

Installez Lc0 et notez son chemin absolu :

```sh
brew install lc0
which lc0
```

Sur Apple Silicon, `which lc0` donne `/opt/homebrew/bin/lc0`. Une commande
lancée par ssh ne lit pas `~/.zprofile`, où Homebrew règle son PATH : le
proxy a donc besoin de ce chemin absolu.

Téléchargez un réseau depuis la
[page Best Networks de Lc0](https://lczero.org/play/networks/bestnets/) et
rangez-le sur la machine distante, par exemple dans `/Users/user/lc0/`. Les
fichiers compressés (`.pb.gz`) et non compressés (`.pb`) fonctionnent tous
les deux. Sans réseau configuré, Lc0 prend le réseau le plus récent trouvé
à côté de son binaire : avec Homebrew, le petit réseau fourni par la
[formule](https://formulae.brew.sh/formula/lc0).

Les tables Syzygy sont facultatives. Lc0 cherche les fichiers de tables
directement dans chaque dossier listé, jamais dans les sous-dossiers. Lc0
n'utilise pas les tables Gaviota.

Depuis l'ordinateur de l'interface, vérifiez une fois toute la chaîne à la
main. Cette première connexion interactive permet aussi de vérifier et
d'accepter la clé de la machine :

```sh
ssh user@host /opt/homebrew/bin/lc0 bench --weights=/Users/user/lc0/net.pb
```

Attendu : `Loading weights file from:` avec votre réseau, puis, sur un Mac,
`Initialized metal backend on device Apple M1` (ou votre puce), et les
résultats du benchmark terminés par `Nodes/second`.

Avec un gros réseau sur Apple Silicon, `bench` annonce des vitesses très
basses. Le backend Metal compile le réseau une fois pour chaque taille de
lot, ce qui prend environ 200 ms pour un réseau comme BT3 sur un M1, et les
500 ms que `bench` accorde à chaque position passent en compilations. Pour
mesurer la vraie vitesse, utilisez `backendbench`, qui répète une taille de
lot fixe :

```sh
/opt/homebrew/bin/lc0 backendbench --weights=/Users/user/lc0/net.pb \
  --start-batch-size=64 --max-batch-size=64 --batches=10
```

Une longue analyse donne la même réponse : avec BT3, un Apple M1 a atteint
environ 260 nœuds par seconde une fois passées les 20 premières secondes de
compilation.

### Durcissement facultatif

La clé utilisée par le proxy n'a besoin que de lancer Lc0 : vous pouvez
donc la verrouiller sur la machine distante. Utilisez une clé dédiée, car
elle ne pourra plus ouvrir de shell. Dans `~/.ssh/authorized_keys` sur la
machine distante, préfixez cette clé d'une commande forcée et de l'option
`restrict` :

```text
restrict,command="/opt/homebrew/bin/lc0" ssh-ed25519 AAAA... vous@portable
```

La clé ne peut alors que lancer Lc0, quelle que soit la commande demandée
par le client : ni shell, ni redirection, ni pseudo-terminal. La machine
distante ignore `lc0_command` : les éventuelles options de ligne de
commande de Lc0 vont donc dans la commande forcée. Voir la section
AUTHORIZED_KEYS FILE FORMAT de sshd(8).

## Configuration

Copiez `config.example.json` en `config.json`, dans le même dossier que
`proxy_lc0.py` ou `proxy_lc0.exe`, puis modifiez-le. Les clés qui
commencent par `_` sont des commentaires. Réglages :

- `host` (obligatoire) : nom ou adresse de la machine, ou alias `Host` de
  votre configuration ssh.
- `username` : utilisateur distant. Absent : valeur par défaut de ssh
  (configuration ssh ou nom d'utilisateur local).
- `port` : port SSH. Absent : valeur par défaut de ssh (configuration ssh
  ou 22).
- `key_file` : clé privée. `~` est développé, un chemin relatif part du
  dossier du proxy. Absent : clés par défaut et agent de ssh.
- `ssh_options` : arguments supplémentaires pour ssh, sous forme de liste
  de chaînes. Ils viennent en premier sur la ligne de commande et
  l'emportent donc sur les valeurs par défaut du proxy.
- `lc0_command` (obligatoire) : commande lancée sur la machine distante,
  le chemin absolu de Lc0, éventuellement suivi d'options de ligne de
  commande de Lc0.
- `lc0_weights` : fichier de réseau (`WeightsFile`).
- `lc0_syzygy` : dossier Syzygy (`SyzygyPath`), ou liste de dossiers.
- `lc0_nncache` : taille du cache du réseau, en positions (`NNCacheSize`,
  2000000 par défaut dans Lc0).
- `lc0_ramlimit_mb` : limite mémoire de la recherche, en Mo (`RamLimitMb`,
  0 par défaut dans Lc0, sans limite). Voir plus bas.
- `lc0_logfile` : fichier de journal de Lc0 sur la machine distante
  (`LogFile`), complété à chaque lancement. Utile pour voir exactement ce
  que Lc0 a reçu.
- `logging_enabled` : `true` pour écrire le journal du proxy (`false` par
  défaut).
- `log_file` : fichier de journal du proxy, `debug.log` par défaut dans le
  dossier du proxy.

Tous les chemins `lc0_*` sont des chemins sur la machine distante. Ils
doivent être absolus et commencer par `/` : Lc0 les ouvre tels quels et ne
développe pas `~`.

Les clés absentes de `config.json` n'envoient rien : Lc0 garde ses valeurs
par défaut. Le proxy vérifie le fichier au démarrage et signale les erreurs
sur sa sortie d'erreur, que l'interface affiche. Pour vérifier seulement la
syntaxe JSON :

```sh
python -m json.tool config.json
```

L'option `ServerAliveInterval=15` de l'exemple fait détecter à ssh une
connexion morte au bout d'environ 45 secondes, après quoi le proxy
s'arrête.

L'exemple règle aussi `StrictHostKeyChecking=accept-new` : à la première
connexion, ssh enregistre sans rien demander la clé d'une machine inconnue,
et il refuse une machine dont la clé a changé. Sans cette option,
connectez-vous une fois à la main avant d'utiliser le proxy (voir la
préparation de la machine distante) : le proxy lance ssh en mode batch et
ne peut pas répondre à la question.

### Mémoire

Pendant une analyse infinie, l'arbre de recherche de Lc0 grossit avec le
nombre de nœuds. `RamLimitMb` arrête cette croissance quand l'estimation
de Lc0 (arbre de recherche plus cache du réseau, sans le réseau lui-même)
atteint la limite ; Lc0 répond tout de même à `stop` avec son meilleur
coup, comme l'exige UCI. L'aide de Lc0 prévient que cette estimation est
grossière. Choisissez la limite à partir de la RAM de la machine distante,
moins le système et le réseau : par exemple 6000 sur un Mac de 16 Go avec
un réseau d'environ 2,6 Go.

## Construire l'exécutable Windows

Dans le dossier du projet, sous PowerShell ou cmd, avec un environnement
virtuel dédié :

```powershell
python -m venv venv
.\venv\Scripts\pip install pyinstaller
.\venv\Scripts\pyinstaller --onefile --console --name proxy_lc0 proxy_lc0.py
copy config.json dist\
```

L'exécutable est `dist\proxy_lc0.exe`, avec son `config.json` à côté.
Gardez `--console` : le dialogue UCI passe par l'entrée et la sortie
standard. Aucun exécutable précompilé n'est fourni : construisez le vôtre à
partir des sources.

## Test manuel

Lancez `proxy_lc0.exe` (ou `python proxy_lc0.py`) dans un terminal et
tapez :

```text
uci
isready
position startpos
go nodes 200
quit
```

Attendu : la liste des options de Lc0 terminée par `uciok`, puis
`readyok`, quelques lignes `info` et `bestmove`. Les lignes de la sortie
d'erreur de Lc0, comme `Loading weights file from:`, s'affichent aussi dans
le terminal.

## Utilisation dans une interface

Nibbler (libellés de l'interface anglaise) : Engine > Choose engine...,
puis sélectionnez `proxy_lc0.exe`. Rien d'autre à régler : Nibbler
reconnaît Lc0 à sa ligne `id name` et envoie ses options habituelles.
Engine > Weights > Lc0 WeightsFile... et Engine > Choose Syzygy path...
restent sans effet à travers le proxy, qui le signale dans l'infobox : les
chemins distants viennent de `config.json`.

Autres interfaces : installez `proxy_lc0.exe` comme un moteur UCI, de la
même façon qu'un moteur local.

## Dépannage

Le journal du proxy (`logging_enabled`) enregistre chaque ligne dans les
deux sens (`>>` depuis l'interface, `<<` depuis le moteur), les lignes de
la sortie d'erreur, et les options envoyées ou ignorées. Il grossit vite
pendant une analyse : ne l'activez que pour diagnostiquer.

Messages que l'interface peut afficher :

- `Host key verification failed.` : la clé de la machine a changé, ou la
  machine est inconnue et `StrictHostKeyChecking=accept-new` n'est pas
  réglé. Vérifiez la clé, puis lancez une fois à la main la commande ssh
  de la préparation de la machine distante.
- `Permission denied (publickey)` : la machine distante refuse la clé,
  vérifiez `key_file` et le fichier `authorized_keys` de l'utilisateur
  distant.
- `command not found` ou `No such file or directory`, puis `ssh exit
  status 127` : `lc0_command` doit être le chemin absolu de Lc0.
- `Cannot read weights from` : vérifiez `lc0_weights`.
- `Failed to load Syzygy tablebases!` : vérifiez `lc0_syzygy`.
- `connection to the remote engine closed` : le moteur ou la connexion
  s'est arrêté ; le proxy s'arrête pour que l'interface puisse relancer le
  moteur.

`Failed to import MPS module` n'apparaît que dans le journal du proxy.
Observé sous macOS 27.2 avec un Apple M1, une fois par nouvelle taille de
lot : le compilateur du Neural Engine rejette une partie du réseau et
MPSGraph la garde sur le GPU. La vitesse a été mesurée identique avec et
sans ce message : le proxy le tient donc à l'écart de l'interface, où
Nibbler l'afficherait en rouge.

Pour arrêter un moteur resté actif sur la machine distante (cela arrête
tous les Lc0 de cet utilisateur) :

```sh
ssh user@host pkill -x lc0
```

Avec le durcissement facultatif, lancez cette commande avec une autre clé
ou depuis une session locale : la clé restreinte ne lance que Lc0.

## Configuration testée

- Côté interface : Nibbler sous Windows 11, avec le client OpenSSH de
  Windows.
- Côté moteur : Apple M1 avec 16 Go, macOS 27.2, Lc0 0.32.1 de Homebrew
  avec le backend Metal, tables Syzygy.
- Vitesse : environ 2 100 nœuds par seconde avec le réseau fourni par
  Homebrew, environ 260 avec BT3-768x15x24h-swa-2790000 une fois chaud.

## Limites

- La machine distante doit tourner sous macOS ou Linux (chemins POSIX
  absolus).
- Chaque proxy lancé démarre son propre Lc0 sur la machine distante, avec
  sa propre copie du réseau en mémoire : évitez de nombreuses instances
  simultanées, par exemple des tournois menés par plusieurs processus en
  parallèle.

## Différences avec proxy_stockfish

- Le relais n'attend jamais : ni délai d'attente ni `stop` automatique,
  l'interface mène seule le dialogue UCI.
- `config.json` ne décrit que la machine distante (réseau, tables,
  mémoire) ; les options d'analyse restent entre les mains de l'interface.
- La sortie d'erreur du côté distant parvient à l'interface.

## Licence

CC0 1.0 Universal (dédicace au domaine public), comme proxy_stockfish. Voir
le fichier `LICENSE`.

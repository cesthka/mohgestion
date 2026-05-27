# 🤖 Gestion Bot

Bot Discord **francophone** de gestion communautaire complet, inspiré de **CrowBots Gestion V2 / Black Raven**, écrit en Python (discord.py 2.3+).

> Prefix configurable (par défaut `+`), commandes hybrides (prefix **et** slash), système de **9 niveaux de permissions hiérarchiques**, modération avancée, automod, antiraid, tickets, logs, bienvenue, niveaux, reminders, commandes custom et éditeur de messages.

---

## ✨ Fonctionnalités

- ⭐ **Système de permissions hiérarchiques** (Perm 1 → Perm 9 cumulatives)
- 🛡️ **Modération** : ban (temporaire ou permanent), kick, mute (timeout natif), warn, clear, lock, slowmode
- 🤖 **Automod** : antispam, antilink (discord/all), antibadword, antimassmention, anticaps + whitelist
- 🚨 **Antiraid** : détection automatique, lockdown, kick des comptes récents
- 🎫 **Tickets** : panel à boutons, transcripts HTML envoyés en MP
- 📋 **Logs** : messages, modération, membres, vocal, rôles
- 👋 **Bienvenue / Départ** : salon + MP + autorôles + variables
- 📊 **Niveaux / XP** : rangs, leaderboard, rôles par palier
- ⏰ **Reminders** persistants (survivent aux redémarrages)
- 🔧 **Commandes custom** par serveur avec variables
- ✏️ **Éditeur de messages** : personnalise tous les textes du bot
- ⚙️ **Config par serveur** : prefix, couleur d'embed, statut

---

## 📋 Prérequis

- Python **3.11+**
- Un bot Discord créé sur le [Developer Portal](https://discord.com/developers/applications)
- Les **intents privilégiés** activés :
  - ✅ `MESSAGE CONTENT INTENT`
  - ✅ `SERVER MEMBERS INTENT`
- (Optionnel mais recommandé pour Railway) un compte GitHub et Railway

---

## 🚀 Installation locale

```bash
# 1. Cloner le repo
git clone <ton-url-github> gestion-bot
cd gestion-bot

# 2. Créer un venv (optionnel mais propre)
python -m venv .venv
source .venv/bin/activate     # Linux/Mac
.venv\Scripts\activate        # Windows

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer l'env
cp .env.example .env
# Edite .env et remplis DISCORD_TOKEN et OWNER_ID

# 5. Lancer
python main.py
```

À la première connexion, la base SQLite est créée automatiquement à `./data/bot.db` (ou `DB_PATH` si défini).

---

## ☁️ Déploiement Railway (étape par étape)

### 1. Push sur GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<ton-user>/<ton-repo>.git
git push -u origin main
```

> ⚠️ Vérifie que `.env` est bien ignoré (déjà dans `.gitignore`).

### 2. Créer le projet Railway

1. Rends-toi sur [railway.app](https://railway.app) → **New Project**.
2. Choisis **Deploy from GitHub repo** et sélectionne le repo que tu viens de push.
3. Railway détecte automatiquement Python (Nixpacks).

### 3. Ajouter un Volume persistant (pour la base SQLite)

1. Dans le projet Railway → onglet **Variables / Volumes** du service.
2. Clique **Add Volume**.
3. **Mount path** : `/data`
4. La base sera persistée à `/data/bot.db` même après redéploiement.

### 4. Configurer les variables d'environnement

Dans l'onglet **Variables**, ajoute :

| Clé              | Valeur                                           |
|------------------|--------------------------------------------------|
| `DISCORD_TOKEN`  | Ton token de bot Discord                         |
| `OWNER_ID`       | Ton ID Discord (clic droit → Copier l'identifiant) |
| `DEFAULT_PREFIX` | `+` (ou ce que tu préfères)                      |
| `DB_PATH`        | `/data/bot.db`                                   |

### 5. Déployer

Railway lance automatiquement le service avec la commande définie dans `railway.json` (`python main.py`).
Consulte les **Logs** : tu dois voir `Logged in as ...` quand tout va bien.

### 6. Inviter le bot

Génère un lien d'invitation depuis le Developer Portal avec les scopes :
- `bot`
- `applications.commands`

Permissions recommandées : `Administrator` (le plus simple), ou un mix de `Manage Channels / Manage Roles / Manage Messages / Ban Members / Kick Members / Moderate Members / View Audit Log`.

---

## 🛂 Tableau des permissions par défaut

Le bot utilise **9 niveaux cumulatifs** : un membre avec Perm `N` a accès à toutes les commandes de niveau ≤ `N`.

| Niveau | Commandes par défaut |
|:-----:|----------------------|
| **1** | `remind`, `reminders`, `delreminder`, `rank`, `leaderboard`, `warns`, `listcmd`, `perms`, `helpall` |
| **3** | `ban`, `unban`, `kick`, `mute`, `unmute`, `warn`, `clear` |
| **5** | `lock`, `unlock`, `slowmode`, `delwarn`, `antispam`, `antilink`, `antibadword`, `antimassmention`, `anticaps`, `whitelist`, `ticket`, `welcome`, `goodbye`, `autorole`, `levelroles`, `xpchannel`, `addcmd`, `delcmd`, `editcmd`, `config` |
| **7** | `antiraid`, `lockdown`, `unlockdown`, `setlog`, `dellog`, `logs`, `setlevel`, `setmsg`, `resetmsg`, `listmsg` |
| **9** | `setprefix`, `setcolor`, `setstatus`, `set perm`, `del perm`, `changeall` |

> 🔑 L'owner du bot (`OWNER_ID`), l'owner du serveur et tout membre avec **Administrator** Discord ont toujours **Perm 9**.
> 🔑 Tu peux overrider le niveau requis d'une commande avec `+set perm <commande> <@cible>`.

---

## 📚 Commandes principales

### Permissions

```
+set perm <1-9> <@rôle|@membre>       # Donne un niveau de perm
+set perm <commande> <@rôle|@membre>  # Override pour UNE commande
+del perm <1-9> <@rôle|@membre>       # Retire une perm
+del perm <commande> <@rôle|@membre>
+changeall <ancienperm> <nouvelperm>  # Migre toutes les commandes
+perms <@membre>                       # Affiche le niveau d'un membre
+helpall                               # Liste les commandes par niveau
```

### Modération

```
+ban <@user> [durée] [raison]         # ex: +ban @x 2h spam
+unban <id> [raison]
+kick <@user> [raison]
+mute <@user> <durée> [raison]        # ex: +mute @x 30m
+unmute <@user>
+warn <@user> <raison>
+warns <@user>
+delwarn <id>
+clear <nombre>                        # max 100
+lock / +unlock
+slowmode <secondes>
```

### Automod

```
+antispam on/off
+antispam <msgs>/<secs>                # ex: 4/5
+antilink on/off [discord|all]
+antibadword on/off
+antibadword add/del <mot>
+antibadword list
+antimassmention on/off [seuil]
+anticaps on/off
+whitelist add/del <@rôle|@user>
+whitelist list
```

### Tickets

```
+ticket setup <#salon-panel> [#salon-logs] [@rôle-support] [#catégorie]
+ticket close
+ticket add <@user>
+ticket remove <@user>
```

### Logs

```
+setlog <moderation|messages|members|voice|roles> <#salon>
+dellog <type>
+logs                                  # Affiche la config actuelle
```

### Bienvenue / Départ

```
+welcome channel <#salon>
+welcome message <texte>               # variables: {user} {server} {count}
+welcome dm on/off
+welcome dm message <texte>
+welcome test
+goodbye channel <#salon>
+goodbye message <texte>
+autorole add/del <@rôle>
+autorole list
```

### Niveaux

```
+rank [@user]
+leaderboard
+setlevel <@user> <niveau>
+levelroles add <niveau> <@rôle>
+levelroles del <niveau>
+levelroles list
+xpchannel disable/enable <#salon>
```

### Reminders

```
+remind <durée> <message>              # ex: +remind 2h faire les courses
+reminders
+delreminder <id>
```

### Commandes custom

```
+addcmd <nom> <réponse>
+editcmd <nom> <nouvelle réponse>
+delcmd <nom>
+listcmd
```

### Messages personnalisables

```
+setmsg <clé> <message>                # clés: welcome, ban, kick, mute, warn, etc.
+resetmsg <clé>
+listmsg
```

### Config serveur

```
+setprefix <prefix>
+setcolor <#hex>                       # ex: +setcolor #5865F2
+setstatus <texte>                     # owner only
+config                                # récap complet
```

### Owner (réservé à `OWNER_ID`)

```
+reload <cog>                          # ex: +reload moderation
+sync [guild]
+shutdown
+eval <code>
+stats
```

---

## 🧱 Architecture

```
gestion-bot/
├── main.py                   # Point d'entrée, gestion d'erreurs globale
├── requirements.txt
├── Procfile                  # worker: python main.py
├── runtime.txt
├── railway.json
├── .env.example
├── .gitignore
├── config/
│   ├── config.py             # Constantes + DEFAULT_COMMAND_PERMS
│   └── default_messages.py
├── database/
│   ├── db_manager.py         # Wrapper aiosqlite (WAL, helpers)
│   └── schema.sql
├── utils/
│   ├── checks.py             # @perm(N), @owner_only()
│   ├── embed_builder.py      # EmbedBuilder (success/error/info/mod_action)
│   ├── logger.py             # colorlog
│   └── time_parser.py        # "1h30m" → secondes
└── cogs/
    ├── permissions_system.py # ⭐ set/del perm, changeall, helpall, perms
    ├── moderation.py
    ├── automod.py
    ├── antiraid.py
    ├── tickets.py
    ├── logs.py
    ├── welcome.py
    ├── levels.py
    ├── reminders.py
    ├── custom_commands.py
    ├── messages_editor.py
    ├── config_cog.py
    └── owner.py
```

Chaque cog est **isolé** : si un cog plante au chargement, les autres continuent à fonctionner.

---

## 🐛 Dépannage

| Problème | Solution |
|---------|----------|
| `discord.errors.LoginFailure` | Token invalide. Vérifie `DISCORD_TOKEN` dans `.env`. |
| `PrivilegedIntentsRequired` | Active **MESSAGE CONTENT** et **SERVER MEMBERS** dans le Developer Portal. |
| `+commande` ne marche pas | Lance `+sync` (owner) puis attends quelques minutes pour les slash. |
| Permission denied sur `/data/bot.db` | Sur Railway, vérifie que le volume est monté sur `/data`. |
| La DB ne persiste pas entre deux deploys | Tu n'as pas configuré le volume Railway. |
| Les niveaux/perms ne sont pas appliqués | Les owners et admins Discord ont toujours Perm 9 — c'est normal. |

---

## 📄 Licence

Code libre d'usage personnel et communautaire. Inspiré de la philosophie CrowBots / Black Raven sans réutiliser leur code propriétaire.

---

## ❤️ Crédits

Réalisé avec discord.py par la communauté FR Discord.
Si tu utilises ce bot, un ⭐ sur le repo fait toujours plaisir.

# Installer Phare

Tout se fait en root sur l'hôte Proxmox. Les adresses de ce guide sont des exemples
(réseau 192.168.10.0/24) : remplace-les par les tiennes.

## Ce qu'il faut avant

- Un hôte Proxmox VE avec un seul nœud.
- Le gabarit Debian 13 :

  ```
  pveam update
  pveam available --section system | grep debian-13
  pveam download local <nom du gabarit>
  ```

- Un CT avec Caddy installé par le paquet officiel (il crée l'utilisateur `caddy`), qui sert
  déjà tes sites.
- Facultatif : Pi-hole dans un CT pour les noms locaux, une tâche de sauvegarde Proxmox, un CT
  qui garde les résumés GoAccess des journaux de Caddy.
- `git` sur l'hôte : `apt install git`.

## 1. Récupérer le dépôt sur l'hôte

```
cd /root
git clone https://github.com/tom-lion38/phare.git
cd phare
```

## 2. Régler

```
bash deploy/installer.sh
```

Au premier passage, le script copie `deploy/agent.conf.exemple` dans `/etc/phare/agent.conf` et
s'arrête. Ouvre ce fichier (`nano /etc/phare/agent.conf`) et remplis au moins :

| Réglage | Exemple | À quoi ça sert |
| :-- | :-- | :-- |
| `NOEUD` | `pve` | nom du nœud (`pvesh get /nodes`) |
| `HOTE_IP` | `192.168.10.2` | IP de l'hôte Proxmox |
| `STOCKAGE`, `GABARIT`, `PONT` | `local-lvm`, le gabarit téléchargé, `vmbr0` | où et comment créer les CT |
| `RESEAU`, `MASQUE`, `PASSERELLE`, `DNS` | `192.168.10`, `24`, `192.168.10.1`, `192.168.10.3` | réseau des CT |
| `BLOC_SITES` | `120-169` | dernier octet des IP données aux nouveaux sites |
| `CT_CADDY`, `CADDY_IP` | `101`, `192.168.10.4` | le CT de Caddy |
| `CT_PHARE`, `IP_PHARE` | `103`, `192.168.10.5` | le CT de Phare (créé par le script s'il n'existe pas) |
| `CT_PROTEGES` | `100,101,102,103` | CT que Phare ne pilotera jamais |
| `RESEAUX_WEB` | `192.168.10.0/24, 10.8.0.0/24` | qui peut ouvrir l'interface : réseau local et VPN |

Laisse vides `CT_PIHOLE`, `CT_TRAFIC` ou `JOB_SAUVEGARDE` si tu n'as pas ces services : les
étapes qui en dépendent sont sautées.

## 3. Installer

```
bash deploy/installer.sh
```

Le script :

1. installe `phare-agent` et `modele-ct` dans `/usr/local/sbin` ;
2. crée le CT de Phare s'il n'existe pas (1 cœur, 512 Mo, 4 Go, non privilégié) et y installe
   Python, le client SSH et nftables ;
3. copie le code dans `/opt/phare`, pose le service `phare.service` et le pare-feu ;
4. crée `/etc/phare/phare.env` dans le CT ;
5. génère la clé SSH de Phare dans le CT et ajoute cette ligne sur l'hôte, dans
   `/etc/pve/priv/authorized_keys` (le fichier vers lequel pointe `/root/.ssh/authorized_keys`
   sur Proxmox), après en avoir gardé une copie :

   ```
   command="/usr/local/sbin/phare-agent",from="192.168.10.5",restrict ssh-ed25519 AAAA... phare@ct103
   ```

6. crée l'utilisateur Proxmox `phare@pve` avec un jeton `lecture` (rôle PVEAuditor), copié dans
   le CT sans être affiché ;
7. démarre Phare.

Ouvre ensuite `http://192.168.10.5/` (l'adresse de `IP_PHARE`).

Si tu poses la clé à la main, garde bien `command=`, `from=` et `restrict` : sans eux, cette
clé ouvrirait un shell root sur l'hôte.

## 4. Préparer Caddy

- Phare range ses blocs après la ligne `# ==== Sites ajoutés par Phare ====`. Elle est ajoutée
  à la fin du Caddyfile au premier domaine créé.
- La page de maintenance : Phare ajoute seul le snippet `(maintenance)` la première fois qu'il
  éteint un site, et pousse la page dans `/etc/caddy/maintenance/index.html` à chaque démarrage.
- Extraits facultatifs : si ton Caddyfile définit `(trafic)`, `(securite)` ou `(journal)`, les
  blocs créés par Phare les importent. Exemple de `(trafic)` pour avoir un journal JSON à donner
  à GoAccess :

  ```
  (trafic) {
  	log trafic {
  		output file /var/log/caddy/trafic.log
  		format json
  	}
  }
  ```

- Chaque nouvelle version est validée par `runuser -u caddy -- caddy validate`. Lancé en root,
  `caddy validate` crée les fichiers de journal au nom de root, et le rechargement suivant
  échoue. Si ta validation se fait autrement, change `VALIDER_CADDY` dans `agent.conf`.
- Avant la première modification, tu peux tester Phare sur ton vrai Caddyfile. Copie-le sur ton
  PC, dans le dossier du dépôt, puis :

  ```
  CADDYFILE_REEL=Caddyfile python -m unittest tests.test_caddy
  ```

  Le test vérifie que Phare relit tous tes domaines et peut mettre puis retirer la maintenance
  sur chacun sans rien changer d'autre.

## 5. Pare-feu

- Le CT de Phare n'accepte le port 80 que depuis `RESEAUX_WEB`, et SSH que depuis
  `RESEAUX_SSH` (fichier `/etc/nftables.conf` du CT, fait à partir de `deploy/nftables.conf`).
  Si ton VPN donne des adresses d'un autre réseau, ajoute-le dans `RESEAUX_WEB` et relance
  l'installation.
- Les CT de sites passés par `modele-ct` n'acceptent le port du site que depuis `CADDY_IP` (et
  `ADMIN_IPS` si tu en mets), et ne peuvent pas joindre le réseau local, sauf le DNS, le DHCP et
  les services de supervision réglés dans `INFLUX` et `LOKI`.

## 6. Mettre à jour

```
cd /root/phare
git pull
bash deploy/installer.sh
```

Le script remplace le code et l'agent. Il garde `/etc/phare/agent.conf`, `/etc/phare/phare.env`,
la clé SSH et le jeton.

## 7. Désinstaller

```
bash deploy/desinstaller.sh
```

Le script retire la ligne de la clé dans `authorized_keys` (avec une copie avant), l'agent,
`modele-ct` et l'utilisateur `phare@pve`. Le CT de Phare n'est pas supprimé : fais
`pct stop 103` puis `pct destroy 103` si tu n'en veux plus. Les blocs ajoutés par Phare restent
dans le Caddyfile.

## En cas de souci

- « L'agent de l'hôte Proxmox ne répond pas » : teste depuis le CT de Phare

  ```
  runuser -u phare -- ssh -i /etc/phare/cle_agent -o UserKnownHostsFile=/etc/phare/known_hosts root@192.168.10.2 etat
  ```

  et vérifie que l'adresse de `from=` dans la ligne de la clé est bien celle du CT.
- Journal de l'agent : `/var/log/phare-agent.log` sur l'hôte. Journal de Phare :
  `journalctl -u phare` dans le CT.
- « Hôte refusé » (code 421) : ajoute le nom ou l'IP que tu tapes dans le navigateur à
  `PHARE_HOTES` dans `/etc/phare/phare.env`, puis `systemctl restart phare`.
- Le DNS d'un nouveau domaine n'est jamais « bon » : règle `PHARE_IP_PUBLIQUE` (l'IP publique de
  ta box) dans `/etc/phare/phare.env`.

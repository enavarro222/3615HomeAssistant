# Changelog

## 0.5.0

- **Les courbes défilent** : le point le plus récent est toujours à droite, contre un axe qui porte l'échelle, et la durée couverte est écrite en dessous. Au plus un point toutes les 5 s.
- **Les jauges** prennent la largeur de l'écran, avec la valeur en double taille et les bornes de la carte, et leur entité est relevée toutes les 10 s.

## 0.4.0

- **Courbes en mosaïque** : les cartes de graphique (Capteur avec graphique, Graphique d'historique, `mini-graph-card`…) deviennent des courbes, tirées de l'historique, qui avancent en direct comme un oscilloscope. Une courbe courte fait relever son entité à chaque point, jusqu'à une fois par seconde.
- L'option `pages` (des lumières écrites à la main) disparaît : le tableau de bord la remplace. Une ancienne valeur est ignorée.

## 0.3.0

- **Tableaux de bord** : le Minitel affiche un tableau de bord de Home Assistant, choisi dans l'option `dashboard`, ou celui par défaut. Ses vues forment le sommaire, ses cartes deviennent des lignes : lumières, interrupteurs, volets, thermostats, serrures, scènes, capteurs. Sans tableau de bord lisible, une page par pièce.
- États **en direct** par l'API WebSocket de Home Assistant : seules les lignes qui changent sont redessinées.
- **Numéro + SUITE / RETOUR** : plus ou moins de lumière, de chauffage, d'ouverture de volet.
- Les sections et cartes à visibilité conditionnelle (une porte ouverte…) apparaissent et disparaissent sur le Minitel.
- **GUIDE** affiche l'aide des touches. La rangée du haut signale quand Home Assistant ne répond plus.

## 0.2.0

- Panneau **Minitel** dans la barre latérale : un Minitel dans le navigateur pour tester sans matériel. L'appel passe par SIP, Asterisk et le softmodem, avec un modem V.23 logiciel, comme le vrai.
- Compte SIP à part pour le panneau, limité à la machine, au mot de passe tiré à chaque démarrage.
- Jusqu'à 4 adaptateurs enregistrés en même temps sur le compte `minitel`, un par Minitel.

## 0.1.0

Première version.

- Asterisk 20 avec `app_softmodem` (V.23, 7E1) et le service Vidéotex dans un seul conteneur.
- Pages de lumières configurables depuis l'interface, 16 lumières par page au plus.
- Basculement des entités `light` et `switch`, états rafraîchis à chaque page.
- Enregistrement SIP limité au réseau local, détecté automatiquement.
- Testé avec un Minitel 1 Alcatel et un Cisco SPA112.

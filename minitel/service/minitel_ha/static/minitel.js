// Un Minitel dans le navigateur : l'écran Vidéotex (40 × 24 plus la ligne de
// service) et le clavier. L'appel, lui, part de l'app par SIP, comme le vrai.
"use strict";

const COLS = 40;
const ROWS = 25; // la rangée 0 est la ligne de service
const PX = 4; // une case du Minitel fait 8 × 10 points, dessinés ici en 4 × 4
const CW = 8 * PX;
const CH = 10 * PX;

// Le Minitel 1 est monochrome : chaque couleur devient un niveau de gris du
// phosphore, un blanc légèrement bleuté. Codes Vidéotex : noir, rouge, vert,
// jaune, bleu, magenta, cyan, blanc.
const GRAY = [0, 0.5, 0.7, 0.9, 0.4, 0.6, 0.8, 1];
const shade = (level) => `rgb(${Math.round(214 * level)}, ${Math.round(234 * level)}, ${Math.round(240 * level)})`;

// Police matricielle 5 × 7 dans la case 8 × 10 : majuscules sur les rangées 1
// à 7, jambages sur 8 et 9. Une chaîne par caractère, une rangée par mot.
const FONT = {
  " ": "", "!": "..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..", '"': ".#.#. .#.#. .#.#.",
  "#": ".#.#. .#.#. ##### .#.#. ##### .#.#. .#.#.", "$": "..#.. .#### #.#.. .###. ..#.# ####. ..#..",
  "%": "##... ##..# ...#. ..#.. .#... #..## ...##", "&": ".##.. #..#. #.#.. .#... #.#.# #..#. .##.#",
  "'": "..#.. ..#.. .#...", "(": "...#. ..#.. .#... .#... .#... ..#.. ...#.",
  ")": ".#... ..#.. ...#. ...#. ...#. ..#.. .#...", "*": "..... ..#.. #.#.# .###. #.#.# ..#.. .....",
  "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....", ",": "..... ..... ..... ..... .##.. ..#.. .#...",
  "-": "..... ..... ..... ##### ..... ..... .....", ".": "..... ..... ..... ..... ..... .##.. .##..",
  "/": "..... ....# ...#. ..#.. .#... #.... .....",
  "0": ".###. #...# #..## #.#.# ##..# #...# .###.", "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
  "2": ".###. #...# ....# ...#. ..#.. .#... #####", "3": "##### ...#. ..#.. ...#. ....# #...# .###.",
  "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.", "5": "##### #.... ####. ....# ....# #...# .###.",
  "6": "..##. .#... #.... ####. #...# #...# .###.", "7": "##### ....# ...#. ..#.. .#... .#... .#...",
  "8": ".###. #...# #...# .###. #...# #...# .###.", "9": ".###. #...# #...# .#### ....# ...#. .##..",
  ":": "..... .##.. .##.. ..... .##.. .##.. .....", ";": "..... .##.. .##.. ..... .##.. ..#.. .#...",
  "<": "...#. ..#.. .#... #.... .#... ..#.. ...#.", "=": "..... ..... ##### ..... ##### ..... .....",
  ">": ".#... ..#.. ...#. ....# ...#. ..#.. .#...", "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
  "@": ".###. #...# ....# .##.# #.#.# #.#.# .###.",
  A: ".###. #...# #...# ##### #...# #...# #...#", B: "####. #...# #...# ####. #...# #...# ####.",
  C: ".###. #...# #.... #.... #.... #...# .###.", D: "###.. #..#. #...# #...# #...# #..#. ###..",
  E: "##### #.... #.... ####. #.... #.... #####", F: "##### #.... #.... ####. #.... #.... #....",
  G: ".###. #...# #.... #.### #...# #...# .####", H: "#...# #...# #...# ##### #...# #...# #...#",
  I: ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.", J: "..### ...#. ...#. ...#. ...#. #..#. .##..",
  K: "#...# #..#. #.#.. ##... #.#.. #..#. #...#", L: "#.... #.... #.... #.... #.... #.... #####",
  M: "#...# ##.## #.#.# #.#.# #...# #...# #...#", N: "#...# #...# ##..# #.#.# #..## #...# #...#",
  O: ".###. #...# #...# #...# #...# #...# .###.", P: "####. #...# #...# ####. #.... #.... #....",
  Q: ".###. #...# #...# #...# #.#.# #..#. .##.#", R: "####. #...# #...# ####. #.#.. #..#. #...#",
  S: ".#### #.... #.... .###. ....# ....# ####.", T: "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
  U: "#...# #...# #...# #...# #...# #...# .###.", V: "#...# #...# #...# #...# #...# .#.#. ..#..",
  W: "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.", X: "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
  Y: "#...# #...# #...# .#.#. ..#.. ..#.. ..#..", Z: "##### ....# ...#. ..#.. .#... #.... #####",
  "[": ".###. .#... .#... .#... .#... .#... .###.", "\\": "..... #.... .#... ..#.. ...#. ....# .....",
  "]": ".###. ...#. ...#. ...#. ...#. ...#. .###.", "^": "..#.. .#.#. #...#",
  _: "..... ..... ..... ..... ..... ..... #####", "`": ".#... ..#.. ...#.",
  a: "..... ..... .###. ....# .#### #...# .####", b: "#.... #.... #.##. ##..# #...# #...# ####.",
  c: "..... ..... .###. #.... #.... #...# .###.", d: "....# ....# .##.# #..## #...# #...# .####",
  e: "..... ..... .###. #...# ##### #.... .###.", f: "..##. .#..# .#... ###.. .#... .#... .#...",
  g: "..... ..... .#### #...# #...# #...# .#### ....# .###.", h: "#.... #.... #.##. ##..# #...# #...# #...#",
  i: "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.", j: "...#. ..... ..##. ...#. ...#. ...#. ...#. #..#. .##..",
  k: "#.... #.... #..#. #.#.. ##... #.#.. #..#.", l: ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
  m: "..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#", n: "..... ..... #.##. ##..# #...# #...# #...#",
  o: "..... ..... .###. #...# #...# #...# .###.", p: "..... ..... ####. #...# #...# #...# ####. #.... #....",
  q: "..... ..... .#### #...# #...# #...# .#### ....# ....#", r: "..... ..... #.##. ##..# #.... #.... #....",
  s: "..... ..... .###. #.... .###. ....# ####.", t: ".#... .#... ###.. .#... .#... .#..# ..##.",
  u: "..... ..... #...# #...# #...# #..## .##.#", v: "..... ..... #...# #...# #...# .#.#. ..#..",
  w: "..... ..... #...# #...# #.#.# #.#.# .#.#.", x: "..... ..... #...# .#.#. ..#.. .#.#. #...#",
  y: "..... ..... #...# #...# #...# #...# .#### ....# .###.", z: "..... ..... ##### ...#. ..#.. .#... #####",
  "{": "...#. ..#.. ..#.. .#... ..#.. ..#.. ...#.", "|": "..#.. ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
  "}": ".#... ..#.. ..#.. ...#. ..#.. ..#.. .#...", "~": "..... ..... .#... #.#.# ...#.",
  "£": "..##. .#..# .#... ###.. .#... .#..# #####", "§": ".###. #.... .###. #...# .###. ....# .###.",
  "°": ".##.. #..#. #..#. .##..", "±": "..#.. ..#.. ##### ..#.. ..#.. ..... #####",
  "÷": "..... ..#.. ..... ##### ..... ..#.. .....", "ß": ".##.. #..#. #..#. #.#.. #..#. #..#. #.##.",
  "¼": "#.... #...# #..#. ..#.. .#.#. #.### ...#.", "½": "#.... #...# #..#. ..#.. .#.## #...# ...##",
  "¾": "##... .#..# ##.#. ..#.. .#.#. #.### ...#.", Œ: ".#### #.#.. #.#.. #.### #.#.. #.#.. .####",
  œ: "..... ..... .#.#. #.#.# #.### #.#.. .#.##", "←": "..... ..#.. .#... ##### .#... ..#.. .....",
  "↑": "..#.. .###. #.#.# ..#.. ..#.. ..#.. .....", "→": "..... ..#.. ...#. ##### ...#. ..#.. .....",
  "↓": "..#.. ..#.. ..#.. #.#.# .###. ..#.. .....",
};

// Les accents se posent sur la lettre, comme sur le Minitel : rangées 1 et 2
// pour ceux du dessus, 8 et 9 pour la cédille.
const ACCENT_ROWS = {
  "̀": [1, ".#... ..#.."], "́": [1, "...#. ..#.."], "̂": [1, "..#.. .#.#."],
  "̈": [1, ".#.#. ....."], "̧": [8, "..#.. .##.."],
};

function rowBits(row) {
  let bits = 0;
  for (let c = 0; c < 5; c++) if (row[c] === "#") bits |= 1 << (6 - c); // colonnes 1 à 5 de la case
  return bits;
}

const glyphCache = new Map();

function glyph(ch) {
  if (glyphCache.has(ch)) return glyphCache.get(ch);
  let rows = new Uint8Array(10);
  if (ch === "█") {
    rows.fill(0xff);
  } else if (ch in FONT) {
    FONT[ch].split(" ").filter(Boolean).forEach((row, i) => { rows[1 + i] = rowBits(row); });
  } else {
    const [base, ...marks] = ch.normalize("NFD");
    rows = base in FONT && marks.length ? Uint8Array.from(glyph(base)) : Uint8Array.from(glyph("?"));
    for (const mark of marks) {
      const accent = ACCENT_ROWS[mark];
      if (!accent) continue;
      const [first, pattern] = accent;
      pattern.split(" ").forEach((row, i) => {
        if (first === 1) rows[first + i] = rowBits(row); // remplace le point du i
        else rows[first + i] |= rowBits(row);
      });
    }
  }
  glyphCache.set(ch, rows);
  return rows;
}

// Jeu G2, après SS2 : accents (suivis de la lettre) et caractères spéciaux.
const ACCENTS = { 0x41: "̀", 0x42: "́", 0x43: "̂", 0x48: "̈", 0x4b: "̧" };
const G2 = {
  0x23: "£", 0x24: "$", 0x26: "#", 0x27: "§", 0x2c: "←", 0x2d: "↑", 0x2e: "→", 0x2f: "↓",
  0x30: "°", 0x31: "±", 0x38: "÷", 0x3c: "¼", 0x3d: "½", 0x3e: "¾", 0x6a: "Œ", 0x7a: "œ", 0x7b: "ß",
};

const SEP = 0x13;
const KEYS = {
  ENVOI: 0x41, RETOUR: 0x42, REPETITION: 0x43, GUIDE: 0x44,
  ANNULATION: 0x45, SOMMAIRE: 0x46, CORRECTION: 0x47, SUITE: 0x48,
};

class Videotex {
  constructor(canvas) {
    this.ctx = canvas.getContext("2d");
    this.indicator = "F"; // F hors ligne, C connecté, comme en haut à droite du vrai
    this.connecting = false;
    this.blinkOn = true;
    this.onDisconnect = null;
    this.clearAll();
    setInterval(() => { this.blinkOn = !this.blinkOn; this.draw(); }, 500);
  }

  blank(bg = 0) {
    return { ch: " ", fg: 7, bg, inv: false, size: 0, mosaic: false, blink: false, cover: false };
  }

  clearAll() {
    this.cells = Array.from({ length: ROWS }, () => Array.from({ length: COLS + 1 }, () => this.blank()));
    this.row = 1;
    this.col = 1;
    this.cursor = false;
    this.next = null; // suite d'une séquence de plusieurs octets
    this.last = null;
    this.resetAttributes();
    this.draw();
  }

  resetAttributes() {
    this.fg = 7;
    this.bg = 0;
    this.inv = false;
    this.size = 0; // 0 normale, 1 double hauteur, 2 double largeur, 3 double taille
    this.blink = false;
    this.g1 = false;
  }

  feed(bytes) {
    for (const b of bytes) this.byte(b & 0x7f);
    this.draw();
  }

  byte(b) {
    if (this.next) {
      const next = this.next;
      this.next = null;
      next(b);
      return;
    }
    switch (b) {
      case 0x08: this.left(); break; // BS
      case 0x09: this.advance(1); break; // HT
      case 0x0a: this.row = this.row === 0 ? 1 : (this.row % 24) + 1; break; // LF
      case 0x0b: this.row = this.row > 1 ? this.row - 1 : 24; break; // VT
      case 0x0c: this.formFeed(); break; // FF
      case 0x0d: this.col = 1; break; // CR
      case 0x0e: this.g1 = true; break; // SO : mosaïques
      case 0x0f: this.g1 = false; break; // SI
      case 0x11: this.cursor = true; break; // CON
      case 0x14: this.cursor = false; break; // COFF
      case 0x12: this.next = (n) => this.repeat(n - 0x40); break; // REP
      case 0x13: this.next = () => {}; break; // SEP + code : rien à afficher
      case 0x18: this.cancel(); break; // CAN
      case 0x19: this.next = (c) => this.ss2(c); break; // SS2
      case 0x1b: this.next = (c) => this.escape(c); break; // ESC
      case 0x1e: this.row = 1; this.col = 1; this.resetAttributes(); break; // RS
      case 0x1f: this.next = (r) => this.position(r); break; // US
      default:
        if (b >= 0x20) this.printable(b);
    }
  }

  position(r) {
    if (r >= 0x30 && r <= 0x32) { // forme décimale : US 3x 3x 3x 3x
      this.next = (r2) => { this.next = (c1) => { this.next = (c2) => {
        this.moveTo((r - 0x30) * 10 + r2 - 0x30, (c1 - 0x30) * 10 + c2 - 0x30);
      }; }; };
      return;
    }
    this.next = (c) => this.moveTo(r - 0x40, c - 0x40);
  }

  moveTo(row, col) {
    if (row >= 0 && row < ROWS && col >= 1 && col <= COLS) {
      this.row = row;
      this.col = col;
    }
    this.resetAttributes(); // le positionnement remet les attributs par défaut
  }

  escape(c) {
    if (c >= 0x40 && c <= 0x47) this.fg = c - 0x40;
    else if (c >= 0x50 && c <= 0x57) this.bg = c - 0x50;
    else if (c === 0x48) this.blink = true;
    else if (c === 0x49) this.blink = false;
    else if (c >= 0x4c && c <= 0x4f) this.size = c - 0x4c;
    else if (c === 0x5c) this.inv = false;
    else if (c === 0x5d) this.inv = true;
    else if (c === 0x39) this.next = (p) => { if (p === 0x67 && this.onDisconnect) this.onDisconnect(); }; // PRO1
    else if (c === 0x3a) this.skip(2); // PRO2
    else if (c === 0x3b) this.skip(3); // PRO3
    else if (c === 0x23) this.skip(2);
    else if (c === 0x5b) this.next = (p) => this.csi(p);
  }

  csi(p) {
    if (p < 0x40 || p > 0x7e) this.next = (q) => this.csi(q);
  }

  skip(n) {
    if (n > 0) this.next = () => this.skip(n - 1);
  }

  ss2(c) {
    if (ACCENTS[c]) {
      this.next = (letter) => this.put((String.fromCharCode(letter) + ACCENTS[c]).normalize("NFC"));
    } else {
      this.put(G2[c] || "?");
    }
  }

  printable(b) {
    if (this.g1 && (b < 0x40 || b >= 0x60)) {
      this.put((b & 0x1f) | (b & 0x40 ? 0x20 : 0), true);
    } else if (b === 0x7f) {
      this.put("█");
    } else {
      this.put(String.fromCharCode(b));
    }
  }

  put(ch, mosaic = false) {
    const size = mosaic ? 0 : this.size;
    const wide = size & 2 ? 2 : 1;
    const row = this.row;
    const col = this.col;
    this.cells[row][col] = { ch, fg: this.fg, bg: this.bg, inv: this.inv, size, mosaic, blink: this.blink, cover: false };
    for (let dc = 0; dc < wide; dc++) {
      if (dc > 0 && col + dc <= COLS) this.cells[row][col + dc] = { ...this.blank(this.bg), cover: true };
      if (size & 1 && row > 1 && col + dc <= COLS) this.cells[row - 1][col + dc] = { ...this.blank(this.bg), cover: true };
    }
    this.last = { ch, mosaic };
    this.advance(wide);
  }

  repeat(n) {
    if (!this.last) return;
    for (let i = 0; i < n; i++) this.put(this.last.ch, this.last.mosaic);
  }

  advance(n) {
    this.col += n;
    if (this.col > COLS) {
      this.col = 1;
      if (this.row > 0) this.row = (this.row % 24) + 1;
    }
  }

  left() {
    if (this.col > 1) { this.col--; return; }
    this.col = COLS;
    if (this.row > 0) this.row = this.row > 1 ? this.row - 1 : 24;
  }

  formFeed() {
    for (let r = 1; r < ROWS; r++) {
      for (let c = 1; c <= COLS; c++) this.cells[r][c] = this.blank();
    }
    this.row = 1;
    this.col = 1;
    this.resetAttributes();
  }

  cancel() {
    for (let c = this.col; c <= COLS; c++) this.cells[this.row][c] = this.blank(this.bg);
  }

  draw() {
    const ctx = this.ctx;
    // Fond transparent : le noir du tube est en CSS, et la lueur du phosphore
    // (drop-shadow) ne s'applique ainsi qu'aux points allumés.
    ctx.clearRect(0, 0, COLS * CW, ROWS * CH);
    for (let r = 0; r < ROWS; r++) {
      for (let c = 1; c <= COLS; c++) {
        const cell = this.cells[r][c];
        if (!cell.cover) this.drawCell(r, c, cell);
      }
    }
    if (this.cursor && this.blinkOn && this.indicator === "C") {
      ctx.fillStyle = shade(1);
      ctx.fillRect((this.col - 1) * CW + PX, this.row * CH + 9 * PX, 6 * PX, PX);
    }
    if (this.indicator === "F" || !this.connecting || this.blinkOn) {
      this.drawCell(0, COLS, { ...this.blank(), ch: this.indicator });
    }
  }

  drawCell(r, c, cell) {
    const ctx = this.ctx;
    const wide = cell.size & 2 ? 2 : 1;
    const tall = cell.size & 1 ? 2 : 1;
    const x = (c - 1) * CW;
    const top = tall === 2 && r > 1 ? (r - 1) * CH : r * CH;
    const fg = shade(GRAY[cell.inv ? cell.bg : cell.fg]);
    const bg = GRAY[cell.inv ? cell.fg : cell.bg];
    if (bg > 0) {
      ctx.fillStyle = shade(bg);
      ctx.fillRect(x, top, CW * wide, CH * tall);
    }
    if (cell.blink && !this.blinkOn) return;
    ctx.fillStyle = fg;
    if (cell.mosaic) {
      const heights = [13, 14, 13];
      let y = top;
      for (let part = 0; part < 3; part++) {
        if (cell.ch & (1 << (part * 2))) ctx.fillRect(x, y, CW / 2, heights[part]);
        if (cell.ch & (1 << (part * 2 + 1))) ctx.fillRect(x + CW / 2, y, CW / 2, heights[part]);
        y += heights[part];
      }
      return;
    }
    if (cell.ch === " ") return;
    const rows = glyph(cell.ch);
    const w = PX * wide;
    const h = PX * tall;
    for (let gy = 0; gy < 10; gy++) {
      const bits = rows[gy];
      if (!bits) continue;
      for (let gx = 0; gx < 8; gx++) {
        if (bits & (0x80 >> gx)) ctx.fillRect(x + gx * w, top + gy * h, w, h);
      }
    }
  }
}

// --- La liaison avec l'app -------------------------------------------------

const display = new Videotex(document.getElementById("screen"));
const statusLine = document.getElementById("status");
const STATUS = {
  appel: "Numérotation du 3615, puis attente de la réponse d'Asterisk…",
  porteuse: "Asterisk a décroché : attente de la porteuse du softmodem…",
  connecte: "En ligne : porteuse V.23 reçue.",
  fin: "Raccroché.",
};

let socket = null;
let online = false; // de la demande de connexion jusqu'au raccrochage

function setStatus(text, error = false) {
  statusLine.textContent = text;
  statusLine.classList.toggle("error", error);
}

function setLine(state) {
  online = state !== "fin";
  display.indicator = state === "fin" ? "F" : "C";
  display.connecting = state === "appel" || state === "porteuse";
  display.draw();
}

function openSocket() {
  const base = location.pathname.endsWith("/") ? location.pathname : location.pathname + "/";
  socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${base}ws`);
  socket.binaryType = "arraybuffer";
  socket.onmessage = (event) => {
    if (typeof event.data !== "string") {
      display.feed(new Uint8Array(event.data));
      return;
    }
    const message = JSON.parse(event.data);
    if (message.error) setStatus(message.error, true);
    if (message.status) {
      setLine(message.status);
      if (!(message.status === "fin" && statusLine.classList.contains("error"))) {
        setStatus(STATUS[message.status] || message.status);
      }
    }
  };
  socket.onclose = () => {
    if (online) setLine("fin");
    setStatus("Panneau déconnecté de l'app, nouvelle tentative…", true);
    setTimeout(openSocket, 3000);
  };
  socket.onopen = () => {
    if (statusLine.classList.contains("error")) setStatus("Appuyez sur Connexion/Fin pour appeler le 3615.");
  };
}

function send(bytes) {
  if (online && socket && socket.readyState === WebSocket.OPEN) socket.send(new Uint8Array(bytes));
}

function connexionFin() {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  if (online) {
    socket.send(JSON.stringify({ action: "fin" }));
  } else {
    setStatus(STATUS.appel);
    setLine("appel");
    socket.send(JSON.stringify({ action: "connexion" }));
  }
}

// Le service demande de libérer la ligne (PRO1 + déconnexion) : le Minitel raccroche.
display.onDisconnect = () => { if (online) socket.send(JSON.stringify({ action: "fin" })); };

function pressKey(name) {
  send([SEP, KEYS[name]]);
}

function flash(button) {
  if (!button) return;
  button.classList.add("pressed");
  setTimeout(() => button.classList.remove("pressed"), 120);
}

document.querySelectorAll("[data-char]").forEach((button) => {
  button.addEventListener("click", () => send([button.dataset.char.charCodeAt(0)]));
});
document.querySelectorAll("[data-code]").forEach((button) => {
  button.addEventListener("click", () => send([Number(button.dataset.code)]));
});
document.querySelectorAll("[data-key]").forEach((button) => {
  button.addEventListener("click", () => pressKey(button.dataset.key));
});
document.getElementById("connexion").addEventListener("click", connexionFin);

const PHYSICAL = {
  Enter: "ENVOI", Backspace: "CORRECTION", Escape: "ANNULATION", Delete: "ANNULATION",
  PageDown: "SUITE", PageUp: "RETOUR", Home: "SOMMAIRE", F1: "GUIDE", F2: "REPETITION",
};

document.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.key === "F10") {
    event.preventDefault();
    flash(document.getElementById("connexion"));
    connexionFin();
    return;
  }
  const name = PHYSICAL[event.key];
  if (name) {
    event.preventDefault();
    flash(document.querySelector(`[data-key="${name}"]`));
    pressKey(name);
    return;
  }
  if (event.key.length === 1) {
    const upper = event.key.toUpperCase(); // le Minitel tape en majuscules
    const code = upper.charCodeAt(0);
    if (code >= 0x20 && code < 0x7f) {
      event.preventDefault();
      flash(document.querySelector(`[data-char="${CSS.escape(upper)}"]`));
      send([code]);
    }
  }
});

openSocket();

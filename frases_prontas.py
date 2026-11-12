#!/usr/bin/env python3
"""Frases Prontas - copie frases salvas com um clique (Linux e Windows).

Linux - dependências: python3 e python3-tk
  Debian/Ubuntu/Mint: sudo apt install python3 python3-tk
  Fedora:             sudo dnf install python3-tkinter
  Arch:               sudo pacman -S tk

Linux - opcionais:
  xclip ou wl-clipboard  (mantém o texto copiado após fechar o app)
  xrandr                 (detecta vários monitores)

Windows: só o Python oficial (já inclui o Tkinter). Nada mais a instalar.

Ícone da janela (opcional): coloque "icone.png" (Linux) ou "icone.ico" (Windows)
na mesma pasta do script.
"""
import json
import colorsys
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

DEBUG = bool(os.environ.get("FRASES_DEBUG"))      # FRASES_DEBUG=1 mostra o que decide recolher

LINUX = sys.platform.startswith("linux")
WINDOWS = sys.platform.startswith("win")

if WINDOWS:
    _base_cfg = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming")
    CONFIG_DIR = os.path.join(_base_cfg, "FrasesProntas")
else:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "frases-prontas")

ARQUIVO = os.path.join(CONFIG_DIR, "frases.json")
ARQUIVO_CFG = os.path.join(CONFIG_DIR, "config.json")

TEMAS = {
    "claro": dict(bg="#f3f4f8", card="#ffffff", borda="#e2e5ec", texto="#1f2933",
                  suave="#6b7785", acento="#4f6df5", acento_hover="#3b57e0",
                  acento_txt="#ffffff", chip="#e6e9f0", ok="#2e9e5b", perigo="#d64545",
                  alerta="#fde4e4"),
    "escuro": dict(bg="#15171c", card="#20232a", borda="#2f333c", texto="#e8eaed",
                   suave="#9aa3b0", acento="#7c93ff", acento_hover="#9aabff",
                   acento_txt="#10131a", chip="#2a2e37", ok="#4cc38a", perigo="#ff6b6b",
                   alerta="#4d2428"),
}

# Tamanho e posição da janela principal
LARGURA, ALTURA = 360, 520
MARGEM = 12
CANTO = "meio-direito"        # opções: meio-direito, meio-esquerdo, inferior-direito, inferior-esquerdo,
                              #         superior-direito, superior-esquerdo

# Clipboard (Linux): True = usa xclip/wl-copy quando existirem (o texto sobrevive ao fechar o app)
#                    False = usa só o clipboard do Tkinter (o texto some ao fechar o app)
USAR_CLIPBOARD_EXTERNO = True

# Tempo mínimo padrão entre duas cópias (em segundos). Pode ser desativado nas Configurações.
INTERVALO_COPIA = 1.5

# Barra lateral (quando o app não está fixado e perde o foco)
RECOLHER_AUTOMATICO = True    # False = nunca recolhe
INTERVALO_VIGIA = 50          # ms entre checagens do foco (menor = recolhe mais rápido)
ESPERA_FOCUS_OUT = 60         # ms de espera após o evento FocusOut antes de confirmar
LARGURA_ABA = 12              # largura da barra, em pixels
ALTURA_ABA = 120              # altura da barra, em pixels

EXEMPLOS = [
    {"categoria": "Saudações", "texto": "Olá! Tudo bem? Como posso ajudar?"},
    {"categoria": "Saudações", "texto": "Obrigado pelo contato! Retorno em breve."},
    {"categoria": "Atendimento", "texto": "Um momento, por favor, vou verificar essa informação para você."},
    {"categoria": "Atendimento", "texto": "Fico à disposição para qualquer dúvida."},
    {"categoria": "Email", "texto": "Atenciosamente,\nSeu Nome"},
]


# ---------- arquivos (leitura/gravação seguras) ----------
def _ler(caminho, padrao):
    """Lê o JSON. Se estiver corrompido, guarda uma cópia e tenta o backup (.bak)."""
    for tentativa in (caminho, caminho + ".bak"):
        try:
            with open(tentativa, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            if tentativa == caminho:
                try:
                    os.replace(caminho, caminho + ".corrompido")
                except OSError:
                    pass
            continue
    return padrao


def _gravar(caminho, dados):
    """Grava em arquivo temporário e troca de uma vez: não deixa o JSON pela metade."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(caminho):
        try:
            shutil.copy2(caminho, caminho + ".bak")
        except OSError:
            pass
    os.replace(tmp, caminho)


def escolher_fonte(root):
    disponiveis = set(tkfont.families(root))
    for nome in ("Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell", "Open Sans", "DejaVu Sans"):
        if nome in disponiveis:
            return nome
    return tkfont.nametofont("TkDefaultFont").actual("family")


# ---------- área de transferência ----------
_WIN_API = None


def _api_windows():
    """Carrega (uma vez) as funções do Windows com os tipos corretos (32/64 bits)."""
    global _WIN_API
    if _WIN_API is None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.restype = wintypes.BOOL
        k32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        k32.GlobalFree.restype = wintypes.HGLOBAL
        u32.OpenClipboard.argtypes = [wintypes.HWND]
        u32.OpenClipboard.restype = wintypes.BOOL
        u32.CloseClipboard.argtypes = []
        u32.CloseClipboard.restype = wintypes.BOOL
        u32.EmptyClipboard.argtypes = []
        u32.EmptyClipboard.restype = wintypes.BOOL
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.GetClipboardData.argtypes = [wintypes.UINT]
        u32.GetClipboardData.restype = wintypes.HANDLE
        _WIN_API = (k32, u32)
    return _WIN_API


def _ler_utf16(ptr):
    """Lê uma string UTF-16 terminada em zero a partir de um endereço de memória."""
    import ctypes
    unidades = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint16))
    n = 0
    while unidades[n]:
        n += 1
    return ctypes.string_at(ptr, n * 2).decode("utf-16-le", "replace")


def copiar_windows(texto, hwnd, tentativas=40):
    """Coloca o texto no clipboard do Windows. Se o clipboard estiver ocupado,
    tenta de novo; só devolve True depois de conferir que o texto gravado é o certo."""
    import ctypes
    k32, u32 = _api_windows()
    cf_unicodetext, gmem_moveable = 13, 0x0002
    esperado = texto.replace("\r\n", "\n").replace("\n", "\r\n")
    dados = (esperado + "\0").encode("utf-16-le")

    for _ in range(tentativas):
        if u32.OpenClipboard(hwnd):
            try:
                h = k32.GlobalAlloc(gmem_moveable, len(dados))
                if h:
                    ptr = k32.GlobalLock(h)
                    if ptr:
                        ctypes.memmove(ptr, dados, len(dados))
                        k32.GlobalUnlock(h)
                        u32.EmptyClipboard()
                        if u32.SetClipboardData(cf_unicodetext, h):
                            h = None            # agora o Windows é o dono do bloco
                            atual = u32.GetClipboardData(cf_unicodetext)
                            p2 = k32.GlobalLock(atual) if atual else None
                            if p2:
                                try:
                                    if _ler_utf16(p2) == esperado:
                                        return True
                                finally:
                                    k32.GlobalUnlock(atual)
                    if h:
                        k32.GlobalFree(h)
            finally:
                u32.CloseClipboard()
        time.sleep(0.015)
    return False


class ClipboardExterno:
    """Linux: mantém UM processo xclip/wl-copy em primeiro plano segurando a seleção.
    Antes de copiar de novo, o anterior é encerrado e aguardado, então nunca existem
    dois donos disputando o clipboard e a última cópia é sempre a que vale."""

    def __init__(self):
        self._proc = None

    def _encerrar(self):
        p, self._proc = self._proc, None
        if p is None or p.poll() is not None:
            return
        try:
            p.terminate()
            p.wait(timeout=0.5)
        except Exception:
            try:
                p.kill()
                p.wait(timeout=0.5)
            except Exception:
                pass

    def copiar(self, texto):
        self._encerrar()
        xclip = ["xclip", "-selection", "clipboard", "-quiet"]   # -quiet = fica em primeiro plano
        wlcopy = ["wl-copy", "--foreground"]
        cmds = [wlcopy, xclip] if os.environ.get("WAYLAND_DISPLAY") else [xclip, wlcopy]

        for cmd in cmds:
            if not shutil.which(cmd[0]):
                continue
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                p.stdin.write(texto.encode("utf-8"))
                p.stdin.close()
            except Exception:
                continue
            try:
                codigo = p.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                self._proc = p           # continua rodando = está segurando a seleção
                return True
            if codigo == 0:
                time.sleep(0.08)         # versão que foi para segundo plano: dá tempo de assumir
                return True
        return False


# ---------- monitores ----------
def _monitores_linux():
    mons = []
    if not shutil.which("xrandr"):
        return mons
    try:
        out = subprocess.run(["xrandr", "--query"], capture_output=True,
                             text=True, timeout=2).stdout
        for linha in out.splitlines():
            if " connected" in linha:
                m = re.search(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", linha)
                if m:
                    w, h, x, y = (int(v) for v in m.groups())
                    mons.append((x, y, w, h))
    except Exception:
        pass
    return mons


def _monitores_windows():
    mons = []
    try:
        import ctypes
        from ctypes import wintypes
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(wintypes.RECT), ctypes.c_void_p)

        def cb(hmon, hdc, rect, dados):
            r = rect.contents
            mons.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    except Exception:
        return []
    return mons


_MON = {"dados": None, "t": 0.0, "ocupado": False}


def _atualizar_monitores():
    try:
        mons = _monitores_windows() if WINDOWS else _monitores_linux()
    except Exception:
        mons = []
    if mons:
        _MON["dados"] = mons
    _MON["t"] = time.monotonic()
    _MON["ocupado"] = False


def precarregar_monitores():
    """Consulta os monitores em outra thread (o xrandr pode demorar segundos)."""
    if _MON["ocupado"]:
        return
    _MON["ocupado"] = True
    threading.Thread(target=_atualizar_monitores, daemon=True).start()


def listar_monitores(root):
    """Retorna [(x, y, largura, altura), ...] de cada monitor conectado.
    Nunca bloqueia: usa o último resultado conhecido e atualiza em segundo plano."""
    if time.monotonic() - _MON["t"] > 5:
        precarregar_monitores()
    mons = _MON["dados"]
    if not mons:
        mons = [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]
    return mons


def monitor_do_ponto(mons, px, py):
    """Monitor que contém o ponto (ou o mais próximo dele)."""
    melhor, menor = mons[0], None
    for m in mons:
        x, y, w, h = m
        if x <= px < x + w and y <= py < y + h:
            return m
        dx = max(x - px, 0, px - (x + w))
        dy = max(y - py, 0, py - (y + h))
        d = dx * dx + dy * dy
        if menor is None or d < menor:
            melhor, menor = m, d
    return melhor


# ---------- widgets auxiliares ----------
def botao(pai, texto, comando, bg, fg, hover, fonte, negrito=False, px=14, py=7):
    b = tk.Label(pai, text=texto, bg=bg, fg=fg, padx=px, pady=py, cursor="hand2",
                 font=(fonte, 10, "bold" if negrito else "normal"))
    b.bind("<Enter>", lambda e: b.config(bg=hover))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    b.bind("<Button-1>", lambda e: comando())
    return b


def retangulo_arredondado(canvas, x1, y1, x2, y2, r, **kw):
    """Desenha um retângulo com cantos arredondados no canvas."""
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
           x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
           x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
           x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def desenhar_icone(cv, tipo, ox, oy, s, cor, bg):
    """Desenha um ícone de contorno no canvas (idêntico em qualquer sistema).
    ox, oy = canto superior esquerdo; s = tamanho; cor = traço; bg = fundo atual."""
    def P(x, y):
        return ox + x * s, oy + y * s

    w = max(2, round(s / 8))

    if tipo in ("cadeado", "aberto"):
        retangulo_arredondado(cv, *P(.18, .46), *P(.82, .92), max(1, s * .08),
                              fill="", outline=cor, width=w)
        if tipo == "cadeado":
            cv.create_arc(*P(.3, .10), *P(.7, .62), start=0, extent=180,
                          style="arc", outline=cor, width=w)
            cv.create_line(*P(.3, .36), *P(.3, .48), fill=cor, width=w)
            cv.create_line(*P(.7, .36), *P(.7, .48), fill=cor, width=w)
        else:
            cv.create_arc(*P(.3, .0), *P(.7, .52), start=0, extent=180,
                          style="arc", outline=cor, width=w)
            cv.create_line(*P(.3, .26), *P(.3, .48), fill=cor, width=w)
        cv.create_oval(*P(.43, .62), *P(.57, .76), fill=cor, outline=cor)

    elif tipo == "lua":
        cv.create_oval(*P(.15, .15), *P(.85, .85), fill=cor, outline=cor)
        cv.create_oval(*P(.38, .04), *P(1.0, .68), fill=bg, outline=bg)

    elif tipo == "sol":
        # círculo com metade preenchida + 8 raios (estilo "contraste")
        cv.create_oval(*P(.28, .28), *P(.72, .72), outline=cor, width=w)
        cv.create_arc(*P(.28, .28), *P(.72, .72), start=90, extent=180,
                      fill=cor, outline=cor)
        for i in range(8):
            a = i * math.pi / 4
            cv.create_line(*P(.5 + .35 * math.cos(a), .5 + .35 * math.sin(a)),
                           *P(.5 + .49 * math.cos(a), .5 + .49 * math.sin(a)),
                           fill=cor, width=w, capstyle="round")

    elif tipo == "grip":
        r = .09
        for cx in (.33, .67):
            for cy in (.2, .5, .8):
                cv.create_oval(*P(cx - r, cy - r), *P(cx + r, cy + r), fill=cor, outline=cor)

    elif tipo == "engrenagem":
        pts = []
        dentes = 8
        for i in range(dentes):
            a = i * 2 * math.pi / dentes
            for da, rr in ((-.30, .34), (-.17, .48), (.17, .48), (.30, .34)):
                ang = a + da
                pts.extend(P(.5 + rr * math.cos(ang), .5 + rr * math.sin(ang)))
        fino = max(1, w - 1)
        cv.create_polygon(*pts, fill="", outline=cor, width=fino, joinstyle="round")
        cv.create_oval(*P(.37, .37), *P(.63, .63), outline=cor, width=fino)

    elif tipo == "lupa":
        cv.create_oval(*P(.12, .12), *P(.64, .64), outline=cor, width=w)
        cv.create_line(*P(.58, .58), *P(.88, .88), fill=cor, width=w, capstyle="round")


class IconeBotao(tk.Canvas):
    """Botão com ícone desenhado, com efeito de hover.
    Se 'borda' for informada, o fundo vira um quadrado de cantos arredondados."""

    def __init__(self, pai, tipo, comando, bg, fg, hover, tam=16, px=9, py=5,
                 cursor="hand2", borda=None, raio=8, bg_pai=None):
        larg, alt = tam + 2 * px, tam + 2 * py
        super().__init__(pai, width=larg, height=alt, bg=bg_pai or bg,
                         highlightthickness=0, bd=0, cursor=cursor)
        self._tipo, self._cmd = tipo, comando
        self._bg, self._fg, self._hover = bg, fg, hover
        self._tam, self._px, self._py = tam, px, py
        self._larg, self._alt = larg, alt
        self._borda, self._raio, self._bg_pai = borda, raio, bg_pai
        self._sobre = False
        self.bind("<Enter>", lambda e: self._set_sobre(True))
        self.bind("<Leave>", lambda e: self._set_sobre(False))
        self.bind("<Button-1>", lambda e: self._cmd())
        self._desenhar()

    def _set_sobre(self, valor):
        self._sobre = valor
        self._desenhar()

    def _desenhar(self):
        bg = self._hover if self._sobre else self._bg
        self.delete("all")
        if self._borda is not None:
            self.config(bg=self._bg_pai or self._bg)
            retangulo_arredondado(self, 1, 1, self._larg - 1, self._alt - 1, self._raio,
                                  fill=bg, outline=self._borda)
        else:
            self.config(bg=bg)
        desenhar_icone(self, self._tipo, self._px, self._py, self._tam, self._fg, bg)

    def configurar(self, tipo, bg, fg, hover, borda=None):
        self._tipo, self._bg, self._fg, self._hover = tipo, bg, fg, hover
        if borda is not None:
            self._borda = borda
        self._desenhar()


class BotaoArredondado(tk.Canvas):
    """Botão de texto com cantos arredondados e efeito de hover."""

    def __init__(self, pai, texto, comando, bg, fg, hover, fonte, bg_pai,
                 negrito=False, px=14, py=7, raio=8):
        fonte_obj = tkfont.Font(family=fonte, size=10, weight="bold" if negrito else "normal")
        larg = fonte_obj.measure(texto) + 2 * px
        alt = fonte_obj.metrics("linespace") + 2 * py
        super().__init__(pai, width=larg, height=alt, bg=bg_pai,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._fonte_obj = fonte_obj          # precisa ficar guardada, senão o Tk apaga a fonte
        self._texto, self._cmd = texto, comando
        self._bg, self._fg, self._hover = bg, fg, hover
        self._larg, self._alt, self._raio = larg, alt, raio
        self._sobre = False
        self.bind("<Enter>", lambda e: self._set_sobre(True))
        self.bind("<Leave>", lambda e: self._set_sobre(False))
        self.bind("<Button-1>", lambda e: self._cmd())
        self._desenhar()

    def _set_sobre(self, valor):
        self._sobre = valor
        self._desenhar()

    def _desenhar(self):
        self.delete("all")
        cor = self._hover if self._sobre else self._bg
        retangulo_arredondado(self, 1, 1, self._larg - 1, self._alt - 1, self._raio,
                              fill=cor, outline=cor)
        self.create_text(self._larg / 2, self._alt / 2, text=self._texto,
                         font=self._fonte_obj, fill=self._fg)


class Arredondado(tk.Canvas):
    """Moldura de cantos arredondados que cresce conforme o conteúdo.
    Coloque os widgets dentro de self.interno."""

    def __init__(self, pai, bg_pai, fill, borda, raio=9, pad=4, cursor=""):
        super().__init__(pai, width=1, height=2 * pad + 2, bg=bg_pai,
                         highlightthickness=0, bd=0, cursor=cursor)
        self._raio, self._pad = raio, pad
        self._cor_fill, self._cor_borda = fill, borda
        self._larg = self._alt = 0
        self._alt_pedida = 0
        self._forma = None
        # o 'pad' impede que o canto quadrado do conteúdo apareça fora da curva
        self.interno = tk.Frame(self, bg=fill, cursor=cursor)
        self._janela = self.create_window(pad, pad, window=self.interno, anchor="nw")
        self.bind("<Configure>", self._ao_canvas)
        self.interno.bind("<Configure>", self._ao_interno)

    def _ao_canvas(self, e):
        self._larg, self._alt = e.width, e.height
        self.itemconfigure(self._janela, width=max(1, e.width - 2 * self._pad))
        self.coords(self._janela, self._pad, self._pad)
        self._desenhar()

    def _ao_interno(self, e=None):
        h = self.interno.winfo_reqheight() + 2 * self._pad
        if h != self._alt_pedida:
            self._alt_pedida = h
            self.config(height=h)

    def _desenhar(self):
        if self._forma is not None:
            self.delete(self._forma)
            self._forma = None
        if self._larg < 4 or self._alt < 4:
            return
        self._forma = retangulo_arredondado(self, 1, 1, self._larg - 1, self._alt - 1,
                                            self._raio, fill=self._cor_fill,
                                            outline=self._cor_borda, width=1)
        self.tag_lower(self._forma)

    def definir(self, fill=None, borda=None):
        if fill is not None:
            self._cor_fill = fill
        if borda is not None:
            self._cor_borda = borda
        self.interno.config(bg=self._cor_fill)
        if self._forma is not None:
            self.itemconfigure(self._forma, fill=self._cor_fill, outline=self._cor_borda)


class Chip(tk.Canvas):
    """Categoria: texto simples quando inativa; pílula colorida com brilho quando ativa."""
    HALO = 3

    def __init__(self, pai, texto, fonte_n, fonte_b, cores, comando, px=11, py=5):
        h = self.HALO
        larg = fonte_b.measure(texto) + 2 * px + 2 * h     # sempre mede em negrito: não "pula"
        alt = fonte_b.metrics("linespace") + 2 * py + 2 * h
        super().__init__(pai, width=larg, height=alt, bg=cores["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._texto, self._fn, self._fb, self._cores = texto, fonte_n, fonte_b, cores
        self._larg, self._alt = larg, alt
        self._ativo = False
        self._sobre = False
        self.bind("<Enter>", lambda e: self._set_sobre(True))
        self.bind("<Leave>", lambda e: self._set_sobre(False))
        self.bind("<Button-1>", lambda e: comando())
        self._desenhar()

    def _set_sobre(self, valor):
        self._sobre = valor
        self._desenhar()

    def definir(self, ativo):
        self._ativo = ativo
        self._desenhar()

    def _desenhar(self):
        self.delete("all")
        w, h, hl, c = self._larg, self._alt, self.HALO, self._cores
        if self._ativo:
            retangulo_arredondado(self, 0, 0, w, h, 11, fill=c["halo"], outline="")
            retangulo_arredondado(self, hl, hl, w - hl, h - hl, 9, fill=c["acento"], outline="")
            self.create_text(w / 2, h / 2, text=self._texto, font=self._fb, fill=c["acento_txt"])
        else:
            if self._sobre:
                retangulo_arredondado(self, hl, hl, w - hl, h - hl, 9, fill=c["chip"], outline="")
            self.create_text(w / 2, h / 2, text=self._texto, font=self._fn, fill=c["texto"])


class CartaoFrase(tk.Canvas):
    """Cartão de frase desenhado em UM único Canvas (sem widgets filhos): bem mais leve
    para criar, mostrar e redimensionar do que um Frame com vários Labels.
    À direita fica o botão de três pontos (abre o menu Editar/Excluir)."""
    X_TEXTO = 34
    TOPO = 13

    def __init__(self, pai, cores, d, fonte, largura_inicial, ao_copiar, ao_arrastar, ao_menu):
        super().__init__(pai, width=1, height=40, bg=cores["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self._cores, self._fonte = cores, fonte
        self.d = d
        self.data_index = -1
        self.hover = False
        self.fundo_forcado = None       # usado pela animação de "tremer"
        self.borda_forcada = None       # usado pelo feedback de "copiado"
        self.tremendo = False
        self._ao_copiar, self._ao_arrastar, self._ao_menu = ao_copiar, ao_arrastar, ao_menu
        self._larg = 0
        self._h = 0
        self._alt_pedida = 0
        self._rect_menu = (0, 0, 0, 0)  # área clicável dos três pontos (x1, y1, x2, y2)
        self._sobre_menu = False
        self._cursor = "hand2"
        self._forma = None
        self._cor_fill, self._cor_borda = cores["card"], cores["borda"]
        self._cor_btn = misturar(cores["card_hover"], cores["texto"], 0.14)

        self._grip = [self.create_oval(0, 0, 0, 0, fill=cores["suave"], outline=cores["suave"])
                      for _ in range(6)]
        self._i_cat = self.create_text(0, 0, text=d["categoria"].upper(), anchor="nw",
                                       fill=cores["acento"], font=(fonte, 8, "bold"))
        self._i_txt = self.create_text(0, 0, text=d["texto"], anchor="nw", justify="left",
                                       fill=cores["texto"], font=(fonte, 11))
        # botão de três pontos: círculo de fundo (só no hover) + 3 pontinhos
        self._i_btn = self.create_oval(0, 0, 0, 0, fill=self._cor_btn, outline=self._cor_btn,
                                       state="hidden")
        self._pts = [self.create_oval(0, 0, 0, 0, fill=cores["suave"], outline=cores["suave"])
                     for _ in range(3)]

        h = self._calcular(largura_inicial)
        self._alt_pedida = h
        self.config(height=h)

        self.bind("<Configure>", self._ao_canvas)
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Motion>", self._movimento)
        self.bind("<Button-1>", self._clique)

    # ---- layout --------------------------------------------------------
    def _calcular(self, larg):
        """Posiciona tudo para a largura dada e devolve a altura necessária."""
        x_btn = larg - 22
        quebra = max(60, (x_btn - 13) - 8 - self.X_TEXTO)
        self.itemconfigure(self._i_txt, width=quebra)
        self.coords(self._i_cat, self.X_TEXTO, self.TOPO)
        y = self.bbox(self._i_cat)[3] + 2
        self.coords(self._i_txt, self.X_TEXTO, y)
        h = int(self.bbox(self._i_txt)[3] + self.TOPO)
        self._h = h
        ym = h / 2

        k = 0
        for gx in (12, 18):
            for gy in (-7, 0, 7):
                self.coords(self._grip[k], gx - 1.4, ym + gy - 1.4, gx + 1.4, ym + gy + 1.4)
                k += 1

        for k, dy in enumerate((-6, 0, 6)):
            self.coords(self._pts[k], x_btn - 1.8, ym + dy - 1.8, x_btn + 1.8, ym + dy + 1.8)
        self.coords(self._i_btn, x_btn - 13, ym - 13, x_btn + 13, ym + 13)
        self._rect_menu = (x_btn - 13, ym - 16, x_btn + 13, ym + 16)
        return h

    def _ao_canvas(self, e):
        self._larg = e.width
        h = self._calcular(e.width)
        if h != self._alt_pedida:
            self._alt_pedida = h
            self.config(height=h)
        self._desenhar_forma()

    def _desenhar_forma(self):
        if self._forma is not None:
            self.delete(self._forma)
            self._forma = None
        if self._larg < 4 or self._h < 4:
            return
        self._forma = retangulo_arredondado(self, 1, 1, self._larg - 1, self._h - 1, 9,
                                            fill=self._cor_fill, outline=self._cor_borda, width=1)
        self.tag_lower(self._forma)

    def posicao_menu(self):
        """Canto inferior direito do botão de três pontos, em coordenadas da tela."""
        x1, y1, x2, y2 = self._rect_menu
        return self.winfo_rootx() + x2, self.winfo_rooty() + y2, y2 - y1

    # ---- estado visual -------------------------------------------------
    def atualizar(self):
        """Pinta o cartão conforme o estado: normal, hover, copiado ou alerta."""
        c = self._cores
        self._cor_fill = self.fundo_forcado or (c["card_hover"] if self.hover else c["card"])
        self._cor_borda = self.borda_forcada or (c["borda_hover"] if self.hover else c["borda"])
        if self._forma is not None:
            self.itemconfigure(self._forma, fill=self._cor_fill, outline=self._cor_borda)

    def set_categoria(self, texto, cor):
        self.itemconfigure(self._i_cat, text=texto, fill=cor)

    def _set_hover(self, valor):
        if valor == self.hover:
            return
        self.hover = valor
        if not valor:
            self._pintar_menu(False)
            if self._cursor != "hand2":
                self._cursor = "hand2"
                self.config(cursor="hand2")
        self.atualizar()

    def _pintar_menu(self, sobre):
        if sobre == self._sobre_menu:
            return
        self._sobre_menu = sobre
        c = self._cores
        self.itemconfigure(self._i_btn, state="normal" if sobre else "hidden")
        for item in self._pts:
            cor = c["texto"] if sobre else c["suave"]
            self.itemconfigure(item, fill=cor, outline=cor)

    # ---- mouse ---------------------------------------------------------
    def _zona(self, x, y):
        if 4 <= x <= 28 and abs(y - self._h / 2) <= 15:      # só em volta dos pontinhos
            return "grip"
        x1, y1, x2, y2 = self._rect_menu
        if x1 <= x <= x2 and y1 <= y <= y2:
            return "menu"
        return None

    def _movimento(self, e):
        if e.state & 0x100:                 # botão pressionado (arrastando): ignora
            return
        if not self.hover:
            self._set_hover(True)
        zona = self._zona(e.x, e.y)
        cursor = "fleur" if zona == "grip" else "hand2"
        if cursor != self._cursor:
            self._cursor = cursor
            self.config(cursor=cursor)
        self._pintar_menu(zona == "menu")

    def _clique(self, e):
        zona = self._zona(e.x, e.y)
        if zona == "grip":
            self._ao_arrastar()
        elif zona == "menu":
            self._ao_menu()
        else:
            self._ao_copiar()


class MenuPopup(tk.Toplevel):
    """Menu flutuante simples (Editar / Excluir) com as cores do tema.
    Não rouba o foco do app; quem abre é responsável por fechar (clique fora, Esc...)."""

    def __init__(self, pai, c, fonte, itens, x_dir, y_base, altura_botao, ao_escolher):
        super().__init__(pai, bg=c["borda_hover"])
        self.withdraw()
        self.overrideredirect(True)
        interno = tk.Frame(self, bg=c["card"])
        interno.pack(padx=1, pady=1)
        for texto, cor, comando in itens:
            l = tk.Label(interno, text=texto, bg=c["card"], fg=cor, font=(fonte, 10),
                         anchor="w", padx=16, pady=7, cursor="hand2")
            l.pack(fill="x")
            l.bind("<Enter>", lambda e, l=l: l.config(bg=c["chip"]))
            l.bind("<Leave>", lambda e, l=l: l.config(bg=c["card"]))
            l.bind("<Button-1>", lambda e, cmd=comando: ao_escolher(cmd))
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = max(0, min(x_dir - w, sw - w))
        y = y_base
        if y + h > sh:                       # sem espaço embaixo: abre para cima
            y = max(0, y_base - altura_botao - h)
        self.geometry(f"+{int(x)}+{int(y)}")
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.deiconify()


class BarraRolagem(tk.Canvas):
    """Barra de rolagem fina, sem setas, que só mostra o "polegar" quando há o que rolar.
    Funciona como o tk.Scrollbar: use set() em yscrollcommand e passe yview em 'comando'."""

    def __init__(self, pai, comando, bg, cor, cor_hover, largura=10, espessura=5):
        super().__init__(pai, width=largura, bg=bg, highlightthickness=0, bd=0)
        self._cmd = comando
        self._cor, self._cor_hover = cor, cor_hover
        self._esp, self._lar = espessura, largura
        self._primeiro, self._ultimo = 0.0, 1.0
        self._sobre = False
        self._arrastando = False
        self._desloc = 0.0
        self.bind("<Configure>", lambda e: self._desenhar())
        self.bind("<Button-1>", self._clique)
        self.bind("<B1-Motion>", self._arrasto)
        self.bind("<ButtonRelease-1>", self._soltar)
        self.bind("<Enter>", lambda e: self._set_sobre(True))
        self.bind("<Leave>", lambda e: self._set_sobre(False))

    def set(self, primeiro, ultimo):
        self._primeiro, self._ultimo = float(primeiro), float(ultimo)
        self._desenhar()

    def _set_sobre(self, valor):
        self._sobre = valor
        self._desenhar()

    def _limites(self):
        h = max(1, self.winfo_height())
        y1 = self._primeiro * h
        y2 = max(self._ultimo * h, y1 + 24)          # polegar nunca fica minúsculo
        return h, y1, min(y2, h)

    def _desenhar(self):
        self.delete("all")
        if self._ultimo - self._primeiro >= 0.999:    # tudo cabe: não mostra nada
            return
        h, y1, y2 = self._limites()
        x1 = (self._lar - self._esp) / 2
        cor = self._cor_hover if (self._sobre or self._arrastando) else self._cor
        retangulo_arredondado(self, x1, y1, x1 + self._esp, y2, min(self._esp / 2, (y2 - y1) / 2),
                              fill=cor, outline="")

    def _clique(self, e):
        h, y1, y2 = self._limites()
        if y1 <= e.y <= y2:
            self._arrastando = True
            self._desloc = e.y - y1
            self._desenhar()
        elif e.y < y1:
            self._cmd("scroll", -1, "pages")
        else:
            self._cmd("scroll", 1, "pages")

    def _arrasto(self, e):
        if not self._arrastando:
            return
        h = max(1, self.winfo_height())
        self._cmd("moveto", max(0.0, (e.y - self._desloc) / h))

    def _soltar(self, e):
        self._arrastando = False
        self._desenhar()


def moldura(pai, c):
    """Frame com borda fina que destaca quando recebe foco."""
    f = tk.Frame(pai, bg=c["card"], highlightthickness=1,
                 highlightbackground=c["borda"], highlightcolor=c["borda"])
    return f


def e_descendente(w, ancestral):
    """True se o widget w for o próprio ancestral ou estiver dentro dele."""
    while w is not None:
        if w is ancestral:
            return True
        w = w.master
    return False


def posicionar_sobre(janela, pai, w, h):
    """Centraliza um diálogo (de tamanho w x h) sobre 'pai', sem sair do monitor.
    Só mexe na POSIÇÃO; o tamanho continua o que o diálogo já definiu."""
    try:
        pai.update_idletasks()
        px, py = pai.winfo_rootx(), pai.winfo_rooty()
        pw, ph = pai.winfo_width(), pai.winfo_height()
        if pw < 50 or ph < 50:
            return
        mx, my, mw, mh = monitor_do_ponto(listar_monitores(janela), px + pw / 2, py + ph / 2)
        x = max(mx, min(px + (pw - w) // 2, mx + mw - w))
        y = max(my, min(py + (ph - h) // 2, my + mh - h))
        janela.geometry(f"+{int(x)}+{int(y)}")
    except Exception:
        pass


class Dialogo(tk.Toplevel):
    def __init__(self, pai, c, fonte, categorias, frase=None):
        super().__init__(pai, bg=c["bg"])
        self.withdraw()          # fica escondida até estar pronta e posicionada
        self.c, self.f = c, fonte
        self.resultado = None
        self.title("Editar frase" if frase else "Nova frase")
        self.geometry("360x400")
        self.minsize(320, 340)
        self.transient(pai)
        posicionar_sobre(self, pai, 360, 400)

        corpo = tk.Frame(self, bg=c["bg"])
        corpo.pack(fill="both", expand=True, padx=16, pady=14)

        tk.Label(corpo, text="Editar frase" if frase else "Nova frase", bg=c["bg"], fg=c["texto"],
                 font=(fonte, 13, "bold")).pack(anchor="w")

        tk.Label(corpo, text="Categoria", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 9, "bold")).pack(anchor="w", pady=(14, 4))
        mc = moldura(corpo, c)
        mc.pack(fill="x")

        self.cat = tk.Entry(mc, relief="flat", bd=0, bg=c["card"], fg=c["texto"],
                            insertbackground=c["texto"], font=(fonte, 11),
                            highlightthickness=0)
        self.cat.pack(fill="x", padx=10, pady=8)

        if categorias:
            sug = tk.Frame(corpo, bg=c["bg"])
            sug.pack(fill="x", pady=(6, 0))
            for nome in categorias[:6]:
                l = tk.Label(sug, text=nome, bg=c["chip"], fg=c["texto"], padx=9, pady=2,
                             cursor="hand2", font=(fonte, 9))
                l.pack(side="left", padx=(0, 5))
                l.bind("<Button-1>", lambda e, n=nome: (self.cat.delete(0, "end"), self.cat.insert(0, n)))

        tk.Label(corpo, text="Frase", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 9, "bold")).pack(anchor="w", pady=(14, 4))
        mt = moldura(corpo, c)
        mt.pack(fill="both", expand=True)

        self.texto = tk.Text(mt, wrap="word", relief="flat", bd=0, bg=c["card"], fg=c["texto"],
                             insertbackground=c["texto"], font=(fonte, 11), height=6,
                             padx=10, pady=8, highlightthickness=0)
        self.texto.pack(fill="both", expand=True)

        for entrada, m in ((self.cat, mc), (self.texto, mt)):
            entrada.bind("<FocusIn>", lambda e, m=m: m.config(highlightbackground=c["acento"], highlightcolor=c["acento"]))
            entrada.bind("<FocusOut>", lambda e, m=m: m.config(highlightbackground=c["borda"], highlightcolor=c["borda"]))

        if frase:
            self.cat.insert(0, frase["categoria"])
            self.texto.insert("1.0", frase["texto"])

        rodape = tk.Frame(corpo, bg=c["bg"])
        rodape.pack(fill="x", pady=(16, 0))
        botao(rodape, "Salvar", self.ok, c["acento"], c["acento_txt"], c["acento_hover"],
              fonte, True, 20, 8).pack(side="right")
        botao(rodape, "Cancelar", self.destroy, c["chip"], c["texto"], c["borda"],
              fonte, False, 16, 8).pack(side="right", padx=8)

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Control-Return>", lambda e: self.ok())
        self.texto.focus_set()
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()

    def ok(self):
        texto = self.texto.get("1.0", "end").strip()
        if not texto:
            messagebox.showwarning("Aviso", "Digite a frase.", parent=self)
            return
        self.resultado = {"categoria": self.cat.get().strip() or "Geral", "texto": texto}
        self.destroy()


class ConfirmarDialogo(tk.Toplevel):
    def __init__(self, pai, c, fonte, titulo, mensagem):
        super().__init__(pai, bg=c["bg"])
        self.withdraw()          # fica escondida até estar pronta e posicionada
        self.c, self.f = c, fonte
        self.resultado = False
        self.title(titulo)
        self.geometry("325x150")
        self.minsize(300, 140)
        self.transient(pai)
        posicionar_sobre(self, pai, 325, 150)

        corpo = tk.Frame(self, bg=c["bg"])
        corpo.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(corpo, text=titulo, bg=c["bg"], fg=c["texto"],
                 font=(fonte, 12, "bold")).pack(anchor="w", pady=(0, 6))

        tk.Label(corpo, text=mensagem, bg=c["bg"], fg=c["suave"],
                 font=(fonte, 10), wraplength=280, justify="left").pack(anchor="w", pady=(0, 16))

        rodape = tk.Frame(corpo, bg=c["bg"])
        rodape.pack(fill="x", side="bottom")

        botao(rodape, "Excluir", self.ok, c["perigo"], "#ffffff", c["perigo"],
              fonte, True, 16, 7).pack(side="right")
        botao(rodape, "Cancelar", self.destroy, c["chip"], c["texto"], c["borda"],
              fonte, False, 14, 7).pack(side="right", padx=8)

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.ok())
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()

    def ok(self):
        self.resultado = True
        self.destroy()


# ---------------------------------------------------------------- conversões
def rgb_para_hex(r, g, b):
    """(0-255, 0-255, 0-255) -> '#rrggbb' (valores fora da faixa são cortados)."""
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in (r, g, b))


def hex_valido(texto):
    """Normaliza '#abc', 'AABBCC' ou '#aabbcc' para '#aabbcc'. Devolve None se inválido."""
    t = (texto or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", t):
        t = "".join(ch * 2 for ch in t)
    if re.fullmatch(r"[0-9a-fA-F]{6}", t):
        return "#" + t.lower()
    return None


def hex_para_rgb(cor):
    cor = cor.lstrip("#")
    return int(cor[0:2], 16), int(cor[2:4], 16), int(cor[4:6], 16)


def hsv_para_hex(h, s, v):
    """h, s, v em 0..1 -> '#rrggbb'."""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return rgb_para_hex(r * 255, g * 255, b * 255)


def hex_para_hsv(cor):
    r, g, b = hex_para_rgb(cor)
    return colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)


def misturar(cor_a, cor_b, t):
    """Mistura duas cores hex: t=0 -> cor_a, t=1 -> cor_b."""
    a, b = hex_para_rgb(cor_a), hex_para_rgb(cor_b)
    return rgb_para_hex(*(x + (y - x) * t for x, y in zip(a, b)))


def luminancia(cor):
    """Luminância relativa (WCAG)."""
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = hex_para_rgb(cor)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contraste(cor_a, cor_b):
    maior, menor = sorted((luminancia(cor_a), luminancia(cor_b)), reverse=True)
    return (maior + 0.05) / (menor + 0.05)


def cor_do_texto_sobre(cor, claro="#ffffff", escuro="#10131a"):
    """Escolhe texto claro ou escuro, o que tiver mais contraste sobre 'cor'."""
    return claro if contraste(cor, claro) >= contraste(cor, escuro) else escuro


def derivar_acento(base, tema="claro"):
    """A partir de UMA cor, gera as 3 chaves de acento usadas pelo tema do app."""
    alvo = "#ffffff" if tema == "escuro" else "#000000"
    return dict(acento=base,
                acento_hover=misturar(base, alvo, 0.18),
                acento_txt=cor_do_texto_sobre(base))


# ------------------------------------------------------------------- roda
class RodaDeCores(tk.Canvas):
    """Roda de cores clicável/arrastável.

    - <Button-1>   : começa a selecionar (só se o clique for sobre a roda)
    - <B1-Motion>  : atualiza a cor em tempo real; o seletor fica preso ao círculo
    - comando(hex) : chamado a cada mudança de cor
    """
    MARGEM = 8          # folga para o marcador não ser cortado na borda
    RAIO_MARCADOR = 7

    def __init__(self, pai, tamanho=200, bg="#ffffff", cor_inicial="#4f6df5",
                 comando=None, **kw):
        super().__init__(pai, width=tamanho, height=tamanho, bg=bg,
                         highlightthickness=0, bd=0, cursor="crosshair", **kw)
        self.tamanho = tamanho
        self.cx = self.cy = tamanho / 2
        self.raio = tamanho / 2 - self.MARGEM
        self.comando = comando
        self._bg = bg                     # precisa ser '#rrggbb'
        self._h, self._s, self._v = 0.0, 0.0, 1.0
        self._arrastando = False
        self._redesenho_pendente = False

        self._img = tk.PhotoImage(width=tamanho, height=tamanho)
        self._base = self._calcular_base()
        self.create_image(0, 0, image=self._img, anchor="nw")

        self._m_fora = self.create_oval(0, 0, 0, 0, outline="#000000", width=1)
        self._m_dentro = self.create_oval(0, 0, 0, 0, outline="#ffffff", width=2)

        self.bind("<Button-1>", self._clique)
        self.bind("<B1-Motion>", self._arrasto)
        self.bind("<ButtonRelease-1>", self._soltar)

        self.set_color(cor_inicial, notificar=False)

    # ---- desenho -------------------------------------------------------
    def _calcular_base(self):
        """Pré-calcula a roda com brilho máximo (V=1). Só roda uma vez."""
        n, cx, cy, R = self.tamanho, self.cx, self.cy, self.raio
        linhas = []
        for y in range(n):
            dy = y + 0.5 - cy
            linha = []
            for x in range(n):
                dx = x + 0.5 - cx
                d = math.hypot(dx, dy)
                if d > R + 1:
                    linha.append(None)
                    continue
                s = min(d / R, 1.0)
                h = (math.atan2(-dy, dx) / (2 * math.pi)) % 1.0
                r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
                alfa = 1.0 if d <= R else 1.0 - (d - R)      # borda suave de 1 px
                linha.append((r * 255, g * 255, b * 255, alfa))
            linhas.append(linha)
        return linhas

    def _desenhar_roda(self):
        v = self._v
        br, bg_, bb = hex_para_rgb(self._bg)
        bg_hex = self._bg
        linhas = []
        for linha in self._base:
            celulas = []
            for px in linha:
                if px is None:
                    celulas.append(bg_hex)
                    continue
                r, g, b, a = px
                r, g, b = r * v, g * v, b * v
                if a < 1.0:
                    r, g, b = br + (r - br) * a, bg_ + (g - bg_) * a, bb + (b - bb) * a
                celulas.append("#%02x%02x%02x" % (int(r), int(g), int(b)))
            linhas.append("{" + " ".join(celulas) + "}")
        self._img.put(" ".join(linhas))

    def _agendar_redesenho(self):
        """Junta vários pedidos seguidos (ex.: arrastar a barra) em um só redesenho."""
        if self._redesenho_pendente:
            return
        self._redesenho_pendente = True

        def feito():
            self._redesenho_pendente = False
            try:
                self._desenhar_roda()
            except tk.TclError:
                pass
        self.after_idle(feito)

    def _atualizar_marcador(self):
        ang = self._h * 2 * math.pi
        dist = self._s * self.raio
        px = self.cx + math.cos(ang) * dist
        py = self.cy - math.sin(ang) * dist
        r = self.RAIO_MARCADOR
        self.coords(self._m_fora, px - r - 1, py - r - 1, px + r + 1, py + r + 1)
        self.coords(self._m_dentro, px - r, py - r, px + r, py + r)
        self.itemconfig(self._m_dentro, fill=self.get_hex())

    def _notificar(self):
        if self.comando:
            self.comando(self.get_hex())

    # ---- mouse ---------------------------------------------------------
    def _clique(self, e):
        dist = math.hypot(e.x - self.cx, e.y - self.cy)
        if dist > self.raio + 6:          # clicou fora da roda: ignora
            self._arrastando = False
            return
        self._arrastando = True
        self._apontar(e.x, e.y)

    def _arrasto(self, e):
        if self._arrastando:
            self._apontar(e.x, e.y)

    def _soltar(self, e):
        self._arrastando = False

    def _apontar(self, x, y):
        """Converte (x, y) em matiz/saturação, travando o ponto dentro do círculo."""
        dx, dy = x - self.cx, y - self.cy
        dist = math.hypot(dx, dy)
        if dist > self.raio:              # limite: projeta na borda da roda
            dx *= self.raio / dist
            dy *= self.raio / dist
            dist = self.raio
        if dist > 0:                      # no centro exato o matiz é indefinido: mantém
            self._h = (math.atan2(-dy, dx) / (2 * math.pi)) % 1.0
        self._s = dist / self.raio
        self._atualizar_marcador()
        self._notificar()

    # ---- API pública ---------------------------------------------------
    def get_hex(self):
        return hsv_para_hex(self._h, self._s, self._v)

    def get_hsv(self):
        return self._h, self._s, self._v

    def set_color(self, cor, notificar=True):
        """Define a cor por hexadecimal. Devolve False se o valor for inválido."""
        hx = hex_valido(cor)
        if not hx:
            return False
        h, s, v = hex_para_hsv(hx)
        if s > 0 and v > 0:               # cinza/preto não têm matiz: mantém o anterior
            self._h = h
        self._s, self._v = s, v
        self._desenhar_roda()
        self._atualizar_marcador()
        if notificar:
            self._notificar()
        return True

    def set_valor(self, v, notificar=True):
        """Define o brilho (0..1) mantendo matiz e saturação."""
        self._v = max(0.0, min(1.0, v))
        self._agendar_redesenho()
        self._atualizar_marcador()
        if notificar:
            self._notificar()

    def set_bg(self, bg):
        """Muda a cor de fundo ao redor da roda (use o fundo do tema)."""
        self._bg = bg
        self.config(bg=bg)
        self._desenhar_roda()


# ----------------------------------------------------------------- brilho
class BarraBrilho(tk.Canvas):
    """Barra horizontal de brilho: preto -> cor pura (para o matiz/saturação atuais)."""
    PAD = 8

    def __init__(self, pai, largura=184, altura=16, bg="#ffffff", comando=None):
        super().__init__(pai, width=largura + 2 * self.PAD, height=altura + 10, bg=bg,
                         highlightthickness=0, bd=0, cursor="sb_h_double_arrow")
        self.largura, self.altura, self.comando = largura, altura, comando
        self._v = 1.0
        self._chave = None
        y1, y2 = 5, 5 + altura
        self._y1, self._y2 = y1, y2
        self._linhas = [self.create_line(self.PAD + i, y1, self.PAD + i, y2)
                        for i in range(largura)]
        self._alca_fora = self.create_rectangle(0, 0, 0, 0, outline="#000000", width=1)
        self._alca = self.create_rectangle(0, 0, 0, 0, outline="#ffffff", width=2)
        self.bind("<Button-1>", self._mouse)
        self.bind("<B1-Motion>", self._mouse)
        self.definir(0.0, 0.0, 1.0)

    def _mouse(self, e):
        v = (e.x - self.PAD) / max(1, self.largura - 1)
        v = max(0.0, min(1.0, v))
        self._posicionar(v)
        if self.comando:
            self.comando(v)

    def _posicionar(self, v):
        self._v = v
        x = self.PAD + v * (self.largura - 1)
        self.coords(self._alca_fora, x - 4, self._y1 - 4, x + 4, self._y2 + 4)
        self.coords(self._alca, x - 3, self._y1 - 3, x + 3, self._y2 + 3)

    def definir(self, h, s, v):
        """Atualiza o degradê (se matiz/saturação mudaram) e a posição da alça."""
        chave = (round(h, 3), round(s, 3))
        if chave != self._chave:
            self._chave = chave
            n = max(1, self.largura - 1)
            for i, item in enumerate(self._linhas):
                self.itemconfig(item, fill=hsv_para_hex(h, s, i / n))
        self._posicionar(v)


# --------------------------------------------------------- seletor composto
class SeletorCor(tk.Frame):
    """Roda + barra de brilho + amostra + campo hexadecimal.

    Parâmetros de aparência são cores simples (#rrggbb) para não depender do seu tema.
    comando(hex) é chamado a cada mudança (clique, arraste, brilho ou digitação).
    """

    def __init__(self, pai, bg="#f3f4f8", card="#ffffff", fg="#1f2933", suave="#6b7785",
                 borda="#e2e5ec", acento="#4f6df5", fonte=None, cor_inicial="#4f6df5",
                 tamanho=200, comando=None):
        super().__init__(pai, bg=bg)
        self.comando = comando
        fonte = fonte or tkfont.nametofont("TkDefaultFont").actual("family")
        self._editando = False

        self.roda = RodaDeCores(self, tamanho=tamanho, bg=bg, cor_inicial=cor_inicial,
                                comando=self._mudou)
        self.roda.pack()
        self.barra = BarraBrilho(self, largura=tamanho - 2 * BarraBrilho.PAD, bg=bg,
                                 comando=self.roda.set_valor)
        self.barra.pack(pady=(4, 0))

        linha = tk.Frame(self, bg=bg)
        linha.pack(fill="x", pady=(10, 0))
        self.amostra = tk.Frame(linha, width=40, height=30, bg=cor_inicial,
                                highlightthickness=1, highlightbackground=borda)
        self.amostra.pack(side="left")
        self.amostra.pack_propagate(False)

        moldura = tk.Frame(linha, bg=card, highlightthickness=1,
                           highlightbackground=borda, highlightcolor=acento)
        moldura.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._var = tk.StringVar()
        self.entrada = tk.Entry(moldura, textvariable=self._var, relief="flat", bd=0,
                                bg=card, fg=fg, insertbackground=fg, font=(fonte, 11),
                                highlightthickness=0, width=9)
        self.entrada.pack(fill="x", padx=8, pady=5)
        self.entrada.bind("<KeyRelease>", self._digitado)
        self.entrada.bind("<Return>", self._confirmar_texto)
        self.entrada.bind("<FocusOut>", self._confirmar_texto)
        self.entrada.bind("<FocusIn>", lambda e: moldura.config(highlightbackground=acento))
        self.entrada.bind("<FocusOut>", lambda e: moldura.config(highlightbackground=borda), add="+")

        self._atualizar_partes(self.roda.get_hex())

    # ---- sincronização -------------------------------------------------
    def _atualizar_partes(self, hx):
        h, s, v = self.roda.get_hsv()
        self.barra.definir(h, s, v)
        self.amostra.config(bg=hx)
        if not self._editando:
            self._var.set(hx.upper())

    def _mudou(self, hx):
        self._atualizar_partes(hx)
        if self.comando:
            self.comando(hx)

    def _digitado(self, e=None):
        """Aplica a cor assim que houver 6 dígitos hex válidos (sem reescrever o campo)."""
        texto = self._var.get().strip()
        if re.fullmatch(r"#?[0-9a-fA-F]{6}", texto):
            self._editando = True
            try:
                self.roda.set_color(texto)
            finally:
                self._editando = False

    def _confirmar_texto(self, e=None):
        hx = hex_valido(self._var.get())
        if hx:
            self.roda.set_color(hx)
        self._var.set(self.roda.get_hex().upper())     # normaliza / desfaz valor inválido

    # ---- API pública ---------------------------------------------------
    def get_hex(self):
        return self.roda.get_hex()

    def set_color(self, cor):
        return self.roda.set_color(cor)


# ----------------------------------------------------------------- diálogo
def _botao(pai, texto, comando, bg, fg, hover, fonte, negrito=False, px=14, py=7):
    b = tk.Label(pai, text=texto, bg=bg, fg=fg, padx=px, pady=py, cursor="hand2",
                 font=(fonte, 10, "bold" if negrito else "normal"))
    b.bind("<Enter>", lambda e: b.config(bg=hover))
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    b.bind("<Button-1>", lambda e: comando())
    return b


class DialogoCor(tk.Toplevel):
    """Janela modal para escolher a cor do tema."""

    def __init__(self, pai, c, fonte, cor_atual, tema="claro", ao_mudar=None):
        super().__init__(pai, bg=c["bg"])
        self.withdraw()          # fica escondida até estar pronta e posicionada
        self.c, self.f, self.tema = c, fonte, tema
        self.ao_mudar = ao_mudar
        self.resultado = None
        self.title("Cor do tema")
        self.resizable(False, False)
        self.transient(pai)

        corpo = tk.Frame(self, bg=c["bg"])
        corpo.pack(fill="both", expand=True, padx=16, pady=12)

        tk.Label(corpo, text="Cor do tema", bg=c["bg"], fg=c["texto"],
                 font=(fonte, 13, "bold")).pack(anchor="w")
        tk.Label(corpo, text="Clique e arraste na roda. A barra ajusta o brilho.",
                 bg=c["bg"], fg=c["suave"], font=(fonte, 9)).pack(anchor="w", pady=(2, 8))

        self.seletor = SeletorCor(corpo, bg=c["bg"], card=c["card"], fg=c["texto"],
                                  suave=c["suave"], borda=c["borda"], acento=c["acento"],
                                  fonte=fonte, cor_inicial=cor_atual, tamanho=168,
                                  comando=self._previa)
        self.seletor.pack()

        # Prévia em tempo real
        tk.Label(corpo, text="Prévia", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 9, "bold")).pack(anchor="w", pady=(10, 3))
        prev = tk.Frame(corpo, bg=c["card"], highlightthickness=1,
                        highlightbackground=c["borda"])
        prev.pack(fill="x")
        interno = tk.Frame(prev, bg=c["card"])
        interno.pack(fill="x", padx=10, pady=6)
        self.prev_cat = tk.Label(interno, text="CATEGORIA", bg=c["card"], fg=c["acento"],
                                 font=(fonte, 8, "bold"))
        self.prev_cat.pack(side="left")
        self.prev_btn = tk.Label(interno, text="+ Nova", bg=c["acento"], fg=c["acento_txt"],
                                 padx=12, pady=4, font=(fonte, 10, "bold"))
        self.prev_btn.pack(side="right")
        self._pintar_previa(self.seletor.get_hex())

        rodape = tk.Frame(corpo, bg=c["bg"])
        rodape.pack(fill="x", side="bottom", pady=(12, 0))
        _botao(rodape, "Aplicar", self.ok, c["acento"], c["acento_txt"], c["acento_hover"],
               fonte, True, 18, 8).pack(side="right")
        _botao(rodape, "Cancelar", self.destroy, c["chip"], c["texto"], c["borda"],
               fonte, False, 14, 8).pack(side="right", padx=8)
        _botao(rodape, "Padrão", self.padrao, c["chip"], c["texto"], c["borda"],
               fonte, False, 14, 8).pack(side="left")

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        w, h = max(self.winfo_reqwidth(), 300), self.winfo_reqheight()
        self.geometry(f"{w}x{h}")
        posicionar_sobre(self, pai, w, h)
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()

    def _pintar_previa(self, hx):
        d = derivar_acento(hx, self.tema)
        self.prev_btn.config(bg=d["acento"], fg=d["acento_txt"])
        self.prev_cat.config(fg=d["acento"])

    def _previa(self, hx):
        self._pintar_previa(hx)
        if self.ao_mudar:
            self.ao_mudar(hx)

    def ok(self):
        self.resultado = self.seletor.get_hex()
        self.destroy()

    def padrao(self):
        self.resultado = ""
        self.destroy()


# ------------------------------------------------------------------ tela de configurações
class DialogoConfiguracoes(tk.Toplevel):
    """Janela modal de configurações gerais do aplicativo."""

    def __init__(self, pai, c, fonte, tema):
        super().__init__(pai, bg=c["bg"])
        self.withdraw()          # fica escondida até estar pronta e posicionada
        self.c, self.f, self.tema = c, fonte, tema
        self.title("Configurações")
        self.geometry("345x325")
        self.resizable(False, False)
        self.transient(pai)
        posicionar_sobre(self, pai, 345, 325)

        corpo = tk.Frame(self, bg=c["bg"])
        corpo.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(corpo, text="Configurações", bg=c["bg"], fg=c["texto"],
                 font=(fonte, 13, "bold")).pack(anchor="w", pady=(0, 14))

        # Seção Aparência
        tk.Label(corpo, text="APARÊNCIA", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 9, "bold")).pack(anchor="w", pady=(0, 6))

        moldura_card = tk.Frame(corpo, bg=c["card"], highlightthickness=1,
                                highlightbackground=c["borda"])
        moldura_card.pack(fill="x", pady=(0, 14))

        sub_card = tk.Frame(moldura_card, bg=c["card"])
        sub_card.pack(fill="x", padx=12, pady=10)

        tk.Label(sub_card, text="Personalizar cores do tema", bg=c["card"], fg=c["texto"],
                 font=(fonte, 10)).pack(side="left")

        btn_cor = tk.Label(sub_card, text="Alterar", bg=c["acento"], fg=c["acento_txt"],
                           padx=10, pady=4, font=(fonte, 9, "bold"), cursor="hand2")
        btn_cor.pack(side="right")
        btn_cor.bind("<Button-1>", lambda e: self.abrir_seletor_cor())

        # Seção Comportamento
        tk.Label(corpo, text="COMPORTAMENTO", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 9, "bold")).pack(anchor="w", pady=(0, 6))

        moldura_copia = tk.Frame(corpo, bg=c["card"], highlightthickness=1,
                                 highlightbackground=c["borda"])
        moldura_copia.pack(fill="x", pady=(0, 14))

        sub_copia = tk.Frame(moldura_copia, bg=c["card"])
        sub_copia.pack(fill="x", padx=12, pady=10)

        tk.Label(sub_copia, text="Intervalo entre cópias (1.5s)", bg=c["card"], fg=c["texto"],
                 font=(fonte, 10)).pack(side="left")

        usando = self.master.cfg.get("usar_intervalo", True)
        self.btn_intervalo_texto = tk.StringVar(value="Ativado" if usando else "Desativado")

        self.btn_intervalo = tk.Label(
            sub_copia,
            textvariable=self.btn_intervalo_texto,
            bg=c["acento"] if usando else c["chip"],
            fg=c["acento_txt"] if usando else c["texto"],
            padx=10, pady=4, font=(fonte, 9, "bold"), cursor="hand2"
        )
        self.btn_intervalo.pack(side="right")
        self.btn_intervalo.bind("<Button-1>", lambda e: self.alternar_intervalo())

        # Rodapé
        rodape = tk.Frame(corpo, bg=c["bg"])
        rodape.pack(fill="x", side="bottom")
        _botao(rodape, "Fechar", self.destroy, c["chip"], c["texto"], c["borda"],
               fonte, False, 16, 7).pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()

    def alternar_intervalo(self):
        atual = self.master.cfg.get("usar_intervalo", True)
        novo = not atual
        self.master.cfg["usar_intervalo"] = novo
        _gravar(ARQUIVO_CFG, self.master.cfg)

        c = self.c
        if novo:
            self.btn_intervalo_texto.set("Ativado")
            self.btn_intervalo.config(bg=c["acento"], fg=c["acento_txt"])
        else:
            self.btn_intervalo_texto.set("Desativado")
            self.btn_intervalo.config(bg=c["chip"], fg=c["texto"])

    def abrir_seletor_cor(self):
        cor_atual = self.master.cfg.get("cor_personalizada") or TEMAS[self.tema]["acento"]
        dialogo = DialogoCor(self, self.c, self.f, cor_atual, tema=self.tema)
        resultado = dialogo.resultado

        if resultado is None:
            return

        if resultado == "":
            self.master.cfg.pop("cor_personalizada", None)
        else:
            self.master.cfg["cor_personalizada"] = resultado

        _gravar(ARQUIVO_CFG, self.master.cfg)
        self.master.construir()
        self.destroy()
        self.master.mostrar_rodape(
            "Cor padrão restaurada" if resultado == ""
            else f"Cor do tema alterada para {resultado.upper()}",
            self.master.c["acento"],
            volta="clique para copiar"
        )


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Frases Prontas")
        self.minsize(300, 360)
        self._icone_janela()
        self.dados = _ler(ARQUIVO, list(EXEMPLOS))
        self.cfg = _ler(ARQUIVO_CFG, {})
        self.tema = self.cfg.get("tema", "claro")
        if self.tema not in TEMAS:
            self.tema = "claro"
        self.fixado = bool(self.cfg.get("fixado", False))
        self.categoria = "Todas"
        self.busca = tk.StringVar()
        self.busca.trace_add("write", lambda *_: self.listar())
        self.f = escolher_fonte(self)
        self.fonte_chip = tkfont.Font(root=self, family=self.f, size=10)
        self.fonte_chip_b = tkfont.Font(root=self, family=self.f, size=10, weight="bold")
        self.largura_texto = 560
        self._toast_id = None
        self._ultima_copia = 0.0
        self.clip_externo = ClipboardExterno() if (LINUX and USAR_CLIPBOARD_EXTERNO) else None

        self.arrasto_ativo = False
        self.arrastando = None
        self.drag_window = None
        self.barra = None
        self.slot = None
        self.cartoes_widgets = []
        self.cache = {}
        self.chip_labels = {}
        self.pin_btn = None
        self.ph = None

        # barra lateral (recolhimento automático)
        self.recolhido = False
        self.aba = None
        self.geo_salva = None
        self._foco_id = None
        self._aba_pronta = False
        self.menu_aberto = None       # menu de três pontos aberto (ou None)
        self.menu_card = None
        self._menu_t = 0.0
        self._menu_ignorar_clique = False
        self._tinha_foco = False      # o app já recebeu foco desde que foi aberto/expandido?
        self._sem_foco_cont = 0
        self._expandido_em = None     # quando a barra lateral foi expandida pela última vez

        precarregar_monitores()
        self.construir()
        self.posicionar()
        self.aplicar_fixar()

        self.bind_all("<Button-4>", lambda e: self.rolar(-3))
        self.bind_all("<Button-5>", lambda e: self.rolar(3))
        self.bind_all("<MouseWheel>", lambda e: self.rolar(-1 if e.delta > 0 else 1))
        self.bind_all("<Button-1>", self._clique_global, add="+")
        self.bind("<Escape>", lambda e: self.fechar_menu())
        self.bind_all("<B1-Motion>", self.movendo_arrasto)
        self.bind_all("<ButtonRelease-1>", self.soltar_arrasto)
        self.bind("<Control-n>", lambda e: self.adicionar())
        self.bind("<Control-f>", lambda e: self.entrada.focus_set())
        self.bind("<FocusOut>", self._ao_perder_foco)
        self.after(INTERVALO_VIGIA, self._vigiar_foco)

    def _icone_janela(self):
        """Ícone da janela/barra de tarefas (opcional: só se o arquivo existir)."""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        try:
            if WINDOWS:
                ico = os.path.join(base, "icone.ico")
                if os.path.exists(ico):
                    self.iconbitmap(ico)
            else:
                png = os.path.join(base, "icone.png")
                if os.path.exists(png):
                    self._img_icone = tk.PhotoImage(file=png)
                    self.iconphoto(True, self._img_icone)
        except tk.TclError:
            pass

    # ---------- fixar na tela (sempre por cima) ----------
    def aplicar_fixar(self):
        """Liga/desliga o modo 'sempre por cima' da janela."""
        try:
            self.attributes("-topmost", self.fixado)
        except tk.TclError:
            pass

    def atualizar_pin(self):
        if not self.pin_btn:
            return
        c = self.c
        if self.fixado:
            self.pin_btn.configurar("cadeado", c["acento"], c["acento_txt"], c["acento_hover"],
                                    borda=c["acento"])
        else:
            self.pin_btn.configurar("aberto", c["card"], c["texto"], c["chip"],
                                    borda=c["borda"])

    def alternar_fixar(self):
        self.fixado = not self.fixado
        self.cfg["fixado"] = self.fixado
        _gravar(ARQUIVO_CFG, self.cfg)
        self.aplicar_fixar()
        self.atualizar_pin()
        self.mostrar_rodape(
            "Fixado: o app fica sempre por cima" if self.fixado
            else "Solto: vira barra lateral ao clicar em outra janela",
            self.c["acento"] if self.fixado else None,
            volta="clique para copiar")

    # ---------- barra lateral (recolher / expandir) ----------
    def _dbg(self, msg):
        if DEBUG:
            print("[%8.3f] %s" % (time.monotonic() % 10000, msg), file=sys.stderr, flush=True)

    def _ponteiro_na_janela(self):
        """True se o mouse está sobre a janela, incluindo barra de título e bordas."""
        try:
            px, py = self.winfo_pointerxy()
            rx, ry = self.winfo_rootx(), self.winfo_rooty()
            borda = max(rx - self.winfo_x(), 0) + 2
            topo = max(ry - self.winfo_y(), 36)      # barra de título
            x1, y1 = rx - borda, ry - topo
            x2 = rx + self.winfo_width() + borda
            y2 = ry + self.winfo_height() + borda
            return x1 <= px <= x2 and y1 <= py <= y2
        except tk.TclError:
            return False

    def _app_tem_foco(self):
        """True se o app está em primeiro plano. No Windows pergunta ao sistema
        (o foco interno do Tk pode ficar 'fantasma' depois de forçar o foreground);
        nos outros sistemas usa o foco do Tk."""
        if WINDOWS:
            try:
                import ctypes
                from ctypes import wintypes
                u32 = ctypes.windll.user32
                fg = u32.GetForegroundWindow()
                if not fg:
                    return False
                pid = wintypes.DWORD(0)
                u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
                return pid.value == os.getpid()
            except Exception:
                pass
        try:
            return self.focus_displayof() is not None
        except (tk.TclError, KeyError):
            return False

    def _ao_perder_foco(self, e=None):
        """Agenda uma checagem: o foco saiu do app de verdade?"""
        self._dbg("FocusOut em %s" % (getattr(e, "widget", None),))
        if not RECOLHER_AUTOMATICO or self.fixado or self.recolhido:
            return
        if self._foco_id:
            self.after_cancel(self._foco_id)
        self._foco_id = self.after(ESPERA_FOCUS_OUT, self._checar_foco)

    def _checar_foco(self):
        self._foco_id = None
        if self.fixado or self.recolhido or self.drag_window:
            self._dbg("checar_foco: ignorado (fixado=%s recolhido=%s arrastando=%s)"
                      % (self.fixado, self.recolhido, bool(self.drag_window)))
            return
        try:
            if self.state() != "normal":
                self._dbg("checar_foco: janela não está 'normal' (%s)" % self.state())
                return
            # Algum widget do app (inclusive diálogos) tem foco: não recolhe
            if self._app_tem_foco():
                self._dbg("checar_foco: app ainda tem foco")
                return
            # Sem foco, mas o mouse está sobre a janela (ex.: arrastando pela
            # barra de título): espera e confere de novo
            if self._ponteiro_na_janela():
                self._dbg("checar_foco: mouse sobre a janela, tenta de novo em 500 ms")
                self._foco_id = self.after(150, self._checar_foco)
                return
            self._dbg("checar_foco: recolhendo")
            self.recolher()
        except (tk.TclError, KeyError):
            pass

    def _vigiar_foco(self):
        """Confere o foco a cada 200 ms. O evento FocusOut pode atrasar ou nem chegar
        (clicar na barra de tarefas, painéis, apps Wayland...), então isto garante que
        a barra lateral aparece logo depois que o app perde o foco."""
        try:
            self.after(INTERVALO_VIGIA, self._vigiar_foco)
        except tk.TclError:
            return
        if self.menu_aberto is not None and time.monotonic() - self._menu_t > 0.4:
            try:
                if not self._app_tem_foco():
                    self.fechar_menu()
            except (tk.TclError, KeyError):
                pass
        if DEBUG:
            try:
                estado = (self.recolhido, self.fixado, bool(self.drag_window),
                          self.state(), self._app_tem_foco(),
                          self._tinha_foco, self._ponteiro_na_janela(),
                          self.menu_aberto is not None)
            except (tk.TclError, KeyError):
                estado = None
            if estado != getattr(self, "_dbg_estado", None):
                self._dbg_estado = estado
                self._dbg("estado: recolhido=%s fixado=%s arrastando=%s state=%s "
                          "tem_foco=%s tinha_foco=%s mouse_na_janela=%s menu=%s"
                          % (estado or ("?",) * 8))
        if not RECOLHER_AUTOMATICO or self.fixado or self.recolhido or self.drag_window:
            self._sem_foco_cont = 0
            return
        try:
            if self.state() != "normal":
                return
            if self._app_tem_foco():
                self._tinha_foco = True
                self._sem_foco_cont = 0
                return
            if self._ponteiro_na_janela():
                self._sem_foco_cont = 0
                return
            if not self._tinha_foco:
                # nunca recebeu foco após expandir (Windows pode negar): só recolhe
                # se foi expandido pela barra e já passou um tempo com o mouse fora
                if (self._expandido_em is None
                        or time.monotonic() - self._expandido_em < 0.8):
                    self._sem_foco_cont = 0
                    return
            self._sem_foco_cont += 1
            if self._sem_foco_cont >= 2:          # 2 checagens seguidas = ~0,1 s
                self._sem_foco_cont = 0
                self._dbg("vigia: sem foco e mouse fora, recolhendo")
                self.recolher()
        except (tk.TclError, KeyError):
            pass

    def _dialogo_aberto(self):
        """True se há uma janela modal aberta (configurações, cor, nova frase...).
        Com um grab ativo o Tk bloqueia os eventos da barra lateral, então não dá
        para recolher nesse momento."""
        try:
            return self.grab_current() is not None
        except (tk.TclError, KeyError):
            return False

    def recolher(self):
        if self.recolhido or self.fixado:
            return
        if self._dialogo_aberto():
            self._dbg("recolher: ignorado (há janela modal aberta)")
            return
        self._tinha_foco = False
        self._sem_foco_cont = 0
        self.fechar_menu()
        self.update_idletasks()
        self.geo_salva = self.geometry()
        larg, alt = self.winfo_width(), self.winfo_height()
        x, y = self.winfo_rootx(), self.winfo_rooty()

        # monitor onde a janela está
        cx, cy = x + larg / 2, y + alt / 2
        mx, my, mw, mh = monitor_do_ponto(listar_monitores(self), cx, cy)

        # lado do MONITOR mais próximo da janela
        lado = "esquerdo" if cx < mx + mw / 2 else "direito"
        ypos = int(min(max(cy - ALTURA_ABA / 2, my + MARGEM),
                       my + mh - ALTURA_ABA - MARGEM))
        xpos = mx if lado == "esquerdo" else mx + mw - LARGURA_ABA

        self.recolhido = True
        self.criar_aba(xpos, ypos)
        self.withdraw()

    def criar_aba(self, xpos, ypos):
        c = self.c
        w, h = LARGURA_ABA, ALTURA_ABA

        aba = tk.Toplevel(self)
        aba.withdraw()
        aba.overrideredirect(True)

        # Tenta transparência real nos cantos (Windows); se o sistema não
        # suportar (Linux), usa a cor de fundo do tema no lugar.
        chave = "#010203"
        try:
            aba.attributes("-transparentcolor", chave)
        except tk.TclError:
            chave = c["bg"]
        aba.configure(bg=chave)

        cv = tk.Canvas(aba, width=w, height=h, bg=chave, highlightthickness=0, bd=0,
                       cursor="hand2")
        cv.pack()
        retangulo_arredondado(cv, 0, 0, w, h, w // 2, fill=c["acento"], outline="")
        cv.create_line(w // 2, h // 2 - 14, w // 2, h // 2 + 14,
                       fill=c["acento_txt"], width=2, capstyle="round")

        aba.geometry(f"{w}x{h}+{xpos}+{ypos}")
        try:
            aba.attributes("-topmost", True)
        except tk.TclError:
            pass

        # ignora o "Enter" logo ao criar (caso o mouse já esteja em cima)
        self._aba_pronta = False
        self.after(400, lambda: setattr(self, "_aba_pronta", True))
        cv.bind("<Enter>", lambda e: self.expandir() if self._aba_pronta else None)

        self.aba = aba
        aba.update_idletasks()
        aba.deiconify()

    def _win_hwnd(self):
        import ctypes
        u32 = ctypes.windll.user32
        return u32.GetParent(self.winfo_id()) or self.winfo_id()

    def _win_topmost(self, ligado):
        """Liga/desliga 'sempre por cima' direto no Windows (sem passar pelo cache do Tk)."""
        import ctypes
        u32 = ctypes.windll.user32
        # SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE = 0x0013 ; HWND_TOPMOST=-1, HWND_NOTOPMOST=-2
        u32.SetWindowPos(self._win_hwnd(), -1 if ligado else -2, 0, 0, 0, 0, 0x0013)

    def _tentar_foreground(self):
        """Uma tentativa de assumir o foreground no Windows."""
        import ctypes
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        hwnd = self._win_hwnd()
        self._win_topmost(True)
        fg = u32.GetForegroundWindow()
        t_fg = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        t_me = k32.GetCurrentThreadId()
        anexado = False
        if t_fg and t_fg != t_me:
            anexado = bool(u32.AttachThreadInput(t_fg, t_me, True))
        try:
            u32.ShowWindow(hwnd, 9)              # SW_RESTORE
            u32.BringWindowToTop(hwnd)
            u32.SetForegroundWindow(hwnd)
            u32.SwitchToThisWindow(hwnd, True)   # ignora a trava de foreground
        finally:
            if anexado:
                u32.AttachThreadInput(t_fg, t_me, False)
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _trazer_para_frente(self):
        """Traz a janela para frente. No Windows tenta 3 vezes (0, 80 e 250 ms),
        porque a primeira tentativa pode ser negada pela trava de foreground."""
        if getattr(self, "_soltar_id", None):
            try:
                self.after_cancel(self._soltar_id)
            except (tk.TclError, ValueError):
                pass
            self._soltar_id = None

        try:
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

        if WINDOWS:
            def tentar():
                if self.recolhido:
                    return
                try:
                    self._tentar_foreground()
                except Exception as ex:
                    self._dbg("foreground falhou: %r" % (ex,))
            tentar()
            self.after(80, tentar)
            self.after(250, tentar)

    def expandir(self):
        if not self.recolhido:
            return
        self._dbg("expandir()")
        self.recolhido = False
        self._tinha_foco = False
        self._sem_foco_cont = 0
        self._expandido_em = time.monotonic()
        if self.aba:
            try:
                self.aba.destroy()
            except tk.TclError:
                pass
            self.aba = None
        self.deiconify()
        if self.geo_salva:
            self.geometry(self.geo_salva)
        self._trazer_para_frente()

    # ---------- interface ----------
    def posicionar(self):
        """Janela pequena encaixada no canto da tela."""
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        larg, alt = min(LARGURA, sw - 2 * MARGEM), min(ALTURA, sh - 2 * MARGEM)
        x = MARGEM if "esquerdo" in CANTO else sw - larg - MARGEM
        if "meio" in CANTO:
            y = (sh - alt) // 2
        elif "superior" in CANTO:
            y = MARGEM + 30
        else:
            y = sh - alt - MARGEM - 40
        self.geometry(f"{larg}x{alt}+{x}+{max(y, 0)}")

    def construir(self):
        self.menu_aberto = self.menu_card = None
        for w in self.winfo_children():
            w.destroy()
        self.cache = {}
        self.cartoes_widgets = []
        self.chip_labels = {}
        self.ph = None
        c = dict(TEMAS[self.tema])
        cor_salva = self.cfg.get("cor_personalizada")
        if cor_salva:
            c.update(derivar_acento(cor_salva, self.tema))
        # cores derivadas (hover dos cartões e brilho do chip ativo)
        c["card_hover"] = misturar(c["card"], c["texto"], 0.08)
        c["borda_hover"] = misturar(c["borda"], c["suave"], 0.55)
        c["halo"] = misturar(c["bg"], c["acento"], 0.30)
        self.c = c
        f = self.f
        self.configure(bg=c["bg"])

        # Cabeçalho: [título] ............ [+ Nova] [cadeado] [tema] [engrenagem]
        topo = tk.Frame(self, bg=c["bg"])
        topo.pack(fill="x", padx=14, pady=(14, 10))
        tk.Label(topo, text="Frases Prontas", bg=c["bg"], fg=c["texto"],
                 font=(f, 15, "bold")).pack(side="left")

        def icone_cab(tipo, comando):
            return IconeBotao(topo, tipo, comando, c["card"], c["texto"], c["chip"],
                              tam=17, px=5, py=5, borda=c["borda"], bg_pai=c["bg"])

        # (ordem de empacotamento com side="right": o primeiro fica mais à direita)
        icone_cab("engrenagem", self.abrir_configuracoes).pack(side="right", padx=(6, 0))
        icone_cab("lua" if self.tema == "claro" else "sol",
                  self.alternar_tema).pack(side="right", padx=(6, 0))

        # Cadeado (liga/desliga fixar)
        self.pin_btn = icone_cab("aberto", self.alternar_fixar)
        self.pin_btn.pack(side="right", padx=(6, 0))
        self.atualizar_pin()

        BotaoArredondado(topo, "+ Nova", self.adicionar, c["acento"], c["acento_txt"],
                         c["acento_hover"], f, c["bg"], negrito=True,
                         px=9, py=6, raio=8).pack(side="right", padx=(8, 0))

        # Busca (com lupa à direita e texto de dica)
        mb = Arredondado(self, c["bg"], c["card"], c["borda"], raio=9, pad=4)
        mb.pack(fill="x", padx=14)
        lupa = IconeBotao(mb.interno, "lupa", lambda: self.entrada.focus_set(),
                          c["card"], c["suave"], c["card"], tam=16, px=3, py=3,
                          cursor="xterm")
        lupa.pack(side="right", padx=(0, 8))
        self.entrada = tk.Entry(mb.interno, textvariable=self.busca, relief="flat", bd=0,
                                bg=c["card"], fg=c["texto"], insertbackground=c["texto"],
                                font=(f, 11), highlightthickness=0)
        self.entrada.pack(side="left", fill="x", expand=True, pady=6, padx=(10, 4))
        self.ph = tk.Label(mb.interno, text="Buscar", bg=c["card"], fg=c["suave"],
                           font=(f, 11), cursor="xterm")
        self.ph.bind("<Button-1>", lambda e: self.entrada.focus_set())
        self.entrada.bind("<FocusIn>", lambda e: mb.definir(borda=c["acento"]))
        self.entrada.bind("<FocusOut>", lambda e: mb.definir(borda=c["borda"]))

        # Categorias
        self.chips = tk.Frame(self, bg=c["bg"])
        self.chips.pack(fill="x", padx=14, pady=(8, 2))
        self.desenhar_chips()

        # Rodapé
        self.rodape = tk.Label(self, bg=c["bg"], fg=c["suave"], font=(f, 9), anchor="w")
        self.rodape.pack(side="bottom", fill="x", padx=14, pady=(4, 10))

        # Lista com rolagem
        area = tk.Frame(self, bg=c["bg"])
        area.pack(fill="both", expand=True, padx=(14, 6))
        self.canvas = tk.Canvas(area, bg=c["bg"], highlightthickness=0, bd=0)
        sb = BarraRolagem(area, self.canvas.yview, c["bg"],
                          misturar(c["borda"], c["suave"], 0.35), c["suave"])
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.lista = tk.Frame(self.canvas, bg=c["bg"])
        self.janela = self.canvas.create_window((0, 0), window=self.lista, anchor="nw")
        self.lista.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self.ao_redimensionar)

        # Mensagem de lista vazia
        self.vazio = tk.Label(self.lista, text="Nenhuma frase encontrada.\nClique em “Nova” para criar.",
                              bg=c["bg"], fg=c["suave"], font=(f, 11), justify="center")
        self.listar()

    def ao_redimensionar(self, e):
        self.canvas.itemconfig(self.janela, width=e.width)
        self.largura_texto = max(200, e.width - 120)

    def rolar(self, n):
        self.fechar_menu()
        try:
            if self.lista.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(n, "units")
        except tk.TclError:
            pass

    def desenhar_chips(self):
        cats = ["Todas"] + self.categorias()
        if self.categoria not in cats:
            self.categoria = "Todas"

        if list(self.chip_labels.keys()) != cats:
            for w in self.chips.winfo_children():
                w.destroy()
            self.chip_labels = {}
            for nome in cats:
                ch = Chip(self.chips, nome, self.fonte_chip, self.fonte_chip_b, self.c,
                          lambda n=nome: self.escolher_categoria(n))
                ch.pack(side="left", padx=(0, 2))
                self.chip_labels[nome] = ch

        for nome, ch in self.chip_labels.items():
            ch.definir(nome == self.categoria)

    def escolher_categoria(self, nome):
        if nome == self.categoria:
            return
        self.categoria = nome
        self.desenhar_chips()
        self.listar()

    def resetar_destaques(self):
        for cw in self.cache.values():
            try:
                cw.hover = False
                cw.borda_forcada = None
                cw.atualizar()
            except tk.TclError:
                pass

    def _atualizar_placeholder(self):
        """Mostra o texto 'Buscar' só enquanto o campo estiver vazio."""
        if self.ph is None:
            return
        try:
            if self.busca.get():
                self.ph.place_forget()
            else:
                self.ph.place(in_=self.entrada, x=1, rely=0.5, anchor="w")
                self.ph.lift()
        except tk.TclError:
            pass

    def listar(self):
        self.fechar_menu()
        self._atualizar_placeholder()

        vivos = {id(d) for d in self.dados}
        for k in list(self.cache.keys()):
            if k not in vivos:
                self.cache.pop(k).destroy()

        for cw in self.cache.values():
            cw.pack_forget()
        self.vazio.pack_forget()
        self.cartoes_widgets = []

        termo = self.busca.get().lower().strip()
        for i, d in enumerate(self.dados):
            if self.categoria != "Todas" and d["categoria"] != self.categoria:
                continue
            if termo and termo not in d["texto"].lower() and termo not in d["categoria"].lower():
                continue
            cw = self.cache.get(id(d))
            if cw is None or cw.d is not d:
                cw = self.cartao(d)
                self.cache[id(d)] = cw
            cw.data_index = i
            cw.pack(fill="x", pady=5, padx=(0, 8))
            self.cartoes_widgets.append(cw)

        total = len(self.cartoes_widgets)
        if total == 0:
            self.vazio.pack(pady=60)

        self.resetar_destaques()
        self.canvas.yview_moveto(0)
        self.mostrar_rodape(f"{total} frase{'s' if total != 1 else ''}  ·  clique para copiar")

    def cartao(self, d):
        card = CartaoFrase(
            self.lista, self.c, d, self.f, self.largura_texto + 75,
            ao_copiar=lambda: self.copiar(self._indice(card.d), card, card.d["categoria"].upper()),
            ao_arrastar=lambda: self.iniciar_arrasto(card.d),
            ao_menu=lambda: self.abrir_menu(card))
        return card

    # ---------- menu de três pontos (Editar / Excluir) ----------
    def abrir_menu(self, card):
        mesmo = self.menu_aberto is not None and self.menu_card is card
        self.fechar_menu()
        if mesmo:                                  # clicar de novo nos pontos fecha
            return
        c = self.c
        itens = [("Editar", c["texto"], lambda: self.editar(self._indice(card.d))),
                 ("Excluir", c["perigo"], lambda: self.excluir(self._indice(card.d)))]
        x, y, alt = card.posicao_menu()
        self.menu_aberto = MenuPopup(self, c, self.f, itens, x, y, alt, self._escolher_menu)
        self.menu_card = card
        self._menu_t = time.monotonic()
        self._menu_ignorar_clique = True           # o clique que abriu não deve fechar

    def _escolher_menu(self, comando):
        self.fechar_menu()
        self.after(1, comando)

    def fechar_menu(self):
        m, self.menu_aberto, self.menu_card = self.menu_aberto, None, None
        if m is not None:
            try:
                m.destroy()
            except tk.TclError:
                pass

    def _clique_global(self, e):
        """Clique em qualquer lugar fora do menu aberto fecha o menu."""
        if self.menu_aberto is None:
            return
        if self._menu_ignorar_clique:
            self._menu_ignorar_clique = False
            return
        try:
            dentro = e_descendente(e.widget, self.menu_aberto)
        except AttributeError:
            dentro = False
        if not dentro:
            self.fechar_menu()

    def mostrar_rodape(self, msg, cor=None, volta=None):
        self.rodape.config(text=msg, fg=cor or self.c["suave"])
        if self._toast_id:
            self.after_cancel(self._toast_id)
            self._toast_id = None
        if volta:
            self._toast_id = self.after(2200, lambda: self.mostrar_rodape(volta))

    # ---------- dados ----------
    def categorias(self):
        return sorted({d["categoria"] for d in self.dados})

    def salvar(self, chips=True):
        _gravar(ARQUIVO, self.dados)
        if chips:
            self.desenhar_chips()

    def _indice(self, d):
        for k, x in enumerate(self.dados):
            if x is d:
                return k
        return -1

    # ---------- arrastar e soltar ----------
    def iniciar_arrasto(self, d):
        self.arrasto_ativo = True
        self.arrastando = d
        self.slot = None
        self.mostrar_rodape("Arraste e solte na nova posição...", self.c["acento"])

        c, f = self.c, self.f
        orig_card = next((cw for cw in self.cartoes_widgets if cw.d is d), None)
        largura = orig_card.winfo_width() if orig_card and orig_card.winfo_width() > 1 else 300
        altura = orig_card.winfo_height() if orig_card and orig_card.winfo_height() > 1 else 65
        x, y = self.winfo_pointerxy()

        self.barra = tk.Frame(self.lista, bg=c["acento"], height=3)

        jan = tk.Toplevel(self, bg=c["card"])
        jan.withdraw()
        jan.overrideredirect(True)
        self.drag_window = jan

        card_preview = tk.Frame(jan, bg=c["card"], highlightthickness=1,
                                highlightbackground=c["acento"], highlightcolor=c["acento"])
        card_preview.pack(fill="both", expand=True)

        corpo = tk.Frame(card_preview, bg=c["card"])
        corpo.pack(side="left", fill="both", expand=True, padx=12, pady=9)

        tk.Label(corpo, text=d["categoria"].upper(), bg=c["card"], fg=c["acento"],
                 font=(f, 8, "bold"), anchor="w").pack(fill="x")
        tk.Label(corpo, text=d["texto"], bg=c["card"], fg=c["texto"], font=(f, 11),
                 justify="left", anchor="w", wraplength=self.largura_texto).pack(fill="x", pady=(3, 0))

        jan.geometry(f"{largura}x{altura}+{x - 40}+{y - 20}")
        jan.update_idletasks()
        try:
            jan.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        if self.fixado:
            try:
                jan.attributes("-topmost", True)
            except tk.TclError:
                pass
        jan.deiconify()

    def _esconder_barra(self):
        self.slot = None
        if self.barra:
            try:
                self.barra.place_forget()
            except tk.TclError:
                pass

    def atualizar_indicador(self, x, y):
        cards = self.cartoes_widgets
        if not cards or not self.barra:
            self._esconder_barra()
            return

        cx, cy = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
        if not (cx <= x <= cx + self.canvas.winfo_width() and
                cy <= y <= cy + self.canvas.winfo_height()):
            self._esconder_barra()
            return

        y_rel = y - self.lista.winfo_rooty()
        slot = len(cards)
        for k, cw in enumerate(cards):
            if y_rel < cw.winfo_y() + cw.winfo_height() / 2:
                slot = k
                break

        origem = next((k for k, cw in enumerate(cards) if cw.d is self.arrastando), -1)
        if origem < 0 or slot in (origem, origem + 1):
            self._esconder_barra()
            return

        self.slot = slot
        if slot < len(cards):
            ypos = cards[slot].winfo_y() - 6
        else:
            ypos = cards[-1].winfo_y() + cards[-1].winfo_height() + 4
        self.barra.place(x=0, y=max(ypos, 0), relwidth=1, width=-8, height=3)
        self.barra.lift()

    def movendo_arrasto(self, event):
        if not self.drag_window:
            return
        x, y = self.winfo_pointerxy()
        self.drag_window.geometry(f"+{x - 40}+{y - 20}")
        self.atualizar_indicador(x, y)

    def soltar_arrasto(self, event):
        if self.drag_window:
            try:
                self.drag_window.withdraw()
                self.drag_window.destroy()
            except Exception:
                pass
            self.drag_window = None

        slot = self.slot
        if self.barra:
            try:
                self.barra.destroy()
            except tk.TclError:
                pass
            self.barra = None
        self.slot = None

        if not self.arrasto_ativo:
            return
        self.arrasto_ativo = False
        d = self.arrastando
        self.arrastando = None

        cards = list(self.cartoes_widgets)
        self.mostrar_rodape(f"{len(cards)} frases  ·  clique para copiar")
        if slot is None:
            return

        movido = next((cw for cw in cards if cw.d is d), None)
        if movido is None:
            return
        origem = self._indice(d)
        if origem < 0:
            return

        item = self.dados.pop(origem)
        if slot < len(cards):
            alvo = cards[slot]
            self.dados.insert(self._indice(alvo.d), item)
        else:
            ultimo = cards[-1]
            self.dados.insert(self._indice(ultimo.d) + 1, item)
        self.salvar(chips=False)

        opcoes = dict(fill="x", pady=5, padx=(0, 8))
        if slot < len(cards):
            movido.pack(before=cards[slot], **opcoes)
        else:
            movido.pack(after=cards[-1], **opcoes)

        self.cartoes_widgets.remove(movido)
        if slot < len(cards):
            self.cartoes_widgets.insert(self.cartoes_widgets.index(cards[slot]), movido)
        else:
            self.cartoes_widgets.append(movido)
        for cw in self.cartoes_widgets:
            cw.data_index = self._indice(cw.d)

    # ---------- copiar ----------
    def _copiar_texto(self, texto):
        """Coloca o texto no clipboard e só devolve True se a cópia realmente valeu."""
        try:
            if WINDOWS:
                if copiar_windows(texto, self.winfo_id()):
                    return True
            elif self.clip_externo and self.clip_externo.copiar(texto):
                return True
        except Exception:
            pass

        try:
            self.clipboard_clear()
            self.clipboard_append(texto)
            self.update_idletasks()
            return True
        except tk.TclError:
            return False

    def _tremer_cartao(self, card):
        """Pisca o cartão em vermelho e balança de um lado para o outro."""
        if getattr(card, "tremendo", False):
            return
        c = self.c
        card.tremendo = True
        desloc = [8, 0, 8, 0, 6, 0, 4, 0, 2, 0]

        def passo(i=0):
            try:
                if i >= len(desloc):
                    card.fundo_forcado = None
                    card.borda_forcada = None
                    card.atualizar()
                    if card.winfo_manager() == "pack":
                        card.pack_configure(padx=(0, 8))
                    card.tremendo = False
                    return
                l = desloc[i]
                if card.winfo_manager() == "pack":
                    card.pack_configure(padx=(l, 8 - l))
                card.fundo_forcado = c["alerta"] if ((i // 2) % 2 == 0 and i < 8) else None
                card.borda_forcada = c["perigo"] if i < 8 else None
                card.atualizar()
                self.after(35, passo, i + 1)
            except tk.TclError:
                card.tremendo = False

        passo()

    def copiar(self, i, card, original):
        c = self.c
        if i < 0:
            return

        usar_intervalo = self.cfg.get("usar_intervalo", True)
        if usar_intervalo and (time.monotonic() - self._ultima_copia < INTERVALO_COPIA):
            self._tremer_cartao(card)
            self.mostrar_rodape("Aguarde um instante para copiar de novo", c["perigo"],
                                volta="clique para copiar")
            return

        texto = self.dados[i]["texto"]

        if not self._copiar_texto(texto):
            self.mostrar_rodape("Não foi possível copiar. Tente de novo.", c["perigo"],
                                volta="clique para copiar")
            return

        self._ultima_copia = time.monotonic()
        card.set_categoria("COPIADO", c["ok"])
        card.borda_forcada = c["ok"]
        card.atualizar()

        def restaurar():
            try:
                card.set_categoria(original, c["acento"])
                card.borda_forcada = None
                card.atualizar()
            except tk.TclError:
                pass

        anterior = getattr(card, "restaurar_id", None)
        if anterior:
            self.after_cancel(anterior)
        card.restaurar_id = self.after(800, restaurar)

    def adicionar(self):
        r = Dialogo(self, self.c, self.f, self.categorias()).resultado
        if r:
            self.dados.append(r)
            self.salvar()
            self.listar()

    def editar(self, i):
        self.resetar_destaques()
        if i < 0:
            return
        r = Dialogo(self, self.c, self.f, self.categorias(), self.dados[i]).resultado
        if r:
            self.dados[i] = r
            self.salvar()
            self.listar()

    def excluir(self, i):
        self.resetar_destaques()
        if i < 0:
            return
        dlg = ConfirmarDialogo(self, self.c, self.f, "Excluir frase",
                               "Tem certeza de que deseja excluir esta frase?")
        if dlg.resultado:
            del self.dados[i]
            self.salvar()
            self.listar()
        else:
            self.resetar_destaques()

    def abrir_configuracoes(self):
        """Abre a tela de opções de configuração."""
        DialogoConfiguracoes(self, self.c, self.f, self.tema)

    def alternar_tema(self):
        self.tema = "escuro" if self.tema == "claro" else "claro"
        self.cfg["tema"] = self.tema
        _gravar(ARQUIVO_CFG, self.cfg)
        self.construir()


if __name__ == "__main__":
    App().mainloop()

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
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

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
                  acento_txt="#ffffff", chip="#e6e9f0", ok="#2e9e5b", perigo="#d64545"),
    "escuro": dict(bg="#15171c", card="#20232a", borda="#2f333c", texto="#e8eaed",
                   suave="#9aa3b0", acento="#7c93ff", acento_hover="#9aabff",
                   acento_txt="#10131a", chip="#2a2e37", ok="#4cc38a", perigo="#ff6b6b"),
}

# Tamanho e posição da janela principal
LARGURA, ALTURA = 360, 520
MARGEM = 12
CANTO = "inferior-direito"    # opções: inferior-direito, inferior-esquerdo, superior-direito, superior-esquerdo

# Clipboard (Linux): True = usa xclip/wl-copy quando existirem (o texto sobrevive ao fechar o app)
#                    False = usa só o clipboard do Tkinter (o texto some ao fechar o app)
USAR_CLIPBOARD_EXTERNO = True

# Barra lateral (quando o app não está fixado e perde o foco)
RECOLHER_AUTOMATICO = True    # False = nunca recolhe
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
# O clipboard do Tkinter falha de forma silenciosa quando a cópia é rápida:
#  - Windows: se outro programa (histórico do Windows, gerenciador de clipboard,
#    área de trabalho remota) estiver com o clipboard aberto, o Tk não consegue
#    abri-lo, ignora o erro e a frase anterior continua lá.
#  - Linux: o xclip/wl-copy "vão para segundo plano" e o processo antigo podia
#    assumir a seleção depois do novo.
# Por isso a cópia é feita de forma direta, com tentativas e conferência.
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


def listar_monitores(root):
    """Retorna [(x, y, largura, altura), ...] de cada monitor conectado."""
    mons = _monitores_windows() if WINDOWS else _monitores_linux()
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


def desenhar_icone(cv, tipo, ox, oy, s, cor, bg):
    """Desenha um ícone simples no canvas (idêntico em qualquer sistema).
    ox, oy = canto superior esquerdo; s = tamanho; cor = traço; bg = fundo atual."""
    def P(x, y):
        return ox + x * s, oy + y * s

    w = max(2, round(s / 8))

    if tipo in ("cadeado", "aberto"):
        cv.create_rectangle(*P(.2, .46), *P(.8, .92), fill=cor, outline=cor)
        if tipo == "cadeado":
            cv.create_arc(*P(.3, .10), *P(.7, .62), start=0, extent=180,
                          style="arc", outline=cor, width=w)
            cv.create_line(*P(.3, .36), *P(.3, .48), fill=cor, width=w)
            cv.create_line(*P(.7, .36), *P(.7, .48), fill=cor, width=w)
        else:
            cv.create_arc(*P(.3, .0), *P(.7, .52), start=0, extent=180,
                          style="arc", outline=cor, width=w)
            cv.create_line(*P(.3, .26), *P(.3, .48), fill=cor, width=w)
        cv.create_oval(*P(.44, .60), *P(.56, .74), fill=bg, outline=bg)

    elif tipo == "lua":
        cv.create_oval(*P(.15, .15), *P(.85, .85), fill=cor, outline=cor)
        cv.create_oval(*P(.38, .04), *P(1.0, .68), fill=bg, outline=bg)

    elif tipo == "sol":
        cv.create_oval(*P(.30, .30), *P(.70, .70), fill=cor, outline=cor)
        for i in range(8):
            a = i * math.pi / 4
            cv.create_line(*P(.5 + .33 * math.cos(a), .5 + .33 * math.sin(a)),
                           *P(.5 + .48 * math.cos(a), .5 + .48 * math.sin(a)),
                           fill=cor, width=w)

    elif tipo == "grip":
        r = .09
        for cx in (.33, .67):
            for cy in (.2, .5, .8):
                cv.create_oval(*P(cx - r, cy - r), *P(cx + r, cy + r), fill=cor, outline=cor)


class IconeBotao(tk.Canvas):
    """Botão com ícone desenhado, com efeito de hover."""

    def __init__(self, pai, tipo, comando, bg, fg, hover, tam=16, px=9, py=5, cursor="hand2"):
        super().__init__(pai, width=tam + 2 * px, height=tam + 2 * py, bg=bg,
                         highlightthickness=0, bd=0, cursor=cursor)
        self._tipo, self._cmd = tipo, comando
        self._bg, self._fg, self._hover = bg, fg, hover
        self._tam, self._px, self._py = tam, px, py
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
        self.config(bg=bg)
        self.delete("all")
        desenhar_icone(self, self._tipo, self._px, self._py, self._tam, self._fg, bg)

    def configurar(self, tipo, bg, fg, hover):
        self._tipo, self._bg, self._fg, self._hover = tipo, bg, fg, hover
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


def retangulo_arredondado(canvas, x1, y1, x2, y2, r, **kw):
    """Desenha um retângulo com cantos arredondados no canvas."""
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1,
           x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2,
           x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2,
           x1, y2 - r, x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Dialogo(tk.Toplevel):
    def __init__(self, pai, c, fonte, categorias, frase=None):
        super().__init__(pai, bg=c["bg"])
        self.c, self.f = c, fonte
        self.resultado = None
        self.title("Editar frase" if frase else "Nova frase")
        self.geometry("360x400")
        self.minsize(320, 340)
        self.transient(pai)

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
        self.c, self.f = c, fonte
        self.resultado = False
        self.title(titulo)
        self.geometry("325x150")
        self.minsize(300, 140)
        self.transient(pai)

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
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window()

    def ok(self):
        self.resultado = True
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Frases Prontas")
        self.minsize(300, 360)
        self._icone_janela()
        self.dados = _ler(ARQUIVO, list(EXEMPLOS))
        self.cfg = _ler(ARQUIVO_CFG, {})
        self.tema = self.cfg.get("tema", "claro")
        self.fixado = bool(self.cfg.get("fixado", False))
        self.categoria = "Todas"
        self.busca = tk.StringVar()
        self.busca.trace_add("write", lambda *_: self.listar())
        self.f = escolher_fonte(self)
        self.largura_texto = 560
        self._toast_id = None
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

        # barra lateral (recolhimento automático)
        self.recolhido = False
        self.aba = None
        self.geo_salva = None
        self._foco_id = None
        self._aba_pronta = False

        self.construir()
        self.posicionar()
        self.aplicar_fixar()

        self.bind_all("<Button-4>", lambda e: self.rolar(-3))
        self.bind_all("<Button-5>", lambda e: self.rolar(3))
        self.bind_all("<MouseWheel>", lambda e: self.rolar(-1 if e.delta > 0 else 1))
        self.bind_all("<B1-Motion>", self.movendo_arrasto)
        self.bind_all("<ButtonRelease-1>", self.soltar_arrasto)
        self.bind("<Control-n>", lambda e: self.adicionar())
        self.bind("<Control-f>", lambda e: self.entrada.focus_set())
        self.bind("<FocusOut>", self._ao_perder_foco)

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
            self.pin_btn.configurar("cadeado", c["acento"], c["acento_txt"], c["acento_hover"])
        else:
            self.pin_btn.configurar("aberto", c["chip"], c["texto"], c["borda"])

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

    def _ao_perder_foco(self, e=None):
        """Agenda uma checagem: o foco saiu do app de verdade?"""
        if not RECOLHER_AUTOMATICO or self.fixado or self.recolhido:
            return
        if self._foco_id:
            self.after_cancel(self._foco_id)
        self._foco_id = self.after(250, self._checar_foco)

    def _checar_foco(self):
        self._foco_id = None
        if self.fixado or self.recolhido or self.drag_window:
            return
        try:
            if self.state() != "normal":
                return
            # Algum widget do app (inclusive diálogos) tem foco: não recolhe
            if self.focus_displayof() is not None:
                return
            # Sem foco, mas o mouse está sobre a janela (ex.: arrastando pela
            # barra de título): espera e confere de novo
            if self._ponteiro_na_janela():
                self._foco_id = self.after(500, self._checar_foco)
                return
            self.recolher()
        except (tk.TclError, KeyError):
            pass

    def recolher(self):
        if self.recolhido or self.fixado:
            return
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

    def expandir(self):
        if not self.recolhido:
            return
        self.recolhido = False
        if self.aba:
            try:
                self.aba.destroy()
            except tk.TclError:
                pass
            self.aba = None
        self.deiconify()
        if self.geo_salva:
            self.geometry(self.geo_salva)
        self.aplicar_fixar()
        self.lift()
        self.focus_force()

    # ---------- interface ----------
    def posicionar(self):
        """Janela pequena encaixada no canto da tela."""
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        larg, alt = min(LARGURA, sw - 2 * MARGEM), min(ALTURA, sh - 2 * MARGEM)
        x = MARGEM if "esquerdo" in CANTO else sw - larg - MARGEM
        y = MARGEM + 30 if "superior" in CANTO else sh - alt - MARGEM - 40
        self.geometry(f"{larg}x{alt}+{x}+{max(y, 0)}")

    def construir(self):
        for w in self.winfo_children():
            w.destroy()
        self.cache = {}
        self.cartoes_widgets = []
        self.chip_labels = {}
        c = self.c = TEMAS[self.tema]
        f = self.f
        self.configure(bg=c["bg"])

        # Cabeçalho: [título] ............ [+ Nova] [cadeado] [tema]
        topo = tk.Frame(self, bg=c["bg"])
        topo.pack(fill="x", padx=14, pady=(14, 8))
        tk.Label(topo, text="Frases Prontas", bg=c["bg"], fg=c["texto"],
                 font=(f, 15, "bold")).pack(side="left")

        # (ordem de empacotamento com side="right": o primeiro fica mais à direita)
        IconeBotao(topo, "lua" if self.tema == "claro" else "sol", self.alternar_tema,
                   c["chip"], c["texto"], c["borda"]).pack(side="right", padx=(8, 0))

        # Cadeado (liga/desliga fixar)
        self.pin_btn = IconeBotao(topo, "aberto", self.alternar_fixar,
                                  c["chip"], c["texto"], c["borda"])
        self.pin_btn.pack(side="right", padx=(8, 0))
        self.atualizar_pin()

        botao(topo, "+ Nova", self.adicionar, c["acento"], c["acento_txt"],
              c["acento_hover"], f, True, 12, 5).pack(side="right")

        # Busca
        mb = moldura(self, c)
        mb.pack(fill="x", padx=14)
        tk.Label(mb, text="Buscar", bg=c["card"], fg=c["suave"], font=(f, 10)).pack(side="left", padx=(12, 6))
        self.entrada = tk.Entry(mb, textvariable=self.busca, relief="flat", bd=0, bg=c["card"],
                                fg=c["texto"], insertbackground=c["texto"], font=(f, 11),
                                highlightthickness=0)
        self.entrada.pack(side="left", fill="x", expand=True, pady=7, padx=(0, 10))
        self.entrada.bind("<FocusIn>", lambda e: mb.config(highlightbackground=c["acento"], highlightcolor=c["acento"]))
        self.entrada.bind("<FocusOut>", lambda e: mb.config(highlightbackground=c["borda"], highlightcolor=c["borda"]))

        # Categorias
        self.chips = tk.Frame(self, bg=c["bg"])
        self.chips.pack(fill="x", padx=14, pady=(10, 4))
        self.desenhar_chips()

        # Rodapé
        self.rodape = tk.Label(self, bg=c["bg"], fg=c["suave"], font=(f, 9), anchor="w")
        self.rodape.pack(side="bottom", fill="x", padx=14, pady=(4, 10))

        # Lista com rolagem
        area = tk.Frame(self, bg=c["bg"])
        area.pack(fill="both", expand=True, padx=(14, 6))
        self.canvas = tk.Canvas(area, bg=c["bg"], highlightthickness=0, bd=0)
        sb = tk.Scrollbar(area, orient="vertical", command=self.canvas.yview, width=8, bd=0,
                          relief="flat", troughcolor=c["bg"], bg=c["borda"],
                          activebackground=c["suave"], highlightthickness=0)
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
        for cw in self.cache.values():
            try:
                cw.txt.config(wraplength=self.largura_texto)
            except tk.TclError:
                pass

    def rolar(self, n):
        try:
            if self.lista.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(n, "units")
        except tk.TclError:
            pass

    def desenhar_chips(self):
        c = self.c
        cats = ["Todas"] + self.categorias()
        if self.categoria not in cats:
            self.categoria = "Todas"

        if list(self.chip_labels.keys()) != cats:
            for w in self.chips.winfo_children():
                w.destroy()
            self.chip_labels = {}
            for nome in cats:
                l = tk.Label(self.chips, text=nome, cursor="hand2", padx=10, pady=3,
                             font=(self.f, 10))
                l.pack(side="left", padx=(0, 6))
                l.bind("<Button-1>", lambda e, n=nome: self.escolher_categoria(n))
                self.chip_labels[nome] = l

        for nome, l in self.chip_labels.items():
            ativo = nome == self.categoria
            l.config(bg=c["acento"] if ativo else c["chip"],
                     fg=c["acento_txt"] if ativo else c["texto"],
                     font=(self.f, 10, "bold" if ativo else "normal"))

    def escolher_categoria(self, nome):
        if nome == self.categoria:
            return
        self.categoria = nome
        self.desenhar_chips()
        self.listar()

    def resetar_destaques(self):
        c = self.c
        for cw in self.cache.values():
            try:
                cw.config(highlightbackground=c["borda"], highlightcolor=c["borda"])
            except tk.TclError:
                pass

    def listar(self):
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
        c, f = self.c, self.f
        card = tk.Frame(self.lista, bg=c["card"], highlightthickness=1,
                        highlightbackground=c["borda"], highlightcolor=c["borda"], cursor="hand2")
        card.data_index = -1
        card.d = d

        grip = IconeBotao(card, "grip", lambda d=d: self.iniciar_arrasto(d),
                          c["card"], c["suave"], c["card"], tam=14, px=5, py=4, cursor="fleur")
        grip.pack(side="left", padx=(4, 0), pady=5)

        corpo = tk.Frame(card, bg=c["card"], cursor="hand2")
        corpo.pack(side="left", fill="both", expand=True, padx=4, pady=9)
        cat = tk.Label(corpo, text=d["categoria"].upper(), bg=c["card"], fg=c["acento"],
                       font=(f, 8, "bold"), anchor="w", cursor="hand2")
        cat.pack(fill="x")
        txt = tk.Label(corpo, text=d["texto"], bg=c["card"], fg=c["texto"], font=(f, 11),
                       justify="left", anchor="w", wraplength=self.largura_texto, cursor="hand2")
        txt.pack(fill="x", pady=(3, 0))
        card.txt = txt

        acoes = tk.Frame(card, bg=c["card"])
        acoes.pack(side="right", padx=(4, 8), pady=2)

        for texto, cor, fn in (("Editar", c["acento"], lambda: self.editar(self._indice(card.d))),
                               ("Excluir", c["perigo"], lambda: self.excluir(self._indice(card.d)))):
            b = tk.Label(acoes, text=texto, bg=c["card"], fg=cor,
                         font=(f, 9), cursor="hand2", padx=3, pady=3)
            b.pack(side="top", anchor="e", pady=1)
            b.bind("<Enter>", lambda e, b=b, cor=cor: b.config(fg=cor, font=(f, 9, "bold")))
            b.bind("<Leave>", lambda e, b=b, cor=cor: b.config(fg=cor, font=(f, 9)))
            b.bind("<Button-1>", lambda e, fn=fn: fn())

        def entrar(e):
            card.config(highlightbackground=c["acento"], highlightcolor=c["acento"])

        def sair(e):
            x, y = card.winfo_pointerxy()
            w = card.winfo_containing(x, y)
            if w is None or not e_descendente(w, card):
                card.config(highlightbackground=c["borda"], highlightcolor=c["borda"])

        for w in (card, corpo, cat, txt):
            w.bind("<Enter>", entrar)
            w.bind("<Leave>", sair)
            w.bind("<Button-1>", lambda e, d=d, cat=cat, card=card, orig=d["categoria"].upper():
                   self.copiar(self._indice(d), cat, card, orig))
        return card

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
                jan.attributes("-topmost", True)   # fica acima da janela principal fixada
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
            pass                     # cai no clipboard do Tkinter abaixo

        try:
            self.clipboard_clear()
            self.clipboard_append(texto)
            self.update_idletasks()  # (sem update(): evita reentrância em cliques rápidos)
            return True
        except tk.TclError:
            return False

    def copiar(self, i, cat_lbl, card, original):
        c = self.c
        if i < 0:
            return
        texto = self.dados[i]["texto"]

        if not self._copiar_texto(texto):
            self.mostrar_rodape("Não foi possível copiar. Tente de novo.", c["perigo"],
                                volta="clique para copiar")
            return

        cat_lbl.config(text="COPIADO", fg=c["ok"])
        card.config(highlightbackground=c["ok"], highlightcolor=c["ok"])

        def restaurar():
            try:
                cat_lbl.config(text=original, fg=c["acento"])
                card.config(highlightbackground=c["borda"], highlightcolor=c["borda"])
            except tk.TclError:
                pass

        # Cliques repetidos no mesmo cartão: o aviso antigo não pode apagar o novo
        anterior = getattr(card, "restaurar_id", None)
        if anterior:
            self.after_cancel(anterior)
        card.restaurar_id = self.after(1300, restaurar)
        self.mostrar_rodape("Copiado!", c["ok"], volta="clique para copiar")

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

    def alternar_tema(self):
        self.tema = "escuro" if self.tema == "claro" else "claro"
        self.cfg["tema"] = self.tema
        _gravar(ARQUIVO_CFG, self.cfg)
        self.construir()


if __name__ == "__main__":
    App().mainloop()

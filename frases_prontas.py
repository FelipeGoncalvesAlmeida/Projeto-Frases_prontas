#!/usr/bin/env python3
"""Frases Prontas - copie frases salvas com um clique (Linux e Windows).

Linux - dependências: python3 e python3-tk
  Debian/Ubuntu/Mint: sudo apt install python3 python3-tk
  Fedora:             sudo dnf install python3-tkinter
  Arch:               sudo pacman -S tk

Linux - opcionais:
  xclip ou wl-clipboard  (mantém o texto copiado após fechar o app)
  xrandr                 (detecta vários monitores)
  zenity ou kdialog      (janela de salvar/abrir arquivo igual à do navegador)

Windows: só o Python oficial (já inclui o Tkinter). Nada mais a instalar.

Ícone da janela (opcional): coloque "icone.png" (Linux) ou "icone.ico" (Windows)
na mesma pasta do script.
"""
import copy
import json
import colorsys
from contextlib import contextmanager
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
from tkinter import filedialog

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
ARQUIVO_TRAVA = os.path.join(CONFIG_DIR, "instancia.lock")     # trava de instância única (Linux)
ARQUIVO_MOSTRAR = os.path.join(CONFIG_DIR, "mostrar.flag")     # 2ª instância pede: "apareça"

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

# Ícones do cabeçalho: espessura do traço em pixels (1 = fino/minimalista, 2 = mais marcado)
ESPESSURA_ICONE = 1

# Telas com escala de 125% / 150% (Windows). Sem ajuste, o Windows amplia a janela como se
# fosse uma imagem e o texto fica levemente borrado. Opções:
#   "gdi"    = o Windows desenha o texto já na escala real e o tamanho da janela não muda
#              (recomendado; vale para o Windows 10 recente, nos antigos fica como "nenhum")
#   "nitido" = texto 100% nítido, mas a janela fica fisicamente menor em telas de alta resolução
#   "nenhum" = como antes (o Windows amplia a imagem)
DPI_MODO = "gdi"

EXEMPLOS = [
    {"categoria": "Atendimento", "texto": "Bom dia"},
    {"categoria": "Atendimento", "texto": "Em que posso ajudar ?"},
    {"categoria": "Atendimento", "texto": "Me passa o acesso do AnyDesk por gentileza"},
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


def _gravar(caminho, dados, tentativas=4):
    """Grava em arquivo temporário e troca de uma vez: não deixa o JSON pela metade.
    No Windows, antivírus ou programas de sincronização podem segurar o arquivo por
    instantes (PermissionError): por isso tenta algumas vezes antes de desistir.
    Se não conseguir, levanta OSError (quem chamou decide como avisar)."""
    tmp = caminho + ".tmp"
    for n in range(tentativas):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
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
            return
        except OSError:
            if n == tentativas - 1:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.15)


# ---------- backup (exportar / restaurar) ----------
BACKUP_APP = "FrasesProntas"
BACKUP_VERSAO = 1
# Só vai no backup o que é preferência pessoal. "Fixar" e "Iniciar com o computador"
# dependem da máquina, então ficam de fora.
CFG_NO_BACKUP = ("tema", "cor_personalizada", "usar_intervalo")


def montar_backup(frases, cfg):
    return {
        "app": BACKUP_APP,
        "versao": BACKUP_VERSAO,
        "exportado_em": time.strftime("%Y-%m-%d %H:%M:%S"),
        "frases": frases,
        "config": {k: cfg[k] for k in CFG_NO_BACKUP if k in cfg},
    }


def gravar_backup(caminho, frases, cfg):
    """Grava o backup onde o usuário escolheu (arquivo temporário + troca, sem deixar .bak)."""
    tmp = caminho + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(montar_backup(frases, cfg), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, caminho)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def ler_backup(caminho):
    """Lê e valida um backup. Devolve (frases, config). Levanta ValueError se for inválido.
    'config' é None quando o arquivo não traz configurações (ex.: um frases.json puro)."""
    try:
        with open(caminho, "r", encoding="utf-8-sig") as f:
            obj = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("O arquivo não é um backup válido.")

    cfg = None
    if isinstance(obj, dict):
        if obj.get("app") != BACKUP_APP:
            raise ValueError("Este arquivo não é um backup do Frases Prontas.")
        versao = obj.get("versao", 1)
        if not isinstance(versao, int) or versao > BACKUP_VERSAO:
            raise ValueError("Este backup foi criado por uma versão mais nova do app.\n"
                             "Atualize o Frases Prontas e tente de novo.")
        brutas = obj.get("frases")
        bruto_cfg = obj.get("config")
        cfg = {}
        if isinstance(bruto_cfg, dict):
            if bruto_cfg.get("tema") in TEMAS:
                cfg["tema"] = bruto_cfg["tema"]
            cor = hex_valido(bruto_cfg.get("cor_personalizada"))
            if cor:
                cfg["cor_personalizada"] = cor
            if isinstance(bruto_cfg.get("usar_intervalo"), bool):
                cfg["usar_intervalo"] = bruto_cfg["usar_intervalo"]
    elif isinstance(obj, list):          # também aceita o frases.json "cru"
        brutas = obj
    else:
        raise ValueError("O arquivo não é um backup válido.")

    if not isinstance(brutas, list):
        raise ValueError("O arquivo não é um backup válido.")
    frases = []
    for d in brutas:
        if isinstance(d, dict) and isinstance(d.get("texto"), str) and d["texto"].strip():
            item = dict(d)
            cat = d.get("categoria")
            item["categoria"] = cat.strip() if isinstance(cat, str) and cat.strip() else "Geral"
            frases.append(item)
    if not frases:
        raise ValueError("O backup não contém nenhuma frase.")
    return frases, cfg


def mesclar_frases(atuais, novas):
    """Junta as frases do backup às atuais, sem duplicar (mesma categoria + mesmo texto).
    Devolve (lista_final, quantidade_adicionada)."""
    vistas = {(d["categoria"], d["texto"]) for d in atuais}
    final, add = list(atuais), 0
    for d in novas:
        chave = (d["categoria"], d["texto"])
        if chave not in vistas:
            vistas.add(chave)
            final.append(d)
            add += 1
    return final, add


def sanear_frases(bruto):
    """Valida a lista lida de frases.json. Devolve (frases_validas, descartados).
    'descartados' = quantos itens foram ignorados, ou None se o conteúdo nem era uma lista."""
    if not isinstance(bruto, list):
        return [], None
    frases = []
    for d in bruto:
        if isinstance(d, dict) and isinstance(d.get("texto"), str) and d["texto"].strip():
            item = dict(d)
            cat = d.get("categoria")
            item["categoria"] = cat.strip() if isinstance(cat, str) and cat.strip() else "Geral"
            frases.append(item)
    return frases, len(bruto) - len(frases)


def sanear_cfg(bruto):
    """Valida o config.json. Devolve (config_valida, houve_problema).
    Valores fora do formato são descartados (o app volta ao padrão deles)."""
    if not isinstance(bruto, dict):
        return {}, True
    cfg, ruim = dict(bruto), False
    if "tema" in cfg and not (isinstance(cfg["tema"], str) and cfg["tema"] in TEMAS):
        del cfg["tema"]
        ruim = True
    if "cor_personalizada" in cfg:
        cor = hex_valido(cfg["cor_personalizada"]) if isinstance(cfg["cor_personalizada"], str) else None
        if cor:
            cfg["cor_personalizada"] = cor
        else:
            del cfg["cor_personalizada"]
            ruim = True
    for chave in ("fixado", "usar_intervalo", "iniciar_com_sistema"):
        if chave in cfg and not isinstance(cfg[chave], bool):
            del cfg[chave]
            ruim = True
    return cfg, ruim


def guardar_copia_invalida(caminho):
    """Antes de o app sobrescrever um arquivo com problema, guarda uma cópia dele."""
    try:
        if os.path.exists(caminho):
            shutil.copy2(caminho, caminho + ".invalido-" + time.strftime("%Y%m%d-%H%M%S"))
    except OSError:
        pass


def escolher_fonte(root):
    disponiveis = set(tkfont.families(root))
    for nome in ("Segoe UI", "Inter", "Noto Sans", "Ubuntu", "Cantarell", "Open Sans", "DejaVu Sans"):
        if nome in disponiveis:
            return nome
    return tkfont.nametofont("TkDefaultFont").actual("family")


# ---------- iniciar com o computador ----------
APP_ID = "FrasesProntas"
FLAG_RECOLHIDO = "--iniciar-recolhido"      # abre já como barra lateral (usado no login)
AUTOSTART_SUPORTADO = WINDOWS or LINUX
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ARQUIVO_AUTOSTART = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
    "autostart", "frases-prontas.desktop")


def _comando_inicio():
    """Comando (lista) que abre o app no login: o .exe empacotado, ou python + este script."""
    if getattr(sys, "frozen", False):
        return [sys.executable, FLAG_RECOLHIDO]
    exe = sys.executable
    if WINDOWS:                                   # pythonw não abre janela de console
        pw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pw):
            exe = pw
    return [exe, os.path.abspath(__file__), FLAG_RECOLHIDO]


def _exec_desktop(partes):
    """Monta a linha Exec= de um arquivo .desktop (com aspas/escapes quando preciso)."""
    def q(a):
        if a and not re.search(r"[\s\"'\\<>~|&;$*?#()`%]", a):
            return a
        a = a.replace("%", "%%").replace("\\", "\\\\\\\\")
        for ch in ('"', "`", "$"):
            a = a.replace(ch, "\\" + ch)
        return '"' + a + '"'
    return " ".join(q(a) for a in partes)


def autostart_ativo():
    """True se o app está registrado para iniciar com o computador."""
    try:
        if WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, APP_ID)
            return True
        return os.path.exists(ARQUIVO_AUTOSTART)
    except (OSError, ImportError):
        return False


def definir_autostart(ligado):
    """Liga/desliga o início automático. Devolve True se deu certo."""
    try:
        if WINDOWS:
            import winreg
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                if ligado:
                    winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ,
                                      subprocess.list2cmdline(_comando_inicio()))
                else:
                    try:
                        winreg.DeleteValue(k, APP_ID)
                    except FileNotFoundError:
                        pass
        elif LINUX:
            if ligado:
                os.makedirs(os.path.dirname(ARQUIVO_AUTOSTART), exist_ok=True)
                conteudo = ("[Desktop Entry]\n"
                            "Type=Application\n"
                            "Name=Frases Prontas\n"
                            "Comment=Copie frases salvas com um clique\n"
                            "Exec=%s\n"
                            "Terminal=false\n"
                            "X-GNOME-Autostart-enabled=true\n"
                            "X-GNOME-Autostart-Delay=3\n" % _exec_desktop(_comando_inicio()))
                tmp = ARQUIVO_AUTOSTART + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(conteudo)
                os.replace(tmp, ARQUIVO_AUTOSTART)
            else:
                try:
                    os.remove(ARQUIVO_AUTOSTART)
                except FileNotFoundError:
                    pass
        else:
            return False
        return True
    except Exception:
        return False


# ---------- instância única ----------
_TRAVA = None        # mantém a trava viva enquanto o app roda (o sistema solta ao encerrar)


def obter_instancia_unica():
    """True se esta é a única instância aberta. Se não for possível verificar, devolve
    True: um problema na trava nunca pode impedir o app de abrir."""
    global _TRAVA
    try:
        if WINDOWS:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            k32.CreateMutexW.restype = wintypes.HANDLE
            h = k32.CreateMutexW(None, False, "Local\\FrasesProntas_instancia_unica")
            if not h:
                return True
            _TRAVA = h
            return ctypes.get_last_error() != 183          # 183 = ERROR_ALREADY_EXISTS
        import fcntl
        os.makedirs(CONFIG_DIR, exist_ok=True)
        f = open(ARQUIVO_TRAVA, "a+")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            f.close()
            return False
        _TRAVA = f
        return True
    except Exception:
        return True


def pedir_para_mostrar():
    """Avisa a instância que já está aberta para aparecer (a aberta confere o arquivo)."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(ARQUIVO_MOSTRAR, "w", encoding="utf-8") as f:
            f.write("1")
    except OSError:
        pass


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


def _arco(cx, cy, r, a0, delta, n=22):
    """Pontos de um arco de circunferência (a0 e delta em radianos)."""
    return [(cx + r * math.cos(a0 + delta * i / n), cy + r * math.sin(a0 + delta * i / n))
            for i in range(n + 1)]


def _pontos_lua():
    """Contorno de uma lua crescente (unidades 0..1): círculo grande menos um círculo
    menor deslocado para o canto superior direito. Devolve lista de (x, y)."""
    c1x, c1y, r1 = .47, .53, .41          # corpo da lua
    c2x, c2y, r2 = .66, .37, .35          # círculo que "morde" a lua
    d = math.hypot(c2x - c1x, c2y - c1y)
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0))
    ux, uy = (c2x - c1x) / d, (c2y - c1y) / d
    mx, my = c1x + a * ux, c1y + a * uy
    p = (mx + h * uy, my - h * ux)        # as duas pontas da lua
    q = (mx - h * uy, my + h * ux)

    # arco externo (círculo 1) de p até q, pelo lado que NÃO está dentro do círculo 2
    a0 = math.atan2(p[1] - c1y, p[0] - c1x)
    a1 = math.atan2(q[1] - c1y, q[0] - c1x)
    delta = (a1 - a0) % (2 * math.pi)
    mid = (c1x + r1 * math.cos(a0 + delta / 2), c1y + r1 * math.sin(a0 + delta / 2))
    if math.hypot(mid[0] - c2x, mid[1] - c2y) < r2:
        delta -= 2 * math.pi
    externo = _arco(c1x, c1y, r1, a0, delta)

    # arco interno (círculo 2) de q de volta até p, pelo lado que está dentro do círculo 1
    b0 = math.atan2(q[1] - c2y, q[0] - c2x)
    b1 = math.atan2(p[1] - c2y, p[0] - c2x)
    delta2 = (b1 - b0) % (2 * math.pi)
    mid2 = (c2x + r2 * math.cos(b0 + delta2 / 2), c2y + r2 * math.sin(b0 + delta2 / 2))
    if math.hypot(mid2[0] - c1x, mid2[1] - c1y) > r1:
        delta2 -= 2 * math.pi
    interno = _arco(c2x, c2y, r2, b0, delta2)
    return externo + interno[1:-1]


def _haste_cadeado(aberto):
    """Haste (o arco de cima) do cadeado, em unidades 0..1.
    Fechado: arco completo com as duas pernas presas ao corpo.
    Aberto: haste levantada e solta, sem a perna da direita."""
    cx, cy, r, n = .5, .29, .21, 24
    esq = cx - r
    pts = [(esq, .46), (esq, cy)]
    giro = 168.5 if aberto else 180
    for i in range(1, n + 1):
        a = math.radians(180 + giro * i / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    if not aberto:
        pts.append((cx + r, .46))
    return pts


def _pontos_engrenagem(dentes=8, r_ponta=.46, r_base=.36, a_ponta=11, a_base=17):
    """Contorno de uma engrenagem simétrica (unidades 0..1). Os dentes têm ponta e vale
    em arco (não em linha reta), então o desenho fica redondo e sem 'quinas' estranhas.
    Com 8 dentes há um dente em cima, embaixo, à esquerda, à direita e nas diagonais."""
    passo = 360 / dentes
    pts = []
    for i in range(dentes):
        c = i * passo
        for ang, r in ((c - a_base, r_base), (c - a_ponta, r_ponta), (c, r_ponta),
                       (c + a_ponta, r_ponta), (c + a_base, r_base),
                       (c + passo / 2, r_base)):          # meio do vale
            rad = math.radians(ang)
            pts.append((.5 + r * math.cos(rad), .5 + r * math.sin(rad)))
    return pts


def desenhar_icone(cv, tipo, ox, oy, s, cor, bg):
    """Ícones minimalistas de traço fino, idênticos em qualquer sistema.
    ox, oy = canto superior esquerdo; s = tamanho; cor = traço; bg = fundo atual."""
    def P(x, y):
        return ox + x * s, oy + y * s

    w = ESPESSURA_ICONE

    if tipo in ("cadeado", "aberto"):
        # só contorno: a haste fechada (presa ao corpo) ou levantada e solta
        haste = [v for x, y in _haste_cadeado(tipo == "aberto") for v in P(x, y)]
        cv.create_line(*haste, fill=cor, width=w, capstyle="round", joinstyle="round")
        retangulo_arredondado(cv, *P(.16, .46), *P(.84, .92), s * .09,
                              fill="", outline=cor, width=w)

    elif tipo == "lua":
        pts = [v for x, y in _pontos_lua() for v in P(x, y)]
        cv.create_polygon(*pts, fill="", outline=cor, width=w, joinstyle="round")

    elif tipo == "sol":
        cv.create_oval(*P(.30, .30), *P(.70, .70), outline=cor, width=w)
        for i in range(8):
            a = i * math.pi / 4
            cv.create_line(*P(.5 + .34 * math.cos(a), .5 + .34 * math.sin(a)),
                           *P(.5 + .46 * math.cos(a), .5 + .46 * math.sin(a)),
                           fill=cor, width=w, capstyle="round")

    elif tipo == "ajustes":
        # três linhas com um botão deslizante cada (configurações, bem minimalista)
        for y, x in ((.24, .66), (.50, .34), (.76, .60)):
            cv.create_line(*P(.12, y), *P(.88, y), fill=cor, width=w, capstyle="round")
            r = .10
            cv.create_oval(*P(x - r, y - r), *P(x + r, y + r), fill=bg, outline=cor, width=w)

    elif tipo == "grip":
        r = .09
        for cx in (.33, .67):
            for cy in (.2, .5, .8):
                cv.create_oval(*P(cx - r, cy - r), *P(cx + r, cy + r), fill=cor, outline=cor)

    elif tipo == "engrenagem":
        pts = [v for x, y in _pontos_engrenagem() for v in P(x, y)]
        cv.create_polygon(*pts, fill="", outline=cor, width=w, joinstyle="round")
        cv.create_oval(*P(.35, .35), *P(.65, .65), outline=cor, width=w)

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
        # usa o monitor onde o cartão está (não só a tela principal)
        mx, my, mw, mh = monitor_do_ponto(listar_monitores(self),
                                          x_dir - 1, y_base - altura_botao / 2)
        x = max(mx, min(x_dir - w, mx + mw - w))
        y = y_base
        if y + h > my + mh:                  # sem espaço embaixo: abre para cima
            y = max(my, y_base - altura_botao - h)
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
            MensagemDialogo(self, self.c, self.f, "Frase vazia", "Digite a frase.", tipo="aviso")
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


# Símbolos dos ícones de status, em coordenadas 0..1: lista de "cápsulas"
# (x1, y1, x2, y2, raio). Segmento com raio = traço de ponta redonda; ponto = x1,y1 == x2,y2.
_SIMBOLOS_STATUS = {
    "ok":    [(0.29, 0.52, 0.44, 0.67, 0.062), (0.44, 0.67, 0.72, 0.35, 0.062)],
    "aviso": [(0.50, 0.26, 0.50, 0.52, 0.066), (0.50, 0.73, 0.50, 0.73, 0.075)],
    "erro":  [(0.50, 0.26, 0.50, 0.52, 0.066), (0.50, 0.73, 0.50, 0.73, 0.075)],
    "info":  [(0.50, 0.27, 0.50, 0.27, 0.075), (0.50, 0.45, 0.50, 0.73, 0.066)],
}


def _dist_segmento(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    comp = dx * dx + dy * dy
    t = 0.0 if comp == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / comp))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def rasterizar_icone_status(tipo, tam, fundo, circulo, simbolo, ss=4):
    """Desenha um ícone redondo com símbolo, com bordas suavizadas (supersampling).
    Devolve linhas de pixels '#rrggbb'. Pura: não usa Tk."""
    cap = _SIMBOLOS_STATUS.get(tipo, _SIMBOLOS_STATUS["info"])
    cf, cc, cs = hex_para_rgb(fundo), hex_para_rgb(circulo), hex_para_rgb(simbolo)
    raio = 0.5 - 0.5 / tam                    # deixa meio pixel de folga na borda
    linhas = []
    for j in range(tam):
        linha = []
        for i in range(tam):
            r = g = b = 0
            for sj in range(ss):
                for si in range(ss):
                    x = (i + (si + 0.5) / ss) / tam
                    y = (j + (sj + 0.5) / ss) / tam
                    if any(_dist_segmento(x, y, *seg[:4]) <= seg[4] for seg in cap):
                        cor = cs
                    elif math.hypot(x - 0.5, y - 0.5) <= raio:
                        cor = cc
                    else:
                        cor = cf
                    r += cor[0]; g += cor[1]; b += cor[2]
            n = ss * ss
            linha.append(rgb_para_hex(r / n, g / n, b / n))
        linhas.append(linha)
    return linhas


def icone_status(master, tipo, tam, fundo, circulo, simbolo):
    """tk.PhotoImage suavizado do ícone de status (guarde uma referência a ele!)."""
    linhas = rasterizar_icone_status(tipo, tam, fundo, circulo, simbolo)
    img = tk.PhotoImage(master=master, width=tam, height=tam)
    img.put(" ".join("{" + " ".join(l) + "}" for l in linhas))
    return img


def _exibir_modal(janela, pai):
    """Dimensiona a janela pelo conteúdo, centraliza sobre 'pai' e espera fechar."""
    janela.update_idletasks()
    w, h = max(janela.winfo_reqwidth(), 340), janela.winfo_reqheight()
    janela.geometry(f"{w}x{h}")
    posicionar_sobre(janela, pai, w, h)
    janela.deiconify()
    try:
        janela.wait_visibility()
        janela.grab_set()
    except tk.TclError:
        pass
    janela.wait_window()


class MensagemDialogo(tk.Toplevel):
    """Aviso ou confirmação no estilo do app (substitui os messagebox cinzas do Tk).
    tipo: "info", "ok", "aviso" ou "erro". resultado = True se clicou no botão principal."""
    def __init__(self, pai, c, fonte, titulo, mensagem, tipo="info", rotulo_ok="OK",
                 rotulo_cancelar=None, perigo=False):
        super().__init__(pai, bg=c["bg"])
        self.withdraw()
        self.resultado = False
        self.title(titulo)
        self.resizable(False, False)
        self.transient(pai)

        cor = {"info": c["acento"], "ok": c["ok"], "aviso": c["perigo"],
               "erro": c["perigo"]}.get(tipo, c["acento"])
        cor_txt = c["acento_txt"] if tipo == "info" else "#ffffff"

        corpo = tk.Frame(self, bg=c["bg"])
        corpo.pack(fill="both", expand=True, padx=18, pady=16)

        topo = tk.Frame(corpo, bg=c["bg"])
        topo.pack(fill="x", pady=(0, 10))
        self._img = icone_status(self, tipo, 30, c["bg"], cor, cor_txt)   # guarda a referência
        tk.Label(topo, image=self._img, bg=c["bg"], bd=0).pack(side="left", padx=(0, 10))
        tk.Label(topo, text=titulo, bg=c["bg"], fg=c["texto"],
                 font=(fonte, 12, "bold")).pack(side="left")

        tk.Label(corpo, text=mensagem, bg=c["bg"], fg=c["suave"], font=(fonte, 10),
                 wraplength=310, justify="left").pack(anchor="w", pady=(0, 16))

        rodape = tk.Frame(corpo, bg=c["bg"])
        rodape.pack(fill="x")
        if perigo:
            principal = (c["perigo"], "#ffffff", c["perigo"])
        else:
            principal = (c["acento"], c["acento_txt"], c["acento_hover"])
        botao(rodape, rotulo_ok, self.ok, *principal, fonte, True, 16, 7).pack(side="right")
        if rotulo_cancelar:
            botao(rodape, rotulo_cancelar, self.destroy, c["chip"], c["texto"], c["borda"],
                  fonte, False, 14, 7).pack(side="right", padx=8)

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self.ok())
        self.focus_set()
        _exibir_modal(self, pai)

    def ok(self):
        self.resultado = True
        self.destroy()


class RestaurarDialogo(tk.Toplevel):
    """Escolha de como restaurar o backup: duas opções em cartões clicáveis.
    resultado = "mesclar", "substituir" ou None (cancelou)."""

    def __init__(self, pai, c, fonte, n_frases):
        super().__init__(pai, bg=c["bg"])
        self.withdraw()
        self.resultado = None
        self.c, self.f = c, fonte
        self.title("Restaurar backup")
        self.resizable(False, False)
        self.transient(pai)

        corpo = tk.Frame(self, bg=c["bg"])
        corpo.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(corpo, text="Restaurar backup", bg=c["bg"], fg=c["texto"],
                 font=(fonte, 13, "bold")).pack(anchor="w")
        tk.Label(corpo, text=f"O arquivo tem {n_frases} frase{'s' if n_frases != 1 else ''}. "
                             "Como você quer restaurar?", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 10), wraplength=310, justify="left").pack(anchor="w", pady=(4, 12))

        self._opcao(corpo, "Mesclar",
                    "Mantém as suas frases e adiciona só as que ainda não existem.",
                    c["acento"], "mesclar")
        self._opcao(corpo, "Substituir tudo",
                    "Troca todas as frases pelas do backup e aplica o tema e as cores salvos nele.",
                    c["perigo"], "substituir")

        rodape = tk.Frame(corpo, bg=c["bg"])
        rodape.pack(fill="x", pady=(6, 0))
        botao(rodape, "Cancelar", self.destroy, c["chip"], c["texto"], c["borda"],
              fonte, False, 14, 7).pack(side="right")

        self.bind("<Escape>", lambda e: self.destroy())
        _exibir_modal(self, pai)

    def _opcao(self, pai, titulo, descricao, cor, valor):
        c, f = self.c, self.f
        card = tk.Frame(pai, bg=c["card"], highlightthickness=1,
                        highlightbackground=c["borda"], cursor="hand2")
        card.pack(fill="x", pady=(0, 8))
        interno = tk.Frame(card, bg=c["card"], cursor="hand2")
        interno.pack(fill="x", padx=12, pady=10)
        t = tk.Label(interno, text=titulo, bg=c["card"], fg=cor, font=(f, 11, "bold"),
                     cursor="hand2", anchor="w")
        t.pack(fill="x")
        d = tk.Label(interno, text=descricao, bg=c["card"], fg=c["suave"], font=(f, 9),
                     wraplength=290, justify="left", anchor="w", cursor="hand2")
        d.pack(fill="x", pady=(2, 0))
        partes = (card, interno, t, d)

        def pintar(sobre):
            fundo = c["card_hover"] if sobre else c["card"]
            card.config(bg=fundo, highlightbackground=c["borda_hover"] if sobre else c["borda"])
            for w in partes[1:]:
                w.config(bg=fundo)

        def sair(e=None):
            try:
                w = card.winfo_containing(*card.winfo_pointerxy())
            except (tk.TclError, KeyError):
                w = None
            if not any(w is x for x in partes):
                pintar(False)

        for w in partes:
            w.bind("<Enter>", lambda e: pintar(True))
            w.bind("<Leave>", sair)
            w.bind("<Button-1>", lambda e, v=valor: self._escolher(v))

    def _escolher(self, valor):
        self.resultado = valor
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
        self.geometry("345x500")
        self.resizable(False, False)
        self.transient(pai)
        posicionar_sobre(self, pai, 345, 500)

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

        # Iniciar com o computador
        if AUTOSTART_SUPORTADO:
            tk.Frame(moldura_copia, bg=c["borda"], height=1).pack(fill="x", padx=12)
            sub_inicio = tk.Frame(moldura_copia, bg=c["card"])
            sub_inicio.pack(fill="x", padx=12, pady=10)

            tk.Label(sub_inicio, text="Iniciar com o computador", bg=c["card"], fg=c["texto"],
                     font=(fonte, 10)).pack(side="left")

            ativo = autostart_ativo()
            self.btn_inicio_texto = tk.StringVar(value="Ativado" if ativo else "Desativado")
            self.btn_inicio = tk.Label(
                sub_inicio,
                textvariable=self.btn_inicio_texto,
                bg=c["acento"] if ativo else c["chip"],
                fg=c["acento_txt"] if ativo else c["texto"],
                padx=10, pady=4, font=(fonte, 9, "bold"), cursor="hand2"
            )
            self.btn_inicio.pack(side="right")
            self.btn_inicio.bind("<Button-1>", lambda e: self.alternar_inicio())

        # Seção Dados (backup)
        tk.Label(corpo, text="DADOS", bg=c["bg"], fg=c["suave"],
                 font=(fonte, 9, "bold")).pack(anchor="w", pady=(0, 6))

        moldura_dados = tk.Frame(corpo, bg=c["card"], highlightthickness=1,
                                 highlightbackground=c["borda"])
        moldura_dados.pack(fill="x", pady=(0, 14))
        self._linha_acao(moldura_dados, "Salvar backup das frases", "Salvar",
                         self.fazer_backup, primeira=True)
        self._linha_acao(moldura_dados, "Restaurar de um backup", "Restaurar",
                         self.restaurar_backup, primeira=False)

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

    def _linha_acao(self, moldura, texto, rotulo_botao, comando, primeira):
        c = self.c
        if not primeira:
            tk.Frame(moldura, bg=c["borda"], height=1).pack(fill="x", padx=12)
        linha = tk.Frame(moldura, bg=c["card"])
        linha.pack(fill="x", padx=12, pady=10)
        tk.Label(linha, text=texto, bg=c["card"], fg=c["texto"],
                 font=(self.f, 10)).pack(side="left")
        btn = tk.Label(linha, text=rotulo_botao, bg=c["acento"], fg=c["acento_txt"],
                       padx=10, pady=4, font=(self.f, 9, "bold"), cursor="hand2")
        btn.pack(side="right")
        btn.bind("<Button-1>", lambda e: comando())

    def fazer_backup(self):
        self.master.exportar_backup(self)

    def restaurar_backup(self):
        if self.master.importar_backup(self):
            self.destroy()

    def alternar_intervalo(self):
        atual = self.master.cfg.get("usar_intervalo", True)
        novo = not atual
        self.master.cfg["usar_intervalo"] = novo
        self.master.gravar(ARQUIVO_CFG, self.master.cfg, self)

        c = self.c
        if novo:
            self.btn_intervalo_texto.set("Ativado")
            self.btn_intervalo.config(bg=c["acento"], fg=c["acento_txt"])
        else:
            self.btn_intervalo_texto.set("Desativado")
            self.btn_intervalo.config(bg=c["chip"], fg=c["texto"])

    def alternar_inicio(self):
        novo = not autostart_ativo()
        if not definir_autostart(novo):
            MensagemDialogo(self, self.c, self.f, "Aviso",
                            "Não foi possível alterar a inicialização automática.", tipo="aviso")
            return
        self.master.cfg["iniciar_com_sistema"] = novo
        self.master.gravar(ARQUIVO_CFG, self.master.cfg, self)

        c = self.c
        if novo:
            self.btn_inicio_texto.set("Ativado")
            self.btn_inicio.config(bg=c["acento"], fg=c["acento_txt"])
        else:
            self.btn_inicio_texto.set("Desativado")
            self.btn_inicio.config(bg=c["chip"], fg=c["texto"])

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

        self.master.gravar(ARQUIVO_CFG, self.master.cfg, self)
        self.master.construir()
        self.destroy()
        self.master.mostrar_rodape(
            "Cor padrão restaurada" if resultado == ""
            else f"Cor do tema alterada para {resultado.upper()}",
            self.master.c["acento"],
            volta="clique para copiar"
        )


class App(tk.Tk):
    def __init__(self, iniciar_recolhido=False):
        super().__init__()
        if WINDOWS and _DPI_NITIDO_ATIVO:
            # com DPI ligado o Tk aumentaria as fontes, mas os tamanhos em pixels do layout
            # são fixos: mantém a escala de 96 dpi para tudo continuar do mesmo tamanho
            try:
                self.tk.call("tk", "scaling", 96 / 72)
            except tk.TclError:
                pass
        self.title("Frases Prontas")
        self.minsize(300, 360)
        self._icone_janela()
        self._aviso_dados = None
        self.dados, self.cfg = self._carregar_dados()
        self.tema = self.cfg.get("tema", "claro")
        if self.tema not in TEMAS:
            self.tema = "claro"
        self.fixado = bool(self.cfg.get("fixado", False))
        if iniciar_recolhido and not self.fixado:
            self.withdraw()          # aberto pelo login: a janela nem chega a aparecer
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
        self._bloqueios = 0           # >0 = há uma janela externa aberta: não recolher
        self._livre_apos = 0.0        # folga após fechar uma janela externa

        precarregar_monitores()
        self.construir()
        self.posicionar()
        self.aplicar_fixar()
        if self._aviso_dados:                     # fica no rodapé até a próxima ação
            self.mostrar_rodape(self._aviso_dados, self.c["perigo"])

        # pedido de outra instância ("apareça"): ignora o que sobrou de execuções antigas
        try:
            os.remove(ARQUIVO_MOSTRAR)
        except OSError:
            pass
        self.after(500, self._ver_pedido)

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

        # Iniciar com o computador: ligado por padrão na primeira execução; nas seguintes,
        # só atualiza o caminho (caso o app tenha sido movido) se o registro ainda existir
        if AUTOSTART_SUPORTADO:
            if "iniciar_com_sistema" not in self.cfg:
                self.cfg["iniciar_com_sistema"] = definir_autostart(True)
                self.gravar(ARQUIVO_CFG, self.cfg, avisar=False)
            elif self.cfg["iniciar_com_sistema"] and autostart_ativo():
                definir_autostart(True)

        # Aberto pelo login: já começa como barra lateral (a menos que esteja fixado)
        if iniciar_recolhido and not self.fixado:
            try:
                self.recolher()
            except Exception:                     # se algo falhar, ao menos o app aparece
                self.recolhido = False
                if self.aba is not None:
                    try:
                        self.aba.destroy()
                    except tk.TclError:
                        pass
                    self.aba = None
                self.deiconify()

    def _carregar_dados(self):
        """Lê frases e configurações. O que estiver fora do formato (arquivo editado à mão,
        versão antiga, etc.) é ignorado em vez de impedir o app de abrir; antes, guarda uma
        cópia do arquivo original."""
        problemas = []

        bruto = _ler(ARQUIVO, None)
        if bruto is None:                          # primeira execução: frases iniciais
            frases = copy.deepcopy(EXEMPLOS)
        else:
            frases, descartados = sanear_frases(bruto)
            if descartados is None:                # não era uma lista
                frases = copy.deepcopy(EXEMPLOS)
            if descartados is None or descartados > 0:
                guardar_copia_invalida(ARQUIVO)
                problemas.append("frases")

        bruto_cfg = _ler(ARQUIVO_CFG, {})
        cfg, ruim = sanear_cfg(bruto_cfg)
        if ruim:
            guardar_copia_invalida(ARQUIVO_CFG)
            problemas.append("configurações")

        if problemas:
            self._aviso_dados = ("Dados com erro foram ignorados (" + " e ".join(problemas)
                                 + "). Cópia guardada.")
        return frases, cfg

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

    def _ver_pedido(self):
        """Outra instância foi aberta (ex.: atalho clicado de novo): em vez de duplicar,
        esta aparece."""
        try:
            self.after(500, self._ver_pedido)
        except tk.TclError:
            return
        try:
            if not os.path.exists(ARQUIVO_MOSTRAR):
                return
            os.remove(ARQUIVO_MOSTRAR)
            if self.recolhido:
                self.expandir()
            else:
                self.deiconify()
                self._trazer_para_frente()
        except (OSError, tk.TclError):
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
        self.gravar(ARQUIVO_CFG, self.cfg)
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
        """True se há qualquer janela secundária aberta: nunca recolhe nesse caso.
        Vale automaticamente para TODA tela nova (Toplevel visível ou com grab) e para
        janelas externas do sistema (ver sem_recolher). Só a aba lateral, a janela de
        arrasto e o menu de três pontos não contam."""
        if self._bloqueios > 0 or time.monotonic() < self._livre_apos:
            return True
        # Há um grab ativo? Pergunta direto ao Tcl: grab_current() do tkinter dá KeyError
        # quando a janela é do próprio Tk (messagebox, seletor de arquivos interno).
        try:
            if str(self.tk.call("grab", "current")):
                return True
        except tk.TclError:
            pass
        try:
            ignorar = (self.aba, self.drag_window, self.menu_aberto)
            for w in self.winfo_children():
                if isinstance(w, tk.Toplevel) and not any(w is x for x in ignorar) \
                        and w.winfo_viewable():
                    return True
        except (tk.TclError, KeyError):
            pass
        return False

    @contextmanager
    def sem_recolher(self, parent=None):
        """Use em volta de qualquer janela que NÃO é do Tk (seletor de arquivos do sistema,
        etc.): enquanto ela estiver aberta o app não vira barra lateral."""
        self._bloqueios += 1
        try:
            yield
        finally:
            self._bloqueios -= 1
            self._livre_apos = time.monotonic() + 1.5
            self._sem_foco_cont = 0
            try:
                (parent or self).focus_force()
            except tk.TclError:
                pass

    def _retangulo_janela(self):
        """(x, y, largura, altura) da janela. Se ela ainda não foi exibida (abertura pelo
        login), usa a geometria definida, porque winfo_* só valem para janela visível."""
        self.update_idletasks()
        if self.winfo_viewable():
            return self.winfo_rootx(), self.winfo_rooty(), self.winfo_width(), self.winfo_height()
        m = re.match(r"(\d+)x(\d+)[+-](-?\d+)[+-](-?\d+)", self.geometry())
        if m:
            w, h, x, y = (int(v) for v in m.groups())
            return x, y, w, h
        return 0, 0, LARGURA, ALTURA

    def recolher(self):
        if self.recolhido or self.fixado:
            return
        if self._dialogo_aberto():
            self._dbg("recolher: ignorado (há janela modal aberta)")
            return
        self._tinha_foco = False
        self._sem_foco_cont = 0
        self.fechar_menu()
        x, y, larg, alt = self._retangulo_janela()
        self.geo_salva = self.geometry()

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
                              tam=19, px=4, py=4, borda=c["borda"], bg_pai=c["bg"])

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
    def gravar(self, caminho, dados, parent=None, avisar=True):
        """Grava no disco. Se falhar, avisa o usuário em vez de falhar em silêncio.
        Devolve True se gravou."""
        try:
            _gravar(caminho, dados)
            return True
        except OSError as e:
            self._dbg("falha ao gravar %s: %r" % (caminho, e))
            if avisar:
                self.mostrar_rodape("Não foi possível gravar em disco", self.c["perigo"],
                                    volta="clique para copiar")
                MensagemDialogo(
                    parent or self, self.c, self.f, "Não foi possível salvar",
                    "A alteração vale por enquanto, mas não foi gravada em disco e pode "
                    "se perder ao fechar o app.\n\n"
                    "Se um antivírus ou programa de sincronização estiver usando a pasta "
                    "de configuração, tente de novo em instantes.\n\n" + str(e), tipo="erro")
            return False

    def categorias(self):
        return sorted({d["categoria"] for d in self.dados})

    def salvar(self, chips=True):
        self.gravar(ARQUIVO, self.dados)
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
        self.mostrar_rodape(f"{len(cards)} frase{'s' if len(cards) != 1 else ''}  ·  clique para copiar")
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

    # ---------- janela de arquivos do sistema ----------
    def _dialogo_arquivo_linux(self, parent, salvar, titulo, nome_inicial):
        """Seletor nativo no Linux: kdialog (KDE/Qt) ou zenity (GTK, o mesmo do Chrome no
        GNOME/Mint/Ubuntu). Devolve o caminho, "" se cancelou ou None se não deu para usar."""
        pasta = os.path.expanduser("~")
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
        kd, zn = shutil.which("kdialog"), shutil.which("zenity")
        try:
            wid = str(parent.winfo_id())     # janela-mãe: o seletor abre por cima dela
        except tk.TclError:
            wid = None

        def montar(anexar):
            if kd and ("KDE" in desktop or not zn):
                args = [kd, "--title", titulo]
                if anexar and wid:
                    args += ["--attach", wid]
                args += ["--getsavefilename" if salvar else "--getopenfilename",
                         os.path.join(pasta, nome_inicial) if salvar else pasta,
                         "*.json|Backup do Frases Prontas (*.json)\n*|Todos os arquivos"]
                return args
            if zn:
                args = [zn, "--file-selection", "--title=" + titulo,
                        "--file-filter=Backup do Frases Prontas | *.json",
                        "--file-filter=Todos os arquivos | *"]
                if anexar and wid:
                    args += ["--modal", "--attach=" + wid]
                if salvar:
                    args += ["--save", "--confirm-overwrite",
                             "--filename=" + os.path.join(pasta, nome_inicial)]
                else:
                    args.append("--filename=" + pasta + "/")
                return args
            return None

        for anexar in (True, False):          # se esta versão não aceitar --attach, tenta sem
            args = montar(anexar)
            if args is None:
                return None
            try:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
            except OSError:
                return None
            # espera sem travar a interface do app
            pronto = tk.BooleanVar(master=self, value=False)

            def vigiar(proc=proc, pronto=pronto):
                if proc.poll() is None:
                    self.after(80, vigiar)
                else:
                    pronto.set(True)
            self.after(80, vigiar)
            self.wait_variable(pronto)
            saida = proc.stdout.read().strip()
            if proc.returncode == 0:
                return saida.splitlines()[0] if saida else ""
            if proc.returncode == 1:          # cancelou
                return ""
        return None                           # erro: usa o seletor do Tk

    def _pedir_arquivo(self, parent, salvar, titulo, nome_inicial=""):
        """Abre a janela de salvar/abrir arquivo do sistema. Devolve o caminho ou ""."""
        # "Sempre por cima" deixaria o seletor escondido atrás do app: desliga enquanto ele
        # estiver aberto e restaura depois.
        janelas, antes = [self, parent], []
        for j in janelas:
            try:
                antes.append(bool(int(j.attributes("-topmost"))))
                j.attributes("-topmost", False)
            except (tk.TclError, ValueError, TypeError):
                antes.append(None)
        try:
            with self.sem_recolher(parent):
                if LINUX:
                    r = self._dialogo_arquivo_linux(parent, salvar, titulo, nome_inicial)
                    if r is not None:
                        return r
                tipos = [("Backup do Frases Prontas", "*.json"), ("Todos os arquivos", "*.*")]
                if salvar:
                    return filedialog.asksaveasfilename(
                        parent=parent, title=titulo, defaultextension=".json",
                        initialfile=nome_inicial, filetypes=tipos) or ""
                return filedialog.askopenfilename(
                    parent=parent, title=titulo, filetypes=tipos) or ""
        finally:
            for j, valor in zip(janelas, antes):
                if valor is None:
                    continue
                try:
                    j.attributes("-topmost", valor)
                except tk.TclError:
                    pass

    # ---------- backup ----------
    def exportar_backup(self, parent):
        caminho = self._pedir_arquivo(parent, True, "Salvar backup das frases",
                                      f"frases-backup-{time.strftime('%Y-%m-%d')}.json")
        if not caminho:
            return
        if not os.path.splitext(caminho)[1]:
            caminho += ".json"
        try:
            gravar_backup(caminho, self.dados, self.cfg)
        except OSError as e:
            MensagemDialogo(parent, self.c, self.f, "Não foi possível salvar",
                            f"O backup não pôde ser gravado:\n{e}", tipo="erro")
            return
        n = len(self.dados)
        MensagemDialogo(
            parent, self.c, self.f, "Backup salvo",
            f"{n} frase{'s' if n != 1 else ''} salva{'s' if n != 1 else ''} em:\n{caminho}\n\n"
            "Para restaurar o backup, selecione a opção Restaurar e escolha o arquivo de backup.", tipo="ok")

    def _copia_antes_de_restaurar(self):
        try:
            if os.path.exists(ARQUIVO):
                shutil.copy2(ARQUIVO,
                             os.path.join(CONFIG_DIR, "frases-antes-da-restauracao.json"))
        except OSError:
            pass

    def importar_backup(self, parent):
        """Pergunta o arquivo e como restaurar. Devolve True se algo foi alterado."""
        caminho = self._pedir_arquivo(parent, False, "Escolher backup para restaurar")
        if not caminho:
            return False
        try:
            frases, cfg = ler_backup(caminho)
        except ValueError as e:
            MensagemDialogo(parent, self.c, self.f, "Backup inválido", str(e), tipo="erro")
            return False
        except OSError as e:
            MensagemDialogo(parent, self.c, self.f, "Não foi possível abrir",
                            f"O arquivo não pôde ser lido:\n{e}", tipo="erro")
            return False

        n = len(frases)
        escolha = RestaurarDialogo(parent, self.c, self.f, n).resultado
        if escolha is None:
            return False

        visual_mudou = False
        if escolha == "mesclar":
            novas, adicionadas = mesclar_frases(self.dados, frases)
            if adicionadas == 0:
                MensagemDialogo(parent, self.c, self.f, "Nada a restaurar",
                                "Todas as frases do backup já estavam no app.\n"
                                "Nada foi alterado.", tipo="info")
                return False
            self._copia_antes_de_restaurar()
            self.dados = novas
            msg = f"{adicionadas} frase{'s' if adicionadas != 1 else ''} adicionada" \
                  f"{'s' if adicionadas != 1 else ''}"
        else:                                           # substituir
            if self.dados and not MensagemDialogo(
                    parent, self.c, self.f, "Substituir tudo?",
                    f"Isso troca as suas {len(self.dados)} frases atuais pelas {n} do backup.\n\n"
                    "Uma cópia das atuais fica guardada na pasta de configuração do app.",
                    tipo="aviso", rotulo_ok="Substituir", rotulo_cancelar="Cancelar",
                    perigo=True).resultado:
                return False
            self._copia_antes_de_restaurar()
            self.dados = frases
            if cfg is not None:
                for k in CFG_NO_BACKUP:
                    if k in cfg:
                        self.cfg[k] = cfg[k]
                    else:
                        self.cfg.pop(k, None)
                self.tema = self.cfg.get("tema", "claro")
                if self.tema not in TEMAS:
                    self.tema = "claro"
                visual_mudou = True
            msg = f"Backup restaurado: {n} frase{'s' if n != 1 else ''}"

        self.gravar(ARQUIVO, self.dados, parent)
        self.gravar(ARQUIVO_CFG, self.cfg, parent)
        self.categoria = "Todas"
        if visual_mudou:
            self.construir()            # reaplica tema e cores
        else:
            self.desenhar_chips()
            self.listar()
        total = len(self.dados)
        self.mostrar_rodape(msg, self.c["acento"],
                            volta=f"{total} frase{'s' if total != 1 else ''}  ·  clique para copiar")
        return True

    def abrir_configuracoes(self):
        """Abre a tela de opções de configuração."""
        DialogoConfiguracoes(self, self.c, self.f, self.tema)

    def alternar_tema(self):
        self.tema = "escuro" if self.tema == "claro" else "claro"
        self.cfg["tema"] = self.tema
        self.gravar(ARQUIVO_CFG, self.cfg)
        self.construir()


_DPI_NITIDO_ATIVO = False


def configurar_dpi():
    """Windows: chamar ANTES de criar a janela. Não faz nada nos outros sistemas, e qualquer
    falha é ignorada (o app só fica como era antes)."""
    global _DPI_NITIDO_ATIVO
    if not WINDOWS or DPI_MODO not in ("gdi", "nitido"):
        return
    try:
        import ctypes
        if DPI_MODO == "gdi":
            # "UNAWARE_GDISCALED" (-5): continua sem escala própria (mesmos tamanhos), mas o
            # texto e os traços são desenhados na resolução real da tela.
            u32 = ctypes.windll.user32
            u32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-5))
            return
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)      # 1 = ciente do DPI do sistema
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        _DPI_NITIDO_ATIVO = True       # a App fixa a escala das fontes (ver __init__)
    except Exception:
        pass


def _definir_id_windows():
    """Faz a barra de tarefas tratar o app como programa próprio (com o ícone dele e
    agrupado à parte), e não como mais uma janela do Python."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


if __name__ == "__main__":
    recolhido = FLAG_RECOLHIDO in sys.argv[1:]
    if not obter_instancia_unica():
        if not recolhido:              # aberto à mão: faz a instância existente aparecer
            pedir_para_mostrar()
        sys.exit(0)
    if WINDOWS:
        _definir_id_windows()
        configurar_dpi()
    App(iniciar_recolhido=recolhido).mainloop()

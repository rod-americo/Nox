import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os
import threading
import time
import subprocess
import json
import queue
from pathlib import Path
from datetime import datetime

# Módulos locais
import config
import loop
import logger
import downloader

# ============================================================
# ESTILOS / CONSTANTES
# ============================================================
FONT_MONO = ("Courier New", 12)
FONT_NORMAL = ("Segoe UI", 10) if sys.platform == "win32" else ("Helvetica", 12)
FONT_BOLD = ("Segoe UI", 10, "bold") if sys.platform == "win32" else ("Helvetica", 12, "bold")

# Cores Base (Defaults)
COLOR_GREEN = "#00e676"
COLOR_RED = "#ff5252"
COLOR_GRAY = "#9e9e9e"
COLOR_WHITE = "#ffffff"
COLOR_DARK = "#2d2d2d"

# Paletas
THEME_DARK = {
    "bg": "#2d2d2d",
    "fg": "#ffffff",
    "card": "#424242",
    "input_bg": "#505050",
    "input_fg": "#ffffff",
    "select_bg": "#00e676",
    "select_fg": "#000000"
}

THEME_LIGHT = {
    "bg": "#f0f0f0",
    "fg": "#000000",
    "card": "#ffffff",
    "input_bg": "#ffffff",
    "input_fg": "#000000",
    "select_bg": "#00e676",
    "select_fg": "#ffffff"
}

class AppState:
    def __init__(self, scenarios=None, no_prepare=False):
        self.loop_controller = loop.LoopController()
        # Inicializa sem thread rodando, para usuário dar Start
        self.loop_thread = None
        self.loop_running = False
        self.scenarios = scenarios or [] # Lista de nomes de arquivo
        self.no_prepare = no_prepare

    def start_loop(self, on_exit_callback=None):
        if not self.loop_thread or not self.loop_thread.is_alive():
            self.loop_controller = loop.LoopController()
            self.loop_controller.resume()

            def runner():
                try:
                    # Coletar caminhos completos dos cenários selecionados (arquivos em queries/)
                    selected_paths = []
                    queries_dir = Path("queries").resolve()
                    
                    for s_name in self.scenarios:
                        # Se não tiver extensão .json, adiciona
                        filename = s_name if s_name.lower().endswith(".json") else f"{s_name}.json"
                        p = queries_dir / filename
                        
                        if p.exists():
                            selected_paths.append(str(p))
                        else:
                             # Fallback: tenta o nome original
                            selected_paths.append(s_name)

                    args_to_pass = list(selected_paths)
                    if self.no_prepare:
                        args_to_pass.append("--no-prepare")

                    loop.main(controller=self.loop_controller, args=args_to_pass)
                except (Exception, SystemExit) as e:
                    # Captura erros de inicialização (prepare.py) ou runtime
                    logger.log_erro(f"Loop Thread Crashed: {e}")
                    print(f"Loop Thread Crashed: {e}") 
                finally:
                    self.loop_running = False
                    if on_exit_callback:
                        on_exit_callback()

            self.loop_thread = threading.Thread(
                target=runner,
                daemon=True
            )
            self.loop_thread.start()
            self.loop_running = True

    def stop_loop(self):
        self.loop_controller.stop()
        self.loop_running = False

    def toggle_pause_loop(self):
        if self.loop_controller.should_stop: 
            return # Já parado
        if self.loop_controller._pause_event.is_set():
            self.loop_controller.pause()
            return "PAUSADO"
        else:
            self.loop_controller.resume()
            return "RODANDO"

# Arquivo de estado da GUI
GUI_STATE_FILE = config.DATA_DIR / "gui_config.json"

class NoxApp(tk.Tk):
    def __init__(self, scenarios=None, no_prepare=False):
        super().__init__()
        self.title(f"Nox v{config.VERSION}")
        
        # Estado
        self.state = AppState(scenarios=scenarios or config.SCENARIOS, no_prepare=no_prepare)
        self.log_queue = queue.Queue()
        self.session_downloads = 0
        self.all_items = []
        
        # Tema
        self.colors = THEME_LIGHT if config.THEME == "light" else THEME_DARK
        self.apply_theme()
        
        # Carrega Geometria ou usa padrão
        self.load_window_state()
        self.minsize(400, 500)
        
        # Bind Close Event para salvar estado
        self.protocol("WM_DELETE_WINDOW", self.on_close_window)

        # Configuração de Logger para Queue
        logger.set_gui_callback(self.queue_log)

        # UI Initialization
        self.create_widgets()
        self.load_queries_files() # Popula checkboxes
        self.refresh_data_loop() # Inicia watcher de arquivos
        self.process_log_queue() # Inicia update da GUI via logs

    def verbose_log(self, msg):
        # Helper simples para debug local
        print(f"[GUI DEBUG] {msg}")

    def load_window_state(self):
        default_geo = "450x700"
        try:
            if GUI_STATE_FILE.exists():
                data = json.loads(GUI_STATE_FILE.read_text(encoding="utf-8"))
                geo = data.get("geometry", default_geo)
                self.geometry(geo)
            else:
                self.geometry(default_geo)
        except Exception as e:
            print(f"Erro ao carregar estado da janela: {e}")
            self.geometry(default_geo)

    def save_window_state(self):
        try:
            data = {"geometry": self.geometry()}
            GUI_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            print(f"Erro ao salvar estado da janela: {e}")

    def on_close_window(self):
        self.save_window_state()
        self.destroy()

    def apply_theme(self):
        self.configure(bg=self.colors["bg"])
        
        style = ttk.Style(self)
        style.theme_use("default") # Base mais limpa para customizar
        
        # Frame
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabelframe", background=self.colors["bg"], foreground=self.colors["fg"])
        style.configure("TLabelframe.Label", background=self.colors["bg"], foreground=self.colors["fg"])
        
        # Label
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["fg"])
        
        # Button
        style.configure("TButton", background=self.colors["card"], foreground=self.colors["fg"])
        style.map("TButton", 
            background=[("active", self.colors["select_bg"])],
            foreground=[("active", self.colors["select_fg"])]
        )
        
        # Entry
        style.configure("TEntry", fieldbackground=self.colors["input_bg"], foreground=self.colors["input_fg"])
        
        # Checkbox/Radio
        style.configure("TCheckbutton", background=self.colors["bg"], foreground=self.colors["fg"])
        style.configure("TRadiobutton", background=self.colors["bg"], foreground=self.colors["fg"])
        
        # Treeview
        style.configure("Treeview", 
            background=self.colors["card"], 
            foreground=self.colors["fg"],
            fieldbackground=self.colors["card"]
        )
        style.map("Treeview", background=[('selected', self.colors["select_bg"])])
        
        style.configure("Treeview.Heading", background=self.colors["input_bg"], foreground=self.colors["fg"])

    def verbose_log(self, msg):
        # Helper simples para debug local
        print(f"[GUI DEBUG] {msg}")

    def queue_log(self, ts, tipo, msg):
        self.log_queue.put((ts, tipo, msg))

    def process_log_queue(self):
        try:
            while True:
                ts, tipo, msg = self.log_queue.get_nowait()
                
                # Atualiza Log Line
                self.lbl_log.config(text=f"[{ts}] {msg}", fg=self.get_log_color(tipo))
                
                # Conta downloads
                if tipo == "FINALIZADO" and "completo" in msg:
                    self.session_downloads += 1
                    self.lbl_session.config(text=f"Sessão: {self.session_downloads}")
                
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_log_queue)

    def get_log_color(self, tipo):
        if tipo == "ERRO": return COLOR_RED
        if tipo == "FINALIZADO": return "#00b0ff" if config.THEME == "light" else "#40c4ff"
        if tipo == "OK": return COLOR_GREEN
        return self.colors["fg"]

    def create_widgets(self):
        # --- Header (Status) ---
        frame_header = ttk.Frame(self, padding=10)
        frame_header.pack(fill=tk.X)

        self.btn_status = tk.Button(
            frame_header, 
            text="PARADO (Clique para Iniciar)", 
            bg="#757575", fg="#ffffff", # Cinza fixo para parado
            font=FONT_BOLD,
            command=self.toggle_status,
            relief=tk.FLAT,
            height=2
        )
        self.btn_status.pack(fill=tk.X)

        # --- Scenarios (Collapsible ish - Simplificado para lista fixa com scroll se precisar) ---
        frame_scenarios = ttk.LabelFrame(self, text="Cenários", padding=5)
        frame_scenarios.pack(fill=tk.X, padx=10, pady=5)

        self.scenario_vars = {} # name -> BooleanVar
        self.frame_checks = ttk.Frame(frame_scenarios)
        self.frame_checks.pack(fill=tk.X)

        # Botão de Atualizar removido conforme solicitação

        # --- Manual Download ---
        frame_manual = ttk.Frame(self, padding=10)
        frame_manual.pack(fill=tk.X)
        
        lbl_manual = ttk.Label(frame_manual, text="Download Manual:")
        lbl_manual.pack(side=tk.LEFT)

        self.var_server = tk.StringVar(value="HAC")
        rb_hac = ttk.Radiobutton(frame_manual, text="HAC", variable=self.var_server, value="HAC")
        rb_hac.pack(side=tk.LEFT, padx=5)
        rb_hbr = ttk.Radiobutton(frame_manual, text="HBR", variable=self.var_server, value="HBR")
        rb_hbr.pack(side=tk.LEFT, padx=5)

        self.entry_an = ttk.Entry(frame_manual, width=15)
        self.entry_an.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.entry_an.bind("<Return>", lambda e: self.do_manual_download())

        btn_dl = ttk.Button(frame_manual, text="Baixar", command=self.do_manual_download, width=8)
        btn_dl.pack(side=tk.LEFT)

        # --- Search ---
        frame_search = ttk.Frame(self, padding=(10, 0))
        frame_search.pack(fill=tk.X)
        self.entry_search = ttk.Entry(frame_search)
        self.entry_search.pack(fill=tk.X)
        self.entry_search.insert(0, "")
        self.entry_search.bind("<KeyRelease>", self.filter_list)
        # Label removido conforme solicitação

        # --- List View ---
        frame_list = ttk.Frame(self, padding=10)
        frame_list.pack(fill=tk.BOTH, expand=True)

        cols = ("AN", "Nome", "Status")
        self.tree = ttk.Treeview(frame_list, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("AN", text="AN")
        self.tree.heading("Nome", text="Exame")
        self.tree.heading("Status", text="Status")
        
        self.tree.column("AN", width=80, anchor="center")
        self.tree.column("Nome", width=250)
        self.tree.column("Status", width=80, anchor="center")

        scrollbar = ttk.Scrollbar(frame_list, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree.bind("<Double-1>", self.on_item_double_click)

        # --- Footer ---
        frame_footer = ttk.Frame(self, padding=10)
        frame_footer.pack(fill=tk.X, side=tk.BOTTOM)

        # Config / Slider
        frame_cfg = ttk.Frame(frame_footer)
        frame_cfg.pack(fill=tk.X, pady=5)
        
        self.lbl_session = ttk.Label(frame_cfg, text="Sessão: 0", font=FONT_BOLD)
        self.lbl_session.pack(side=tk.LEFT)

        slider_limit = max(config.SLIDER_MAX, config.MAX_EXAMES)
        self.scale_max = tk.Scale(
            frame_cfg, from_=5, to=slider_limit, 
            orient=tk.HORIZONTAL, showvalue=0, command=self.on_slider_change,
            bg=self.colors["bg"], fg=self.colors["fg"], highlightthickness=0
        )
        self.scale_max.set(config.MAX_EXAMES)
        self.scale_max.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        self.lbl_max = ttk.Label(frame_cfg, text=f"Manter: {config.MAX_EXAMES}")
        self.lbl_max.pack(side=tk.RIGHT)

        # Log Line
        self.lbl_log = tk.Label(frame_footer, text="Aguardando...", fg=self.colors["fg"], bg=self.colors["bg"], anchor="w")
        self.lbl_log.pack(fill=tk.X)


    def load_queries_files(self):
        # Limpa widgets anteriores
        for widget in self.frame_checks.winfo_children():
            widget.destroy()
        
        q_dir = Path("queries")
        if not q_dir.exists():
            q_dir.mkdir(parents=True, exist_ok=True)
            
        files = sorted([f.stem for f in q_dir.glob("*.json")])
        
        # Atualiza self.scenario_vars preservando estados se possível? Não, recriando é mais seguro por enquanto.
        self.scenario_vars = {}
        
        # Lista dos que devem estar marcados
        current_active = self.state.scenarios

        for f in files:
            var = tk.BooleanVar(value=(f in current_active))
            chk = ttk.Checkbutton(self.frame_checks, text=f, variable=var, command=lambda name=f, v=var: self.on_scenario_toggle(name, v))
            chk.pack(anchor="w", padx=10)
            self.scenario_vars[f] = var

    def on_scenario_toggle(self, name, var):
        if var.get():
            if name not in self.state.scenarios:
                self.state.scenarios.append(name)
        else:
            if name in self.state.scenarios:
                self.state.scenarios.remove(name)
        
        # Salva config
        self.save_config_value("SETTINGS", "scenarios", json.dumps(self.state.scenarios))
        logger.log_info(f"Cenários ativos: {self.state.scenarios}")

    def toggle_status(self):
        if not self.state.loop_running:
            # START
            self.state.start_loop(on_exit_callback=self.on_loop_exit)
            self.update_status_ui("RODANDO")
        else:
            # PAUSE / RESUME logic
            st = self.state.toggle_pause_loop()
            self.update_status_ui(st)

    def update_status_ui(self, status):
        if status == "RODANDO":
            self.btn_status.config(text="MONITORANDO (Clique para Pausar)", bg=COLOR_GREEN, fg=COLOR_DARK)
        elif status == "PAUSADO":
            self.btn_status.config(text="PAUSADO (Clique para Retomar)", bg="orange", fg=COLOR_WHITE)
        else: # Parado
            self.btn_status.config(text="PARADO (Clique para Iniciar)", bg=COLOR_GRAY, fg=COLOR_WHITE)

    def on_loop_exit(self):
        # Chamado via thread, usar after para mexer na UI
        self.after(0, lambda: self.update_status_ui("PARADO"))

    def do_manual_download(self):
        an = self.entry_an.get().strip()
        server = self.var_server.get()
        if not an: return
        
        self.entry_an.delete(0, tk.END)
        logger.log_info(f"Iniciando manual: {server} {an}")
        
        def run():
            if downloader.baixar_an(server, an):
                logger.log_ok(f"Download concluído: {an}")
            else:
                logger.log_erro(f"Falha download: {an}")

        threading.Thread(target=run, daemon=True).start()

    def filter_list(self, event=None):
        term = self.entry_search.get().lower().strip()
        
        # Limpa treeview
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        for i in self.all_items:
            # Busca em AN, Nome, Desc, Modality
            combo = f"{i.get('an','')} {i.get('nome','')} {i.get('desc','')} {i.get('mod','')}".lower()
            if not term or term in combo:
                values = (i.get("an"), f"{i.get('nome')} - {i.get('desc')}", i.get("qtd"))
                # Armazena path no item tags ou ID se precisar, vamos usar o ID como indice ou algo assim
                # O ID da row será o PATH do dicom para recuperar no double click
                self.tree.insert("", tk.END, iid=i["path"], values=values)

    def refresh_data_loop(self):
        try:
            new_items = self.scan_recentes()
            # Diferença básica para não piscar tela toda hora? 
            # Por enquanto, redraw bruto pois scan_recentes lê disco.
            self.all_items = new_items
            self.filter_list()
        except Exception as e:
            print(f"Erro refresh: {e}")
        finally:
            self.after(5000, self.refresh_data_loop) # 5 segundos

    def scan_recentes(self):
        if not config.PROGRESS_DIR.exists(): return []
        results = []
        for p in config.PROGRESS_DIR.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                an = data.get("an", p.stem)
                nome = data.get("patient_name", "—")
                desc = data.get("study_desc", "")
                mod  = data.get("modality", "")
                total = data.get("total", 0)
                baixadas = data.get("baixadas", 0)
                status = data.get("status", "desconhecido")
                
                if status == "completo": qtd_str = f"{total} img"
                elif status == "baixando": qtd_str = f"{baixadas}/{total}"
                else: qtd_str = status
                
                dcm_path = config.RADIANT_DICOM_DIR / an
                
                results.append({
                    "an": an, "nome": nome, "mod": mod, 
                    "desc": desc, "qtd": qtd_str, 
                    "path": str(dcm_path),
                    "mtime": p.stat().st_mtime
                })
            except: continue
        results.sort(key=lambda x: x["nome"])
        return results

    def on_item_double_click(self, event):
        selected_id = self.tree.focus() # Retorna o IID que setamos como Path
        if selected_id:
            path = selected_id
            # Recupera AN dos values
            item = self.tree.item(selected_id)
            an = item['values'][0]
            self.open_viewer(path, an)

    def open_viewer(self, path, an):
        viewer_type = config.VIEWER
        if viewer_type in ["osirix", "horos"]:
            url = f"osirix://?methodName=displayStudy&AccessionNumber={an}"
            self.open_folder(url)
            logger.log_info(f"OsiriX chamado: {an}")
        else:
            radiant_exe = Path(config.RADIANT_EXE)
            if radiant_exe.exists():
                cmd = [str(radiant_exe), "-cl", "-d", str(path)]
                try:
                    subprocess.Popen(cmd)
                    logger.log_info(f"RadiAnt aberto: {an}")
                except Exception as e:
                    logger.log_erro(f"Erro RadiAnt: {e}")
            else:
                self.open_folder(path)
                logger.log_aviso("Viewer não encontrado. Abrindo pasta.")

    def open_folder(self, path):
        if sys.platform == "win32": os.startfile(path)
        elif sys.platform == "darwin": subprocess.Popen(["open", path])
        else: subprocess.Popen(["xdg-open", path])

    def on_slider_change(self, value):
        val = int(float(value))
        self.lbl_max.config(text=f"Manter: {val}")
        config.MAX_EXAMES = val
        self.save_config_value("SETTINGS", "max_exames", val)
        # Trigger cleanup
        threading.Thread(target=self.trigger_cleanup, daemon=True).start()

    def trigger_cleanup(self):
        try:
            loop.verificar_retencao_exames()
            # O refresh loop vai pegar as mudanças na próxima iteração
        except: pass

    def save_config_value(self, section, key, value):
        import configparser
        parser = configparser.ConfigParser()
        parser.read(config.CONFIG_FILE)
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, str(value))
        with open(config.CONFIG_FILE, 'w') as f:
            parser.write(f)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nox Assistant - Monitoramento de Downloads DICOM")
    
    # Modos de operação
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--gui", "-g", action="store_true", help="Executa com Interface Gráfica (Padrão)")
    group.add_argument("--cli", "-c", action="store_true", help="Executa em modo Linha de Comando")
    
    # Opções globais
    parser.add_argument("--no-prepare", action="store_true", help="Pular etapa de preparação (Playwright/Login)")
    parser.add_argument("cenarios", metavar="CENARIOS", nargs="*", help="Cenários específicos (ex: MONITOR MONITOR_RX)")
    
    args = parser.parse_args()
    
    cenarios = args.cenarios if args.cenarios else None

    if args.cli:
        print("--- INICIANDO O NOX (CLI) ---")
        try:
            loop_args = []
            if cenarios: loop_args.extend(cenarios)
            if args.no_prepare: loop_args.append("--no-prepare")
            loop.main(args=loop_args)
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.")
            sys.exit(0)
    else:
        # GUI Mode
        print("--- INICIANDO O NOX (TKINTER GUI) ---")
        app = NoxApp(scenarios=cenarios, no_prepare=args.no_prepare)
        app.mainloop()

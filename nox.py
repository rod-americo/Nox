import customtkinter as ctk
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
# CustomTkinter usa strings para fonts ou tuplas
FONT_NORMAL = ("Roboto", 12)
FONT_BOLD = ("Roboto", 12, "bold")

# Cores Base (Adaptadas para o tema do CTk)
# CTk gerencia cores automaticamente (Dark/Light), mas podemos forçar algumas
COLOR_GREEN = "#00e676"
COLOR_RED = "#ff5252"
COLOR_GRAY = "#9e9e9e"

# Arquivo de estado da GUI
GUI_STATE_FILE = config.DATA_DIR / "gui_config.json"

class AppState:
    def __init__(self, scenarios=None, no_prepare=False):
        self.loop_controller = loop.LoopController()
        self.loop_thread = None
        self.loop_running = False
        self.scenarios = scenarios or []
        self.no_prepare = no_prepare

    def start_loop(self, on_exit_callback=None):
        if not self.loop_thread or not self.loop_thread.is_alive():
            self.loop_controller = loop.LoopController()
            self.loop_controller.resume()

            def runner():
                try:
                    selected_paths = []
                    queries_dir = Path("queries").resolve()
                    
                    for s_name in self.scenarios:
                        filename = s_name if s_name.lower().endswith(".json") else f"{s_name}.json"
                        p = queries_dir / filename
                        
                        if p.exists():
                            selected_paths.append(str(p))
                        else:
                            selected_paths.append(s_name)

                    args_to_pass = list(selected_paths)
                    if self.no_prepare:
                        args_to_pass.append("--no-prepare")

                    loop.main(controller=self.loop_controller, args=args_to_pass)
                except (Exception, SystemExit) as e:
                    logger.log_erro(f"Loop Thread Crashed: {e}")
                finally:
                    self.loop_running = False
                    if on_exit_callback:
                        on_exit_callback()

            self.loop_thread = threading.Thread(target=runner, daemon=True)
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

class NoxApp(ctk.CTk):
    def __init__(self, scenarios=None, no_prepare=False):
        super().__init__()
        
        # Configuração Inicial CTk
        ctk.set_appearance_mode(config.THEME if config.THEME in ["dark", "light"] else "System")
        ctk.set_default_color_theme("green") 
        
        self.title(f"Nox v{config.VERSION}")
        
        # Estado
        self.app_state = AppState(scenarios=scenarios or config.SCENARIOS, no_prepare=no_prepare)
        self.log_queue = queue.Queue()
        self.session_downloads = 0
        self.all_items = []
        
        # Carrega Geometria
        self.load_window_state()
        self.minsize(450, 600)
        
        # Bind Close
        self.protocol("WM_DELETE_WINDOW", self.on_close_window)

        # Logger
        logger.set_gui_callback(self.queue_log)

        # UI
        self.create_widgets()
        self.load_queries_files()
        self.refresh_data_loop()
        self.process_log_queue()

    def load_window_state(self):
        default_geo = "500x750"
        try:
            if GUI_STATE_FILE.exists():
                data = json.loads(GUI_STATE_FILE.read_text(encoding="utf-8"))
                geo = data.get("geometry", default_geo)
                self.geometry(geo)
                
                # Always on Top
                top = data.get("always_on_top", False)
                self.attributes("-topmost", top)
                self.start_topmost = top # Armazena para setar o switch depois da criação
            else:
                self.geometry(default_geo)
                self.start_topmost = False
        except Exception as e:
            print(f"Erro ao carregar estado da janela: {e}")
            self.geometry(default_geo)
            self.start_topmost = False

    def save_window_state(self):
        try:
            data = {
                "geometry": self.geometry(),
                "always_on_top": self.attributes("-topmost")
            }
            GUI_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            print(f"Erro ao salvar estado da janela: {e}")

    def on_close_window(self):
        self.save_window_state()
        self.destroy()

    def create_widgets(self):
        # --- Grid Layout ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1) # Lista expande

        # --- Header (Status Button) ---
        self.frame_header = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.frame_header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        
        self.btn_status = ctk.CTkButton(
            self.frame_header,
            text="PARADO (Clique para Iniciar)",
            font=FONT_BOLD,
            command=self.toggle_status,
            height=40,
            fg_color="#546e7a", # Cinza/Azulado
            hover_color="#455a64" 
        )
        self.btn_status.pack(fill="x")

        # --- Scenarios (Collapsible) ---
        self.frame_scenarios = ctk.CTkFrame(self)
        self.frame_scenarios.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        
        # Header
        self.frame_scenarios_header = ctk.CTkFrame(self.frame_scenarios, fg_color="transparent")
        self.frame_scenarios_header.pack(fill="x", padx=5, pady=5)
        
        self.lbl_scenarios_title = ctk.CTkLabel(self.frame_scenarios_header, text="Cenários Ativos", font=FONT_BOLD)
        self.lbl_scenarios_title.pack(side="left", padx=5)
        
        self.btn_toggle_scenarios = ctk.CTkButton(
            self.frame_scenarios_header, 
            text="▼ Mostrar", 
            width=80, 
            height=24,
            command=self.toggle_scenarios,
            fg_color="transparent", 
            border_width=1, 
            text_color=("gray10", "gray90")
        )
        self.btn_toggle_scenarios.pack(side="right", padx=5)

        # Content (Initially Hidden)
        self.scroll_checks = ctk.CTkScrollableFrame(self.frame_scenarios, height=0, fg_color="transparent")
        
        self.scenarios_expanded = False
        self.scenario_vars = {} # name -> CTkCheckBox

        # --- Manual Download & Search ---
        self.frame_mid = ctk.CTkFrame(self)
        self.frame_mid.grid(row=2, column=0, sticky="ew", padx=10, pady=5)

        # Download Manual
        lbl_manual = ctk.CTkLabel(self.frame_mid, text="Download Manual", font=("Roboto", 12))
        lbl_manual.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.var_server = tk.StringVar(value="HAC")
        
        rb_hac = ctk.CTkRadioButton(self.frame_mid, text="HAC", variable=self.var_server, value="HAC", width=50)
        rb_hac.grid(row=0, column=1, sticky="w", padx=5)
        
        rb_hbr = ctk.CTkRadioButton(self.frame_mid, text="HBR", variable=self.var_server, value="HBR", width=50)
        rb_hbr.grid(row=0, column=2, sticky="w", padx=5)

        self.entry_an = ctk.CTkEntry(self.frame_mid, placeholder_text="Accession Number")
        self.entry_an.grid(row=0, column=3, sticky="ew", padx=5)
        self.entry_an.bind("<Return>", lambda e: self.do_manual_download())
        self.frame_mid.grid_columnconfigure(3, weight=1)

        btn_dl = ctk.CTkButton(self.frame_mid, text="Baixar", width=60, command=self.do_manual_download)
        btn_dl.grid(row=0, column=4, sticky="e", padx=10)

        # Search
        self.entry_search = ctk.CTkEntry(self.frame_mid, placeholder_text="Filtrar por nome, AN, modalidade...")
        self.entry_search.grid(row=1, column=0, columnspan=5, sticky="ew", padx=10, pady=(5,10))
        self.entry_search.bind("<KeyRelease>", self.filter_list)

        # --- List View (Treeview via Custom Style) ---
        self.frame_list = ctk.CTkFrame(self, fg_color="transparent")
        self.frame_list.grid(row=3, column=0, sticky="nsew", padx=10, pady=5)

        # Treeview Style
        style = ttk.Style()
        style.theme_use("default")
        
        # Cores Treeview Compatíveis com Dark/Light
        bg_color = "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#ffffff"
        fg_color = "#ffffff" if ctk.get_appearance_mode() == "Dark" else "#000000"
        field_bg = "#2b2b2b" if ctk.get_appearance_mode() == "Dark" else "#ffffff"
        head_bg  = "#1f1f1f" if ctk.get_appearance_mode() == "Dark" else "#e0e0e0"


        style.configure("Treeview", 
            background=bg_color, 
            foreground=fg_color, 
            fieldbackground=field_bg,
            borderwidth=0,
            rowheight=22, # Altura menor
            font=("Roboto", 10) # Fonte menor
        )
        style.map('Treeview', background=[('selected', '#00e676')], foreground=[('selected', 'black')])
        
        style.configure("Treeview.Heading", 
            background=head_bg, 
            foreground=fg_color, 
            font=("Roboto", 11, "bold"),
            relief="flat"
        )
        style.map("Treeview.Heading", background=[('active', head_bg)])

        cols = ("AN", "Nome", "Status")
        self.tree = ttk.Treeview(self.frame_list, columns=cols, show="headings", selectmode="browse")
        
        self.tree.heading("AN", text="AN")
        self.tree.heading("Nome", text="Exame")
        self.tree.heading("Status", text="Status")
        
        self.tree.column("AN", width=90, anchor="center")
        self.tree.column("Nome", width=300)
        self.tree.column("Status", width=80, anchor="center")

        # Scrollbar customizada não tem no CTk facilmente para widget ttk
        # Usaremos scrollbar simples do ttk estilizada ou padrão
        scrollbar = ttk.Scrollbar(self.frame_list, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.tree.bind("<Double-1>", self.on_item_double_click)

        # --- Footer ---
        self.frame_footer = ctk.CTkFrame(self)
        self.frame_footer.grid(row=4, column=0, sticky="ew", padx=10, pady=10)

        # Session Count
        self.lbl_session = ctk.CTkLabel(self.frame_footer, text="Sessão: 0", font=FONT_BOLD)
        self.lbl_session.pack(side="left", padx=10)
        
        # Always on Top Switch (Pack Right First -> Rightmost)
        self.switch_top = ctk.CTkSwitch(
            self.frame_footer, 
            text="Topo", 
            command=self.toggle_topmost,
            width=50,
            font=("Roboto", 11)
        )
        self.switch_top.pack(side="right", padx=10)
        if self.start_topmost:
            self.switch_top.select()

        # Label Manter (Pack Right Next -> Left of Switch)
        self.lbl_max = ctk.CTkLabel(self.frame_footer, text=f"Manter: {config.MAX_EXAMES}")
        self.lbl_max.pack(side="right", padx=10)

        # Slider (Pack Right Next -> Left of Label)
        slider_limit = max(config.SLIDER_MAX, config.MAX_EXAMES)
        self.scale_max = ctk.CTkSlider(
            self.frame_footer, from_=5, to=slider_limit, 
            command=self.on_slider_change,
        )
        self.scale_max.set(config.MAX_EXAMES)
        self.scale_max.pack(side="right", fill="x", expand=True, padx=20)

        # Log Line
        self.lbl_log = ctk.CTkLabel(self, text="Aguardando...", anchor="w", text_color="gray")
        self.lbl_log.grid(row=5, column=0, sticky="ew", padx=15, pady=(0, 5))

    def queue_log(self, ts, tipo, msg):
        self.log_queue.put((ts, tipo, msg))

    def process_log_queue(self):
        try:
            while True:
                ts, tipo, msg = self.log_queue.get_nowait()
                color = self.get_log_color(tipo)
                self.lbl_log.configure(text=f"[{ts}] {msg}", text_color=color)
                
                if tipo == "FINALIZADO" and "completo" in msg:
                    self.session_downloads += 1
                    self.lbl_session.configure(text=f"Sessão: {self.session_downloads}")
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_log_queue)

    def get_log_color(self, tipo):
        if tipo == "ERRO": return COLOR_RED
        if tipo == "FINALIZADO": return "#00b0ff" # Azul claro
        if tipo == "OK": return COLOR_GREEN
        return "gray" if ctk.get_appearance_mode() == "Light" else "silver"

    def toggle_topmost(self):
        val = self.switch_top.get()
        self.attributes("-topmost", bool(val))

    def toggle_scenarios(self):
        if self.scenarios_expanded:
            self.scroll_checks.pack_forget()
            self.btn_toggle_scenarios.configure(text="▼ Mostrar")
            self.scenarios_expanded = False
        else:
            self.scroll_checks.configure(height=150)
            self.scroll_checks.pack(fill="x", padx=5, pady=5)
            self.btn_toggle_scenarios.configure(text="▲ Ocultar")
            self.scenarios_expanded = True
            
    def update_scenarios_label(self):
        count = len(self.app_state.scenarios)
        self.lbl_scenarios_title.configure(text=f"Cenários ({count} ativos)")

    def load_queries_files(self):
        # Limpa widgets anteriores
        for widget in self.scroll_checks.winfo_children():
            widget.destroy()
        
        q_dir = Path("queries")
        if not q_dir.exists():
            q_dir.mkdir(parents=True, exist_ok=True)
            
        files = sorted([f.stem for f in q_dir.glob("*.json")])
        
        self.scenario_vars = {}
        current_active = self.app_state.scenarios

        for f in files:
            chk = ctk.CTkCheckBox(
                self.scroll_checks, 
                text=f, 
                command=lambda name=f: self.on_scenario_toggle(name)
            )
            if f in current_active:
                chk.select()
            chk.pack(anchor="w", padx=5, pady=2)
            self.scenario_vars[f] = chk
            
        self.update_scenarios_label()

    def on_scenario_toggle(self, name):
        chk = self.scenario_vars[name]
        if chk.get():
            if name not in self.app_state.scenarios:
                self.app_state.scenarios.append(name)
        else:
            if name in self.app_state.scenarios:
                self.app_state.scenarios.remove(name)
        
        self.save_config_value("SETTINGS", "scenarios", json.dumps(self.app_state.scenarios))
        logger.log_info(f"Cenários ativos: {self.app_state.scenarios}")
        self.update_scenarios_label()

    def toggle_status(self):
        if not self.app_state.loop_running:
            self.app_state.start_loop(on_exit_callback=self.on_loop_exit)
            self.update_status_ui("RODANDO")
        else:
            st = self.app_state.toggle_pause_loop()
            self.update_status_ui(st)

    def update_status_ui(self, status):
        if status == "RODANDO":
            self.btn_status.configure(text="MONITORANDO (Clique para Pausar)", fg_color=COLOR_GREEN, text_color="black")
        elif status == "PAUSADO":
            self.btn_status.configure(text="PAUSADO (Clique para Retomar)", fg_color="#ffa000", text_color="white")
        else:
            self.btn_status.configure(text="PARADO (Clique para Iniciar)", fg_color="#546e7a", text_color="white")

    def on_loop_exit(self):
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
        
        for item in self.tree.get_children():
            self.tree.delete(item)
            
        for i in self.all_items:
            combo = f"{i.get('an','')} {i.get('nome','')} {i.get('desc','')} {i.get('mod','')}".lower()
            if not term or term in combo:
                values = (i.get("an"), f"{i.get('nome')} - {i.get('desc')}", i.get("qtd"))
                self.tree.insert("", tk.END, iid=i["path"], values=values)

    def refresh_data_loop(self):
        try:
            new_items = self.scan_recentes()
            self.all_items = new_items
            self.filter_list()
        except Exception as e:
            print(f"Erro refresh: {e}")
        finally:
            self.after(5000, self.refresh_data_loop)

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
                
                dcm_path = config.OUTPUT_DICOM_DIR / an
                
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
        selected_id = self.tree.focus()
        if selected_id:
            path = selected_id
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
        val = int(value)
        self.lbl_max.configure(text=f"Manter: {val}")
        config.MAX_EXAMES = val
        self.save_config_value("SETTINGS", "max_exames", val)
        threading.Thread(target=self.trigger_cleanup, daemon=True).start()

    def trigger_cleanup(self):
        try:
            loop.verificar_retencao_exames()
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
    parser = argparse.ArgumentParser(description="Nox Assistant - Monitoramento de Downloads DICOM", add_help=False)
    arg_group = parser.add_argument_group("Argumentos")
    opt_group = parser.add_argument_group("Opções")
    
    group = opt_group.add_mutually_exclusive_group()
    group.add_argument("--gui", "-g", action="store_true", help="Executa com Interface Gráfica (Padrão)")
    group.add_argument("--cli", "-c", action="store_true", help="Executa em modo Linha de Comando")
    
    opt_group.add_argument("--no-prepare", action="store_true", help="Pular etapa de preparação")
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")
    arg_group.add_argument("cenarios", metavar="CENARIOS", nargs="*", help="Cenários específicos")
    
    args, extra_loop_args = parser.parse_known_args()
    
    cenarios = args.cenarios if args.cenarios else None

    if extra_loop_args and not args.cli:
        parser.error(f"Argumentos não reconhecidos para modo GUI: {' '.join(extra_loop_args)}")

    if args.cli:
        print("--- INICIANDO O NOX (CLI) ---")
        try:
            loop_args = []
            if cenarios: loop_args.extend(cenarios)
            if args.no_prepare: loop_args.append("--no-prepare")
            loop_args.extend(extra_loop_args)
            loop.main(args=loop_args)
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.")
            sys.exit(0)
    else:
        # GUI Mode
        print("--- INICIANDO O NOX (CUSTOM TKINTER) ---")
        app = NoxApp(scenarios=cenarios, no_prepare=args.no_prepare)
        app.mainloop()

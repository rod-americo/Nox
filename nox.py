import flet as ft
import sys
import os
import threading
import time
import subprocess
from pathlib import Path

# Módulos locais
import config
import loop
import logger
import downloader
import json
import downloader
import prepare # Importar prepare para listar cenários

# ============================================================
# CONSTANTES GUI / TEMA
# ============================================================
COLOR_PRIMARY = "#00E676"  # Verde Radiante (accents)

# Configura Cores baseado no config.THEME
IS_LIGHT = config.THEME == "light"

if IS_LIGHT:
    COLOR_BG      = "#ffffff"
    COLOR_CARD    = "#f0f0f0"
    COLOR_TEXT    = "#000000"
    COLOR_SUBTEXT = "#666666"
    COLOR_DIVIDER = "#e0e0e0"
    THEME_MODE    = ft.ThemeMode.LIGHT
else:
    # Dark (default)
    COLOR_BG      = "#1a1a1a"
    COLOR_CARD    = "#2d2d2d"
    COLOR_TEXT    = "#ffffff"
    COLOR_SUBTEXT = "#9e9e9e"
    COLOR_DIVIDER = "#424242"
    THEME_MODE    = ft.ThemeMode.DARK

class AppState:
    def __init__(self, scenarios=None, no_prepare=False):
        self.loop_controller = loop.LoopController()
        # Inicializa sem thread rodando, para usuário dar Start
        self.loop_thread = None
        self.loop_running = False
        self.scenarios = scenarios or config.SCENARIOS
        self.no_prepare = no_prepare

    def start_loop(self, on_exit_callback=None):
        if not self.loop_thread or not self.loop_thread.is_alive():
            self.loop_controller = loop.LoopController()
            self.loop_controller.resume()

            def runner():
                try:
                    # Passa os cenários definidos na instância e flag --no-prepare se houver
                    args_to_pass = list(self.scenarios)
                    if self.no_prepare:
                        args_to_pass.append("--no-prepare")

                    loop.main(controller=self.loop_controller, args=args_to_pass)
                except (Exception, SystemExit) as e:
                    # Captura erros de inicialização (prepare.py) ou runtime
                    pass 
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

# ler_resumo_dicom removido (agora via JSON)

# ... (código intermediário omitido, pois é grande, vamos manter foco) ...
# REFAZENDO AS MUDANÇAS DE MANEIRA MAIS CIRÚRGICA ABAIXO

# ============================================================
# Layout
# ============================================================

def main(page: ft.Page, scenarios=None):
    page.title = "Nox"
    page.theme_mode = THEME_MODE
    page.bgcolor = COLOR_BG
    page.padding = 5
    
    # Defaults
    set_window_prop(page, "width", 340)
    set_window_prop(page, "height", 600)
    set_window_prop(page, "resizable", True)
    set_window_prop(page, "always_on_top", True)  # Mantém janela sempre visível
    
    # Permitir fechar naturalmente (trataremos cleanup no on_disconnect)
    set_window_prop(page, "prevent_close", False)

    state = AppState(scenarios=scenarios)

# ler_resumo_dicom removido (agora via JSON)

def scan_recentes():
    """
    Retorna lista de exames baseada nos JSONs de progresso em config.PROGRESS_DIR.
    Não depende da existência dos arquivos DICOM (suporta modo Transient).
    """
    if not config.PROGRESS_DIR.exists():
        return []
        
    results = []
    # Itera sobre JSONs
    for p in config.PROGRESS_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            
            an = data.get("an", p.stem)
            
            # Tenta pegar metadados do JSON (novas versões) ou fallback
            nome = data.get("patient_name", "—")
            desc = data.get("study_desc", "")
            mod  = data.get("modality", "")
            
            # Status e Contagem
            total = data.get("total", 0)
            baixadas = data.get("baixadas", 0) # Ou len(historico)
            status = data.get("status", "desconhecido")
            
            # Formatação de visualização 
            if status == "completo":
                qtd_str = f"{total} img"
            elif status == "baixando":
                qtd_str = f"{baixadas}/{total}"
            else:
                qtd_str = f"{status}"

            # Path para Viewer (apenas relevante se Persistent)
            # Se Transient, open_viewer lidará com isso (geralmente URL scheme)
            dcm_path = config.RADIANT_DICOM_DIR / an
            
            results.append({
                "an": an, 
                "nome": nome, 
                "mod": mod, 
                "desc": desc, 
                "qtd": qtd_str, 
                "path": str(dcm_path),
                "mtime": p.stat().st_mtime
            })
        except Exception:
            continue
    
    # Ordena alfabeticamente pelo Nome do Paciente
    results.sort(key=lambda x: x["nome"])
    return results

import json
import downloader
import shutil

# Sync removido (agora feito via downloader no modo Transient)

GUI_STATE_FILE = config.DATA_DIR / "gui_config.json"

def get_window_prop(page, prop_name, default=None):
    # 1. Tenta via page.window (Flet > 0.21)
    if hasattr(page, "window"):
        val = getattr(page.window, prop_name, None)
        if val is not None: return val
        
    # 2. Tenta via page.window_ (Legacy)
    legacy = f"window_{prop_name}"
    if hasattr(page, legacy):
        val = getattr(page, legacy, None)
        if val is not None: return val
        
    # 3. Tenta acesso direto ao atributo se existir (algumas versoes do Flet expõem page.window_top)
    if hasattr(page, prop_name):
         val = getattr(page, prop_name, None)
         if val is not None: return val
         
    return default

def set_window_prop(page, prop_name, value):
    if value is None: return
    # Tenta via page.window (Flet > 0.21)
    if hasattr(page, "window") and hasattr(page.window, prop_name):
        setattr(page.window, prop_name, value)
        return
    # Tenta via page.window_ (Legacy)
    legacy = f"window_{prop_name}"
    if hasattr(page, legacy):
        setattr(page, legacy, value)

    
    # Função movida para escopo interno de main para acesso ao logger/debug
    pass

def load_window_state(page: ft.Page):
    if not GUI_STATE_FILE.exists():
        return
    try:
        data = json.loads(GUI_STATE_FILE.read_text(encoding="utf-8"))
        set_window_prop(page, "width", data.get("width", 340))
        set_window_prop(page, "height", data.get("height", 600))
        set_window_prop(page, "top", data.get("top"))
        set_window_prop(page, "left", data.get("left"))
    except Exception as e:
        print(f"ERRO ao carregar estado: {e}")

def main(page: ft.Page, scenarios=None, no_prepare=False):
    page.title = "Nox"
    page.theme_mode = THEME_MODE
    page.bgcolor = COLOR_BG
    page.padding = 5
    
    # Defaults
    set_window_prop(page, "width", 340)
    set_window_prop(page, "height", 600)
    set_window_prop(page, "resizable", True)
    set_window_prop(page, "always_on_top", True)  # Mantém janela sempre visível
    
    # Permitir fechar naturalmente (trataremos cleanup no on_disconnect)
    set_window_prop(page, "prevent_close", False)

    state = AppState(scenarios=scenarios, no_prepare=no_prepare)
    
    load_window_state(page)

    def window_event(e):
        # Salva estado em eventos de janela
        if e.data in ["moved", "resized", "maximize", "restore"]:
             save_window_state(page)

    page.on_window_event = window_event
    page.on_resized = lambda e: save_window_state(page)

    # --- ELEMENTOS VISUAIS ---

    # Status Ball
    status_indicator = ft.Container(
        width=10, height=10, border_radius=5, bgcolor=ft.Colors.GREY_500,
        animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT)
    )
    status_label = ft.Text("Parado", size=12, color=COLOR_SUBTEXT)

    # Log Line
    log_line = ft.Text("Aguardando...", size=10, color=COLOR_SUBTEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    # Log Line (moved up) e gui_log agora definido junto com Session Counter
    pass
    # logger.set_gui_callback(gui_log) # já setado acima

    def save_window_state(page: ft.Page):
        """Salva dimensões da janela. Posição não é suportada pelo Flet em todas plataformas."""
        try:
            data = {
                "width": get_window_prop(page, "width", 340),
                "height": get_window_prop(page, "height", 600)
            }
            GUI_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except:
            pass  # Falha silenciosa para não interromper o app

    # Boot Sync (OsiriX) - Removido
    # threading.Thread(target=sync_to_osirix, args=(gui_log,), daemon=True).start()

    def reset_loop_ui():
        # Callback para quando o loop morrer (erro ou stop)
        # Reseta visual para Parado
        if state.loop_running: 
            return # Sanity check, mas o finally setou False antes chamar

        status_indicator.bgcolor = ft.Colors.GREY_500
        status_label.value = "Parado"
        # Usa bg_st que será definido abaixo, python resolve em runtime
        try:
            status_container.bgcolor = bg_st # type: ignore
        except: pass

        if IS_LIGHT:
            status_label.color = COLOR_SUBTEXT
        else:
            status_label.color = COLOR_SUBTEXT
        page.update()

    def on_status_click(e):
        if not state.loop_running:
            state.start_loop(on_exit_callback=reset_loop_ui)
            status_indicator.bgcolor = ft.Colors.GREEN_400
            status_label.value = "Monitorando (Clique para Pausar)"
            status_container.bgcolor = ft.Colors.GREEN_900 if not IS_LIGHT else ft.Colors.GREEN_100
            status_label.color = ft.Colors.WHITE
            if IS_LIGHT: status_label.color = ft.Colors.BLACK
        else:
            st = state.toggle_pause_loop()
            if st == "PAUSADO":
                status_indicator.bgcolor = ft.Colors.ORANGE_400
                status_label.value = "Pausado (Clique para Retomar)"
                status_container.bgcolor = ft.Colors.ORANGE_900 if not IS_LIGHT else ft.Colors.ORANGE_100
                status_label.color = ft.Colors.WHITE
                if IS_LIGHT: status_label.color = ft.Colors.BLACK
            else:
                status_indicator.bgcolor = ft.Colors.GREEN_400
                status_label.value = "Monitorando (Clique para Pausar)"
                status_container.bgcolor = ft.Colors.GREEN_900 if not IS_LIGHT else ft.Colors.GREEN_100
                status_label.color = ft.Colors.WHITE
                if IS_LIGHT: status_label.color = ft.Colors.BLACK
        page.update()

    # Status Row Interativa
    bg_st = ft.Colors.GREY_900 if not IS_LIGHT else ft.Colors.GREY_200
    status_container = ft.Container(
        content=ft.Row(
            [status_indicator, status_label], 
            alignment="center",
            spacing=7
        ),
        padding=7,
        border_radius=5,
        bgcolor=bg_st,
        on_click=on_status_click,
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT)
    )

    # Manual Download Input
    
    # Server Selector (Radio)
    rg_server = ft.RadioGroup(
        content=ft.Row([
            ft.Radio(value="HAC", label="HAC"),
            ft.Radio(value="HBR", label="HBR"),
        ]),
        value="HAC" # Default
    )

    txt_download = ft.TextField(
        hint_text="Accession Number", 
        text_size=12, height=35, content_padding=10,
        expand=True, border_color=COLOR_DIVIDER,
        color=COLOR_TEXT
    )
    
    def run_manual_download(server, an):
        gui_log(time.strftime("%H:%M:%S"), "INFO", f"Iniciando manual: {server} {an}")
        try:
            ok = downloader.baixar_an(server, an)
            if ok:
                gui_log(time.strftime("%H:%M:%S"), "OK", f"Download concluído: {an}")
            else:
                gui_log(time.strftime("%H:%M:%S"), "ERRO", f"Falha download: {an}")
        except Exception as e:
            gui_log(time.strftime("%H:%M:%S"), "ERRO", f"Erro: {e}")
            
    def btn_download_click(e):
        srv = rg_server.value
        val = txt_download.value.strip()
        
        if not val: 
            return
        
        # Validação simples
        if " " in val:
             # Usuário pode ter colado algo sujo ou tentado o formato antigo
            gui_log(time.strftime("%H:%M:%S"), "ERRO", "Digite apenas o AN (Use a seleção acima para o servidor).")
            return

        txt_download.value = ""
        page.update()
        
        threading.Thread(target=run_manual_download, args=(srv, val)).start()

    row_manual = ft.Row([
        txt_download,
        ft.Container(content=rg_server, padding=ft.padding.symmetric(horizontal=5)),
        ft.IconButton(ft.Icons.DOWNLOAD, icon_size=20, icon_color=COLOR_TEXT, on_click=btn_download_click, tooltip="Baixar Manualmente")
    ], spacing=2, alignment="center")

    # Lista Scroll
    list_view = ft.ListView(expand=True, spacing=1, padding=0)

    # Search Filter
    all_items = []

    def render_list(e=None):
        term = txt_search.value.lower().strip()
        filtered = []
        
        if not term:
            filtered = all_items
        else:
            for i in all_items:
                # Busca em AN, Nome, Descrição e Modality
                combo = f"{i['an']} {i['nome']} {i['desc']} {i['mod']}".lower()
                if term in combo:
                    filtered.append(i)

        list_view.controls.clear()
        
        if not filtered:
            msg = "Nenhum exame encontrado" if not term else "Nenhum resultado para a busca"
            list_view.controls.append(ft.Text(msg, size=12, italic=True, color=COLOR_SUBTEXT))
        else:
            for i in filtered:
                container = ft.Container(
                    content=ft.Row([
                        ft.Text(i["an"], weight="bold", size=13, color=COLOR_PRIMARY, font_family="Courier New"),
                        ft.Text(f"{i['nome']} .:. {i['mod']} .:. {i['desc']}", size=12, color=COLOR_TEXT, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                        ft.Text(f"{i['qtd']}", size=11, color=COLOR_SUBTEXT),
                    ], spacing=10),
                    padding=ft.padding.symmetric(horizontal=8, vertical=6),
                    bgcolor=COLOR_CARD,
                    border_radius=4,
                    on_click=lambda e, p=i["path"], a=i["an"]: open_viewer(p, a)
                )
                list_view.controls.append(container)
        
        try:
            page.update()
        except: pass

    txt_search = ft.TextField(
        hint_text="Buscar exames...", 
        text_size=12, height=35, content_padding=10, 
        prefix_icon=ft.Icons.SEARCH,
        border_color=COLOR_DIVIDER,
        color=COLOR_TEXT,
        on_change=render_list
    )

    # --- Session Counter ---
    session_downloads = 0
    lbl_session = ft.Text("Sessão: 0", size=12, color=COLOR_PRIMARY, weight="bold")

    # --- Config Writer Helper ---
    def save_config_value(section, key, value):
        import configparser
        parser = configparser.ConfigParser()
        parser.read(config.CONFIG_FILE)
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, key, str(value))
        with open(config.CONFIG_FILE, 'w') as f:
            parser.write(f)
            
    # --- Max Exames Slider ---
    def on_max_change(e):
        val = int(e.control.value)
        lbl_max.value = f"Manter: {val}"
        
        # Atualiza runtime e arquivo
        config.MAX_EXAMES = val
        save_config_value("SETTINGS", "max_exames", val)
        
        # Dispara limpeza imediata sem bloquear UI
        try:
            loop.verificar_retencao_exames()
            refresh_data()
        except: pass
        
        page.update()

    # Log Line
    log_line = ft.Text("Aguardando...", size=10, color=COLOR_SUBTEXT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    def gui_log(ts, tipo, msg):
        try:
            log_line.value = f"[{ts}] {msg}"
            # FINALIZADO usa ciano, OK verde, ERRO vermelho
            if tipo == "ERRO":
                log_line.color = ft.Colors.RED_400
            elif tipo == "FINALIZADO":
                 log_line.color = ft.Colors.CYAN_400
            else:
                log_line.color = COLOR_SUBTEXT
            
            # Hook para contar downloads (FINALIZADO = completo)
            if tipo == "FINALIZADO" and "completo" in msg:
                 nonlocal session_downloads
                 session_downloads += 1
                 lbl_session.value = f"Sessão: {session_downloads}"
                 lbl_session.update()

            page.update()
        except:
            pass  # Ignora erros durante shutdown

    logger.set_gui_callback(gui_log)

    # Slider Max dinâmico
    # Garante que o limite do slider seja pelo menos igual ao valor atual, caso configurado errado
    slider_limit = max(config.SLIDER_MAX, config.MAX_EXAMES)
    
    slider_max = ft.Slider(
        min=5, max=slider_limit, divisions=slider_limit-5, 
        value=config.MAX_EXAMES, 
        label="{value}", 
        on_change_end=on_max_change,
        expand=True
    )
    lbl_max = ft.Text(f"Manter: {config.MAX_EXAMES}", size=12, color=COLOR_SUBTEXT)

    # Layout compacto: [Sessão: X] [Slider] [Manter: Y]
    row_config = ft.Row([
        lbl_session,
        slider_max,
        lbl_max
    ], alignment="center", spacing=10)

    # Manual Download Input

    def open_viewer(path, an):
        viewer_type = config.VIEWER
        
        if viewer_type in ["osirix", "horos"]:
            # Abre via URL Scheme do OsiriX/Horos
            url = f"osirix://?methodName=displayStudy&AccessionNumber={an}"
            try:
                open_folder(url) # Reusa a função open_folder que já trata startfile/open/xdg-open
                gui_log(time.strftime("%H:%M:%S"), "INFO", f"OsiriX chamado: {an}")
            except Exception as e:
                gui_log(time.strftime("%H:%M:%S"), "ERRO", f"Erro OsiriX: {e}")
        else:
            # Default: RadiAnt
            radiant_exe = Path(config.RADIANT_EXE)
            if radiant_exe.exists():
                cmd = [str(radiant_exe), "-cl", "-d", str(path)]
                try:
                    subprocess.Popen(cmd)
                    gui_log(time.strftime("%H:%M:%S"), "INFO", f"RadiAnt aberto: {an}")
                except Exception as e:
                    gui_log(time.strftime("%H:%M:%S"), "ERRO", f"Erro RadiAnt: {e}")
            else:
                # Fallback
                open_folder(path)
                gui_log(time.strftime("%H:%M:%S"), "AVISO", "Viewer não encontrado. Abrindo pasta.")

    def open_folder(path):
        import subprocess
        # Se for URL (osirix://), o 'open' ou 'startfile' deve lidar
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def refresh_data():
        nonlocal all_items
        all_items = scan_recentes()
        render_list()

    # Lista atual de cenários selecionados (começa com o do config/args)
    selected_scenarios = list(state.scenarios)

    def on_scenario_check(e):
        val = e.control.label
        if e.control.value:
            if val not in selected_scenarios:
                selected_scenarios.append(val)
        else:
            if val in selected_scenarios:
                selected_scenarios.remove(val)
        
        # Atualiza state e config runtime
        state.scenarios = selected_scenarios
        config.SCENARIOS = selected_scenarios
        
        # Persistir no INI
        save_config_value("SETTINGS", "scenarios", json.dumps(selected_scenarios))
        
        gui_log(time.strftime("%H:%M:%S"), "INFO", f"Cenários ativos: {', '.join(selected_scenarios)}")
        gui_log(time.strftime("%H:%M:%S"), "INFO", "Reinicie o monitor para aplicar.")

    scenario_column = ft.Column(scroll=ft.ScrollMode.AUTO)

    def load_all_scenarios(e):
        btn_load_scenarios.disabled = True
        btn_load_scenarios.text = "Carregando..."
        page.update()
        
        def _fetch():
            try:
                cmd = [sys.executable, "prepare.py", "--mapear-cenarios"]
                result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
                
                found_list = []
                                
                if result.returncode == 0:
                    output = result.stdout
                    capturing = False
                    ignorar = ["Destaques", "Editar Destaques", "Fav", "Cenários", "Ações", "Origem", "Nativos", "--- CENÁRIOS DISPONÍVEIS ---", "----------------------------"]
                    
                    for line in output.splitlines():
                        line = line.strip()
                        if "--- CENÁRIOS DISPONÍVEIS ---" in line:
                            capturing = True
                            continue
                        if capturing and line.startswith("---"):
                            break
                        
                        if capturing and line:
                            clean_line = line.replace("- ", "").strip()
                            if clean_line and clean_line not in ignorar:
                                found_list.append(clean_line)
                    
                    found_list = sorted(list(set(found_list)))
                else:
                    gui_log(time.strftime("%H:%M:%S"), "ERRO", "Falha ao rodar prepare.py")
                    print(result.stderr)

                if not found_list:
                    gui_log(time.strftime("%H:%M:%S"), "AVISO", "Nenhum cenário extraído.")
                
                current_ui_set = {c.label for c in scenario_column.controls if isinstance(c, ft.Checkbox)}
                
                added_count = 0
                for f in found_list:
                    if f not in current_ui_set:
                        ck = ft.Checkbox(label=f, value=(f in selected_scenarios), on_change=on_scenario_check)
                        scenario_column.controls.append(ck)
                        added_count += 1
                
                if added_count > 0:
                    gui_log(time.strftime("%H:%M:%S"), "INFO", f"{added_count} novos cenários carregados.")
                
                btn_load_scenarios.text = "Atualizar Lista"
            except Exception as ex:
                gui_log(time.strftime("%H:%M:%S"), "ERRO", f"Erro listar: {ex}")
                btn_load_scenarios.text = "Erro (Tentar novamente)"
            finally:
                btn_load_scenarios.disabled = False
                page.update()

        threading.Thread(target=_fetch, daemon=True).start()

    # Popular inicialmente
    for s in selected_scenarios:
        scenario_column.controls.append(
            ft.Checkbox(label=s, value=True, on_change=on_scenario_check)
        )

    btn_load_scenarios = ft.ElevatedButton(
        "Carregar Todos", 
        icon=ft.Icons.CLOUD_DOWNLOAD, 
        height=30, 
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=5)),
        on_click=load_all_scenarios
    )

    scenario_expander = ft.ExpansionTile(
        title=ft.Text("Cenários", size=13, weight="bold"),
        subtitle=ft.Text("Selecione os cenários para monitorar", size=11, color=COLOR_SUBTEXT),
        controls=[
            ft.Container(
                content=ft.Column([
                    ft.Container(content=scenario_column, height=200), # Altura fixa para scroll
                    ft.Divider(height=1),
                    btn_load_scenarios
                ], spacing=5),
                padding=10
            )
        ],
        initially_expanded=False,
        collapsed_text_color=COLOR_TEXT,
        text_color=COLOR_PRIMARY
    )

    # Layout Montagem
    page.add(
        ft.Row([ft.Text(config.TITLE, weight="bold", size=14, color=COLOR_TEXT)], alignment="center"),
        status_container,
        ft.Divider(color=COLOR_DIVIDER, height=1),
        scenario_expander, # Adicionado aqui
        ft.Divider(color=COLOR_DIVIDER, height=1),
        row_manual,
        ft.Divider(color=COLOR_DIVIDER, height=1),
        txt_search,
        list_view, 
        ft.Divider(color=COLOR_DIVIDER, height=1),
        ft.Container(content=row_config, padding=ft.padding.symmetric(horizontal=10)),
        ft.Container(content=log_line, padding=ft.padding.only(bottom=5))
    )
    
    # Limpeza inicial ao abrir a GUI para refletir MAX_EXAMES
    try:
        loop.verificar_retencao_exames()
    except Exception as e:
        print(f"Erro ao verificar retenção na inicialização: {e}")

    # Watcher Thread (Check files every 5s) - SIMPLIFICADO
    def watcher():
        while True:
            time.sleep(5)
            try:
                refresh_data()
            except: 
                pass  # Ignora erros durante shutdown
    
    threading.Thread(target=watcher, daemon=True).start()
    refresh_data()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nox Assistant - Monitoramento de Downloads DICOM")
    
    # Modos de operação
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--gui", "-g", action="store_true", help="Executa com Interface Gráfica (Padrão)")
    group.add_argument("--cli", "-c", action="store_true", help="Executa em modo Linha de Comando")
    
    # Opções globais
    parser.add_argument("--no-prepare", action="store_true", help="Pular etapa de preparação (Playwright/Login)")
    
    # Cenários opcionais
    parser.add_argument("cenarios", metavar="CENARIOS", nargs="*", help="Cenários específicos (ex: MONITOR MONITOR_RX)")
    
    args = parser.parse_args()
    
    cenarios = args.cenarios if args.cenarios else None

    # CLI Mode
    if args.cli:
        print("--- INICIANDO O NOX (CLI) ---")
        try:
            # Reconstrói args para o loop.main
            loop_args = []
            if cenarios: loop_args.extend(cenarios)
            if args.no_prepare: loop_args.append("--no-prepare")
            
            loop.main(args=loop_args)
        except KeyboardInterrupt:
            print("\nInterrompido pelo usuário.")
            sys.exit(0)
            
    # GUI Mode (Default)
    else:
        print("--- INICIANDO O NOX (GUI) ---")
        if args.no_prepare: print("Opção: --no-prepare ativada")
        if cenarios: print(f"Cenários: {cenarios}")

        try:
            # Lambda para injetar argumentos no main da GUI
            ft.app(target=lambda page: main(page, scenarios=cenarios, no_prepare=args.no_prepare))
        except Exception as e:
            print(f"ERRO FATAL: {e}")
            input("Pressione ENTER para sair...")

# -*- coding: utf-8 -*-

"""
fetcher.py — coleta da API Cockpit (Unificado: Nox + Munin)
--------------------------------------------------------------

Modos de Operação:
    1. Padrão (Nox): Extrai apenas ANs e separa por unidade.
       Retorno: {"HBR": [...], "HAC": [...]}
       
    2. Raw/Munin (--raw): Baixa JSON completo e salva em disco.
       Argumentos extras: --inicio, --fim

Este módulo:
    • Em modo padrão, NÃO imprime nada na tela (exceto erros).
"""

import argparse
import json
import os
import requests
import time  # Importado globalmente para rate limiting
# import urllib3  <-- Removido para evitar conflito de versão (usamos via requests)
from pathlib import Path
import time
from math import ceil
from datetime import datetime, timedelta

# Tenta importar tqdm para barra de progresso (apenas modo raw)
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

from logger import log_info, log_erro, log_debug, log, log_ok, log_aviso
import config
from config import (
    URL_BASE,
    SESSION_FILE,
    DATA_DIR,
    COCKPIT_METADATA_DIR,
    SYSTEM_CONFIG,
)

# Silenciar aviso de InsecureRequestWarning (SSL verify=False)
try:
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
except:
    pass

# ============================================================
# Helpers: Payload Dinâmico (Lógica Munin)
# ============================================================

# Mapeamento de Origens (Documentação):
# 1, 2 = Eletivo
# 3    = Urgente
# 4    = Internado

# ============================================================
# CONSTANTES DE REGRAS (SCENARIO_RULES)
# ============================================================
SCENARIO_RULES = {
    # 1. MONITOR (CT/MR/US - Urgente/Internado - Não Assinado)
    "MONITOR": {
        "modalidades": ["CT", "MR", "US"],
        "origens": ["3", "4"],  # 3=Urgente, 4=Internado
        "date_field": "dt_imagem",
        "filtros_extra": {
            "imagem": ["S"],
            "assinado": ["N"],
            "inconformidade": ["N"],
        }
    },
    
    # 2. MONITOR RX (Raio-X - Apenas Internado - Lista Específica de Exames)
    "MONITOR_RX": {
        "modalidades": [],
        "origens": ["4"],
        "date_field": "dt_imagem",
        "filtros_extra": {
            "imagem": ["S"],
            "assinado": ["N"],
            "id_procedimento": ["96"],
            "exame": {
                "id_exame": [
                   5742, 4887, 4889, 4891, 5302, 4890, 5501, 
                   4858, 4859, 5461, 5304, 4903, 4904, 4902
                ],
                "excluindo": False
            },
            "tp_status": [
                "NOVO", "PENDENTELAUDADO", "PENDENTEREVISADO", "PROVISORIO", 
                "DITADO", "DIGITADO", "LAUDADO", "REVISADO", "ASSINADO", 
                "TERCEIRAOPINIAO", "LIBERADO", "ENTREGUE"
            ]
        }
    },

    # 3. SEMANAL ELETIVO (5 dias)
    "SEMANAL_E": {
        "origens": ["1", "2"], # 1=Amb, 2=Ext
        "date_field": "dt_pedido",
        "filtros_extra": {}
    },

    # 4. URGENTE (3 horas)
    "DIA_U": {
         "origens": ["3"],
         "date_field": "dt_pedido",
         "filtros_extra": {}
    },

    # 5. INTERNADO (36 horas)
    "DIAS_I": {
        "origens": ["4"],
        "date_field": "dt_pedido",
        "filtros_extra": {}
    },
    
    # 6. MENSAL / SEMANAL (Geral)
    "MENSAL": { "origens": [], "date_field": "dt_pedido", "filtros_extra": {} },
    "SEMANAL": { "origens": [], "date_field": "dt_pedido", "filtros_extra": {} },
}


def gerar_payload(dt_inicio: str, dt_fim: str, rule: dict = None):
    """
    Gera payload dinamicamente com base nas datas e regras fornecidas.
    """
    if len(dt_inicio) == 10: dt_inicio += "T00:00:00-03:00"
    if len(dt_fim) == 10:   dt_fim += "T23:59:59-03:00"

    # Base "limpa"
    payload = {
        "cd_id": "",
        "shortName": "dt_pedido",
        "shortOrder": "-1",
        "nm_notificacao": "",
        "nm_paciente": "",
        "nm_social_paciente": "",
        "nm_unidade": "",
        "cd_prontuario": "",
        "id_exame_pedido": "",
        "cd_atendimento_his": "",
        "cd_acnumber": "",
        "cd_pedido_his": "",
        "tp_status": [],
        "cd_item_pedido_his": "",
        "nm_exame": "",
        "nm_exame_Unidade": "",
        "nm_setor_executante": [],
        "tp_sexo": "",
        "cd_unidade": [],
        "nm_periodo_pedido": {"value": "outro", "label": "Outro"},
        "dt_pedido": {"dt_inicio": "", "dt_fim": ""}, # Zerado por padrão
        "nm_periodo_estudo": {"value": "", "label": ""},
        "dt_imagem": {"dt_inicio": "", "dt_fim": ""}, # Zerado por padrão
        "nm_periodo_imagem": {"value": "", "label": ""},
        "dt_cadastro": {"dt_inicio": "", "dt_fim": ""},
        "cd_modalidade": [],
        "nm_origem_atendimento": "",
        "nm_classificacao_risco": "",
        "sla": [],
        "tp_criticidade": [],
        "imagem": [],
        "inconformidade": [],
        "ditado": [],
        "digitado": [],
        "revisado": [],
        "laudado": [],
        "assinado": [],
        "liberado": [],
        "entregue": [],
        "id_origem_atendimento": [],
        "id_medico_executante": "",
        "id_medico_revisor": "",
        "nm_medico_executante": "",
        "nm_medico_revisor": "",
        "nm_medico_solicitante": "",
        "id_procedimento": [],
        "nr_prontuario_hospitalar": "",
        "id_convenio": [],
        "id_setor_solicitante": [],
        "id_medico_solicitante": "",
        "dt_entrega": {"sn_datas_futuras": "", "dt_inicio": "", "dt_fim": ""},
        "nm_periodo_entrega": {"value": "", "label": ""},
        "exame": {"id_exame": [], "excluindo": False},
        "cd_status_ia": [],
        "id_risco": [],
        "tp_impresso": "",
        "tp_comentario": "",
        "tp_anexo": "",
        "tp_certificado": ""
    }
    
    # Define Datas: Usa campo definido na regra (dt_imagem por padrão para Monitor)
    # Regra antiga do fetcher era dt_pedido, mas Monitor usava arquivo com dt_imagem.
    
    target_date_field = "dt_imagem" # Default seguro
    if rule and rule.get("date_field"):
        target_date_field = rule["date_field"]
    
    # Preenche o campo alvo
    payload[target_date_field]["dt_inicio"] = dt_inicio
    payload[target_date_field]["dt_fim"]    = dt_fim

    # Ajuste cosmético de label (opcional, mas bom pra manter igual)
    if target_date_field == "dt_imagem":
        payload["nm_periodo_imagem"] = {"value": "outro", "label": "Outro"}
    elif target_date_field == "dt_pedido":
         payload["nm_periodo_pedido"] = {"value": "outro", "label": "Outro"}
    
    # Aplica Regras

    if rule:
        # Modalidades
        if rule.get("modalidades"):
            payload["cd_modalidade"] = rule["modalidades"]
            
        # Origens (converte para lista de strings se não for)
        if rule.get("origens"):
            payload["id_origem_atendimento"] = [str(x) for x in rule["origens"]]
            
        # Filtros Extras (Atribuição Direta)
        extras = rule.get("filtros_extra", {})
        for k, v in extras.items():
            if k in payload:
                payload[k] = v
                
    return payload

# ============================================================
# Helpers
# ============================================================

def gerar_payload_an(an: str):
    """
    Gera um payload focado na busca por um Accession Number específico.
    Zera as datas para evitar filtragem temporal indesejada.
    """
    # Usa regra vazia
    payload = gerar_payload("", "")
    
    # Define o AN no campo correto
    payload["cd_item_pedido_his"] = an
    
    # Limpa filtros de data explicitamente
    payload["nm_periodo_imagem"] = {"value": "", "label": ""}
    
    return payload

def carregar_session():
    """Carrega sessão salva previamente. Falha → exceção clara."""
    try:
        return json.loads(Path(SESSION_FILE).read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Falha ao carregar sessão ({SESSION_FILE}): {e}")

# carregar_payload REMOVIDO EM FAVOR DA GERAÇÃO DINÂMICA


# ============================================================
# Paginador
# ============================================================

def fetch_pagina(pagina, tamanho, cookies, headers, payload):
    url = f"{URL_BASE}/ris/laudo/api/v1/worklist/listar/{pagina}/{tamanho}"

    try:
        r = requests.post(
            url,
            cookies=cookies,
            headers=headers,
            json=payload,
            timeout=120,
            verify=False,
        )
    except Exception as e:
        # Re-raise para ser tratado como erro fatal no loop
        raise RuntimeError(f"Erro de conexão página {pagina}: {e}")

    if r.status_code != 200:
        raise RuntimeError(f"Página {pagina} retornou status HTTP {r.status_code}")

    try:
        data = r.json()
        if not isinstance(data, list):
             raise RuntimeError(f"Página {pagina}: retorno inesperado (não é lista).")
        return data

    except Exception as e:
        raise RuntimeError(f"Falha ao decodificar JSON da página {pagina}: {e}")

# ============================================================
# Modo Raw / Munin
# ============================================================

def fetch_raw_mode(nome_cenario, dt_inicio=None, dt_fim=None, no_tqdm=False, origens=None):
    """
    Comportamento original do Munin: baixa tudo e salva JSON.
    Gera payload dinamicamente com base em SCENARIO_RULES.
    """
    
    # Busca regra
    rule = SCENARIO_RULES.get(nome_cenario)
    
    if not rule and not (dt_inicio and dt_fim):
        log_erro(f"[{nome_cenario}] ERRO: Regra desconhecida e datas não informadas.")
        return

    # Se origens foi passado via CLI, sobrescreve regra (ou cria regra on-the-fly)
    if origens:
        # Se não tiver regra, cria uma básica
        if not rule:
            rule = {"origens": origens, "filtros_extra": {}}
        else:
            # Sobrescreve origens, mantém filtros extra
            rule = rule.copy()
            rule["origens"] = origens

    # Gera Payload
    if not dt_inicio or not dt_fim:
         log_erro(f"[{nome_cenario}] ERRO: É obrigatório informar datas para geração dinâmica.")
         return

    payload = gerar_payload(dt_inicio, dt_fim, rule)

    # 2. Sessão
    try:
        s = carregar_session()
    except Exception as e:
        log_erro(f"[{nome_cenario}][FETCH] Sessão erro: {str(e)}")
        return

    cookies = {c["name"]: c["value"] for c in s.get("cookies", [])}
    headers = {
        "User-Agent": s["headers"]["User-Agent"],
        "Authorization": s["headers"]["Authorization"],
        "Content-Type": "application/json",
    }

    # 3. Paginação
    tamanho = 25
    pagina = 1
    acumulado = []

    # Página 1 (para estimar total)
    dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    
    if not dados:
        log_aviso(f"[{nome_cenario}][FETCH] empty: range={dt_inicio or '?'}/{dt_fim or '?'} (pg1)")
        outfile = DATA_DIR / f"{nome_cenario.lower()}_full.json"
        outfile.write_text("[]", encoding="utf-8")
        return

    # Tenta estimar total se o backend enviar (MOCK ou HEADER)
    total_registros = dados[0].get("quantidadePaginacao", len(dados))
    total_paginas = ceil(total_registros / tamanho)
    
    log_info(f"[{nome_cenario}][FETCH] exp={total_registros} pg={total_paginas}")

    # Barra de Progresso (se disponível e útil)
    # Barra de Progresso (se disponível e útil)
    usar_rich = (not no_tqdm) and (total_paginas > 5)
    
    start_time = time.time()
    last_log_time = start_time

    if usar_rich:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            transient=True
        ) as progress:
            task_id = progress.add_task(f"[cyan]{nome_cenario}", total=total_paginas)
            progress.update(task_id, completed=1)

            while True:
                if not dados:
                    break
                    
                acumulado.extend(dados)
                pagina += 1
                
                dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
                
                progress.update(task_id, advance=1)
    else:
        while True:
            if not dados:
                break
                
            acumulado.extend(dados)
            
            # Log periódico a cada 10s
            if time.time() - last_log_time > 10:
                if total_paginas > 1:
                    log_info(f"[{nome_cenario}][FETCH] prog: {pagina}/{total_paginas} ({len(acumulado)})")
                else:
                    log_info(f"[{nome_cenario}][FETCH] prog: pg {pagina} ({len(acumulado)})")
                last_log_time = time.time()

            pagina += 1
            dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    
    # 4. Salvar
    outfile = DATA_DIR / f"{nome_cenario.lower()}_full.json"
    outfile.write_text(
        json.dumps(acumulado, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    log_ok(f"[{nome_cenario}][FETCH] got: total={len(acumulado)} pg={pagina-1}/{total_paginas} save={outfile.name}")
    
    # log_info(f"Coleta concluída. Total: {len(acumulado)}")
    # log_info(f"Salvo em: {outfile.name}")

# ============================================================
# Extrator AN (Modo Nox)
# ============================================================

def extrair_an_servidor(registro):
    an = str(registro.get("cd_item_pedido_his") or "").strip()
    if not an:
        return None, None
    
    # Adição de id_exame_pedido para o nome da pasta (específico para Linux)
    id_exame = str(registro.get("id_exame_pedido") or "").strip()
    if SYSTEM_CONFIG == "linux" and id_exame:
        an = f"{an}_{id_exame}"

    unidade = (registro.get("nm_unidade") or "").upper().strip()
    srv = None
    if unidade == "HAC":
        srv = "HAC"
    elif unidade == "HOBRA":
        srv = "HBR"
    
    if an and srv:
        # Salva o subjson individual apenas se solicitado (Config ou CLI)
        if getattr(config, 'SAVE_METADATA', False):
            try:
                meta_path = COCKPIT_METADATA_DIR / f"{an}.json"
                meta_path.write_text(json.dumps(registro, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                log_debug(f"Erro ao salvar metadados cockpit para {an}: {e}")
            
        return an, srv
        
    return None, None

def fetch_cenario(nome_cenario: str) -> dict:
    resultado = {"HBR": [], "HAC": []}
    # log_info(f"Cenário {nome_cenario}: iniciando fetch…")

    try:
        s = carregar_session()
    except:
        return resultado
        
    cookies = {c["name"]: c["value"] for c in s.get("cookies", [])}
    headers = {
        "User-Agent": s["headers"]["User-Agent"],
        "Authorization": s["headers"]["Authorization"],
        "Content-Type": "application/json",
    }

    # No modo Nox, payload é obrigatório via regra
    rule = SCENARIO_RULES.get(nome_cenario)
    if not rule:
        # Se for um cenário desconhecido rodando em modo Nox, falhamos
        raise RuntimeError(f"Regra de payload {nome_cenario} inexistente (SCENARIO_RULES).")
        
    # Nox geralmente opera D-1 e D0
    agora = datetime.now()
    ontem = agora - timedelta(days=1)
    
    dt_ini = ontem.strftime("%Y-%m-%d")
    dt_fim = agora.strftime("%Y-%m-%d")
    
    payload = gerar_payload(dt_ini, dt_fim, rule)

    tamanho = 25
    pagina = 1

    dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    if not dados:
        # log_info(f"{nome_cenario}: nenhum exame encontrado.")
        return resultado

    total_registros = dados[0].get("quantidadePaginacao", len(dados))
    total_paginas = max(1, ceil(total_registros / tamanho))

    for r in dados:
        an, srv = extrair_an_servidor(r)
        if an and srv:
            resultado[srv].append(an)

    pagina += 1
    while pagina <= total_paginas:
        dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
        if not dados:
            break
        for r in dados:
            an, srv = extrair_an_servidor(r)
            if an and srv:
                resultado[srv].append(an)
        
        # Log de progresso a cada 2 páginas ou se for a última
        if pagina % 2 == 0 or pagina == total_paginas:
             total_atual = len(resultado['HBR']) + len(resultado['HAC'])
             log_info(f"[{nome_cenario}] Baixando página {pagina}/{total_paginas} (Total parcial: {total_atual})")
             
        pagina += 1

    # log_info(f"Cenário {nome_cenario}: {len(resultado['HBR'])} HBR, {len(resultado['HAC'])} HAC.")
    return resultado

def fetch_varios(cenarios: list[str]) -> dict:
    final = {"HBR": [], "HAC": []}
    for c in cenarios:
        parcial = fetch_cenario(c)
        final["HBR"].extend(parcial["HBR"])
        final["HAC"].extend(parcial["HAC"])
    
    final["HBR"] = list(dict.fromkeys(final["HBR"]))
    final["HAC"] = list(dict.fromkeys(final["HAC"]))
    return final

def parse_br_time(s):
    """
    Tenta parsear string de data com ou sem sufixo de timezone -03:00.
    Suporta timestamps com milliseconds (.000).
    """
    s = s.strip()
    if not s: return None
    
    # Remove milliseconds se presentes (ex: .000, .123, etc)
    # Procura por ponto seguido de 1-6 dígitos antes do timezone ou fim da string
    import re
    s = re.sub(r'\.\d{1,6}(?=[Z\-+]|$)', '', s)
    
    # Remove timezone suffix comum
    if s.endswith("-03:00"):
        s = s[:-6]
    elif s.endswith("Z"):
        s = s[:-1]
        
    if "T" in s:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    else:
        # Se for só data, assume 00:00:00
        return datetime.strptime(s, "%Y-%m-%d")

def ajustar_intervalo_datas(payload: dict):
    """
    Atualiza datas do payload:
    Fim = Agora
    Inicio = Agora - (FimOriginal - InicioOriginal)
    """
    campos = ["dt_imagem", "dt_pedido", "dt_cadastro", "dt_entrega"]
    agora = datetime.now()
    
    for campo in campos:
        if campo not in payload: continue
        
        d = payload[campo]
        if not isinstance(d, dict): continue
        
        ini_str = d.get("dt_inicio", "")
        fim_str = d.get("dt_fim", "")
        
        if not ini_str or not fim_str: continue
        
        try:
            dt_ini = parse_br_time(ini_str)
            dt_fim = parse_br_time(fim_str)
            
            if not dt_ini or not dt_fim: continue
            
            delta = dt_fim - dt_ini
            
            # Novo intervalo
            # Fim = Agora completo (data e hora)
            # Inicio = Fim - Delta
            
            novo_fim = agora
            novo_ini = agora - delta
            
            # Formata para string ISO com sufixo -03:00 (padrão do sistema)
            str_fim = novo_fim.strftime("%Y-%m-%dT%H:%M:%S-03:00")
            str_ini = novo_ini.strftime("%Y-%m-%dT%H:%M:%S-03:00")
            
            d["dt_fim"] = str_fim
            d["dt_inicio"] = str_ini
            
            # Ajusta label para "Outro" para evitar confusão na UI/Backend se houver validação
            label_key = f"nm_periodo_{campo.split('_')[1]}"
            if label_key in payload:
                 payload[label_key] = {"value": "outro", "label": "Outro"}
                 
            # log_info(f"Datas ajustadas ({campo}): {str_ini} até {str_fim} (Delta: {delta})")
            
        except Exception as e:
            log_erro(f"Falha ao ajustar datas dinâmicas para {campo}: {e}")


def fetch_from_file(file_path: str) -> dict:
    """
    Lê um arquivo JSON de payload, ajusta as datas para o momento atual (mantendo intervalo)
    e executa o fetch.
    """
    resultado = {"HBR": [], "HAC": []}
    p = Path(file_path)
    if not p.exists():
        log_erro(f"Arquivo de payload não encontrado: {file_path}")
        return resultado

    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        # Ajuste dinâmico de datas (Fim=Agora, Inicio=Agora-Delta)
        ajustar_intervalo_datas(payload)
    except Exception as e:
        log_erro(f"Erro ao ler/processar JSON {file_path}: {e}")
        return resultado

    try:
        s = carregar_session()
    except:
        return resultado

    cookies = {c["name"]: c["value"] for c in s.get("cookies", [])}
    headers = {
        "User-Agent": s["headers"]["User-Agent"],
        "Authorization": s["headers"]["Authorization"],
        "Content-Type": "application/json",
    }

    tamanho = 25
    pagina = 1

    dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    if not dados:
        log_info(f"[{p.name}] Nenhum exame encontrado para os critérios especificados.")
        return resultado

    total_registros = dados[0].get("quantidadePaginacao", len(dados))
    total_paginas = max(1, ceil(total_registros / tamanho))

    for r in dados:
        an, srv = extrair_an_servidor(r)
        if an and srv:
            resultado[srv].append(an)

    pagina += 1
    while pagina <= total_paginas:
        dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
        if not dados:
            break
        for r in dados:
            an, srv = extrair_an_servidor(r)
            if an and srv:
                resultado[srv].append(an)
        
        if pagina % 2 == 0 or pagina == total_paginas:
             total_atual = len(resultado['HBR']) + len(resultado['HAC'])
             log_info(f"[{p.name}] Baixando página {pagina}/{total_paginas} (Total parcial: {total_atual})")
             
        pagina += 1

    return resultado

def fetch_varios_arquivos(files: list[str]) -> dict:
    final = {"HBR": [], "HAC": []}
    log_info(f"Processando {len(files)} arquivo(s) de payload...")
    for i, f in enumerate(files, 1):
        log_info(f"[{i}/{len(files)}] Processando arquivo: {Path(f).name}")
        parcial = fetch_from_file(f)
        final["HBR"].extend(parcial["HBR"])
        final["HAC"].extend(parcial["HAC"])
        log_info(f"[{i}/{len(files)}] Resultado: HBR={len(parcial['HBR'])}, HAC={len(parcial['HAC'])}")
    
    final["HBR"] = list(dict.fromkeys(final["HBR"]))
    final["HAC"] = list(dict.fromkeys(final["HAC"]))
    return final

# ============================================================
# Wrapper de Compatibilidade (Munin)
# ============================================================
def api_fetch(nome_cenario, dt_inicio=None, dt_fim=None):
    """
    Wrapper para manter compatibilidade com munin/monitor.py
    que chama api_fetch() esperando comportamento Raw.
    """
    return fetch_raw_mode(nome_cenario, dt_inicio, dt_fim, no_tqdm=True)

# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fetcher Cockpit CLI", add_help=False)
    
    # Grupos
    arg_group = parser.add_argument_group("Argumentos")
    opt_group = parser.add_argument_group("Opções")
    flt_group = parser.add_argument_group("Filtros de Origem")

    arg_group.add_argument("cenarios", nargs="*", help="Lista de cenários ou arquivo JSON (ex: queries/monitor.json)")

    opt_group.add_argument("--json", action="store_true", help="Saída JSON pura (apenas ANs)")
    opt_group.add_argument("--raw", action="store_true", help="Modo RAW: Salva JSON completo em disco (comportamento Munin)")
    opt_group.add_argument("--inicio", type=str, help="Data inicio YYYY-MM-DD (apenas modo --raw)")
    opt_group.add_argument("--fim", type=str, help="Data fim YYYY-MM-DD (apenas modo --raw)")
    opt_group.add_argument("--an", nargs="+", help="Busca por lista de Accession Numbers (ignora datas/cenários)")
    opt_group.add_argument("--an-file", type=str, help="Caminho de arquivo JSON contendo lista de ANs")
    opt_group.add_argument("--no-tqdm", action="store_true", help="Desativa barra de progresso (útil para logs)")
    opt_group.add_argument("--metadado", action="store_true", help="Salva metadados Cockpit/DICOM")
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")

    # Filtros de Origem
    flt_group.add_argument("--eletivo", action="store_true", help="Filtra por Eletivo (IDs 1, 2)")
    flt_group.add_argument("--urgente", action="store_true", help="Filtra por Urgente (ID 3)")
    flt_group.add_argument("--internado", action="store_true", help="Filtra por Internado (ID 4)")
    
    args = parser.parse_args()

    # Override config se flag presente
    if args.metadado:
        config.SAVE_METADATA = True

    # Validação: ou tem cenários ou tem AN (arg ou file)
    if not args.cenarios and not args.an and not args.an_file:
        parser.error("É necessário informar pelo menos um cenário ou usar --an / --an-file")

    # Compila lista de origens
    origens_ids = []
    if args.eletivo:
        origens_ids.extend(["1", "2"])
    if args.urgente:
        origens_ids.append("3")
    if args.internado:
        origens_ids.append("4")
    # Remove duplicatas e ordem
    origens_ids = sorted(list(set(origens_ids))) if origens_ids else None

    # Silenciar logs se modo JSON e não RAW
    if args.json and not args.raw:
        import logger
        logger._out = lambda *args, **kwargs: None

    try:
        # --- ROTA RAW (Munin) ---
        if args.raw:
            # Garante que log funciona (já importado globalmente)
            # log_info("=== MODO RAW ATIVADO ===")
            for c in args.cenarios:
                fetch_raw_mode(c, dt_inicio=args.inicio, dt_fim=args.fim, no_tqdm=args.no_tqdm, origens=origens_ids)
            return
            
        # --- ROTA FETCH PADRÃO (NOX) ---
        if args.cenarios:
            arquivos_json = []
            nomes_legados = []
            
            for item in args.cenarios:
                # Se terminar com .json ou for um arquivo existente, trata como path
                p = Path(item)
                if item.lower().endswith(".json") or p.exists():
                    arquivos_json.append(str(item))
                else:
                    nomes_legados.append(item)
            
            # 1. Processa Arquivos JSON (Moderno)
            if arquivos_json:
                fetch_varios_arquivos(arquivos_json)
                
            # 2. Processa Nomes Legados (Compatibilidade)
            if nomes_legados:
                # log_info(f"Processando cenários legados: {nomes_legados}")
                fetch_varios(nomes_legados)
                
            return

        # --- ROTA BUSCA POR AN ---
        if args.an or args.an_file:
            # log_info(f"=== BUSCA POR LISTA DE ANs ===")
            
            # Unificar origens (CLI + File)
            lista_final_ans = []
            if args.an:
                lista_final_ans.extend(args.an)
            
            if args.an_file:
                p = Path(args.an_file)
                if p.exists():
                    try:
                        file_ans = json.loads(p.read_text(encoding="utf-8"))
                        if isinstance(file_ans, list):
                            lista_final_ans.extend([str(x) for x in file_ans])
                        else:
                            log_erro(f"Conteúdo de {p} não é uma lista válida.")
                    except Exception as e:
                        log_erro(f"Erro lendo arquivo AN {p}: {e}")
                else:
                    log_erro(f"Arquivo AN não encontrado: {p}")
            
            # Remove duplicatas
            lista_final_ans = list(dict.fromkeys(lista_final_ans))
            
            if not lista_final_ans:
                log_erro("Nenhum AN válido para processar.")
                return


            log_info(f"[WATCHDOG][FETCH] start: ans={len(lista_final_ans)}")
            
            try:
                s = carregar_session()
            except Exception as e:
                log_erro(str(e))
                return

            cookies = {c["name"]: c["value"] for c in s.get("cookies", [])}
            headers = {
                "User-Agent": s["headers"]["User-Agent"],
                "Authorization": s["headers"]["Authorization"],
                "Content-Type": "application/json",
            }

            acumulado_ans = []
            last_save_time = time.time()

            for i, an_atual in enumerate(lista_final_ans, 1):
                # log_info(f"[{i}/{len(lista_final_ans)}] Consultando AN: {an_atual} ...")
                
                payload = gerar_payload_an(an_atual)
                
                # Retry logic para 429
                sucesso = False
                tentativas = 0
                max_tentativas = 3
                
                while tentativas < max_tentativas:
                    try:
                        # Pequeno delay preventivo entre requisições
                        time.sleep(1)
                        
                        dados = fetch_pagina(1, 25, cookies, headers, payload)
                        if dados:
                            acumulado_ans.extend(dados)
                            # log_info(f"   -> Encontrado(s): {len(dados)} registro(s).")
                        else:
                            pass # log_info("   -> Nenhum registro.")
                        sucesso = True
                        break
                        
                    except Exception as e:
                        msg_erro = str(e)
                        if "429" in msg_erro:
                            log_aviso(f"[WATCHDOG][FETCH] Rate limit (429). Aguardando 60s...")
                            time.sleep(60)
                            tentativas += 1
                        else:
                            log_erro(f"[WATCHDOG][FETCH] Erro AN {an_atual}: {e}")
                            break
                            
                if not sucesso and tentativas == max_tentativas:
                    log_erro(f"[WATCHDOG][FETCH] Falha AN {an_atual}")

                # Parcial Save (a cada 10s)
                if time.time() - last_save_time > 10:
                    outfile = DATA_DIR / "ans_full.json"
                    outfile.write_text(
                        json.dumps(acumulado_ans, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )
                    log_info(f"[WATCHDOG][FETCH] parcial: {len(acumulado_ans)} records saved")
                    last_save_time = time.time()

            # Salvar resultado consolidado
            outfile = DATA_DIR / "ans_full.json"
            outfile.write_text(
                json.dumps(acumulado_ans, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            
            # log_info("=== FINALIZADO ===")
            # log_info(f"Total de registros encontrados: {len(acumulado_ans)}")
            log_ok(f"[WATCHDOG][FETCH] done: found={len(acumulado_ans)} save={outfile.name}")
            return

        # --- ROTA NOX (Padrão) ---
        dados = fetch_varios(args.cenarios)
        
        if args.json:
            print(json.dumps(dados, indent=2, ensure_ascii=False))
        else:
            total_hbr = len(dados["HBR"])
            total_hac = len(dados["HAC"])
            # from logger import log_info  <-- Removido pois causa UnboundLocalError
            log_info(f"Total consolidado: {total_hbr} HBR, {total_hac} HAC")

    except Exception as e:
        if args.json and not args.raw:
            sys.stderr.write(f"ERRO: {e}\n")
            sys.exit(1)
        else:
            log_erro(str(e))
            sys.exit(1)

if __name__ == "__main__":
    main()

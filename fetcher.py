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

import sys
import argparse
import json
import requests
import urllib3
from pathlib import Path
from math import ceil
from datetime import datetime, timedelta

# Tenta importar tqdm para barra de progresso (apenas modo raw)
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from logger import log_info, log_erro, log_debug, log
from config import (
    URL_BASE,
    SESSION_FILE,
    DATA_DIR,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ============================================================
# Helpers: Payload Dinâmico (Lógica Munin)
# ============================================================

def gerar_payload(dt_inicio: str, dt_fim: str):
    """
    Gera um payload padrão filtrando por data de pedido.
    Formatos esperados: YYYY-MM-DD
    """
    if len(dt_inicio) == 10:
        dt_inicio += "T00:00:00-03:00"
    if len(dt_fim) == 10:
        dt_fim += "T23:59:59-03:00"

    return {
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
        "dt_pedido": {"dt_inicio": dt_inicio, "dt_fim": dt_fim},
        "nm_periodo_estudo": {"value": "", "label": ""},
        "dt_imagem": {"dt_inicio": "", "dt_fim": ""},
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


# ============================================================
# Helpers
# ============================================================

def carregar_session():
    """Carrega sessão salva previamente. Falha → exceção clara."""
    try:
        return json.loads(Path(SESSION_FILE).read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"Falha ao carregar sessão ({SESSION_FILE}): {e}")


def carregar_payload(nome_cenario):
    """Carrega payload correspondente ao cenário."""
    path = DATA_DIR / f"payload_{nome_cenario}.json"
    if not path.exists():
        # Retorna None para permitir fallback de datas no modo raw, 
        # mas gera erro no modo Nox se for crucial.
        return None
        
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        
        # Lógica de atualização dinâmica de datas (Nox legacy)
        try:
            val = payload.get("nm_periodo_imagem", {}).get("value")
            if val == "ontemhoje":
                agora = datetime.now()
                ontem = agora - timedelta(days=1)
                
                dt_ini = ontem.strftime("%Y-%m-%dT00:00:00.000Z")
                dt_fim = agora.strftime("%Y-%m-%dT23:59:59.000Z")
                
                payload["dt_imagem"]["dt_inicio"] = dt_ini
                payload["dt_imagem"]["dt_fim"]    = dt_fim
                
                msg_dt_ini = ontem.strftime("%d/%m")
                msg_dt_fim = agora.strftime("%d/%m")
                log_info(f"Payload {nome_cenario}: datas ajustadas ({msg_dt_ini} a {msg_dt_fim})")
                
            elif val == "mes":
                agora = datetime.now()
                inicio = agora - timedelta(days=30)
                
                dt_ini = inicio.strftime("%Y-%m-%dT00:00:00.000Z")
                dt_fim = agora.strftime("%Y-%m-%dT23:59:59.000Z")
                
                payload["dt_imagem"]["dt_inicio"] = dt_ini
                payload["dt_imagem"]["dt_fim"]    = dt_fim
                
                log_info(f"Payload {nome_cenario}: datas ajustadas (últimos 30 dias)")

        except Exception:
            pass

        return payload

    except Exception as e:
        raise RuntimeError(f"Erro lendo payload {path}: {e}")


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
        log_erro(f"Erro de conexão página {pagina}: {e}")
        return None

    if r.status_code != 200:
        log_erro(f"Página {pagina} retornou status HTTP {r.status_code}")
        return None

    try:
        data = r.json()
        if not isinstance(data, list):
            log_erro(f"Página {pagina}: retorno inesperado (não é lista).")
            return []
        return data

    except Exception as e:
        log_erro(f"Falha ao decodificar JSON da página {pagina}: {e}")
        return []


# ============================================================
# Modo Raw / Munin
# ============================================================

def fetch_raw_mode(nome_cenario, dt_inicio=None, dt_fim=None):
    """
    Comportamento original do Munin: baixa tudo e salva JSON.
    """
    log_info(f"=== FETCH RAW: {nome_cenario} ===")
    
    # 1. Resolver Payload
    if dt_inicio and dt_fim:
        log_info(f"Modo Data Range: {dt_inicio} até {dt_fim}")
        payload = gerar_payload(dt_inicio, dt_fim)
    else:
        payload = carregar_payload(nome_cenario)
        if not payload:
            log_erro(f"ERRO: Payload '{nome_cenario}' não encontrado e datas não informadas.")
            return

    # 2. Sessão
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

    # 3. Paginação
    tamanho = 25
    pagina = 1
    acumulado = []

    # Página 1 (para estimar total)
    dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    
    if not dados:
        log_info("Primeira página vazia ou erro.")
        outfile = DATA_DIR / f"{nome_cenario.lower()}_full.json"
        outfile.write_text("[]", encoding="utf-8")
        return

    acumulado.extend(dados)
    total_registros = dados[0].get("quantidadePaginacao", len(dados))
    total_paginas = ceil(total_registros / tamanho)
    
    log_info(f"Total estimado: {total_registros} registros em {total_paginas} páginas")

    # Barra de Progresso (se disponível e útil)
    pbar = None
    usar_tqdm = (tqdm is not None) and (total_paginas > 5)
    
    if usar_tqdm:
        pbar = tqdm(total=total_paginas, desc=nome_cenario, unit="pág")
        pbar.update(1)

    pagina += 1

    # Demais páginas
    while pagina <= total_paginas:
        dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
        if not dados:
            break
        acumulado.extend(dados)
        
        if pbar:
            pbar.update(1)
        elif pagina % 5 == 0 or pagina == total_paginas:
            log_info(f"Progresso: {pagina}/{total_paginas} páginas")
        pagina += 1

    if pbar:
        pbar.close()

    # 4. Salvar
    outfile = DATA_DIR / f"{nome_cenario.lower()}_full.json"
    outfile.write_text(
        json.dumps(acumulado, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    log_info(f"Coleta concluída. Total: {len(acumulado)}")
    log_info(f"Salvo em: {outfile.name}")


# ============================================================
# Extrator AN (Modo Nox)
# ============================================================

def extrair_an_servidor(registro):
    an = str(registro.get("cd_item_pedido_his") or "").strip()
    if not an:
        return None, None
    unidade = (registro.get("nm_unidade") or "").upper().strip()
    if unidade == "HAC":
        return an, "HAC"
    elif unidade == "HOBRA":
        return an, "HBR"
    return None, None


def fetch_cenario(nome_cenario: str) -> dict:
    resultado = {"HBR": [], "HAC": []}
    log_info(f"Cenário {nome_cenario}: iniciando fetch…")

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

    # No modo Nox, payload é obrigatório do arquivo
    payload = carregar_payload(nome_cenario)
    if not payload:
        raise RuntimeError(f"Payload {nome_cenario} inexistente.")

    tamanho = 25
    pagina = 1

    dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    if not dados:
        log_info(f"{nome_cenario}: nenhum exame encontrado.")
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
        pagina += 1

    log_info(f"Cenário {nome_cenario}: {len(resultado['HBR'])} HBR, {len(resultado['HAC'])} HAC.")
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


# ============================================================
# Wrapper de Compatibilidade (Munin)
# ============================================================
def api_fetch(nome_cenario, dt_inicio=None, dt_fim=None):
    """
    Wrapper para manter compatibilidade com munin/monitor.py
    que chama api_fetch() esperando comportamento Raw.
    """
    return fetch_raw_mode(nome_cenario, dt_inicio, dt_fim)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fetcher Cockpit CLI")
    parser.add_argument("cenarios", nargs="+", help="Lista de cenários (ex: MONITOR HOBRA)")
    parser.add_argument("--json", action="store_true", help="Saída JSON pura (apenas ANs)")
    parser.add_argument("--raw", action="store_true", help="Modo RAW: Salva JSON completo em disco (comportamento Munin)")
    parser.add_argument("--inicio", type=str, help="Data inicio YYYY-MM-DD (apenas modo --raw)")
    parser.add_argument("--fim", type=str, help="Data fim YYYY-MM-DD (apenas modo --raw)")
    
    args = parser.parse_args()

    # Silenciar logs se modo JSON e não RAW
    if args.json and not args.raw:
        import logger
        logger._out = lambda *args, **kwargs: None

    try:
        # --- ROTA RAW (Munin) ---
        if args.raw:
            # Garante que log funciona
            from logger import log_info
            # log_info("=== MODO RAW ATIVADO ===")
            for c in args.cenarios:
                fetch_raw_mode(c, dt_inicio=args.inicio, dt_fim=args.fim)
            return

        # --- ROTA NOX (Padrão) ---
        dados = fetch_varios(args.cenarios)
        
        if args.json:
            print(json.dumps(dados, indent=2, ensure_ascii=False))
        else:
            total_hbr = len(dados["HBR"])
            total_hac = len(dados["HAC"])
            from logger import log_info
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

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
import time  # Importado globalmente para rate limiting
# import urllib3  <-- Removido para evitar conflito de versão (usamos via requests)
from pathlib import Path
import time
from math import ceil
from datetime import datetime, timedelta

# Tenta importar tqdm para barra de progresso (apenas modo raw)
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from logger import log_info, log_erro, log_debug, log, log_ok, log_aviso
from config import (
    URL_BASE,
    SESSION_FILE,
    DATA_DIR,
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

def gerar_payload(dt_inicio: str, dt_fim: str, origens: list = None):
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
        "id_origem_atendimento": origens or [],
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

def gerar_payload_an(an: str):
    """
    Gera um payload focado na busca por um Accession Number específico.
    Zera as datas para evitar filtragem temporal indesejada.
    """
    # Parte de um payload "vazio" (datas vazias)
    payload = gerar_payload("", "")
    
    # Define o AN no campo correto
    payload["cd_item_pedido_his"] = an
    
    # Algumas APIs exigem que se limpe explicitamente outros filtros ou
    # que se defina um range de data longo se o backend for mal feito.
    # Por enquanto, tentamos sem data. Se falhar, o usuário testará.
    return payload

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
                # log_info(f"Payload {nome_cenario}: datas ajustadas ({msg_dt_ini} a {msg_dt_fim})")
                
            elif val == "mes":
                agora = datetime.now()
                inicio = agora - timedelta(days=30)
                
                dt_ini = inicio.strftime("%Y-%m-%dT00:00:00.000Z")
                dt_fim = agora.strftime("%Y-%m-%dT23:59:59.000Z")
                
                payload["dt_imagem"]["dt_inicio"] = dt_ini
                payload["dt_imagem"]["dt_fim"]    = dt_fim
                
                # log_info(f"Payload {nome_cenario}: datas ajustadas (últimos 30 dias)")

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
    """
    # Banner removed to reduce verbosity
    # log_info(f"=== FETCH RAW: {nome_cenario} ===")
    
    # 1. Resolver Payload
    if dt_inicio and dt_fim:
        # log_info(f"Modo Data Range: {dt_inicio} até {dt_fim}")
        if origens:
            pass # log_info(f"Filtro de Origens ID: {origens}")
        payload = gerar_payload(dt_inicio, dt_fim, origens)
    else:
        payload = carregar_payload(nome_cenario)
        if not payload:
            log_erro(f"[{nome_cenario}][FETCH] ERRO: Payload não encontrado e datas não informadas.")
            return

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
    pbar = None
    usar_tqdm = (not no_tqdm) and (tqdm is not None) and (total_paginas > 5)
    
    if usar_tqdm:
        pbar = tqdm(total=total_paginas, desc=nome_cenario, unit="pág")
        pbar.update(1)
    
    start_time = time.time()
    last_log_time = start_time

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
        
        if usar_tqdm and pbar:
             pbar.update(1)

    if pbar:
        pbar.close()
    
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
    unidade = (registro.get("nm_unidade") or "").upper().strip()
    if unidade == "HAC":
        return an, "HAC"
    elif unidade == "HOBRA":
        return an, "HBR"
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

    # No modo Nox, payload é obrigatório do arquivo
    payload = carregar_payload(nome_cenario)
    if not payload:
        raise RuntimeError(f"Payload {nome_cenario} inexistente.")

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
    parser = argparse.ArgumentParser(description="Fetcher Cockpit CLI")
    parser.add_argument("cenarios", nargs="*", help="Lista de cenários (ex: MONITOR HOBRA)")
    parser.add_argument("--json", action="store_true", help="Saída JSON pura (apenas ANs)")
    parser.add_argument("--raw", action="store_true", help="Modo RAW: Salva JSON completo em disco (comportamento Munin)")
    parser.add_argument("--inicio", type=str, help="Data inicio YYYY-MM-DD (apenas modo --raw)")
    parser.add_argument("--fim", type=str, help="Data fim YYYY-MM-DD (apenas modo --raw)")
    parser.add_argument("--an", nargs="+", help="Busca por lista de Accession Numbers (ignora datas/cenários)")
    parser.add_argument("--an-file", type=str, help="Caminho de arquivo JSON contendo lista de ANs")
    parser.add_argument("--no-tqdm", action="store_true", help="Desativa barra de progresso (útil para logs)")

    # Filtros de Origem
    parser.add_argument("--eletivo", action="store_true", help="Filtra por Eletivo (IDs 1, 2)")
    parser.add_argument("--urgente", action="store_true", help="Filtra por Urgente (ID 3)")
    parser.add_argument("--internado", action="store_true", help="Filtra por Internado (ID 4)")
    
    args = parser.parse_args()

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

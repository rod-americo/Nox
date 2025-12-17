# -*- coding: utf-8 -*-

"""
fetcher.py — coleta da API Cockpit (Modelo 3 — Fila em Disco)
--------------------------------------------------------------

Funções:
    fetch_cenario(nome)  → {"HBR": [...], "HAC": [...]}
    fetch_varios(lista)  → consolida resultados

Este módulo:
    • NÃO imprime nada na tela
    • NÃO interage com downloader
    • Apenas retorna listas de ANs adequadas para o loop enqueue
"""

import sys
import argparse
import json
import requests
import urllib3
from pathlib import Path
from math import ceil
from datetime import datetime, timedelta

from logger import log_info, log_erro, log_debug
from config import (
    URL_BASE,
    SESSION_FILE,
    DATA_DIR,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
        raise RuntimeError(f"Payload não encontrado: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        
        # Lógica de atualização dinâmica de datas
        # Se for "ontemhoje", forçamos as datas para Ontem 00:00 -> Hoje 23:59
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
                
        except Exception:
            # Se falhar a lógica de data, segue com original
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
            timeout=30,
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
# Extrator AN + servidor
# ============================================================

def extrair_an_servidor(registro):
    """
    Identifica:
        AN = cd_item_pedido_his
        Unidade:
            "HAC"   → HAC
            "HOBRA" → HBR
    """
    an = str(registro.get("cd_item_pedido_his") or "").strip()
    if not an:
        return None, None

    unidade = (registro.get("nm_unidade") or "").upper().strip()

    if unidade == "HAC":
        return an, "HAC"
    elif unidade == "HOBRA":
        return an, "HBR"
    return None, None


# ============================================================
# Fetch de cenário
# ============================================================

def fetch_cenario(nome_cenario: str) -> dict:
    """
    Retorna dicionário:
        { "HBR": [...], "HAC": [...] }
    """
    resultado = {"HBR": [], "HAC": []}

    log_info(f"Cenário {nome_cenario}: iniciando fetch…")

    # sessão
    s = carregar_session()
    cookies = {c["name"]: c["value"] for c in s.get("cookies", [])}
    headers = {
        "User-Agent": s["headers"]["User-Agent"],
        "Authorization": s["headers"]["Authorization"],
        "Content-Type": "application/json",
    }

    # payload
    payload = carregar_payload(nome_cenario)

    tamanho = 25
    pagina = 1

    # primeira página
    dados = fetch_pagina(pagina, tamanho, cookies, headers, payload)
    if not dados:
        log_info(f"{nome_cenario}: nenhum exame encontrado (0 resultados).")
        return resultado

    total_registros = dados[0].get("quantidadePaginacao", len(dados))
    total_paginas = max(1, ceil(total_registros / tamanho))

    # processa página 1
    for r in dados:
        an, srv = extrair_an_servidor(r)
        if an and srv:
            resultado[srv].append(an)

    pagina += 1

    # demais páginas
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


# ============================================================
# Fetch consolidado
# ============================================================

def fetch_varios(cenarios: list[str]) -> dict:
    """
    Retorna:
        { "HBR": [...], "HAC": [...] }
    sem duplicatas, mantendo ordem.
    """
    final = {"HBR": [], "HAC": []}

    for c in cenarios:
        parcial = fetch_cenario(c)
        final["HBR"].extend(parcial["HBR"])
        final["HAC"].extend(parcial["HAC"])

    # remove duplicatas sem alterar ordem
    final["HBR"] = list(dict.fromkeys(final["HBR"]))
    final["HAC"] = list(dict.fromkeys(final["HAC"]))

    return final


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Fetcher Cockpit CLI")
    parser.add_argument("cenarios", nargs="+", help="Lista de cenários (ex: MONITOR HOBRA)")
    parser.add_argument("--json", action="store_true", help="Saída JSON pura")
    args = parser.parse_args()

    # Silenciar logs se modo JSON
    if args.json:
        import logger
        logger._out = lambda *args, **kwargs: None

    try:
        dados = fetch_varios(args.cenarios)
        
        if args.json:
            print(json.dumps(dados, indent=2, ensure_ascii=False))
        else:
            # Resumo simples (os logs detalhados já saem no stderr/stdout via log_info)
            total_hbr = len(dados["HBR"])
            total_hac = len(dados["HAC"])
            from logger import log_info
            log_info(f"Total consolidado: {total_hbr} HBR, {total_hac} HAC")

    except Exception as e:
        if args.json:
            sys.stderr.write(f"ERRO: {e}\n")
            sys.exit(1)
        else:
            log_erro(str(e))
            sys.exit(1)


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
batch_laudo.py — Script para laudar exames em lote no Cockpit Web.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
import requests

import config
# Forçar salvamento de metadados para que o batch_laudo consiga ler o id_exame_pedido
config.SAVE_METADATA = True

import fetcher
import montar_laudo_rtf
import gravar_laudo
from logger import log_info, log_ok, log_erro, log_debug, log_aviso

# Endpoint URLs
LAUDAR_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/laudar"
REVISAR_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/laudarrevisar"
PERMIT_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/permitirlaudar"

def get_exams_from_query(query_input: str, args: argparse.Namespace) -> list[str]:
    """
    Busca ANs a partir de um cenário ou arquivo JSON.
    Inclui lógica de retry caso a sessão esteja expirada.
    """
    p = Path(query_input)
    is_file = query_input.lower().endswith(".json") or p.exists()
    
    def do_fetch():
        if is_file:
            log_info(f"Lendo query do arquivo: {p.name}")
            return fetcher.fetch_from_file(str(p))
        else:
            log_info(f"Lendo query do cenário: {query_input}")
            return fetcher.fetch_cenario(query_input)

    try:
        resultado = do_fetch()
    except RuntimeError as e:
        if "HTTP 401" in str(e) or "sessão" in str(e).lower():
            log_aviso("Sessão expirada ao buscar exames. Tentando renovar...")
            gravar_laudo.refresh_session()
            resultado = do_fetch()
        else:
            raise

    ans = resultado.get("HBR", []) + resultado.get("HAC", [])
    
    if args.one and len(ans) > 1:
        log_info(f"Modo --one ativado. Limitando processamento ao primeiro registro: {ans[0]}")
        ans = [ans[0]]
        
    return ans

def process_batch(args: argparse.Namespace):
    # 1. Carregar Sessão
    try:
        session_payload = gravar_laudo.load_session()
    except FileNotFoundError:
        session_payload = gravar_laudo.refresh_session()
    
    client = gravar_laudo.prepare_client(session_payload)
    
    # 2. Carregar Corpo do Laudo
    if args.texto_file:
        body_text = Path(args.texto_file).read_text(encoding="utf-8")
    else:
        body_text = args.texto or "Laudo gerado automaticamente."
    
    # 3. Obter ANs
    ans = get_exams_from_query(args.query, args)
    if not ans:
        log_aviso("Nenhum exame encontrado para a query informada.")
        return

    log_info(f"Encontrados {len(ans)} exames para processar.")
    
    # 4. Loop de Processamento
    sucessos = 0
    falhas = 0
    
    for idx, an_full in enumerate(ans, 1):
        # O fetcher pode retornar AN_ID (especialmente no Linux), extraímos o AN puro se necessário
        an = an_full.split("_")[0] if "_" in an_full else an_full
        
        log_info(f"[{idx}/{len(ans)}] Processando AN {an}...")
        
        try:
            # A. Obter Metadados (para pegar o id_exame_pedido)
            # O fetcher já salvou metadados se config.SAVE_METADATA for True
            # Mas para garantir o id_laudo exato, precisamos do JSON do cockpit
            meta_path = config.COCKPIT_METADATA_DIR / f"{an_full}.json"
            if not meta_path.exists():
                log_erro(f"Metadado cockpit não encontrado em {meta_path}. Verifique se o fetcher está salvando corretamente.")
                falhas += 1
                continue
            
            cockpit_data = json.loads(meta_path.read_text(encoding="utf-8"))
            id_laudo = cockpit_data.get("id_exame_pedido")
            
            if not id_laudo:
                log_erro(f"ID do laudo não encontrado nos metadados de {an_full}.")
                falhas += 1
                continue

            # B. Verificar Permissão
            permit_payload = {"idLaudo": id_laudo}
            try:
                permit_resp = gravar_laudo.call_endpoint(client, PERMIT_URL, permit_payload)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 401:
                    session_payload = gravar_laudo.refresh_session()
                    client = gravar_laudo.prepare_client(session_payload)
                    permit_resp = gravar_laudo.call_endpoint(client, PERMIT_URL, permit_payload)
                else:
                    raise

            if not permit_resp.get("podeExecutar"):
                motivo = permit_resp.get("motivoBloqueio", "sem motivo")
                log_aviso(f"Pulo: {an} bloqueado ({motivo})")
                falhas += 1
                continue

            # C. Montar Payload
            # Simulamos os argumentos para o montar_laudo_rtf (reaproveitando a lógica de lá)
            class MockArgs:
                pass
            
            m_args = MockArgs()
            m_args.id_laudo = id_laudo
            m_args.medico_id = args.medico_id
            m_args.title = args.title
            m_args.body = body_text
            m_args.body_file = None
            m_args.pipeline_response = None
            m_args.pendente = not args.final
            m_args.provisorio = False
            m_args.urgente = False
            m_args.texto_urgencia = None
            m_args.nome_contato_urgencia = None
            m_args.data_hora_urgencia = None
            m_args.tag = []
            
            # Formatação RTF
            template = montar_laudo_rtf.TEMPLATE_PATH.read_text(encoding="utf-8")
            title_rtf = montar_laudo_rtf.escape_rtf(args.title.upper())
            
            # Lógica simplificada de parágrafos (direto do montar_laudo_rtf)
            lines = body_text.splitlines()
            paragraphs = montar_laudo_rtf.build_paragraphs(lines)
            rtf_content = template.replace("__TITLE__", title_rtf).replace("__BODY__", paragraphs)
            
            payload = montar_laudo_rtf.build_payload(m_args, body_text, rtf_content)
            
            # Campos extras necessários para o endpoint laudarrevisar
            payload.setdefault("idMedicoRevisor", payload.get("idMedicoExecutante"))
            payload.setdefault("idJustificativaRevisao", 0)
            payload.setdefault("justificativaRevisao", "")
            payload.setdefault("terceiraOpiniao", False)
            
            # D. Enviar
            target_url = REVISAR_URL if args.final else LAUDAR_URL
            
            if args.dry_run:
                log_info(f"[DRY-RUN] Enviaria para {target_url}")
                # print(json.dumps(payload, indent=2, ensure_ascii=False))
                sucessos += 1
                continue
            
            result = gravar_laudo.call_endpoint(client, target_url, payload)
            log_ok(f"Enviado: {an} (ID: {id_laudo})")
            sucessos += 1
            
            # Pequeno delay para não sobrecarregar
            if idx < len(ans):
                time.sleep(0.5)

        except Exception as e:
            log_erro(f"Erro ao processar AN {an}: {e}")
            falhas += 1

    log_info(f"Processamento concluído. Sucessos: {sucessos}, Falhas/Pulos: {falhas}")

def parse_args():
    parser = argparse.ArgumentParser(description="Gravar laudos em massa no Cockpit Web.")
    parser.add_argument("query", help="Cenário (MONITOR, etc) ou arquivo JSON de query.")
    parser.add_argument("--texto", help="Texto do laudo.")
    parser.add_argument("--texto-file", help="Arquivo com o texto do laudo.")
    parser.add_argument("--title", required=True, help="Título do laudo (ex: RADIOGRAFIA DE TÓRAX).")
    parser.add_argument("--final", action="store_true", help="Grava como final (revisar) em vez de pendente.")
    parser.add_argument("--medico-id", type=int, default=os.environ.get("MEDICO_EXECUTANTE_ID"),
                        help="ID do médico executante (padrão: MEDICO_EXECUTANTE_ID).")
    parser.add_argument("--dry-run", action="store_true", help="Apenas simula a operação sem enviar.")
    parser.add_argument("--one", action="store_true", help="Executa em apenas um registro da query.")
    
    args = parser.parse_args()
    
    if not args.texto and not args.texto_file:
        parser.error("Informe --texto ou --texto-file.")
    
    if not args.medico_id:
        # Fallback para o ID padrão do pipeline se não informado nem via ENV
        args.medico_id = getattr(config, "PIPELINE_DEFAULT_MEDICO_ID", 165111)
        
    return args

def main():
    args = parse_args()
    process_batch(args)

if __name__ == "__main__":
    main()

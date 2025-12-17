#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
query.py — módulo WADO-Query
Obtém metadata mínima para download:
- StudyInstanceUID
- SeriesInstanceUIDs
- SOPInstanceUIDs

Usa exclusivamente WADO-Query via accessionNumber.
O XML é salvo temporariamente em TMP_DIR e apagado após o parsing.
"""

import sys
import json
import requests
import xml.etree.ElementTree as ET
from pathlib import Path

import config
from logger import log_info, log_erro


# ============================================================
# BAIXAR XML (bytes)
# ============================================================

def _baixar_xml(an: str, servidor: str) -> bytes:
    servidor = servidor.upper()
    if servidor not in config.SERVERS:
        raise ValueError(f"Servidor inválido: {servidor}")

    srv = config.SERVERS[servidor]

    url = (
        f"http://{srv['server']}:{srv['wado_port']}/{srv['wado_path']}"
        f"?requestType=WADO&accessionNumber={an}&ContentType=text/xml"
    )

    log_info(f"WADO-Query AN {an} ({servidor})...")
    r = requests.get(url, timeout=15)

    if r.status_code != 200:
        raise RuntimeError(f"Falha HTTP {r.status_code}")

    if len(r.content.strip()) == 0:
        raise RuntimeError("Servidor retornou resposta vazia (ou apenas espaços).")

    return r.content


# ============================================================
# PARSER XML (UTF-8 / UTF-16)
# ============================================================

def _parse_xml(xml_bytes: bytes, an: str) -> dict:
    
    # Detecção de BOM UTF-16 (comum em servidores Windows/Java antigos)
    # b'\xff\xfe' = LE, b'\xfe\xff' = BE
    if xml_bytes.startswith(b'\xff\xfe') or xml_bytes.startswith(b'\xfe\xff'):
        try:
            # Decodifica para string Python (Unicode) que o ElementTree aceita bem
            xml_input = xml_bytes.decode("utf-16")
        except Exception:
            # Fallback: se falhar o decode, tenta passar os bytes originais
            xml_input = xml_bytes
    else:
        xml_input = xml_bytes

    # Validação pós-decode
    if isinstance(xml_input, str):
        if not xml_input.strip():
            raise RuntimeError("Servidor retornou XML vazio (apenas BOM UTF-16 detectado).")
    
    try:
        root = ET.fromstring(xml_input)
    except ET.ParseError as e:
        # Tenta mostrar o conteúdo exato que o parser recebeu
        if isinstance(xml_input, str):
            sample = repr(xml_input[:200])
            tipo = "String Decodificada"
        else:
            sample = repr(xml_bytes[:200])
            tipo = "Bytes Originais"
            
        raise RuntimeError(f"XML inválido ou corrompido: {e}. [{tipo}]: {sample}")

    ns = {"w": root.tag.split("}")[0].strip("{")}

    study_el = root.find(".//w:Study", ns)
    if study_el is None:
        # Se não achou Study, pode ser XML de erro do servidor
        # Tenta pegar tudo texto do root
        texto = "".join(root.itertext()).strip()
        if texto:
             raise RuntimeError(f"Study não encontrado. Mensagem do servidor: {texto}")
        raise RuntimeError("Study não encontrado no XML.")

    study_uid = study_el.attrib.get("StudyInstanceUID", "")
    if not study_uid:
        raise RuntimeError("StudyInstanceUID ausente no XML.")

    series_list = []
    total_instances = 0

    for s in study_el.findall("w:Series", ns):
        series_uid = s.attrib.get("SeriesInstanceUID", "")
        if not series_uid:
            continue

        sop_list = []
        for inst in s.findall("w:Instance", ns):
            sop = inst.attrib.get("SOPInstanceUID", "")
            if sop:
                sop_list.append(sop)

        total_instances += len(sop_list)

        series_list.append({
            "series_uid": series_uid,
            "instances": sop_list,
        })

    if total_instances == 0:
        raise RuntimeError("Nenhuma imagem encontrada no XML.")

    return {
        "an": an,
        "study_uid": study_uid,
        "series": series_list,
        "total_instances": total_instances,
    }


# ============================================================
# INTERFACE PRINCIPAL
# ============================================================

def obter_metadata(an: str, servidor: str) -> dict:
    # download (retorna bytes)
    xml_bytes = _baixar_xml(an, servidor)

    # parse direto em memória
    meta = _parse_xml(xml_bytes, an)

    return meta


# ============================================================
# CLI
# ============================================================

import argparse

# ... imports ...


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="WADO-Query Client")
    parser.add_argument("servidor", help="Nome do servidor (HBR, HAC, etc)")
    parser.add_argument("an", help="Accession Number")
    parser.add_argument("--json", action="store_true", help="Saída JSON limpa (sem logs)")

    args = parser.parse_args()

    servidor = args.servidor.upper()
    an = args.an.strip()

    # Modo silencioso: suprimir logs do logger se solicitado JSON puro
    if args.json:
        # Monkeypatch simples para silenciar o logger neste processo
        import logger
        logger._out = lambda *args, **kwargs: None

    try:
        meta = obter_metadata(an, servidor)

        if args.json:
            # Apenas JSON no stdout
            print(json.dumps(meta, indent=2, ensure_ascii=False))
        else:
            # Modo Humano (Logs já foram exibidos pelo obter_metadata)
            # Exibir resumo adicional
            qtd_series = len(meta["series"])
            qtd_imgs = meta["total_instances"]
            
            # Usando log_info para manter padronização visual
            from logger import log_info
            log_info(f"Séries: {qtd_series}")
            log_info(f"Imagens: {qtd_imgs}")

    except Exception as e:
        # Se estiver em json mode, imprimir erro no stderr
        from logger import log_erro
        
        if args.json:
             # Se foi silenciado, imprimir erro no stderr para não quebrar pipe JSON
             sys.stderr.write(f"ERRO: {str(e)}\n")
             sys.exit(1)
        else:
            log_erro(str(e))
            sys.exit(1)


if __name__ == "__main__":
    main()
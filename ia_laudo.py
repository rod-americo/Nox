#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ia_laudo.py — Disparo único do pipeline da IA com base em query e keyword.
-------------------------------------------------------------------------
1. Faz busca via fetcher.py usando um query.json.
2. Filtra exame por conter uma substring `keyword` em `nm_exame` ignorando acentos/maiúsculas.
3. Faz o download do DICOM, converte e manda pra IA.
4. Gera laudo como PENDENTE no cockpit.

Uso:
  python ia_laudo.py queries/query_file.json torax
  python ia_laudo.py queries/plantao-rx.json torax --one
  python ia_laudo.py queries/plantao-rx.json torax --api-url http://meu-endpoint/
  python ia_laudo.py queries/plantao-rx.json torax --title "RX DE TÓRAX"
"""

import sys
import argparse
import json
import subprocess
import unicodedata
from pathlib import Path
import pydicom
import requests

import config
from logger import log_info, log_erro, log_ok, log_aviso
import fetcher
import downloader
import img_conversor


def remover_acentos(texto: str) -> str:
    """Retorna o texto em minúsculas e sem acentuação."""
    if not texto: return ""
    norm = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def buscar_exames(query_file: str) -> dict:
    """
    Usa o fetcher para buscar os dados.
    Caso a sessão tenha expirado (retorna 0 ou erro),
    chama o prepare.py para refazer login e tenta novamente.
    """
    log_info(f"Fazendo fetch do arquivo {query_file}...")
    
    # Garantimos que os metadados sejam gravados em disco
    config.SAVE_METADATA = True

    dados = {}
    try:
        dados = fetcher.fetch_varios_arquivos([query_file])
    except Exception as e:
        log_erro(f"Erro no fetch inicial: {e}")

    total_hbr = len(dados.get("HBR", []))
    total_hac = len(dados.get("HAC", []))
    
    if total_hbr == 0 and total_hac == 0:
        log_aviso("Sessão possivelmente expirada (0 exames retornados). Invocando prepare.py...")
        try:
            subprocess.run([sys.executable, "prepare.py", "--login-only"], check=True)
            log_info("Login refeito com sucesso. Tentando fetch novamente...")
            dados = fetcher.fetch_varios_arquivos([query_file])
        except subprocess.CalledProcessError as e:
            log_erro(f"Falha ao rodar prepare.py. Exit code: {e.returncode}")
        except Exception as e:
            log_erro(f"Erro no prepare/segundo fetch: {e}")

    return dados


def enviar_para_ia_e_laudar(an: str, srv: str, destino_base: Path):
    """
    Executa conversão -> POST -> grava_laudo.
    """
    api_url = getattr(config, "PIPELINE_API_URL", "")
    if not api_url:
        log_erro("A variável config.PIPELINE_API_URL não está configurada.")
        return False
        
    token = getattr(config, "PIPELINE_API_TOKEN", "")
    headers = {}
    if token:
           headers["Authorization"] = f"Bearer {token}"

    # 1. Escolhe o último DCM por ORDEM (lexicográfico)
    dicoms = sorted(destino_base.glob("*.dcm"))
    if not dicoms:
        log_erro(f"[{an}] Arquivos .dcm não encontrados após o download em {destino_base}.")
        return False
        
    dcm_target = dicoms[-1]
    
    # 2. Extrai Idade
    idade_text = ""
    try:
        ds = pydicom.dcmread(str(dcm_target), stop_before_pixels=True)
        raw_age = str(getattr(ds, "PatientAge", "") or "").strip()
        if raw_age and len(raw_age) >= 4 and raw_age[:3].isdigit():
            age_num = int(raw_age[:3])
            age_unit = raw_age[3].upper()
            mapping = {"Y": "year old", "M": "month old", "W": "week old", "D": "day old"}
            if age_unit in mapping:
                idade_text = f"{age_num} {mapping[age_unit]}"
    except Exception as e:
        log_aviso(f"[{an}] Não foi possível ler PatientAge do DICOM: {e}")
        
    # 3. Converte
    log_info(f"[{an}] Otimizando imagem: {dcm_target.name}")
    try:
        jpeg_bytes, _ = img_conversor.otimizar_imagem_para_api(str(dcm_target), limite_mb=4.0)
    except Exception as e:
        log_erro(f"[{an}] Erro na conversão para JPEG: {e}")
        return False

    # 4. Envia payload
    form_data = {
        "identificador": str(an),
        "original_filename": dcm_target.name,
    }
    if idade_text:
        form_data["age"] = idade_text
        
    files = {"file": (f"{an}.jpg", jpeg_bytes, "image/jpeg")}
    
    try:
        log_info(f"[{an}] Enviando POST para a API da IA ({len(jpeg_bytes)/1024:.1f} KB)...")
        resp = requests.post(api_url, data=form_data, files=files, headers=headers, timeout=60)
        
        # Salva o response JSON pra gente usar na criação do RTF
        response_trace_file = destino_base / "pipeline_response.json"
        
        try: body = resp.json()
        except: body = resp.text
        
        trace = {
            "request": {
                "url": api_url,
                "an": an,
                "fields": form_data,
            },
            "response": {"status_code": resp.status_code, "body": body}
        }
        response_trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        resp.raise_for_status()
        log_ok(f"[{an}] Respostas salvas com SUCESSO. StatusCode: {resp.status_code}")
    except Exception as e:
        log_erro(f"[{an}] Erro no envio para o PIPELINE HTTP: {e}")
        return False
        
    # 5. Gera Laudo Cockpit
    cockpit_meta_file = config.COCKPIT_METADATA_DIR / f"{an}.json"
    if not cockpit_meta_file.exists():
        log_erro(f"[{an}] Metadata {cockpit_meta_file.name} ausente, não foi possível laudar.")
        return False
        
    try:
        cockpit = json.loads(cockpit_meta_file.read_text(encoding="utf-8"))
    except Exception as e:
        log_erro(f"[{an}] JSON de metadata inválido: {e}")
        return False
        
    id_laudo = cockpit.get("id_exame_pedido")
    if not id_laudo:
        log_erro(f"[{an}] id_exame_pedido ausente no json!")
        return False
        
    medico_id = getattr(config, "PIPELINE_DEFAULT_MEDICO_ID", "165111")
    title = getattr(config, "PIPELINE_REPORT_TITLE", "RADIOGRAFIA DE TÓRAX NO LEITO")
    payload_path = destino_base / "laudo_payload.json"
    
    # 5.1 Montar RTF
    montar_cmd = [
        sys.executable,
        str(config.BASE_DIR / "montar_laudo_rtf.py"),
        "--id-laudo", str(id_laudo),
        "--medico-id", str(medico_id),
        "--title", title,
        "--pipeline-response", str(response_trace_file),
        "--payload-path", str(payload_path),
        "--pendente"
    ]
    
    try:
        montar_proc = subprocess.run(montar_cmd, capture_output=True, text=True)
        if montar_proc.returncode != 0:
            log_erro(f"[{an}] montar_laudo_rtf: {(montar_proc.stderr or montar_proc.stdout).strip()}")
            return False
    except Exception as e:
        log_erro(f"[{an}] erro ao chamar montar_laudo_rtf.py: {e}")
        return False
        
    # 5.2 Gravar API
    gravar_cmd = [
         sys.executable,
         str(config.BASE_DIR / "gravar_laudo.py"),
         str(id_laudo),
         "--payload-file", str(payload_path)
    ]
    try:
        gravar_proc = subprocess.run(gravar_cmd, capture_output=True, text=True)
        if gravar_proc.returncode != 0:
            err_output = (gravar_proc.stderr or gravar_proc.stdout).strip()
            # Tenta pegar apenas a linha do erro final para não poluir com traceback
            linhas_erro = [l for l in err_output.splitlines() if "RuntimeError:" in l or "Exception:" in l]
            msg_limpa = linhas_erro[-1] if linhas_erro else err_output.splitlines()[-1] if err_output else "Erro desconhecido"
            
            log_erro(f"[{an}] gravar_laudo falhou: {msg_limpa.strip()}")
            return False
            
        log_ok(f"[{an}] LAUDO GRAVADO COM SUCESSO (PENDENTE) no ID {id_laudo}!")
        return True
    except Exception as e:
         log_erro(f"[{an}] erro ao chamar gravar_laudo.py: {e}")
         return False


def processar_exame(an: str, srv: str) -> bool:
    """Modo isolado (Baixa, Processa, Manda IA)"""
    log_info(f"Iniciando execução PIPELINE do AN: {an}")
    
    config.STORAGE_MODE = "pipeline"
    pasta = config.OUTPUT_DICOM_DIR / an
    
    ok_download = downloader.baixar_an(srv, an, mostrar_progresso=False)
    if not ok_download:
         log_erro(f"Falha ao baixar {an} do servidor {srv}.")
         return False
         
    # Copia o metadata original pro destino_base
    meta_path = config.COCKPIT_METADATA_DIR / f"{an}.json"
    if meta_path.exists():
         body = meta_path.read_text(encoding="utf-8")
         (pasta / "metadata_cockpit.json").write_text(body, encoding="utf-8")
    
    return enviar_para_ia_e_laudar(an, srv, pasta)
    

def main():
    parser = argparse.ArgumentParser(description="Disparo de AI Pipeline Standalone.")
    parser.add_argument("query_file", help="Path para o arquivo .json da query.")
    parser.add_argument("keyword", help="Palavra-chave do exame (ex: torax).")
    parser.add_argument("--one", action="store_true", help="Processa apenas 1 registro com sucesso e encerra.")
    parser.add_argument("--api-url", help="Override da URL do endpoint da API da IA.")
    parser.add_argument("--title", help="Override do título do laudo (padrão: RADIOGRAFIA DE TÓRAX NO LEITO).")
    args = parser.parse_args()

    if args.api_url:
        config.PIPELINE_API_URL = args.api_url
    if args.title:
        config.PIPELINE_REPORT_TITLE = args.title

    dados = buscar_exames(args.query_file)
    todos_an_srv = []
    
    for srv in ["HBR", "HAC"]:
        ans = dados.get(srv, [])
        for an in ans:
             todos_an_srv.append((an, srv))

    log_info(f"Fetch encontrou um total de {len(todos_an_srv)} ANs na fila.")
    
    query_keyword = remover_acentos(args.keyword)
    processados_com_sucesso = 0
    
    historico_file = config.BASE_DIR / ".ia_laudo_historico.json"
    historico = []
    if historico_file.exists():
        try:
            historico = json.loads(historico_file.read_text(encoding="utf-8"))
        except: pass
        
    for an, srv in todos_an_srv:
        if an in historico:
            log_info(f"[{an}] Pulando porque já consta no histórico de execuções (.ia_laudo_historico.json)")
            continue
            
        meta_file = config.COCKPIT_METADATA_DIR / f"{an}.json"
        
        if not meta_file.exists():
            log_aviso(f"[{an}] Metadado json não armazenado pelo fetcher. Pulando verificação...")
            continue
            
        try:
             meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except: continue
        
        nm_exame = meta.get("nm_exame", "")
        # A regra é: conter a keyword. Pode ser IDÊNTICO ou string inteira!
        if query_keyword not in remover_acentos(nm_exame):
            continue
            
        log_info(f"Encontrado MATCH em {an} (Servidor: {srv}) -> {nm_exame}")
        sucesso = processar_exame(an, srv)
        
        if sucesso:
             # Só gravamos no histórico interno caso o envio para a API tenha
             # dado certo. Erros (como timeout) farão com que ele tente de novo
             # na próxima rodada do cron/script.
             historico.append(an)
             historico_file.write_text(json.dumps(historico, indent=2), encoding="utf-8")
             
             processados_com_sucesso += 1
             if args.one:
                  log_ok("Execução parando porque flag --one foi ativada e 1 sucesso foi alcançado.")
                  break
                  
    log_info(f"Total executados com sucesso: {processados_com_sucesso}")

if __name__ == "__main__":
    main()

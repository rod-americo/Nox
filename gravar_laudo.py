#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Script auxiliar para gravar laudos no Cockpit Web."""

import argparse
import json
import os
import sys
from pathlib import Path
import subprocess

import requests

import config
from logger import log_info, log_ok, log_erro, log_debug

requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)

PERMIT_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/permitirlaudar"
PERMIT_REVISAR_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/permitirlaudarrevisar"
LAUDAR_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/laudar"
REVISAR_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/laudarrevisar"

RTF_TEMPLATE = r"{\\rtf1\\ansi\\deff0 {body}}"

def text_to_rtf(text: str) -> str:
    escaped = (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\par ")
    )
    return RTF_TEMPLATE.format(body=escaped)


def load_session() -> dict:
    if not config.SESSION_FILE.exists():
        raise FileNotFoundError("sessão não encontrada; rode integration/cockpitweb/prepare.py antes")
    return json.loads(config.SESSION_FILE.read_text(encoding="utf-8"))


def prepare_client(session_payload: dict) -> requests.Session:
    client = requests.Session()
    headers = session_payload.get("headers", {})
    headers.setdefault("Content-Type", "application/json")
    client.headers.update(headers)

    cookies = session_payload.get("cookies", [])
    for cookie in cookies:
        client.cookies.set(cookie.get("name"), cookie.get("value"))

    return client


def refresh_session() -> dict:
    cmd = [sys.executable, str(Path(__file__).resolve().parent / "prepare.py"), "--login-only"]
    log_info("Sessão ausente/expirada; executando prepare.py para renovar login")
    subprocess.run(cmd, check=True)
    return load_session()


def _normalize_payload(payload: dict, data: argparse.Namespace) -> dict:
    """
    Garante campos mínimos esperados pelo endpoint sem sobrescrever valores já presentes.
    """
    normalized = dict(payload)

    payload_id = str(normalized.get("idLaudo", "")).strip()
    arg_id = str(data.id_laudo).strip()
    if payload_id and payload_id != arg_id:
        raise RuntimeError(
            f"idLaudo do payload ({payload_id}) não confere com o argumento ({data.id_laudo})"
        )
    if not payload_id:
        normalized["idLaudo"] = data.id_laudo

    if "idMedicoExecutante" not in normalized:
        if not data.medico_id:
            raise RuntimeError("Payload sem idMedicoExecutante e --medico-id não informado")
        normalized["idMedicoExecutante"] = data.medico_id

    normalized.setdefault("idMedicoRevisor", normalized.get("idMedicoExecutante"))
    normalized.setdefault("idJustificativaRevisao", 0)
    normalized.setdefault("justificativaRevisao", "")
    normalized.setdefault("terceiraOpiniao", False)
    normalized.setdefault("pendente", False)
    normalized.setdefault("provisorio", False)
    normalized.setdefault("urgente", False)
    normalized.setdefault("textoDaUrgencia", None)
    normalized.setdefault("nomeContatoUrgencia", None)
    normalized.setdefault("dataHoraUrgencia", None)
    normalized.setdefault("tags", [])

    return normalized


def call_endpoint(client: requests.Session, url: str, payload: dict) -> dict:
    log_debug(f"POST {url} {payload}")
    resp = client.post(url, json=payload, timeout=30, verify=False)
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        log_erro(f"Erro {resp.status_code} {resp.text.strip()}")
        raise
    try:
        return resp.json()
    except ValueError:
        raise RuntimeError("Resposta não pôde ser decodificada como JSON")


def ensure_payload(data: argparse.Namespace) -> dict:
    if data.payload_file or data.payload_stdin:
        if data.payload_file:
            raw = Path(data.payload_file).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Payload JSON inválido: {exc}") from exc
        return _normalize_payload(payload, data)

    texto = data.texto
    if data.texto_file:
        texto = Path(data.texto_file).read_text(encoding="utf-8")
    texto = (texto or data.texto or "Laudo gerado automaticamente").strip()

    texto_rtf = data.rtf
    if data.rtf_file:
        texto_rtf = Path(data.rtf_file).read_text(encoding="utf-8")
    if not texto_rtf:
        texto_rtf = text_to_rtf(texto)

    tags = list(data.tag) if data.tag else []

    # Campos obrigatórios observados no sniffer que estavam faltando
    id_medico_revisor = data.medico_id # Default: mesmo que executante
    
    payload = {
        "idLaudo": data.id_laudo,
        "idMedicoExecutante": data.medico_id,
        "idMedicoRevisor": id_medico_revisor,
        "textoLaudoRTF": texto_rtf,
        "textoLaudoTxt": texto,
        "pendente": data.pendente,
        "provisorio": data.provisorio,
        "urgente": data.urgente,
        "idJustificativaRevisao": 0,
        "justificativaRevisao": "",
        "textoDaUrgencia": data.texto_urgencia, # None se vazio, ok
        "nomeContatoUrgencia": data.nome_contato_urgencia,
        "dataHoraUrgencia": data.data_hora_urgencia,
        "terceiraOpiniao": False,
        "tags": tags,
    }
    return _normalize_payload(payload, data)

def run(args: argparse.Namespace):
    log_info("Carregando sessão")
    try:
        session_payload = load_session()
    except FileNotFoundError:
        session_payload = refresh_session()
    client = prepare_client(session_payload)
    
    # Define URLs based on mode
    target_permit_url = PERMIT_REVISAR_URL if args.revisar else PERMIT_URL
    target_action_url = REVISAR_URL if args.revisar else LAUDAR_URL

    log_info(f"Verificando permissão ({'REVISAR' if args.revisar else 'LAUDAR'}) para {args.id_laudo}")
    permit_payload = {"idLaudo": args.id_laudo}
    try:
        permit_resp = call_endpoint(client, target_permit_url, permit_payload)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            session_payload = refresh_session()
            client = prepare_client(session_payload)
            permit_resp = call_endpoint(client, target_permit_url, permit_payload)
        else:
            raise

    if not permit_resp.get("podeExecutar"):
        motivo = permit_resp.get("motivoBloqueio", "sem motivo")
        msg = f"Não é possível exec: {motivo}"
        motivo_lc = str(motivo).lower()
        if "já foi revisado" in motivo_lc or "ja foi revisado" in motivo_lc:
            msg += " | Dica: este laudo já foi revisado; use o fluxo de revisão (--revisar) se fizer sentido."
        elif "bloqueado" in motivo_lc:
            msg += " | Dica: o laudo está bloqueado por outro usuário; aguarde o desbloqueio ou solicite liberação."
        raise RuntimeError(msg)

    log_info("Permissão concedida")
    payload = ensure_payload(args)
    if target_action_url == REVISAR_URL and "idMedicoRevisor" not in payload:
        payload["idMedicoRevisor"] = payload.get("idMedicoExecutante")

    if args.dry_run:
        log_info(f"Dry run (Target: {target_action_url}): payload preparado mas não enviado")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    log_info(f"Enviando laudo para {target_action_url}")
    try:
        result = call_endpoint(client, target_action_url, payload)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            session_payload = refresh_session()
            client = prepare_client(session_payload)
            result = call_endpoint(client, target_action_url, payload)
        else:
            raise
    log_ok(f"Resposta: {result}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enviar laudo Cockpit Web")
    parser.add_argument("id_laudo", help="ID do laudo a ser gravado")
    
    # New flag
    parser.add_argument("--revisar", action="store_true", help="Usa endpoint de revisão (laudarrevisar) em vez de laudar comum")
    
    parser.add_argument("--medico-id", type=int, default=os.environ.get("MEDICO_EXECUTANTE_ID"),
                        help="ID do médico executante (env MEDICO_EXECUTANTE_ID)")


    parser.add_argument("--payload-file", help="JSON completo do payload (substitui texto/rtf/flags)")
    parser.add_argument("--payload-stdin", action="store_true", help="Lê JSON do payload via stdin")
    parser.add_argument("--texto", help="Texto simples do laudo")
    parser.add_argument("--texto-file", help="Arquivo com o texto do laudo")
    parser.add_argument("--rtf", help="Laudo em RTF completo")
    parser.add_argument("--rtf-file", help="Arquivo com RTF completo")
    parser.add_argument("--pendente", action="store_true", help="Marca o laudo como pendente")
    parser.add_argument("--provisorio", action="store_true", help="Marca o laudo como provisório")
    parser.add_argument("--urgente", action="store_true", help="Marca o laudo como urgente")
    parser.add_argument("--texto-urgencia", help="Texto adicional de urgência")
    parser.add_argument("--nome-contato-urgencia", help="Contato de urgência")
    parser.add_argument("--data-hora-urgencia", help="Data/hora no padrão ISO")
    parser.add_argument("--tag", action="append", default=[], help="Tag que será adicionada ao laudo")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o payload sem enviar")
    args = parser.parse_args()

    if (args.payload_file or args.payload_stdin) and (args.texto or args.texto_file or args.rtf or args.rtf_file):
        parser.error("Use payload JSON OU texto/rtf, não ambos.")

    if not args.medico_id and not (args.payload_file or args.payload_stdin):
        parser.error("Informe --medico-id ou defina MEDICO_EXECUTANTE_ID")

    return args


def main():
    args = parse_args()
    run(args)


if __name__ == "__main__":
    main()

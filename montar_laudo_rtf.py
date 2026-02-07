#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Gera um payload RTF padrão para Cockpit Web com título maiúsculo em Candara 13 centralizado,
uma linha em branco com Candara 13 e corpo justificado em Candara 11, e monta o JSON pronto para
passar ao gravar_laudo (com texto plano + RTF + flags).
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "laudo_rtf.tpl"

PAR_TEMPLATE = (
    "\\pard\\plain\\ql{{\\fcs1\\af4\\ltrch\\fcs0\\hich\\af4\\dbch\\af0\\loch\\f4\\fs22\\cf0 {text}}}"
    "\\fcs1\\af4\\ltrch\\fcs0\\hich\\af4\\dbch\\af0\\loch\\f4\\fs22\\par"
)


def escape_rtf(text: str) -> str:
    if not text:
        return ""

    result: list[str] = []
    for ch in text:
        if ch in {"\\", "{", "}"}:
            result.append(f"\\{ch}")
            continue

        code = ord(ch)
        if 32 <= code <= 126:
            result.append(ch)
        else:
            try:
                byte = ch.encode("cp1252")[0]
            except UnicodeEncodeError:
                byte = 63
            result.append(f"\\u{code}\\'{byte:02x}")
    return "".join(result)


def build_paragraphs(lines: Iterable[str]) -> str:
    paragraphs = [
        PAR_TEMPLATE.format(text=escape_rtf(line))
        for line in lines
    ]
    if not paragraphs:
        paragraphs = [PAR_TEMPLATE.format(text="")]
    return "".join(paragraphs)


def read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    body = args.body or ""
    # Permite passar quebras como \\n no CLI e converte para newline real.
    body = body.replace("\\r\\n", "\n").replace("\\n", "\n")
    return body


def build_payload(args: argparse.Namespace, body_text: str, rtf: str) -> dict:
    tags = list(args.tag or [])
    title_txt = args.title.upper()
    texto_plano = f"{title_txt}\n\n{body_text}" if body_text else title_txt
    if args.data_hora_urgencia:
        data_hora = args.data_hora_urgencia
    else:
        data_hora = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    # Mantem a ordem igual ao exemplo do servidor.
    payload = {
        "idLaudo": args.id_laudo,
        "idMedicoExecutante": args.medico_id,
        "textoLaudoRTF": rtf,
        "textoLaudoTxt": texto_plano,
        "pendente": args.pendente,
        "provisorio": args.provisorio,
        "urgente": args.urgente,
        "textoDaUrgencia": args.texto_urgencia,
        "nomeContatoUrgencia": args.nome_contato_urgencia,
        "dataHoraUrgencia": data_hora,
        "tags": tags,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Gera RTF padrão para laudo Cockpit Web")
    parser.add_argument("--id-laudo", required=True, help="ID enviado como `idLaudo` no payload")
    parser.add_argument("--medico-id", type=int, default=os.environ.get("MEDICO_EXECUTANTE_ID"),
                        help="ID do médico executante (env MEDICO_EXECUTANTE_ID)")
    parser.add_argument("--title", required=True, help="Título do laudo (será convertido para maiúsculas)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--body-file", help="Arquivo texto com o corpo do laudo (linhas => parágrafos)")
    group.add_argument("--body", help="Corpo do laudo em uma string (use \\n para quebra de linha)")
    parser.add_argument("--output", help="Salva o RTF gerado neste arquivo")
    parser.add_argument("--print-rtf", action="store_true", help="Imprime o RTF gerado no stdout (sem --output)")
    parser.add_argument("--plain-out", help="Escreve o texto puro do corpo neste arquivo")
    parser.add_argument("--payload-path", help="Salva o JSON completo do payload neste arquivo")
    parser.add_argument("--pendente", action="store_true", help="Marca `pendente` no payload")
    parser.add_argument("--provisorio", action="store_true", help="Marca `provisorio` no payload")
    parser.add_argument("--urgente", action="store_true", help="Marca `urgente` no payload")
    parser.add_argument("--texto-urgencia", help="Texto adicional da urgência")
    parser.add_argument("--nome-contato-urgencia", help="Contato de urgência")
    parser.add_argument("--data-hora-urgencia", help="Data/hora da urgência (ISO)")
    parser.add_argument("--tag", action="append", help="Tags adicionais para o laudo")
    parser.add_argument("--json", action="store_true", help="Imprime JSON com texto e payload")
    args = parser.parse_args()

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    if "__TITLE__" not in template or "__BODY__" not in template:
        raise SystemExit("o template precisa conter __TITLE__ e __BODY__")

    if not args.medico_id:
        parser.error("Informe --medico-id ou defina MEDICO_EXECUTANTE_ID")

    body_text = read_body(args)
    lines = body_text.splitlines()
    if body_text.endswith(("\n", "\r")):
        lines.append("")
    if not lines:
        lines = [""]

    paragraphs = build_paragraphs(lines)
    title_rtf = escape_rtf(args.title.upper())
    rtf = template.replace("__TITLE__", title_rtf).replace("__BODY__", paragraphs)

    payload = build_payload(args, body_text, rtf)

    if args.output:
        Path(args.output).write_text(rtf, encoding="utf-8")
    elif args.print_rtf:
        print(rtf)

    if args.plain_out:
        Path(args.plain_out).write_text(body_text, encoding="utf-8")

    if args.payload_path:
        Path(args.payload_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json or not args.payload_path:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

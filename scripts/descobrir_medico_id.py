#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
descobrir_medico_id.py
----------------------
Ajuda a descobrir IDs de médico a partir de parte do nome.

Estratégia:
1) Consulta worklist do Cockpit filtrando por nm_medico_executante/revisor.
2) Extrai pares (id, nome) dos registros retornados.
3) Faz fallback em metadados locais (data/cockpit/*.json).
4) Mostra ranking por frequência.
"""

import argparse
import base64
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import fetcher
from logger import log_info, log_ok, log_aviso, log_erro


DEFAULT_STATUS = ["LAUDADO", "REVISADO", "ASSINADO", "ENTREGUE"]


def norm(s: str) -> str:
    s = str(s or "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def parse_status(raw: str) -> list[str]:
    items = [x.strip().upper() for x in (raw or "").split(",") if x.strip()]
    return items or list(DEFAULT_STATUS)


def jwt_payload_from_session() -> dict:
    if not config.SESSION_FILE.exists():
        return {}
    try:
        sess = json.loads(config.SESSION_FILE.read_text(encoding="utf-8"))
        auth = (sess.get("headers", {}) or {}).get("Authorization", "")
        if not auth.startswith("Bearer "):
            return {}
        token = auth.split(" ", 1)[1]
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload_b64 = parts[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        raw = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def build_payload(nome_fragmento: str, role: str, status: list[str], rx_only: bool, days: int) -> dict:
    fim = datetime.now()
    ini = fim - timedelta(days=days)
    dt_ini = ini.strftime("%Y-%m-%dT%H:%M:%S-03:00")
    dt_fim = fim.strftime("%Y-%m-%dT%H:%M:%S-03:00")

    payload = fetcher.gerar_payload(dt_ini, dt_fim, None)
    payload["tp_status"] = status
    payload["imagem"] = ["S"]
    payload["assinado"] = []

    payload["nm_medico_executante"] = ""
    payload["nm_medico_revisor"] = ""
    if role in ("executante", "ambos"):
        payload["nm_medico_executante"] = nome_fragmento
    if role in ("revisor", "ambos"):
        payload["nm_medico_revisor"] = nome_fragmento

    if rx_only:
        payload["id_procedimento"] = ["96"]

    return payload


def collect_from_worklist(payload: dict, max_pages: int, page_size: int) -> list[dict]:
    s = fetcher.carregar_session()
    cookies = {c["name"]: c["value"] for c in s.get("cookies", [])}
    headers = {
        "User-Agent": s["headers"]["User-Agent"],
        "Authorization": s["headers"]["Authorization"],
        "Content-Type": "application/json",
    }

    rows = []
    for page in range(1, max_pages + 1):
        data = fetcher.fetch_pagina(page, page_size, cookies, headers, payload)
        if not data:
            break
        rows.extend(data)
        if len(data) < page_size:
            break
    return rows


def collect_pairs(rows: list[dict], fragmento_norm: str, role: str) -> tuple[Counter, dict]:
    counts = Counter()
    details = defaultdict(lambda: {"nome": "", "roles": Counter(), "exemplos_an": []})

    def add(id_field: str, name_field: str, tag: str, row: dict):
        mid = str(row.get(id_field, "") or "").strip()
        mname = str(row.get(name_field, "") or "").strip()
        if not mid or not mname:
            return
        if fragmento_norm and fragmento_norm not in norm(mname):
            return
        key = mid
        counts[key] += 1
        details[key]["nome"] = mname
        details[key]["roles"][tag] += 1
        an = str(row.get("cd_item_pedido_his", "") or "").strip()
        if an and len(details[key]["exemplos_an"]) < 5 and an not in details[key]["exemplos_an"]:
            details[key]["exemplos_an"].append(an)

    for r in rows:
        if role in ("executante", "ambos"):
            add("id_medico_executante", "nm_medico_executante", "executante", r)
        if role in ("revisor", "ambos"):
            add("id_medico_revisor", "nm_medico_revisor", "revisor", r)

    return counts, details


def collect_from_local_metadata(fragmento_norm: str, role: str) -> tuple[Counter, dict]:
    counts = Counter()
    details = defaultdict(lambda: {"nome": "", "roles": Counter(), "exemplos_an": []})
    files = sorted(config.COCKPIT_METADATA_DIR.glob("*.json"))

    for f in files:
        try:
            row = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue

        an = str(row.get("cd_item_pedido_his", "") or f.stem)

        def add(id_field: str, name_field: str, tag: str):
            mid = str(row.get(id_field, "") or "").strip()
            mname = str(row.get(name_field, "") or "").strip()
            if not mid or not mname:
                return
            if fragmento_norm and fragmento_norm not in norm(mname):
                return
            counts[mid] += 1
            details[mid]["nome"] = mname
            details[mid]["roles"][tag] += 1
            if an and len(details[mid]["exemplos_an"]) < 5 and an not in details[mid]["exemplos_an"]:
                details[mid]["exemplos_an"].append(an)

        if role in ("executante", "ambos"):
            add("id_medico_executante", "nm_medico_executante", "executante")
        if role in ("revisor", "ambos"):
            add("id_medico_revisor", "nm_medico_revisor", "revisor")

    return counts, details


def merge_results(primary: tuple[Counter, dict], secondary: tuple[Counter, dict]) -> tuple[Counter, dict]:
    c1, d1 = primary
    c2, d2 = secondary
    merged_counts = Counter()
    merged_details = defaultdict(lambda: {"nome": "", "roles": Counter(), "exemplos_an": []})

    for mid in set(list(c1.keys()) + list(c2.keys())):
        merged_counts[mid] = c1[mid] + c2[mid]
        for src in (d1, d2):
            if mid in src:
                if not merged_details[mid]["nome"]:
                    merged_details[mid]["nome"] = src[mid]["nome"]
                merged_details[mid]["roles"].update(src[mid]["roles"])
                for an in src[mid]["exemplos_an"]:
                    if an not in merged_details[mid]["exemplos_an"] and len(merged_details[mid]["exemplos_an"]) < 5:
                        merged_details[mid]["exemplos_an"].append(an)
    return merged_counts, merged_details


def main() -> int:
    parser = argparse.ArgumentParser(description="Descobre ID de médico por parte do nome.")
    parser.add_argument("--nome", required=True, help="Parte do nome do médico (ex: rodrigo, cunha).")
    parser.add_argument("--role", choices=["executante", "revisor", "ambos"], default="ambos")
    parser.add_argument("--status", default="LAUDADO,REVISADO,ASSINADO,ENTREGUE")
    parser.add_argument("--days", type=int, default=120, help="Janela retroativa de busca em dias.")
    parser.add_argument("--max-pages", type=int, default=8, help="Máximo de páginas da API worklist.")
    parser.add_argument("--page-size", type=int, default=25)
    parser.add_argument("--rx-only", action="store_true", help="Limita busca para id_procedimento=96 (RX).")
    parser.add_argument("--json", action="store_true", help="Saída em JSON.")
    parser.add_argument("--save-json", help="Arquivo para salvar o resultado completo.")
    args = parser.parse_args()

    fragmento_norm = norm(args.nome)
    status = parse_status(args.status)
    payload = build_payload(args.nome, args.role, status, args.rx_only, args.days)

    worklist_rows = []
    try:
        log_info("Consultando worklist do Cockpit...")
        worklist_rows = collect_from_worklist(payload, args.max_pages, args.page_size)
        log_info(f"Registros varridos no worklist: {len(worklist_rows)}")
    except Exception as exc:
        log_aviso(f"Falha na busca online: {exc}")

    from_api = collect_pairs(worklist_rows, fragmento_norm, args.role)
    from_meta = collect_from_local_metadata(fragmento_norm, args.role)
    merged = merge_results(from_api, from_meta)

    jwt_payload = jwt_payload_from_session()
    lista_medicos_token = []
    try:
        raw = jwt_payload.get("ListaMedicos", "[]")
        lista_medicos_token = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        lista_medicos_token = []

    counts, details = merged
    ranking = sorted(counts.items(), key=lambda x: (-x[1], x[0]))

    result = {
        "query": {
            "nome": args.nome,
            "role": args.role,
            "status": status,
            "days": args.days,
            "rx_only": args.rx_only,
        },
        "sources": {
            "worklist_rows": len(worklist_rows),
            "metadata_files": len(list(config.COCKPIT_METADATA_DIR.glob("*.json"))),
            "token_lista_medicos_count": len(lista_medicos_token),
        },
        "matches": [
            {
                "id_medico": mid,
                "nome": details[mid]["nome"],
                "ocorrencias": qtd,
                "roles": dict(details[mid]["roles"]),
                "exemplos_an": details[mid]["exemplos_an"],
            }
            for mid, qtd in ranking
        ],
        "token_lista_medicos_ids": [
            {
                "Idmu": m.get("Idmu"),
                "Idm": m.get("Idm"),
                "Idu": m.get("Idu"),
                "Idun": m.get("Idun"),
            }
            for m in lista_medicos_token
            if isinstance(m, dict)
        ],
    }

    if args.save_json:
        out = Path(args.save_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log_ok(f"Resultado salvo em: {out}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not ranking:
        log_aviso("Nenhuma correspondência encontrada para esse nome.")
        if lista_medicos_token:
            log_info("IDs presentes no token (sem nome):")
            for m in result["token_lista_medicos_ids"]:
                print(f"- Idmu={m['Idmu']} | Idm={m['Idm']} | Idu={m['Idu']} | Idun={m['Idun']}")
        return 1

    log_ok("Candidatos encontrados:")
    for mid, qtd in ranking:
        d = details[mid]
        roles = ", ".join(f"{k}:{v}" for k, v in d["roles"].items())
        exemplos = ", ".join(d["exemplos_an"])
        print(f"- id_medico={mid} | nome={d['nome']} | ocorrencias={qtd} | roles={roles}")
        if exemplos:
            print(f"  ANs exemplo: {exemplos}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

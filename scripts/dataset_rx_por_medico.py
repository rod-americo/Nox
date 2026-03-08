#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
dataset_rx_por_medico.py
------------------------
Extrai dataset de RX laudados por médico:
1) Busca no Cockpit via query JSON (com filtro de médico).
2) Baixa DICOM de cada AN retornado.
3) Busca texto do laudo por idLaudo.
4) Salva artefatos em uma pasta de dataset.
"""

import argparse
import tempfile
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Garante import dos módulos compartilhados quando executado via scripts/
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import fetcher
import downloader
import gravar_laudo
import img_conversor
from logger import log_info, log_ok, log_erro, log_aviso


LAUDO_TEXTO_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/obtertextoslaudo"


def _parse_status_list(raw: str) -> list[str]:
    items = [x.strip().upper() for x in (raw or "").split(",") if x.strip()]
    return items or ["LAUDADO", "REVISADO", "ASSINADO", "ENTREGUE"]


def _load_query_template(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo de query não encontrado: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _build_query(template: dict, medico_id: str, role: str, status: list[str], assinado: str) -> dict:
    q = dict(template)

    q["id_medico_executante"] = ""
    q["id_medico_revisor"] = ""

    if role in ("executante", "ambos"):
        q["id_medico_executante"] = str(medico_id)
    if role in ("revisor", "ambos"):
        q["id_medico_revisor"] = str(medico_id)

    q["tp_status"] = status
    q["assinado"] = [assinado]
    q.setdefault("imagem", ["S"])
    q.setdefault("id_procedimento", ["96"])
    return q


def _load_session_payload() -> dict:
    try:
        return gravar_laudo.load_session()
    except FileNotFoundError:
        return gravar_laudo.refresh_session()


def _session_headers_cookies(session_payload: dict) -> tuple[dict, dict]:
    cookies = {c.get("name"): c.get("value") for c in session_payload.get("cookies", [])}
    headers = dict(session_payload.get("headers", {}))
    headers.setdefault("Content-Type", "application/json")
    return headers, cookies


def _obter_texto_laudo(id_laudo: str, session_payload: dict) -> dict | None:
    headers, cookies = _session_headers_cookies(session_payload)
    payload = {"idLaudo": str(id_laudo)}

    try:
        resp = requests.post(
            LAUDO_TEXTO_URL,
            headers=headers,
            cookies=cookies,
            json=payload,
            timeout=30,
            verify=False,
        )
        if resp.status_code == 401:
            session_payload = gravar_laudo.refresh_session()
            headers, cookies = _session_headers_cookies(session_payload)
            resp = requests.post(
                LAUDO_TEXTO_URL,
                headers=headers,
                cookies=cookies,
                json=payload,
                timeout=30,
                verify=False,
            )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log_aviso(f"[{id_laudo}] Falha ao obter textos do laudo: {exc}")
        return None


def _find_meta_path(an_full: str, an_puro: str) -> Path | None:
    p1 = config.COCKPIT_METADATA_DIR / f"{an_full}.json"
    if p1.exists():
        return p1
    p2 = config.COCKPIT_METADATA_DIR / f"{an_puro}.json"
    if p2.exists():
        return p2
    return None


def _copy_dicom_if_needed(an_puro: str, target_exam_dir: Path, copy_dicom: bool) -> Path | None:
    src = config.OUTPUT_DICOM_DIR / an_puro
    if not src.exists():
        return None
    if not copy_dicom:
        return src

    dst = target_exam_dir / "dicom"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _find_output_dicom_dir(an_puro: str) -> Path | None:
    src = config.OUTPUT_DICOM_DIR / an_puro
    if src.exists():
        return src
    return None


def _converter_dicom_para_jpgs_flat(an_ref: str, dicom_dir: Path, images_dir: Path, limite_mb: float = 4.0) -> list[str]:
    if not dicom_dir.exists():
        return []

    dicoms = sorted(dicom_dir.glob("*.dcm"))
    if not dicoms:
        return []

    images_dir.mkdir(parents=True, exist_ok=True)
    arquivos_jpg = []

    for idx, dcm in enumerate(dicoms, 1):
        out_name = f"{an_ref}_{idx:03d}.jpg"
        out_path = images_dir / out_name
        try:
            jpg_bytes, _ = img_conversor.otimizar_imagem_para_api(str(dcm), limite_mb=limite_mb)
            out_path.write_bytes(jpg_bytes)
            arquivos_jpg.append(out_name)
        except Exception as exc:
            log_aviso(f"[{dcm.name}] Falha na conversão para JPG: {exc}")

    return arquivos_jpg


def _build_finetune_record(jpg_names: list[str], texto_plano: str) -> dict:
    lista = ", ".join(jpg_names)
    prompt = (
        "Com base nas imagens do estudo, gere o laudo radiológico.\n"
        f"Arquivos do estudo: {lista}"
    )
    return {
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": texto_plano.strip()},
        ]
    }


def _query_fingerprint(payload: dict) -> str:
    norm = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _fetch_queue_path(dataset_root: Path) -> Path:
    return dataset_root / "fetch_queue.json"


def _load_fetch_queue(dataset_root: Path) -> dict | None:
    p = _fetch_queue_path(dataset_root)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


def _save_fetch_queue(dataset_root: Path, query_sha: str, todos: list[tuple[str, str]]):
    payload = {
        "created_at": _now_iso(),
        "query_sha256": query_sha,
        "total": len(todos),
        "items": [{"an": an, "srv": srv} for an, srv in todos],
    }
    _fetch_queue_path(dataset_root).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _now_iso() -> str:
    return datetime.now().isoformat()


def _resolve_log_file(dataset_root: Path, raw_log_file: str | None) -> Path | None:
    if raw_log_file is None:
        return None
    if str(raw_log_file).strip() == "":
        return None
    p = Path(raw_log_file)
    return p if p.is_absolute() else (dataset_root / p)


def _log(level: str, message: str, log_file: Path | None = None):
    if level == "info":
        log_info(message)
    elif level == "ok":
        log_ok(message)
    elif level == "erro":
        log_erro(message)
    else:
        log_aviso(message)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level.upper()}] {message}\n"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line)


def _checkpoint_path(dataset_root: Path) -> Path:
    return dataset_root / "checkpoint.json"


def _load_checkpoint(dataset_root: Path) -> dict:
    p = _checkpoint_path(dataset_root)
    if not p.exists():
        return {
            "run_started_at": _now_iso(),
            "last_update_at": _now_iso(),
            "total_detectado": 0,
            "processados": 0,
            "concluidos": 0,
            "falhas": 0,
            "itens": {},
        }
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {
            "run_started_at": _now_iso(),
            "last_update_at": _now_iso(),
            "total_detectado": 0,
            "processados": 0,
            "concluidos": 0,
            "falhas": 0,
            "itens": {},
        }


def _save_checkpoint(dataset_root: Path, checkpoint: dict):
    checkpoint["last_update_at"] = _now_iso()
    _checkpoint_path(dataset_root).write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _jsonl_index_path(dataset_root: Path) -> Path:
    return dataset_root / "jsonl_index.json"


def _extract_an_from_record(record: dict) -> str | None:
    messages = record.get("messages", [])
    if not isinstance(messages, list):
        return None
    user_content = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            user_content = str(m.get("content") or "")
            break
    if not user_content:
        return None

    m = re.search(r"Arquivos do estudo:\s*(.+)$", user_content, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    part = m.group(1).strip()
    first = part.split(",")[0].strip()
    m2 = re.match(r"(.+)_\d{3}\.jpg$", first, flags=re.IGNORECASE)
    if not m2:
        return None
    return m2.group(1)


def _load_or_build_jsonl_index(dataset_root: Path, finetune_path: Path) -> dict:
    idx_path = _jsonl_index_path(dataset_root)
    if idx_path.exists():
        try:
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    index = {}
    if finetune_path.exists():
        with finetune_path.open("r", encoding="utf-8") as f:
            line_no = 0
            for line in f:
                line_no += 1
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                an = _extract_an_from_record(rec)
                if an:
                    index[an] = {"line": line_no}
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def _append_jsonl_record(finetune_path: Path, jsonl_index: dict, an_full: str, record: dict):
    if an_full in jsonl_index:
        return
    finetune_path.parent.mkdir(parents=True, exist_ok=True)
    with finetune_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    with finetune_path.open("r", encoding="utf-8") as rf:
        line_no = sum(1 for _ in rf)
    jsonl_index[an_full] = {"line": line_no}


def _save_jsonl_index(dataset_root: Path, jsonl_index: dict):
    _jsonl_index_path(dataset_root).write_text(
        json.dumps(jsonl_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _is_done(an_full: str, dataset_root: Path, images_dir: Path, jsonl_index: dict) -> bool:
    if not (dataset_root / an_full).exists():
        # Modo lean: sem pasta por AN; valida por imagem + jsonl
        imgs_ok = any(images_dir.glob(f"{an_full}_*.jpg"))
        jsonl_ok = an_full in jsonl_index
        return bool(imgs_ok and jsonl_ok)

    exam_dir = dataset_root / an_full
    meta_ok = (exam_dir / "metadata_cockpit.json").exists()
    laudo_file = exam_dir / "laudo.json"
    if not (meta_ok and laudo_file.exists()):
        return False
    try:
        laudo = json.loads(laudo_file.read_text(encoding="utf-8"))
        texto_ok = bool(str(laudo.get("texto_plano") or "").strip())
    except Exception:
        texto_ok = False
    imgs_ok = any(images_dir.glob(f"{an_full}_*.jpg"))
    jsonl_ok = an_full in jsonl_index
    return bool(texto_ok and imgs_ok and jsonl_ok)


def run(args: argparse.Namespace) -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dataset_root = Path(args.output_dir) if args.output_dir else (config.DATA_DIR / f"dataset_rx_medico_{args.medico_id}_{timestamp}")
    dataset_root.mkdir(parents=True, exist_ok=True)
    log_file = _resolve_log_file(dataset_root, args.log_file)

    if args.storage_mode:
        config.STORAGE_MODE = args.storage_mode
        _log("info", f"Override de storage_mode aplicado: {config.STORAGE_MODE}", log_file)

    template = _load_query_template(Path(args.query))
    status = _parse_status_list(args.status)
    payload = _build_query(template, args.medico_id, args.role, status, args.assinado.upper())

    query_path = dataset_root / "query_efetiva.json"
    query_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("info", f"Query efetiva salva em: {query_path}", log_file)

    query_sha = _query_fingerprint(payload)
    todos: list[tuple[str, str]] = []
    queue_cache = _load_fetch_queue(dataset_root)
    cache_ok = (
        args.resume
        and (not args.refresh_fetch_queue)
        and queue_cache is not None
        and queue_cache.get("query_sha256") == query_sha
        and isinstance(queue_cache.get("items"), list)
    )

    if cache_ok:
        todos = [
            (str(x.get("an") or "").strip(), str(x.get("srv") or "").strip())
            for x in queue_cache.get("items", [])
            if str(x.get("an") or "").strip() and str(x.get("srv") or "").strip()
        ]
        if args.limit:
            todos = todos[: args.limit]
        _log("info", f"Fila reaproveitada de cache: {len(todos)} registros", log_file)
    else:
        config.SAVE_METADATA = True
        resultado = fetcher.fetch_from_file(str(query_path), limite=args.limit)
        todos = [(an, "HBR") for an in resultado.get("HBR", [])] + [(an, "HAC") for an in resultado.get("HAC", [])]
        _save_fetch_queue(dataset_root, query_sha, todos)
        _log("info", f"Registros retornados via fetch: {len(todos)} (cache atualizado)", log_file)

    if not todos:
        _log("aviso", "Nenhum exame encontrado para os filtros informados.", log_file)
        return 0

    session_payload = _load_session_payload()
    manifest = {
        "created_at": datetime.now().isoformat(),
        "medico_id": str(args.medico_id),
        "role": args.role,
        "status": status,
        "assinado": args.assinado.upper(),
        "source_query": str(Path(args.query).resolve()),
        "effective_query": str(query_path.resolve()),
        "finetune_jsonl": str((dataset_root / args.finetune_jsonl).resolve()),
        "images_dir": str((dataset_root / args.images_dir).resolve()),
        "items": [],
    }
    manifest_path = dataset_root / "manifest.json"
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            existing_items = existing_manifest.get("items", [])
            if isinstance(existing_items, list):
                manifest["items"] = existing_items
        except Exception:
            pass
    finetune_path = dataset_root / args.finetune_jsonl
    images_dir = dataset_root / args.images_dir
    images_dir.mkdir(parents=True, exist_ok=True)

    if not args.resume:
        if finetune_path.exists():
            finetune_path.unlink()
        idxp = _jsonl_index_path(dataset_root)
        if idxp.exists():
            idxp.unlink()
        cp = _checkpoint_path(dataset_root)
        if cp.exists():
            cp.unlink()

    checkpoint = _load_checkpoint(dataset_root)
    checkpoint["total_detectado"] = len(todos)
    checkpoint.setdefault("itens", {})
    _save_checkpoint(dataset_root, checkpoint)

    jsonl_index = _load_or_build_jsonl_index(dataset_root, finetune_path)

    an_srv = {an: srv for an, srv in todos}
    for an in an_srv.keys():
        checkpoint["itens"].setdefault(
            an,
            {"status": "pending", "erro": "", "attempts": 0, "updated_at": _now_iso()},
        )
    _save_checkpoint(dataset_root, checkpoint)

    done_pre = 0
    failed_pre = 0
    pending_pre = 0
    for an in an_srv.keys():
        st = checkpoint["itens"].get(an, {}).get("status", "pending")
        if args.resume and _is_done(an, dataset_root, images_dir, jsonl_index):
            checkpoint["itens"][an]["status"] = "done"
            done_pre += 1
        elif st == "failed":
            failed_pre += 1
        else:
            pending_pre += 1
    _save_checkpoint(dataset_root, checkpoint)
    _log(
        "info",
        f"RESUME: encontrados done={done_pre}, pending={pending_pre}, failed={failed_pre}",
        log_file,
    )

    process_queue = []
    for an, srv in todos:
        st = checkpoint["itens"].get(an, {}).get("status", "pending")
        if args.resume and st == "done":
            _log("info", f"SKIP [{an}] já concluído", log_file)
            continue
        if st == "failed" and not args.retry_failed:
            _log("info", f"SKIP [{an}] falhado anteriormente (--retry-failed desativado)", log_file)
            continue
        process_queue.append((an, srv))

    amostras_finetune_novas = 0
    for i, (an_full, srv) in enumerate(process_queue, 1):
        if i > 1 and args.delay_seconds > 0:
            _log("info", f"Throttle: aguardando {args.delay_seconds:.2f}s antes do próximo AN...", log_file)
            time.sleep(args.delay_seconds)

        an_puro = an_full.split("_")[0]
        _log("info", f"[{i}/{len(process_queue)}] Processando AN={an_full} ({srv})", log_file)
        item_state = checkpoint["itens"].setdefault(an_full, {})
        item_state["attempts"] = int(item_state.get("attempts", 0)) + 1
        item_state["status"] = "pending"
        item_state["updated_at"] = _now_iso()
        _save_checkpoint(dataset_root, checkpoint)

        ok_download = downloader.baixar_an(srv, an_puro, mostrar_progresso=False)
        if not ok_download:
            item_state["status"] = "failed"
            item_state["erro"] = "falha_download_dicom"
            item_state["updated_at"] = _now_iso()
            _save_checkpoint(dataset_root, checkpoint)
            _log("erro", f"FAIL [{an_full}] Falha no download DICOM.", log_file)
            continue

        meta_path = _find_meta_path(an_full, an_puro)
        if not meta_path:
            item_state["status"] = "failed"
            item_state["erro"] = "metadata_cockpit_ausente"
            item_state["updated_at"] = _now_iso()
            _save_checkpoint(dataset_root, checkpoint)
            _log("erro", f"FAIL [{an_full}] Metadata Cockpit não encontrada em {config.COCKPIT_METADATA_DIR}.", log_file)
            continue

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            item_state["status"] = "failed"
            item_state["erro"] = f"metadata_invalida: {exc}"
            item_state["updated_at"] = _now_iso()
            _save_checkpoint(dataset_root, checkpoint)
            _log("erro", f"FAIL [{an_full}] Metadata inválida: {exc}", log_file)
            continue

        id_laudo = meta.get("id_exame_pedido")
        if not id_laudo:
            item_state["status"] = "failed"
            item_state["erro"] = "id_exame_pedido_ausente"
            item_state["updated_at"] = _now_iso()
            _save_checkpoint(dataset_root, checkpoint)
            _log("erro", f"FAIL [{an_full}] id_exame_pedido ausente no metadata.", log_file)
            continue

        laudo_raw = _obter_texto_laudo(str(id_laudo), session_payload)

        exam_dir = dataset_root / an_full
        if not args.lean:
            exam_dir.mkdir(parents=True, exist_ok=True)
            (exam_dir / "metadata_cockpit.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        texto_plano = ""
        if laudo_raw is not None:
            laudo_slim = {
                "id_laudo": laudo_raw.get("idLaudo"),
                "laudo_pendente": laudo_raw.get("laudoPendente"),
                "laudo_provisorio": laudo_raw.get("laudoProvisorio"),
                "texto_plano": laudo_raw.get("plainText"),
                "texto_rtf": laudo_raw.get("richText"),
            }
            texto_plano = str(laudo_slim.get("texto_plano") or "").strip()
            if not args.lean:
                (exam_dir / "laudo.json").write_text(
                    json.dumps(laudo_slim, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        else:
            if not args.lean:
                (exam_dir / "laudo.json").write_text(
                    json.dumps({"id_laudo": id_laudo, "erro": "nao_foi_possivel_obter_texto"}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        dicom_path: Path | None = None
        tmp_dir: Path | None = None
        if args.lean:
            dicom_src = _find_output_dicom_dir(an_puro)
            if dicom_src:
                dicom_path = dicom_src
        else:
            dicom_path = _copy_dicom_if_needed(an_puro, exam_dir, copy_dicom=args.copy_dicom)

        if args.lean and dicom_path is None:
            # fallback de segurança: tenta copiar para temporário apenas para converter
            dicom_src = _find_output_dicom_dir(an_puro)
            if dicom_src:
                tmp_dir = Path(tempfile.mkdtemp(prefix=f"nox_{an_full}_"))
                tmp_dicom = tmp_dir / "dicom"
                shutil.copytree(dicom_src, tmp_dicom)
                dicom_path = tmp_dicom

        if dicom_path is None and config.STORAGE_MODE == "transient":
            _log("aviso",
                f"[{an_full}] DICOM não disponível localmente no modo transient. "
                "Use --storage-mode pipeline/persistent para dataset com JPG.",
                log_file,
            )

        jpg_names = []
        if dicom_path:
            jpg_names = _converter_dicom_para_jpgs_flat(
                an_full,
                Path(dicom_path),
                images_dir,
                limite_mb=args.jpg_limit_mb
            )
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

        if texto_plano and jpg_names:
            record = _build_finetune_record(jpg_names, texto_plano)
            pre_exists = an_full in jsonl_index
            _append_jsonl_record(finetune_path, jsonl_index, an_full, record)
            _save_jsonl_index(dataset_root, jsonl_index)
            if not pre_exists:
                amostras_finetune_novas += 1

        item = {
            "an": an_full,
            "an_puro": an_puro,
            "servidor": srv,
            "id_exame_pedido": id_laudo,
            "nm_exame": meta.get("nm_exame"),
            "nm_unidade": meta.get("nm_unidade"),
            "id_medico_executante": meta.get("id_medico_executante"),
            "nm_medico_executante": meta.get("nm_medico_executante"),
            "id_medico_revisor": meta.get("id_medico_revisor"),
            "nm_medico_revisor": meta.get("nm_medico_revisor"),
            "dataset_dir": str(exam_dir.resolve()) if not args.lean else None,
            "dicom_path": str(dicom_path.resolve()) if dicom_path and Path(dicom_path).exists() else None,
            "jpg_files": jpg_names,
            "jpg_files_dir": str(images_dir.resolve()),
            "texto_plano_len": len(texto_plano),
            "finetune_included": bool(texto_plano and jpg_names),
            "lean_mode": bool(args.lean),
        }
        existing_idx = next((ix for ix, it in enumerate(manifest["items"]) if it.get("an") == an_full), None)
        if existing_idx is None:
            manifest["items"].append(item)
        else:
            manifest["items"][existing_idx] = item

        if _is_done(an_full, dataset_root, images_dir, jsonl_index):
            item_state["status"] = "done"
            item_state["erro"] = ""
            _log("ok", f"DONE [{an_full}] jpg={len(jpg_names)} texto_len={len(texto_plano)}", log_file)
        else:
            item_state["status"] = "failed"
            item_state["erro"] = "incompleto_sem_texto_ou_jpg_ou_jsonl"
            _log("erro", f"FAIL [{an_full}] Incompleto (texto/jpg/jsonl).", log_file)
        item_state["updated_at"] = _now_iso()
        _save_checkpoint(dataset_root, checkpoint)

        done_now = sum(1 for an in an_srv if checkpoint["itens"].get(an, {}).get("status") == "done")
        failed_now = sum(1 for an in an_srv if checkpoint["itens"].get(an, {}).get("status") == "failed")
        pending_now = len(an_srv) - done_now - failed_now
        _log("info", f"PROGRESS done={done_now} pending={pending_now} failed={failed_now}", log_file)

    done_total = sum(1 for an in an_srv if checkpoint["itens"].get(an, {}).get("status") == "done")
    failed_total = sum(1 for an in an_srv if checkpoint["itens"].get(an, {}).get("status") == "failed")
    pending_total = len(an_srv) - done_total - failed_total
    checkpoint["processados"] = done_total + failed_total
    checkpoint["concluidos"] = done_total
    checkpoint["falhas"] = failed_total
    _save_checkpoint(dataset_root, checkpoint)

    manifest["summary"] = {
        "total": len(todos),
        "concluidos": done_total,
        "falhas": failed_total,
        "pendentes": pending_total,
        "amostras_finetune_total": len(jsonl_index),
        "amostras_finetune_novas": amostras_finetune_novas,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    _log("ok", f"Dataset finalizado em: {dataset_root}", log_file)
    _log(
        "info",
        f"Resumo: concluidos={done_total}, falhas={failed_total}, pendentes={pending_total}, "
        f"total={len(todos)}, finetune_total={len(jsonl_index)}, finetune_novas={amostras_finetune_novas}",
        log_file,
    )
    return 0 if done_total > 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrai dataset de RX laudados por médico (imagem + laudo).")
    parser.add_argument(
        "--query",
        default=str(config.BASE_DIR / "queries" / "rx-laudado-medico.template.json"),
        help="Arquivo JSON base da query.",
    )
    parser.add_argument("--medico-id", required=True, help="ID do médico no Cockpit.")
    parser.add_argument(
        "--role",
        choices=["executante", "revisor", "ambos"],
        default="executante",
        help="Campo de filtro médico a ser usado na query.",
    )
    parser.add_argument(
        "--status",
        default="LAUDADO,REVISADO,ASSINADO,ENTREGUE",
        help="Lista CSV de status para tp_status.",
    )
    parser.add_argument(
        "--assinado",
        choices=["S", "N"],
        default="S",
        help="Filtro do campo assinado.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limite máximo de exames.")
    parser.add_argument("--output-dir", help="Diretório de saída do dataset.")
    parser.add_argument(
        "--copy-dicom",
        action="store_true",
        help="Copia os DICOMs para dentro do dataset (padrão: não copia, apenas referencia).",
    )
    parser.add_argument(
        "--storage-mode",
        choices=["transient", "persistent", "pipeline"],
        help="Override temporário do STORAGE_MODE durante esta execução.",
    )
    parser.add_argument(
        "--jpg-limit-mb",
        type=float,
        default=4.0,
        help="Limite de tamanho por JPG (MB), usando a mesma otimização do ia_laudo.py.",
    )
    parser.add_argument(
        "--finetune-jsonl",
        default="finetune.jsonl",
        help="Nome do arquivo JSONL de saída para fine-tuning dentro do dataset.",
    )
    parser.add_argument(
        "--images-dir",
        default="images",
        help="Subpasta única para todas as imagens JPG referenciadas no JSONL.",
    )
    parser.add_argument(
        "--log-file",
        default="run.log",
        help="Arquivo de log da execução (relativo ao output-dir ou absoluto). Use vazio para desativar.",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
        help="Retoma execução no mesmo output-dir e pula ANs já concluídos (padrão).",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Desativa retomada; reprocessa tudo no output-dir.",
    )
    parser.add_argument(
        "--retry-failed",
        dest="retry_failed",
        action="store_true",
        default=True,
        help="Em modo resume, tenta novamente ANs que falharam (padrão).",
    )
    parser.add_argument(
        "--no-retry-failed",
        dest="retry_failed",
        action="store_false",
        help="Em modo resume, mantém ANs falhados como skip.",
    )
    parser.add_argument(
        "--refresh-fetch-queue",
        action="store_true",
        help="Força novo fetch e sobrescreve o cache de fila (fetch_queue.json).",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Atraso (segundos) entre o processamento de ANs para reduzir taxa de chamadas.",
    )
    parser.add_argument(
        "--lean",
        action="store_true",
        help="Não persiste pasta por AN (dicom/laudo/metadata); mantém apenas images, jsonl e arquivos de controle.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))

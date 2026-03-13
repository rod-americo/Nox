#!/usr/bin/env python3
"""Etapa 2: processa fila salva (jsonl) e materializa dataset no formato de saida esperado."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import queue
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from zipfile import ZIP_DEFLATED, ZipFile

import requests
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import downloader
import gravar_laudo
import logger as nox_logger

LAUDO_TEXTO_URL = f"{config.URL_BASE}/ris/laudo/api/v1/laudo/obtertextoslaudo"


def load_queue(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, data: dict[str, Any]):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _manifest_path(dataset_dir: Path) -> Path:
    return dataset_dir / "manifest.jsonl"


def _load_manifest_seen(dataset_dir: Path) -> set[str]:
    manifest_path = _manifest_path(dataset_dir)
    seen: set[str] = set()
    if not manifest_path.exists():
        return seen

    with manifest_path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except Exception:
                continue
            an = str(row.get("an") or "").strip()
            if an:
                seen.add(an)
    return seen


def _append_manifest_once(dataset_dir: Path, manifest_seen: set[str], entry: dict[str, Any]):
    an = str(entry.get("an") or "").strip()
    if not an or an in manifest_seen:
        return

    manifest_path = _manifest_path(dataset_dir)
    with manifest_path.open("a", encoding="utf-8") as mf:
        mf.write(json.dumps(entry, ensure_ascii=False) + "\n")
    manifest_seen.add(an)


def _session_headers_cookies(session_payload: dict) -> tuple[dict[str, str], dict[str, str]]:
    cookies = {c.get("name"): c.get("value") for c in session_payload.get("cookies", [])}
    headers = dict(session_payload.get("headers", {}))
    headers.setdefault("Content-Type", "application/json")
    return headers, cookies


def _load_session_payload() -> dict:
    try:
        return gravar_laudo.load_session()
    except FileNotFoundError:
        return gravar_laudo.refresh_session()


def _obter_texto_laudo(id_laudo: str, session_payload: dict) -> tuple[dict[str, Any] | None, dict]:
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
        return resp.json(), session_payload
    except Exception:
        return None, session_payload


def _an_base(an: str) -> str:
    return str(an).split("_")[0]


def _find_meta_path(an: str) -> Path | None:
    an_base = _an_base(an)
    p1 = config.COCKPIT_METADATA_DIR / f"{an}.json"
    if p1.exists():
        return p1
    p2 = config.COCKPIT_METADATA_DIR / f"{an_base}.json"
    if p2.exists():
        return p2
    return None


def _resolve_id_laudo(queue_row: dict[str, Any], an: str) -> str | None:
    id_laudo = str(queue_row.get("id_exame_pedido") or "").strip()
    if id_laudo:
        return id_laudo

    meta_path = _find_meta_path(an)
    if not meta_path:
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    id_laudo = str(meta.get("id_exame_pedido") or "").strip()
    return id_laudo or None


def _save_reports(base_dir: Path, an: str, report_obj: dict[str, Any]):
    report_dir = base_dir / "reports" / an
    report_dir.mkdir(parents=True, exist_ok=True)

    plain = str(report_obj.get("plainText") or "")
    plain = plain.replace("\r\n", "\n").replace("\r", "\n")
    normalized_lines: list[str] = []
    for line in plain.split("\n"):
        # Remove caracteres invisiveis comuns que geram "linhas em branco"
        cleaned = re.sub(r"[\u00a0\u200b\u200c\u200d\ufeff]", "", line)
        if cleaned.strip():
            normalized_lines.append(line.strip())
    plain = "\n".join(normalized_lines).strip()
    rich = str(report_obj.get("richText") or "")
    html = str(report_obj.get("LaudoHTML") or report_obj.get("laudo") or "")

    (report_dir / "payload.json").write_text(
        json.dumps(report_obj, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (report_dir / "laudo.txt").write_text(plain, encoding="utf-8")
    (report_dir / "laudo.rtf").write_text(rich, encoding="utf-8")
    if html:
        (report_dir / "laudo.html").write_text(html, encoding="utf-8")


def _has_reports(base_dir: Path, an: str) -> bool:
    report_dir = base_dir / "reports" / an
    return (
        (report_dir / "payload.json").exists()
        and (report_dir / "laudo.txt").exists()
        and (report_dir / "laudo.rtf").exists()
    )


def _has_final_zip(base_dir: Path, an: str) -> bool:
    return (base_dir / "images" / f"{an}.zip").exists()


def _copy_download_to_stage(stage_root: Path, an: str) -> Path:
    an_base = _an_base(an)
    src = config.OUTPUT_DICOM_DIR / an_base
    if not src.exists():
        raise RuntimeError(f"dicom_nao_encontrado_em_{src}")

    stage = stage_root / an
    if stage.exists():
        shutil.rmtree(stage)
    stage.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, stage)
    return stage


def _create_exam_zip_tmp(
    an: str,
    image_dir: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> Path:
    if not image_dir.exists():
        raise RuntimeError("images_missing")

    fd, tmp_name = tempfile.mkstemp(prefix=f"{an}_", suffix=".zip.tmp")
    os.close(fd)
    zip_tmp = Path(tmp_name)

    all_files = [p for p in sorted(image_dir.rglob("*")) if p.is_file()]
    if not all_files:
        zip_tmp.unlink(missing_ok=True)
        raise RuntimeError("zip_empty")

    try:
        done = 0
        total = len(all_files)
        if progress_callback:
            progress_callback(done, total)
        with ZipFile(zip_tmp, "w", compression=ZIP_DEFLATED) as zf:
            for p in all_files:
                arcname = str(Path("images") / an / p.relative_to(image_dir))
                zf.write(p, arcname=arcname)
                done += 1
                if progress_callback:
                    progress_callback(done, total)
        return zip_tmp
    except Exception:
        zip_tmp.unlink(missing_ok=True)
        raise


def _move_atomic_or_copy(
    src: Path,
    dst: Path,
    progress_callback: Callable[[int, int, str], None] | None = None,
):
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dst)
        if progress_callback:
            progress_callback(1, 1, "atomic")
        return
    except OSError:
        pass

    tmp_dst = dst.with_suffix(f"{dst.suffix}.tmpmove")
    total = int(src.stat().st_size) if src.exists() else 0
    done = 0
    if progress_callback:
        progress_callback(done, total, "copy")
    with src.open("rb", buffering=0) as fin, tmp_dst.open("wb", buffering=0) as fout:
        while True:
            chunk = fin.read(1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = fout.write(view)
                if written is None or written <= 0:
                    raise OSError("falha ao escrever arquivo temporario de destino")
                done += written
                view = view[written:]
                if progress_callback:
                    progress_callback(done, total, "copy")
    os.replace(tmp_dst, dst)
    src.unlink(missing_ok=True)
    if progress_callback:
        progress_callback(total, total, "copy")


def _format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(max(n, 0))
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)}{units[idx]}"
    return f"{value:.1f}{units[idx]}"


def _read_download_progress(an_base: str) -> tuple[int, int]:
    p = config.PROGRESS_DIR / f"{an_base}.json"
    if not p.exists():
        return 0, 1
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        done = int(data.get("baixadas") or 0)
        total = int(data.get("total") or 1)
        if total <= 0:
            total = 1
        if done < 0:
            done = 0
        if done > total:
            done = total
        return done, total
    except Exception:
        return 0, 1


def _run_download_with_progress(
    srv: str,
    an_base: str,
    progress: Progress | None,
    task_id: int | None,
    on_tick: Callable[[], None] | None = None,
) -> bool:
    result: dict[str, Any] = {"ok": False, "error": None}

    def _worker():
        try:
            result["ok"] = downloader.baixar_an(srv, an_base, mostrar_progresso=False)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True, name=f"download-{an_base}")
    thread.start()

    while thread.is_alive():
        done, total = _read_download_progress(an_base)
        if progress and task_id is not None:
            progress.update(
                task_id,
                total=max(total, 1),
                completed=min(done, total),
                label=f"Download {an_base}",
                count=f"{done}/{total}",
            )
            if on_tick:
                on_tick()
        time.sleep(0.2)

    thread.join()
    done, total = _read_download_progress(an_base)
    if progress and task_id is not None:
        progress.update(
            task_id,
            total=max(total, 1),
            completed=max(total, done),
            label=f"Download {an_base}",
            count=f"{max(total, done)}/{max(total, 1)}",
        )
        if on_tick:
            on_tick()

    if result["error"] is not None:
        raise result["error"]
    return bool(result["ok"])


_PROGRESS_PANEL_HEIGHT = 4


def _log_panel_capacity(term_height: int) -> int:
    # Reserva area fixa para o painel inferior de progresso.
    return max(3, term_height - _PROGRESS_PANEL_HEIGHT - 2)


def _build_dashboard(progress: Progress, recent_logs: deque[str], max_lines: int | None = None) -> Layout:
    visible_logs = list(recent_logs)[-max_lines:] if (max_lines and max_lines > 0) else list(recent_logs)
    logs_text = "\n".join(visible_logs) if visible_logs else "aguardando..."
    # Evita quebra horizontal de linhas longas (ex.: paths), mantendo altura previsivel.
    log_renderable = Text(logs_text, no_wrap=True, overflow="ellipsis")
    layout = Layout()
    layout.split_column(
        Layout(
            Panel(log_renderable, title="Logs", border_style="blue"),
            name="logs",
            ratio=1,
            minimum_size=5,
        ),
        Layout(
            Panel(progress, title="Progresso", border_style="green"),
            name="progress",
            size=_PROGRESS_PANEL_HEIGHT,
        ),
    )
    return layout


def _resolve_workers_config_attr() -> str:
    if hasattr(config, "DOWNLOAD_MAX_THREADS"):
        return "DOWNLOAD_MAX_THREADS"
    if hasattr(config, "DOWNLOAD_WORKERS"):
        return "DOWNLOAD_WORKERS"
    if hasattr(config, "THREADS"):
        return "THREADS"
    return ""


def _resolve_default_tmp_root() -> Path:
    for env_name in ("TMPDIR", "TEMP", "TMP"):
        raw = os.environ.get(env_name)
        if raw and raw.strip():
            return Path(raw).expanduser() / "nox_dataset_pipeline"
    return Path(tempfile.gettempdir()) / "nox_dataset_pipeline"


def _resolve_runtime_progress_dir() -> tuple[Path, bool]:
    primary = Path(getattr(config, "PROGRESS_DIR", PROJECT_ROOT / "progresso")).expanduser()
    if primary.exists():
        return primary, False

    fallback = PROJECT_ROOT / ".progresso.nosync"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback, True


def _mute_nox_logger_for_progress() -> Callable[[], None]:
    """
    Silencia logs do modulo logger durante o painel Rich.
    Suporta duas variantes encontradas no projeto:
    1) logger._out (funil interno)
    2) funcoes publicas (log_info, log_ok, etc.)
    """
    if hasattr(nox_logger, "_out"):
        original_out = nox_logger._out
        nox_logger._out = lambda *args, **kwargs: None

        def _restore():
            nox_logger._out = original_out

        return _restore

    names = [
        "log_info",
        "log_ok",
        "log_erro",
        "log_debug",
        "log_finalizado",
        "log_skip",
        "log_aviso",
        "log",
    ]
    backups: dict[str, Any] = {}
    for name in names:
        if hasattr(nox_logger, name):
            backups[name] = getattr(nox_logger, name)
            setattr(nox_logger, name, lambda *args, **kwargs: None)

    def _restore():
        for name, fn in backups.items():
            setattr(nox_logger, name, fn)

    return _restore


def _zip_worker_loop(
    zip_queue: "queue.Queue[dict[str, Any] | None]",
    progress_ui: Progress | None,
    zip_task_id: int | None,
    ui_tick: Callable[[], None],
    emit: Callable[[str], None],
    state: dict[str, Any],
    state_path: Path,
    state_lock: threading.Lock,
    tmp_dicom_dir: Path,
    counters: dict[str, int],
    counters_lock: threading.Lock,
):
    while True:
        job = zip_queue.get()
        if job is None:
            zip_queue.task_done()
            return

        an = str(job["an"])
        an_base = str(job["an_base"])
        stage_dir = Path(job["stage_dir"])
        zip_final = Path(job["zip_final"])

        try:
            zip_tmp = _create_exam_zip_tmp(
                an,
                stage_dir,
                progress_callback=(
                    (lambda done, total: progress_ui.update(
                        zip_task_id,
                        total=max(total, 1),
                        completed=min(done, max(total, 1)),
                        label=f"ZIP build {an_base}",
                        count=f"{done}/{max(total, 1)}",
                    ) or ui_tick())
                    if (progress_ui and zip_task_id is not None)
                    else None
                ),
            )
            _move_atomic_or_copy(
                zip_tmp,
                zip_final,
                progress_callback=(
                    (lambda done, total, mode: progress_ui.update(
                        zip_task_id,
                        total=max(total, 1),
                        completed=min(done, max(total, 1)),
                        label=f"ZIP move {an_base} ({mode})",
                        count=(
                            f"{_format_bytes(done)}/{_format_bytes(max(total, 1))}"
                            if mode == "copy"
                            else "1/1"
                        ),
                    ) or ui_tick())
                    if (progress_ui and zip_task_id is not None)
                    else None
                ),
            )

            shutil.rmtree(stage_dir, ignore_errors=True)
            shutil.rmtree(tmp_dicom_dir / an_base, ignore_errors=True)

            with state_lock:
                item_state = state.setdefault("items", {}).get(an, {"attempts": 0, "success": False})
                item_state["success"] = True
                item_state["last_result"] = "ok"
                state["items"][an] = item_state
                save_state(state_path, state)

            with counters_lock:
                counters["zip_ok"] += 1

            emit(f"  -> zip ready: {zip_final}")
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(stage_dir, ignore_errors=True)
            shutil.rmtree(tmp_dicom_dir / an_base, ignore_errors=True)

            with state_lock:
                item_state = state.setdefault("items", {}).get(an, {"attempts": 0, "success": False})
                item_state["success"] = False
                item_state["last_result"] = f"zip_error: {exc}"
                state["items"][an] = item_state
                save_state(state_path, state)

            with counters_lock:
                counters["zip_fail"] += 1

            emit(f"  -> zip fail [{an}]: {exc}")
        finally:
            if progress_ui and zip_task_id is not None:
                progress_ui.update(zip_task_id, total=1, completed=1, label="ZIP (idle)", count="1/1")
                ui_tick()
            zip_queue.task_done()


def main() -> int:
    parser = argparse.ArgumentParser(description="Processa fila salva de ANs")
    parser.add_argument("--queue-file", required=True, help="Fila em JSONL")
    parser.add_argument("--dataset-dir", required=True, help="Diretorio raiz do dataset")
    parser.add_argument("--max-items", type=int, default=0, help="Maximo de itens por execucao (0=sem limite)")
    parser.add_argument("--workers", type=int, default=0, help="Override de workers do downloader (0 = padrao do config)")
    parser.add_argument("--start-index", type=int, default=0, help="Indice inicial na fila")
    parser.add_argument(
        "--skip-success",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pular itens ja concluidos no state (padrao: habilitado)",
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exibir barras de progresso Rich (padrao: habilitado)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Nao baixa imagens nem busca laudo")
    parser.add_argument("--sleep", type=float, default=0.0, help="Pausa entre ANs (segundos)")
    parser.add_argument(
        "--no-osirix-copy",
        action="store_true",
        default=True,
        help="Desativa entrega para OsiriX/Horos nesta execucao (padrao: habilitado).",
    )
    parser.add_argument(
        "--force-redownload",
        action="store_true",
        help="Forca reprocessamento de todos os itens (equivale a desativar --skip-success)",
    )
    parser.add_argument(
        "--tmp-root",
        default=None,
        help="Raiz temporaria para download/staging/zip (padrao: usa TMPDIR/TEMP/TMP do sistema)",
    )
    args = parser.parse_args()

    if args.force_redownload:
        args.skip_success = False
    if args.workers < 0:
        raise SystemExit("--workers deve ser >= 0")

    queue_path = Path(args.queue_file).expanduser().resolve()
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "images").mkdir(parents=True, exist_ok=True)
    tmp_root = (
        Path(args.tmp_root).expanduser().resolve()
        if args.tmp_root
        else _resolve_default_tmp_root().resolve()
    )
    tmp_dicom_dir = tmp_root / "dicom"
    tmp_stage_root = tmp_root / "zip_stage"
    tmp_dicom_dir.mkdir(parents=True, exist_ok=True)
    tmp_stage_root.mkdir(parents=True, exist_ok=True)

    print(f"Dataset dir: {dataset_dir}")
    print(f"TMP root: {tmp_root}")

    if args.no_osirix_copy:
        # Para pipeline de dataset, a saida deve ser apenas no destino informado.
        config.OSIRIX_INCOMING = Path("")
        config.VIEWER = "radiant"
        print("Modo dataset: envio para OsiriX/Horos desativado")

    if config.STORAGE_MODE == "transient":
        # Em transient os arquivos podem ser movidos para incoming e nao ficam no OUTPUT_DICOM_DIR.
        config.STORAGE_MODE = "pipeline"
        print("storage_mode transient detectado; usando pipeline para preservar arquivos locais")

    original_output_dir = config.OUTPUT_DICOM_DIR
    workers_attr = _resolve_workers_config_attr()
    current_workers = int(getattr(config, workers_attr)) if workers_attr else 10
    original_download_workers = current_workers
    config.OUTPUT_DICOM_DIR = tmp_dicom_dir
    progress_dir, progress_fallback = _resolve_runtime_progress_dir()
    config.PROGRESS_DIR = progress_dir
    print(f"OUTPUT_DICOM_DIR temporario: {config.OUTPUT_DICOM_DIR}")
    if progress_fallback:
        print(f"PROGRESS_DIR fallback ativo: {config.PROGRESS_DIR}")
    else:
        print(f"PROGRESS_DIR: {config.PROGRESS_DIR}")
    if args.workers > 0:
        if workers_attr:
            setattr(config, workers_attr, args.workers)
            current_workers = args.workers
            print(f"{workers_attr} override: {current_workers}")
        else:
            current_workers = args.workers
            print(f"workers override local: {current_workers} (sem atributo em config)")

    state_path = queue_path.parent / "queue_state.json"
    state = load_state(state_path)
    queue_rows = load_queue(queue_path)
    if args.start_index >= len(queue_rows):
        raise SystemExit(f"start-index ({args.start_index}) fora da fila ({len(queue_rows)} itens)")

    manifest_seen = _load_manifest_seen(dataset_dir)

    processed = 0
    success_count = 0
    fail_count = 0
    downloaded_count = 0
    skipped_existing_count = 0
    state_lock = threading.Lock()
    zip_counters = {"zip_ok": 0, "zip_fail": 0}
    zip_counters_lock = threading.Lock()

    session_payload = None if args.dry_run else _load_session_payload()
    progress_ui: Progress | None = None
    live_ui: Live | None = None
    recent_logs: deque[str] = deque()
    download_task_id: int | None = None
    zip_task_id: int | None = None

    if args.progress:
        progress_ui = Progress(
            SpinnerColumn(),
            TextColumn("[cyan]{task.fields[label]}"),
            BarColumn(bar_width=None),
            TextColumn("[green]{task.fields[count]}"),
            TextColumn("•"),
            TimeElapsedColumn(),
            expand=True,
            transient=False,
            auto_refresh=False,
        )
        download_task_id = progress_ui.add_task(
            "download",
            total=1,
            completed=0,
            label="Download (idle)",
            count="0/1",
        )
        zip_task_id = progress_ui.add_task(
            "zip",
            total=1,
            completed=0,
            label="ZIP (idle)",
            count="0/1",
        )
        initial_term_height = shutil.get_terminal_size(fallback=(120, 40)).lines
        live_ui = Live(
            _build_dashboard(
                progress_ui,
                recent_logs,
                max_lines=_log_panel_capacity(initial_term_height),
            ),
            refresh_per_second=8,
            transient=False,
            screen=True,
        )
        live_ui.start()

    def _ui_tick():
        if live_ui is not None and progress_ui is not None:
            max_lines = _log_panel_capacity(live_ui.console.size.height)
            # Mantem um pequeno historico para resize sem crescer sem limite.
            while len(recent_logs) > max(200, max_lines * 4):
                recent_logs.popleft()
            live_ui.update(
                _build_dashboard(progress_ui, recent_logs, max_lines=max_lines),
                refresh=True,
            )

    def _emit(msg: str):
        if progress_ui is None:
            print(msg)
            return
        recent_logs.append(msg)
        _ui_tick()

    zip_queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
    zip_worker = threading.Thread(
        target=_zip_worker_loop,
        args=(
            zip_queue,
            progress_ui,
            zip_task_id,
            _ui_tick,
            _emit,
            state,
            state_path,
            state_lock,
            tmp_dicom_dir,
            zip_counters,
            zip_counters_lock,
        ),
        daemon=False,
        name="zip-worker",
    )
    zip_worker.start()

    _emit(
        f"Queue={queue_path} | state_entries={len(state.get('items', {}))} "
        f"| start_index={args.start_index} | workers={current_workers}"
    )

    interrupted = False
    restore_logger = lambda: None
    if progress_ui is not None:
        restore_logger = _mute_nox_logger_for_progress()
    try:
        for idx, row in enumerate(queue_rows[args.start_index:], start=args.start_index):
            an = str(row.get("an") or "").strip()
            srv = str(row.get("servidor") or row.get("srv") or "").strip().upper()
            if not an or not srv:
                continue

            item_state = state.setdefault("items", {}).get(an, {"attempts": 0, "success": False})
            if args.skip_success and item_state.get("success") and _has_final_zip(dataset_dir, an):
                continue

            if args.max_items > 0 and processed >= args.max_items:
                break

            processed += 1
            item_state["attempts"] = int(item_state.get("attempts", 0)) + 1

            entry = {
                "an": an,
                "servidor": srv,
                "nm_unidade": row.get("nm_unidade"),
                "nm_exame": row.get("nm_exame"),
                "id_exame_pedido": row.get("id_exame_pedido"),
                "tp_status": row.get("tp_status"),
                "source_page": row.get("source_page"),
            }
            _append_manifest_once(dataset_dir, manifest_seen, entry)

            _emit(f"[{processed}] AN={an} srv={srv} attempt={item_state['attempts']}")

            if _has_final_zip(dataset_dir, an) and _has_reports(dataset_dir, an):
                item_state["success"] = True
                item_state["last_result"] = "already_present_zip_and_reports"
                success_count += 1
                skipped_existing_count += 1
                with state_lock:
                    state["items"][an] = item_state
                    save_state(state_path, state)
                _emit("  -> skip(existing zip+reports)")
                continue

            if args.dry_run:
                item_state["success"] = False
                item_state["last_result"] = "dry_run"
                with state_lock:
                    state["items"][an] = item_state
                    save_state(state_path, state)
                continue

            try:
                an_base = _an_base(an)
                an_tmp_dicom = config.OUTPUT_DICOM_DIR / an_base
                an_tmp_stage = tmp_stage_root / an
                if an_tmp_dicom.exists():
                    shutil.rmtree(an_tmp_dicom, ignore_errors=True)
                if an_tmp_stage.exists():
                    shutil.rmtree(an_tmp_stage, ignore_errors=True)

                if progress_ui and download_task_id is not None:
                    progress_ui.update(download_task_id, total=1, completed=0, label=f"Download {an_base}", count="0/1")
                    progress_ui.update(zip_task_id, total=1, completed=0, label=f"ZIP {an_base}", count="0/1")
                    _ui_tick()
                download_ok = _run_download_with_progress(srv, an_base, progress_ui, download_task_id, on_tick=_ui_tick)
                if not download_ok:
                    raise RuntimeError("download_failed")
                downloaded_count += 1

                id_laudo = _resolve_id_laudo(row, an)
                if not id_laudo:
                    raise RuntimeError("id_exame_pedido_ausente")

                report_obj, session_payload = _obter_texto_laudo(id_laudo, session_payload)
                if not report_obj:
                    raise RuntimeError("report_not_found")

                _save_reports(dataset_dir, an, report_obj)
                stage_dir = _copy_download_to_stage(tmp_stage_root, an)
                zip_final = dataset_dir / "images" / f"{an}.zip"
                zip_queue.put(
                    {
                        "an": an,
                        "an_base": an_base,
                        "stage_dir": str(stage_dir),
                        "zip_final": str(zip_final),
                    }
                )
                item_state["success"] = False
                item_state["last_result"] = "zip_pending"
                _emit("  -> zip queued")
            except KeyboardInterrupt:
                interrupted = True
                item_state["success"] = False
                item_state["last_result"] = "interrupted"
                _emit("  -> interrompido por usuario; encerrando processamento")
                with state_lock:
                    state["items"][an] = item_state
                    save_state(state_path, state)
                break
            except Exception as exc:
                item_state["success"] = False
                item_state["last_result"] = f"error: {exc}"
                fail_count += 1
                _emit(f"  -> fail: {exc}")
                an_base = _an_base(an)
                shutil.rmtree(config.OUTPUT_DICOM_DIR / an_base, ignore_errors=True)
                shutil.rmtree(tmp_stage_root / an, ignore_errors=True)

            with state_lock:
                state["items"][an] = item_state
                save_state(state_path, state)

            if progress_ui and download_task_id is not None and zip_task_id is not None:
                progress_ui.update(download_task_id, total=1, completed=1, label="Download (idle)", count="1/1")
                progress_ui.update(zip_task_id, total=1, completed=1, label="ZIP (idle)", count="1/1")
                _ui_tick()

            if args.sleep > 0:
                time.sleep(args.sleep)
    finally:
        _emit("Aguardando fila de ZIP finalizar...")
        zip_queue.join()
        zip_queue.put(None)
        zip_worker.join(timeout=60)
        with zip_counters_lock:
            success_count += zip_counters["zip_ok"]
            fail_count += zip_counters["zip_fail"]

        restore_logger()
        if workers_attr:
            setattr(config, workers_attr, original_download_workers)
        config.OUTPUT_DICOM_DIR = original_output_dir
        if live_ui:
            live_ui.stop()

    print(
        "Summary: "
        f"processed={processed} ok={success_count} fail={fail_count} "
        f"downloaded={downloaded_count} skipped_existing={skipped_existing_count}"
    )
    if interrupted:
        print("Summary: encerrado por interrupcao de usuario (estado salvo)")

    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

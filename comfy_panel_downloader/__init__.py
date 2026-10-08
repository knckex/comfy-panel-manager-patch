"""
comfy-panel専用の最小カスタムノード。

ComfyUI-Managerの/manager/queue/install_modelは、自分の配布DB(model-list.json)に
完全一致するURLしか受け付けない仕様（実機検証済み）で、任意のカスタムURLは拒否される。
これを迂回するため、ComfyUI自体のWebサーバー（aiohttp）に自前のダウンロード用エンドポイントを
追加する。ComfyUI-Managerは一切経由しない。

エンドポイント:
  POST /comfy_panel/download
    body: {"url": "...", "folder": "checkpoints/custom", "filename": "xxx.safetensors"}
    → models/<folder>/<filename> にバックグラウンドでダウンロードを開始し、即座に返答する。
    進捗は既存の /experiment/models/<folder最初のセグメント> をポーリングすれば追える
    （ComfyUI標準機能。ダウンロード中でもその時点のバイト数を返してくれる）。

  POST /comfy_panel/cancel
    body: {"folder": "...", "filename": "..."}
    → 進行中のダウンロードを中止し、書きかけのファイルを消す。
    ダウンロードは専用スレッドで走っているので外から強制終了はできない。代わりに
    threading.Eventを立て、チャンクを書くループが自分で気づいて抜ける方式にしている。

  GET /comfy_panel/downloads
    → 今動いているダウンロードの一覧。ページを開き直した後でも「何が走っているか」を
    画面側が復元できるようにするためのもの。

再試行とレジューム（2026-10-08に追加）:
  以前は接続が切れるとスレッドがそのまま終わり、書きかけのファイルだけが残って
  「進捗が何時間も動かない」状態になっていた（25GBのチェックポイントが2%で停止、
  4.7GBのLoRAが23%で停止、という形で実機で複数回発生）。
  そこで切断時は、落とせたところまでのバイト数を Range ヘッダで指定して続きから再開する。
  最初からやり直さないので、長い本体ファイルでも落としきれる。
"""

import os
import threading
import time
import urllib.error
import urllib.request

from aiohttp import web
from server import PromptServer
import folder_paths

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

# 1本のダウンロードで何回まで再接続を試みるか。ここを使い切ったら諦めて書きかけを残す
# （画面側が「N秒間進んでいません」で気づける）。
MAX_ATTEMPTS = 8
# 再接続の間隔（秒）。回数に応じて伸ばす（5, 10, 20, 40, 60, 60...）
RETRY_BASE_DELAY = 5
RETRY_MAX_DELAY = 60
CHUNK_SIZE = 8 * 1024 * 1024

# 進行中のダウンロード。 dest_path -> {"cancel": Event, "url": str}
_active = {}
_active_lock = threading.Lock()


def _open_stream(url: str, resume_from: int):
    """URLを開く。resume_from>0ならRangeヘッダで続きから要求する。"""
    headers = {"User-Agent": "comfy-panel/1.0"}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
    req = urllib.request.Request(url, headers=headers)
    response = urllib.request.urlopen(req)
    # 206 Partial Content ならサーバーがレジュームを受け入れた。
    # 200が返ってきた場合はRangeが無視されて先頭から送られてくるので、こちらも先頭から書き直す。
    resumed = response.status == 206 if resume_from > 0 else False
    return response, resumed


def _download(url: str, dest_path: str, cancel: threading.Event):
    # comfy-panel側の進捗表示は/experiment/models/<folder>（本番のファイル名でサイズを見る）を
    # ポーリングする仕組みなので、一時ファイル名(.part等)を使わず本番のファイル名に直接書き込む。
    # 個人用ツールなので、失敗時に不完全なファイルが残るリスクより進捗が見えることを優先する。
    cancelled = False
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        downloaded = 0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if cancel.is_set():
                cancelled = True
                break
            try:
                response, resumed = _open_stream(url, downloaded)
                # レジュームできなかった（サーバーがRange非対応）ときだけ先頭から書き直す
                mode = "ab" if resumed else "wb"
                if not resumed:
                    downloaded = 0
                with response, open(dest_path, mode) as out_file:
                    while True:
                        if cancel.is_set():
                            cancelled = True
                            break
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded += len(chunk)
                if cancelled:
                    break
                print(f"[comfy_panel_downloader] done: {dest_path} ({downloaded} bytes)")
                return
            except Exception as e:
                if cancel.is_set():
                    cancelled = True
                    break
                # 実際に書けたバイト数を見てから次を要求する（途中までバッファに残っている場合に備える）
                try:
                    downloaded = os.path.getsize(dest_path)
                except OSError:
                    downloaded = 0
                if attempt >= MAX_ATTEMPTS:
                    print(f"[comfy_panel_downloader] giving up after {attempt} attempts: {dest_path}: {e}")
                    return
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                print(f"[comfy_panel_downloader] attempt {attempt} failed ({e}); "
                      f"resuming from {downloaded} bytes in {delay}s: {dest_path}")
                # 待っている間も中止できるようにEventのwaitで眠る
                if cancel.wait(delay):
                    cancelled = True
                    break

        if cancelled:
            # 書きかけのファイルは消す。残すと「配置済み」に見えてしまい、
            # 生成時に壊れたモデルを読み込もうとして分かりにくい失敗になる。
            try:
                os.remove(dest_path)
            except OSError:
                pass
            print(f"[comfy_panel_downloader] cancelled: {dest_path}")
    except Exception as e:
        print(f"[comfy_panel_downloader] failed: {url} -> {dest_path}: {e}")
    finally:
        with _active_lock:
            _active.pop(dest_path, None)


def _resolve_dest(data):
    """リクエストのfolder/filenameから保存先パスを作る。問題があればエラー文字列を返す。"""
    folder = (data.get("folder") or "").strip().strip("/")
    filename = (data.get("filename") or "").strip()
    if not folder or not filename:
        return None, "folder, filename are required"
    if ".." in filename or ".." in folder:
        return None, "invalid path"
    return os.path.join(folder_paths.models_dir, folder, filename), None


@PromptServer.instance.routes.post("/comfy_panel/download")
async def comfy_panel_download(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    url = (data.get("url") or "").strip()
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    if "://" not in url:
        return web.json_response({"error": "invalid url"}, status=400)

    dest_path, err = _resolve_dest(data)
    if err:
        return web.json_response({"error": err}, status=400)

    with _active_lock:
        if dest_path in _active:
            # 同じ宛先に2本走るとファイルが壊れるので、重複は始めずに既存を続けさせる
            return web.json_response({"success": True, "path": dest_path, "alreadyRunning": True})
        cancel = threading.Event()
        _active[dest_path] = {"cancel": cancel, "url": url}

    # 再開始のときに前回の書きかけが残っていると、それを「落とし済み」と誤認して
    # 途中から追記してしまう。開始時は必ず消してから始める
    # （途中から再開するのは、同じスレッドが切断を検知した場合だけ）。
    try:
        os.remove(dest_path)
    except OSError:
        pass

    thread = threading.Thread(target=_download, args=(url, dest_path, cancel), daemon=True)
    thread.start()

    return web.json_response({"success": True, "path": dest_path})


@PromptServer.instance.routes.post("/comfy_panel/cancel")
async def comfy_panel_cancel(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    dest_path, err = _resolve_dest(data)
    if err:
        return web.json_response({"error": err}, status=400)

    with _active_lock:
        entry = _active.get(dest_path)
        if entry is not None:
            entry["cancel"].set()

    if entry is None:
        # 既に終わっている/そもそも走っていない場合。画面側は「止めたい」だけなので、
        # 書きかけのファイルが残っていればここで消しておく（中止扱いで成功を返す）。
        removed = False
        try:
            os.remove(dest_path)
            removed = True
        except OSError:
            pass
        return web.json_response({"success": True, "wasRunning": False, "removedFile": removed})

    return web.json_response({"success": True, "wasRunning": True})


@PromptServer.instance.routes.get("/comfy_panel/downloads")
async def comfy_panel_downloads(request):
    with _active_lock:
        paths = sorted(_active.keys())
    return web.json_response({
        "success": True,
        "downloads": [os.path.relpath(p, folder_paths.models_dir).replace("\\", "/") for p in paths],
    })

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
    threading.Eventを立て、チャンクを書くループが自分で気づいて抜ける方式にしている
    （8MBチャンクごとに見るので、押してから実際に止まるまで最大1チャンクぶんの間がある）。

  GET /comfy_panel/downloads
    → 今動いているダウンロードの一覧。ページを開き直した後でも「何が走っているか」を
    画面側が復元できるようにするためのもの。
"""

import os
import threading
import urllib.request

from aiohttp import web
from server import PromptServer
import folder_paths

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

# 進行中のダウンロード。 dest_path -> {"cancel": Event, "url": str}
# ダウンロード開始/終了/中止でしか触らないので、ロック1本で十分。
_active = {}
_active_lock = threading.Lock()


def _download(url: str, dest_path: str, cancel: threading.Event):
    # comfy-panel側の進捗表示は/experiment/models/<folder>（本番のファイル名でサイズを見る）を
    # ポーリングする仕組みなので、一時ファイル名(.part等)を使わず本番のファイル名に直接書き込む。
    # 個人用ツールなので、失敗時に不完全なファイルが残るリスクより進捗が見えることを優先する。
    cancelled = False
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "comfy-panel/1.0"})
        with urllib.request.urlopen(req) as response, open(dest_path, "wb") as out_file:
            while True:
                if cancel.is_set():
                    cancelled = True
                    break
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)
        if cancelled:
            # 書きかけのファイルは消す。残すと「配置済み」に見えてしまい、
            # 生成時に壊れたモデルを読み込もうとして分かりにくい失敗になる。
            try:
                os.remove(dest_path)
            except OSError:
                pass
            print(f"[comfy_panel_downloader] cancelled: {dest_path}")
        else:
            print(f"[comfy_panel_downloader] done: {dest_path}")
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

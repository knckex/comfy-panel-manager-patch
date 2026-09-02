"""
comfy-panel専用の最小カスタムノード。

ComfyUI-Managerの/manager/queue/install_modelは、自分の配布DB(model-list.json)に
完全一致するURLしか受け付けない仕様（実機検証済み）で、任意のカスタムURLは拒否される。
これを迂回するため、ComfyUI自体のWebサーバー（aiohttp）に自前のダウンロード用エンドポイントを
1本追加する。ComfyUI-Managerは一切経由しない。

エンドポイント: POST /comfy_panel/download
  body: {"url": "...", "folder": "checkpoints/custom", "filename": "xxx.safetensors"}
  → models/<folder>/<filename> にバックグラウンドでダウンロードを開始し、即座に返答する。
  進捗は既存の /experiment/models/<folder最初のセグメント> をポーリングすれば追える
  （ComfyUI標準機能。ダウンロード中でもその時点のバイト数を返してくれる）。
"""

import os
import threading
import urllib.request

from aiohttp import web
from server import PromptServer
import folder_paths

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def _download(url: str, dest_path: str):
    # comfy-panel側の進捗表示は/experiment/models/<folder>（本番のファイル名でサイズを見る）を
    # ポーリングする仕組みなので、一時ファイル名(.part等)を使わず本番のファイル名に直接書き込む。
    # 個人用ツールなので、失敗時に不完全なファイルが残るリスクより進捗が見えることを優先する。
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "comfy-panel/1.0"})
        with urllib.request.urlopen(req) as response, open(dest_path, "wb") as out_file:
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)
        print(f"[comfy_panel_downloader] done: {dest_path}")
    except Exception as e:
        print(f"[comfy_panel_downloader] failed: {url} -> {dest_path}: {e}")


@PromptServer.instance.routes.post("/comfy_panel/download")
async def comfy_panel_download(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    url = (data.get("url") or "").strip()
    folder = (data.get("folder") or "").strip().strip("/")
    filename = (data.get("filename") or "").strip()

    if not url or not folder or not filename:
        return web.json_response({"error": "url, folder, filename are required"}, status=400)
    if "://" not in url:
        return web.json_response({"error": "invalid url"}, status=400)
    if ".." in filename or ".." in folder:
        return web.json_response({"error": "invalid path"}, status=400)

    dest_dir = os.path.join(folder_paths.models_dir, folder)
    dest_path = os.path.join(dest_dir, filename)

    thread = threading.Thread(target=_download, args=(url, dest_path), daemon=True)
    thread.start()

    return web.json_response({"success": True, "path": dest_path})

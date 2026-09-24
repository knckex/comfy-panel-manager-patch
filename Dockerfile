# comfy-panel専用の派生イメージ。やっていることは2つだけ:
#   1. 焼き込み済みComfyUI本体を、LTX-2.5が動くバージョン以上へ引き上げる
#   2. ComfyUI-Managerを経由しない自前のモデルダウンロードAPIを追加する
# モデル本体は一切焼き込まない（今まで通りgenerate系の画面から配置する）。
#
# 背景1（ダウンロードAPI）: ComfyUI-Managerの/manager/queue/install_modelは、自分の配布DBに
# 完全一致するURLしか受け付けない仕様（実機検証済み。security_level/channel_urlの変更では回避できない）。
# そこでComfyUI自体のWebサーバーに、自前の /comfy_panel/download エンドポイントを1本追加する。
#
# 背景2（ComfyUIの引き上げ）: runpod/comfyui:latest が積んでいたComfyUI 0.30.0では、
# LTX-2.5のGemma4テキストエンコーダーが動かない。comfy/text_encoders/gemma4.py の process_tokens が
# 戻り値1個なのに本体側は4個を期待しており、CLIPTextEncodeで
# "not enough values to unpack (expected 4, got 1)" になる（実機で確認）。
# v0.31.1以前はすべて同じ壊れ方で、v0.32.0でこの上書き自体が削除されて直っている。
# なおPod上でComfyUI-Manager経由で更新しても、起動時に下記の /opt/comfyui-baked から
# 同期し直される作りのため巻き戻る。だからここ（イメージ側）で上げる必要がある。
#
# 配置先の /opt/comfyui-baked は、start.shが起動時に /workspace/.../ComfyUI へ
# 同期する"焼き込み済みComfyUI"の実体。
FROM runpod/comfyui:latest

# 必要最低バージョン。ベース側がこれ以上なら何もしない（勝手に下げない）
ARG COMFYUI_VERSION=v0.33.1

RUN set -eu; \
    cd /opt/comfyui-baked; \
    want="$(printf '%s' "$COMFYUI_VERSION" | tr -d 'v')"; \
    cur="$(sed -n 's/.*__version__ *= *"\([0-9.]*\)".*/\1/p' comfyui_version.py 2>/dev/null || true)"; \
    echo "baked ComfyUI = ${cur:-unknown} / required >= ${want}"; \
    need=0; \
    if [ -z "$cur" ]; then \
        need=1; \
    elif [ "$cur" != "$want" ] && [ "$(printf '%s\n%s\n' "$cur" "$want" | sort -V | head -n1)" = "$cur" ]; then \
        need=1; \
    fi; \
    if [ "$need" = "1" ]; then \
        echo "upgrading ComfyUI to ${COMFYUI_VERSION}"; \
        curl -fsSL "https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/${COMFYUI_VERSION}.tar.gz" -o /tmp/comfyui.tgz; \
        # custom_nodes・models・user等はベース側の中身を残す（ComfyUI-Manager等の同梱ノードを消さないため）
        tar -xzf /tmp/comfyui.tgz -C /opt/comfyui-baked --strip-components=1 \
            --exclude='*/custom_nodes' --exclude='*/models' --exclude='*/user' \
            --exclude='*/input' --exclude='*/output'; \
        rm -f /tmp/comfyui.tgz; \
        pip install --no-cache-dir -r requirements.txt; \
        echo "upgraded ComfyUI = $(sed -n 's/.*__version__ *= *"\([0-9.]*\)".*/\1/p' comfyui_version.py)"; \
    else \
        echo "no upgrade needed"; \
    fi

COPY comfy_panel_downloader /opt/comfyui-baked/custom_nodes/comfy_panel_downloader

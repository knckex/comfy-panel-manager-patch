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

# LTX-2.3の「Face ID」モード（参照画像で同じ人物の動画を作る）に必要なカスタムノード。
# 使うのは ComfyUI-BFSNodes の LTXIdentityTransfer（= LTXIdentityOverlapConditioning）1個だけ。
# これはLTX-Best-Face-ID LoRA（Alissonerdx）が学習した座標の約束事
# （参照latentをframe-0のRoPEグリッドに重ね、source_id=2の回転位相タグを付ける）を再現するノードで、
# これを通さないとLoRAを読み込んでも同一性転写はまったく効かない。
#
# 稼働中のPodにはComfyUI-Manager経由でも入れられる（Comfy Registryに bfsnodes として登録済み）が、
# start.shが起動時に /opt/comfyui-baked から同期し直す作りのため、消える可能性がある。だからここで焼き込む。
#
# requirements.txt（insightface / onnxruntime / librosa / opencv / scenedetect）は入れない。
# Face IDに使うモジュール ltx_identity_overlap.py の依存は torch / numpy / safetensors だけで、
# __init__.py が無条件に読み込む他のモジュールもこれらを関数内でしか import していないため
# （実機の /object_info にLTXIdentityTransferが出ることを確認済み）。
# insightfaceはArcFace projector（作者いわく効果は限定的で任意）専用で、ビルドも重いので省く。
ARG BFSNODES_COMMIT=90face345c168862f0b7aa9154dc49d9a52db536
RUN set -eu; \
    git clone --filter=blob:none --no-checkout https://github.com/alisson-anjos/ComfyUI-BFSNodes.git \
        /opt/comfyui-baked/custom_nodes/ComfyUI-BFSNodes; \
    cd /opt/comfyui-baked/custom_nodes/ComfyUI-BFSNodes; \
    git checkout -q "$BFSNODES_COMMIT"; \
    rm -rf .git; \
    echo "BFSNodes pinned at $BFSNODES_COMMIT"
